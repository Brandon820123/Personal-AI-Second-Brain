"""Phase 10F routing, context, idempotency and deadline regressions."""

import copy
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from app.agent_core import AgentCore, AgentConfirmationError, route_request
from app.agent_task_control import AgentTaskControl
from app.agent_task_store import AgentTaskStore
from app.conversation_store import ConversationStore
from app.personas import get_persona
from app.tool_registry import ToolRegistry


def step(tool, text="Physics"):
    return {"tool": tool, "arguments": {"text": text}}


class StaticPlanner:
    def __init__(self, steps):
        self.steps = steps
        self.contexts = []

    def plan(self, goal, tools, context):
        self.contexts.append(copy.deepcopy(context))
        return {"goal": goal, "steps": copy.deepcopy(self.steps)}

    def decide(self, goal, tools, context):
        self.contexts.append(copy.deepcopy(context))
        return {"tool": tools[0]["name"], "arguments": {"text": "Physics"}}


class StabilityTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = AgentTaskStore(Path(self.directory.name) / "tasks.db")
        self.reads = []
        self.writes = []

    def agent(self, steps, read=None, write=None, timeout=2):
        registry = ToolRegistry()
        schema = {"type": "object", "properties": {"text": {"type": "string"}},
                  "required": ["text"], "additionalProperties": False}
        read = read or (lambda text: self.reads.append(text) or {"summary": text})
        write = write or (lambda text: self.writes.append(text) or {"saved": text})
        for name in ("search_knowledge", "summarize_knowledge", "search_memory"):
            registry.register_tool(name, name, schema, read)
        for name in ("create_note", "create_todo", "update_memory"):
            registry.register_tool(name, name, schema, write, read_only=False, requires_confirmation=True)
        return AgentCore(registry, StaticPlanner(steps), tool_timeout=timeout)

    def test_a_followups_preserve_context_without_agent_or_task(self):
        from app import gui
        store = ConversationStore(Path(self.directory.name) / "conversation.db")
        store.initialize()
        conversation = store.create("fairy")
        payloads = []
        agent = Mock()
        def stream(messages, callback):
            payloads.append(copy.deepcopy(messages))
            callback(["A GPU processes graphics in parallel.", "Parallel graphics processor.", "Graphics processor."][len(payloads) - 1])
        with patch("app.gui.AgentCore", return_value=agent), \
             patch("app.ai_service.prepare_memory_messages", return_value=[]), \
             patch("app.ai_service.finalize_chat_memory"), \
             patch("app.ai_service._stream_ollama", side_effect=stream):
            for message in ("What is GPU?", "shorter", "even shorter"):
                gui.run_persisted_chat(store, conversation["id"], gui.stream_normal_chat, message,
                    on_token=lambda token: None, persona=get_persona("fairy"), task_store=self.store)
        agent.run.assert_not_called()
        self.assertEqual(self.store.list_tasks(), [])
        self.assertIn("What is GPU?", str(payloads[2]))
        self.assertIn("Parallel graphics processor.", str(payloads[2]))
        self.assertEqual(payloads[2][-1]["content"], "even shorter")

    def test_routes_do_not_confuse_definitions_and_compound_topics(self):
        for message in ("What is GPU?", "A shorter answer?", "Explain the second one.",
                        "What is memory?", "What are notes?", "什么是知识库？"):
            self.assertEqual(route_request(message), "normal", message)
        self.assertEqual(route_request("查询知识库中的 Physics"), "knowledge")
        self.assertEqual(route_request("Search my knowledge base for Physics and Chemistry"), "knowledge")
        self.assertEqual(route_request("查询记忆中的项目历史"), "memory")
        self.assertEqual(route_request("what do you remember"), "memory")
        self.assertEqual(route_request("搜索 Physics 资料并总结"), "agent")

    def test_b_duplicate_reads_reuse_valid_results(self):
        agent = self.agent([step("search_knowledge"), step("search_knowledge"), step("summarize_knowledge")])
        result = agent.run("搜索 Physics 资料并总结")
        self.assertEqual(result["state"], "COMPLETE")
        self.assertEqual(len(self.reads), 2)
        self.assertTrue(result["tool_calls"][1]["cached"])
        self.assertEqual(agent._plan_execution.count, 2)

    def test_c_identical_write_is_confirmed_and_executed_only_once(self):
        agent = self.agent([step("search_knowledge"), step("create_note"), step("create_note")])
        result = agent.run("搜索资料并创建笔记")
        confirmation = result["pending_confirmation"]["confirmation_id"]
        self.assertEqual(self.writes, [])
        result = agent.confirm_action(confirmation, True)
        self.assertEqual(result["state"], "COMPLETE")
        self.assertEqual(self.writes, ["Physics"])
        with self.assertRaises(AgentConfirmationError):
            agent.confirm_action(confirmation, True)

    def test_concurrent_confirmation_is_rejected_without_corrupting_first(self):
        entered, release = threading.Event(), threading.Event()
        def write(text):
            entered.set()
            release.wait(2)
            self.writes.append(text)
            return {"saved": text}
        agent = self.agent([step("create_note")], write=write)
        result = agent.run("整理资料并创建笔记")
        confirmation = result["pending_confirmation"]["confirmation_id"]
        results = []
        thread = threading.Thread(target=lambda: results.append(agent.confirm_action(confirmation, True)))
        thread.start()
        try:
            self.assertTrue(entered.wait(1))
            with self.assertRaises(AgentConfirmationError):
                agent.confirm_action(confirmation, True)
        finally:
            release.set()
            thread.join(3)
        self.assertEqual(self.writes, ["Physics"])
        self.assertEqual(results[0]["state"], "COMPLETE")

    def test_write_invalidates_potentially_stale_read_cache(self):
        agent = self.agent([step("search_memory"), step("update_memory"), step("search_memory")])
        result = agent.run("查询记忆并更新记忆")
        result = agent.confirm_action(result["pending_confirmation"]["confirmation_id"], True)
        self.assertEqual(result["state"], "COMPLETE")
        self.assertEqual(len(self.reads), 2)

    def test_d_cancel_after_first_step_blocks_every_remaining_tool(self):
        control = AgentTaskControl()
        def read(text):
            control.request_cancel()
            return {"summary": text}
        agent = self.agent([step("search_knowledge"), step("create_note")], read=read)
        agent._task_control = control
        result = agent.run("搜索资料并创建笔记")
        self.assertEqual(result["state"], "CANCELLED")
        self.assertIsNone(result["pending_confirmation"])
        self.assertEqual(self.writes, [])

    def test_e_timeout_is_terminal_and_does_not_retry_or_cache_late_results(self):
        release, ended = threading.Event(), threading.Event()
        def read(text):
            self.reads.append(text)
            release.wait(2)
            ended.set()
            return {"summary": "late result"}
        agent = self.agent([step("search_knowledge"), step("create_note")], read=read, timeout=0.1)
        started = time.monotonic()
        try:
            result = agent.run("搜索资料并创建笔记")
            self.assertLess(time.monotonic() - started, 1.5)
            self.assertEqual(result["state"], "FAILED")
            self.assertIn("超时", result["error"])
            self.assertEqual(result["plan"]["steps"][0]["status"], "error")
        finally:
            release.set()
        self.assertTrue(ended.wait(1))
        self.assertEqual(len(self.reads), 1)
        self.assertEqual(self.writes, [])
        self.assertEqual(agent._result_cache, {})
        self.assertEqual(agent.state, "FAILED")

    def test_step_context_contains_original_goal_prior_results_and_references(self):
        agent = self.agent([step("search_knowledge"), {"tool": "summarize_knowledge"}])
        goal = "搜索 Physics 资料并总结"
        result = agent.run(goal, conversation_context=[{"role": "user", "content": "We study Physics"}],
                           relevant_memory=[{"content": "Prefer mechanics"}])
        context = agent.planner.contexts[-1][0]
        self.assertEqual(result["state"], "COMPLETE")
        self.assertEqual(context["original_goal"], goal)
        self.assertIn("Physics", str(context["completed_step_results"]))
        self.assertIn("mechanics", context["relevant_memory"])
        self.assertIn("We study", context["recent_conversation"])
        self.assertEqual(len(context["current_plan"]["steps"]), 2)

    def test_read_route_rejects_model_selected_writes(self):
        agent = self.agent([])
        agent.planner.decide = lambda *args: {"tool": "create_note", "arguments": {"text": "Physics"}}
        result = agent.run("查询知识库中的 Physics")
        self.assertIsNone(result["pending_confirmation"])
        self.assertEqual(self.writes, [])
        self.assertIn("route", result["error"])

    def test_debug_log_records_task_lifecycle(self):
        agent = self.agent([step("search_knowledge"), step("create_note")])
        with self.assertLogs("app.agent_core", level="DEBUG") as captured:
            result = agent.run("搜索资料并创建笔记")
            agent.confirm_action(result["pending_confirmation"]["confirmation_id"], True)
        text = "\n".join(captured.output)
        for expected in ("Routing decision", "Plan ->", "Tool call", "Tool result", "Confirmation", "COMPLETE"):
            self.assertIn(expected, text)
