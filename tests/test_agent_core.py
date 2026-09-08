"""Tests for bounded structured Agent routing and chat integration."""

import unittest
from unittest.mock import Mock, patch

from app.agent_core import (
    AgentCore,
    build_default_tool_registry,
    should_use_agent,
)
from app.ai_service import stream_normal_chat
from app.personas import get_persona
from app.tool_registry import ToolRegistry


QUERY_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {"type": "string", "minLength": 1, "maxLength": 100},
        "memory_type": {
            "type": "string",
            "enum": ["all", "personal", "project", "conversation"],
        },
    },
    "required": ["query"],
    "additionalProperties": False,
}


class SequencePlanner:
    """Return deterministic structured decisions without contacting Ollama."""

    def __init__(self, decisions):
        self.decisions = list(decisions)
        self.calls = []

    def decide(self, user_request, tools, history):
        self.calls.append({
            "request": user_request,
            "tools": tools,
            "history": list(history),
        })
        return self.decisions.pop(0)


def build_registry(search_knowledge=None, search_memory=None):
    """Build a small approved registry for isolated execution tests."""
    registry = ToolRegistry()
    registry.register_tool(
        "search_knowledge",
        "Search test knowledge.",
        QUERY_SCHEMA,
        search_knowledge or (lambda **arguments: arguments),
    )
    registry.register_tool(
        "search_memory",
        "Search test memory.",
        QUERY_SCHEMA,
        search_memory or (lambda **arguments: arguments),
    )
    return registry


class AgentCoreTests(unittest.TestCase):
    """Verify safe tool choice, bounded looping, and Prompt integration."""

    def test_ordinary_chat_bypasses_agent_and_tools(self):
        agent = Mock()

        with (
            patch("app.ai_service.prepare_memory_messages", return_value=[]),
            patch("app.ai_service.finalize_chat_memory"),
            patch("app.ai_service._stream_ollama") as mock_stream,
        ):
            stream_normal_chat(
                "你好，今天过得怎么样？",
                lambda token: None,
                agent_core=agent,
            )

        agent.run.assert_not_called()
        mock_stream.assert_called_once()

    def test_knowledge_request_calls_search_knowledge(self):
        calls = []
        planner = SequencePlanner([
            {
                "tool": "search_knowledge",
                "arguments": {"query": "向量数据库"},
            },
            {"tool": None, "arguments": {}},
        ])
        registry = build_registry(
            search_knowledge=lambda **arguments: calls.append(arguments) or {
                "matches": ["result"]
            },
        )

        result = AgentCore(registry=registry, planner=planner).run(
            "请查询知识库中的向量数据库资料"
        )

        self.assertEqual(calls, [{"query": "向量数据库"}])
        self.assertEqual(result["tool_calls"][0]["tool"], "search_knowledge")
        self.assertEqual(result["tool_calls"][0]["status"], "ok")

    def test_project_history_request_calls_search_memory(self):
        calls = []
        planner = SequencePlanner([
            {
                "tool": "search_memory",
                "arguments": {
                    "query": "Personal AI 项目进度",
                    "memory_type": "project",
                },
            },
            {"tool": None, "arguments": {}},
        ])
        registry = build_registry(
            search_memory=lambda **arguments: calls.append(arguments) or {
                "matches": ["Phase 9 completed"]
            },
        )

        result = AgentCore(registry=registry, planner=planner).run(
            "查询 Personal AI 项目历史"
        )

        self.assertEqual(calls, [{
            "query": "Personal AI 项目进度",
            "memory_type": "project",
        }])
        self.assertEqual(result["tool_calls"][0]["tool"], "search_memory")

    def test_agent_executes_at_most_three_tools(self):
        calls = []

        def repeated_decision(user_request, tools, history):
            return {
                "tool": "search_knowledge",
                "arguments": {"query": "repeat"},
            }

        registry = build_registry(
            search_knowledge=lambda **arguments: calls.append(arguments) or {},
        )
        result = AgentCore(
            registry=registry,
            planner=repeated_decision,
        ).run("查询知识库")

        self.assertEqual(len(calls), 3)
        self.assertEqual(len(result["tool_calls"]), 3)
        self.assertTrue(result["limit_reached"])

    def test_tool_exception_becomes_context_and_does_not_crash(self):
        def fail(**arguments):
            raise OSError("index unavailable")

        planner = SequencePlanner([
            {
                "tool": "search_knowledge",
                "arguments": {"query": "failure"},
            },
            {"tool": None, "arguments": {}},
        ])
        result = AgentCore(
            registry=build_registry(search_knowledge=fail),
            planner=planner,
        ).run("查询知识库")

        self.assertEqual(result["tool_calls"][0]["status"], "error")
        self.assertIn("index unavailable", result["error"])
        self.assertIn("BEGIN_AGENT_TOOL_DATA", result["context"])

    def test_unregistered_tool_decision_is_rejected(self):
        planner = SequencePlanner([
            {"tool": "run_shell", "arguments": {}},
        ])

        result = AgentCore(
            registry=build_registry(),
            planner=planner,
        ).run("查询知识库")

        self.assertEqual(result["tool_calls"], [])
        self.assertIn("not registered", result["error"])
        self.assertIn("agent_error", result["context"])

    def test_default_registry_contains_only_phase_10a_read_tools(self):
        names = {
            tool["name"] for tool in build_default_tool_registry().get_tools()
        }

        self.assertEqual(names, {
            "search_knowledge",
            "search_memory",
            "scan_knowledge_sources",
            "list_knowledge_files",
        })
        self.assertNotIn("shell", names)

    @patch("app.ai_service._stream_ollama")
    def test_agent_context_preserves_persona_and_precedes_user(self, mock_stream):
        agent = Mock()
        agent.run.return_value = {
            "tool_calls": [{"tool": "search_knowledge", "status": "ok"}],
            "context": "BEGIN_AGENT_TOOL_DATA\ntrusted result\nEND_AGENT_TOOL_DATA",
            "limit_reached": False,
            "error": None,
        }
        states = []

        with (
            patch("app.ai_service.prepare_memory_messages", return_value=[]),
            patch("app.ai_service.finalize_chat_memory"),
        ):
            stream_normal_chat(
                "请查询知识库中的向量数据库资料",
                lambda token: None,
                persona=get_persona("fairy"),
                on_state=states.append,
                agent_core=agent,
            )

        messages = mock_stream.call_args.args[0]
        self.assertIn("身份是 Fairy", messages[0]["content"])
        self.assertEqual(messages[-2]["role"], "system")
        self.assertIn("BEGIN_AGENT_TOOL_DATA", messages[-2]["content"])
        self.assertEqual(messages[-1]["role"], "user")
        self.assertEqual(states, ["searching", "thinking"])
        agent.log_final_response.assert_called_once_with()

    def test_route_guard_targets_only_local_data_requests(self):
        self.assertFalse(should_use_agent("解释一下 RAG 是什么"))
        self.assertTrue(should_use_agent("查询知识库中的 RAG 资料"))
        self.assertTrue(should_use_agent("查看 Personal AI 项目历史"))


if __name__ == "__main__":
    unittest.main()
