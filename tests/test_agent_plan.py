"""Phase 10C acceptance and safety tests without Ollama or real writes."""

import copy
import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.agent_core import AgentCore, AgentConfirmationError, should_use_agent
from app.ai_service import stream_normal_chat, stream_confirmed_agent_action
from app.tool_registry import ToolRegistry


class Planner:
    def __init__(self, steps):
        self.steps = steps
        self.contexts = []

    def plan(self, goal, tools, context):
        self.contexts.append(context)
        return {"goal": goal, "steps": copy.deepcopy(self.steps)}

    def decide(self, goal, tools, history):
        self.contexts.append(history[0])
        index = history[1]["execute_step"]
        previous = history[0]["current_plan"]["steps"][:index]
        repeated = sum(step["tool"] == tools[0]["name"] for step in previous)
        text = "Physics review" + (f" {repeated + 1}" if repeated else "")
        return {"tool": tools[0]["name"], "arguments": {"text": text}}


def step(tool, **kwargs):
    return {"tool": tool, **kwargs}


class PlanTests(unittest.TestCase):
    def setUp(self):
        from app.agent_task_store import AgentTaskStore
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        patcher = patch("app.ai_service.AgentTaskStore", return_value=AgentTaskStore(Path(directory.name) / "tasks.db"))
        patcher.start()
        self.addCleanup(patcher.stop)

    def make_agent(self, steps, failure=None, limit=5):
        self.writes = []
        self.reads = []
        registry = ToolRegistry()
        schema = {"type": "object", "properties": {"text": {"type": "string"}},
                  "required": ["text"], "additionalProperties": False}

        def read(text):
            self.reads.append(text)
            if failure:
                raise RuntimeError("offline")
            return {"source": "Physics.md", "content": "Force equals mass times acceleration."}

        for name in ("search_knowledge", "summarize_knowledge"):
            registry.register_tool(name, name, schema, read)
        for name in ("create_note", "create_todo", "update_memory", "complete_todo"):
            registry.register_tool(name, name, schema,
                                   lambda text: self.writes.append(text) or {"saved": text},
                                   read_only=False, requires_confirmation=True)
        return AgentCore(registry, Planner(steps), max_tool_calls=limit)

    def test_a_read_steps_and_context(self):
        agent = self.make_agent([step("search_knowledge"), step("summarize_knowledge")])
        states = []
        result = agent.run("搜索 Physics 资料并总结重点", on_state=states.append,
                           conversation_context=[{"role": "user", "content": "Physics"}],
                           relevant_memory=[{"content": "Learning mechanics"}])
        self.assertEqual(result["state"], "COMPLETE")
        self.assertEqual(len(self.reads), 2)
        self.assertEqual(states, ["PLANNING", "EXECUTING", "COMPLETE"])
        context = agent.planner.contexts[-1]
        self.assertIn("Physics.md", str(context["completed_step_results"]))
        self.assertIn("Learning mechanics", context["relevant_memory"])
        self.assertIn("Physics", context["recent_conversation"])
        self.assertEqual(context["original_goal"], result["plan"]["goal"])

    def test_b_note_waits_and_payload_is_frozen(self):
        agent = self.make_agent([step("search_knowledge"), step("create_note")])
        result = agent.run("整理 Physics 资料并创建复习笔记")
        self.assertEqual(len(self.reads), 1)
        self.assertEqual(self.writes, [])
        self.assertEqual(result["state"], "WAITING_CONFIRMATION")
        pending = result["pending_confirmation"]
        pending["arguments"]["text"] = "tampered"
        result = agent.confirm_action(pending["confirmation_id"], True)
        self.assertEqual(self.writes, ["Physics review"])
        self.assertEqual(result["state"], "COMPLETE")
        self.assertEqual(len(result["tool_calls"]), 2)
        with self.assertRaises(AgentConfirmationError):
            agent.confirm_action(pending["confirmation_id"], True)

    def test_c_three_todos_require_three_approvals(self):
        agent = self.make_agent([step("create_todo") for _ in range(3)])
        result = agent.run("帮我创建三个复习待办")
        for count in range(3):
            self.assertEqual(len(self.writes), count)
            self.assertEqual(result["state"], "WAITING_CONFIRMATION")
            result = agent.confirm_action(result["pending_confirmation"]["confirmation_id"], True)
        self.assertEqual(len(self.writes), 3)
        self.assertEqual(result["state"], "COMPLETE")

    def test_d_failure_retries_once_and_stops_dependencies(self):
        agent = self.make_agent([step("search_knowledge"), step("create_note")], failure=True)
        result = agent.run("整理 Physics 资料并创建复习笔记")
        self.assertEqual(len(self.reads), 2)
        self.assertEqual(result["state"], "FAILED")
        self.assertEqual(self.writes, [])
        self.assertIn("offline", result["error"])

    def test_e_simple_question_bypasses_planner(self):
        self.assertFalse(should_use_agent("What is GPU?"))
        agent = Mock()
        with patch("app.ai_service.prepare_memory_messages", return_value=[]), \
             patch("app.ai_service.finalize_chat_memory"), patch("app.ai_service._stream_ollama"):
            stream_normal_chat("What is GPU?", lambda token: None, agent_core=agent)
        agent.run.assert_not_called()

    def test_optional_failures_obey_global_budget(self):
        agent = self.make_agent([step("search_knowledge", critical=False) for _ in range(5)], failure=True)
        result = agent.run("搜索 Physics 资料并总结重点")
        self.assertEqual(len(self.reads), 5)
        self.assertTrue(result["limit_reached"])
        self.assertEqual(result["state"], "FAILED")

    def test_oversized_plan_rejected_before_execution(self):
        agent = self.make_agent([step("create_todo") for _ in range(6)])
        result = agent.run("帮我创建三个复习待办")
        self.assertEqual(result["state"], "FAILED")
        self.assertEqual(self.writes, [])

    def test_cancel_stops_remaining_writes(self):
        agent = self.make_agent([step("create_todo") for _ in range(3)])
        result = agent.run("帮我创建三个复习待办")
        result = agent.confirm_action(result["pending_confirmation"]["confirmation_id"], True)
        result = agent.confirm_action(result["pending_confirmation"]["confirmation_id"], False)
        self.assertEqual(self.writes, ["Physics review"])
        self.assertEqual(result["state"], "FAILED")

    def test_service_does_not_answer_before_next_confirmation(self):
        agent = self.make_agent([step("create_todo") for _ in range(3)])
        result = agent.run("帮我创建三个复习待办")
        with patch("app.ai_service._stream_ollama") as stream:
            result = stream_confirmed_agent_action("帮我创建三个复习待办",
                result["pending_confirmation"]["confirmation_id"], agent, lambda token: None)
        self.assertEqual(result["state"], "WAITING_CONFIRMATION")
        stream.assert_not_called()

    def test_all_protected_tools_wait(self):
        for name in ("create_note", "update_memory", "create_todo", "complete_todo"):
            agent = self.make_agent([step(name)])
            result = agent.run("整理资料并更新记忆和待办")
            self.assertEqual(result["state"], "WAITING_CONFIRMATION")
            self.assertEqual(self.writes, [])

    def test_budget_survives_confirmation_pause(self):
        agent = self.make_agent([step("search_knowledge"), step("create_note"), step("create_todo")], limit=2)
        result = agent.run("整理 Physics 资料并创建复习笔记")
        result = agent.confirm_action(result["pending_confirmation"]["confirmation_id"], True)
        self.assertEqual(result["state"], "FAILED")
        self.assertEqual(len(self.writes), 1)
        self.assertEqual(len(result["tool_calls"]), 2)

    def test_write_failure_is_not_retried(self):
        agent = self.make_agent([step("create_todo"), step("create_todo")])
        result = agent.run("帮我创建三个复习待办")
        with patch.object(agent.registry, "execute_tool", side_effect=RuntimeError("write failed")) as execute:
            result = agent.confirm_action(result["pending_confirmation"]["confirmation_id"], True)
        execute.assert_called_once()
        self.assertEqual(result["state"], "FAILED")

    def test_new_request_invalidates_old_approval(self):
        agent = self.make_agent([step("create_todo")])
        old = agent.run("帮我创建三个复习待办")["pending_confirmation"]["confirmation_id"]
        agent.run("帮我创建三个复习待办")
        with self.assertRaises(AgentConfirmationError):
            agent.confirm_action(old, True)
        self.assertEqual(self.writes, [])

    def test_optional_failed_read_can_continue(self):
        agent = self.make_agent([step("search_knowledge", critical=False), step("create_todo")], failure=True)
        result = agent.run("整理资料并创建待办")
        self.assertEqual(result["state"], "WAITING_CONFIRMATION")
        self.assertEqual(len(result["tool_calls"]), 2)

    def test_gui_retains_agent_for_second_confirmation(self):
        from app.gui import MainWindow
        agent = self.make_agent([step("create_todo"), step("create_todo")])
        result = agent.run("帮我创建三个复习待办")
        result = agent.confirm_action(result["pending_confirmation"]["confirmation_id"], True)
        window = SimpleNamespace(current_ai_panel=object(), current_chat_mode="normal",
                                 active_agent_core=agent)
        MainWindow._chat_succeeded(window, dict(result, _agent_core=None))
        self.assertIs(window.active_agent_core, agent)
        self.assertEqual(window.pending_agent_confirmation, result["pending_confirmation"])
