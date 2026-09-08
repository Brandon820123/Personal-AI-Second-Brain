"""Tests for strict registration and execution of Agent tools."""

import unittest

from app.tool_registry import (
    ToolArgumentError,
    ToolExecutionError,
    ToolNotFoundError,
    ToolRegistry,
)


QUERY_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {"type": "string", "minLength": 1, "maxLength": 100},
        "limit": {"type": "integer", "minimum": 1, "maximum": 5},
    },
    "required": ["query"],
    "additionalProperties": False,
}


class ToolRegistryTests(unittest.TestCase):
    """Check metadata isolation, argument validation, and safe failures."""

    def setUp(self):
        self.registry = ToolRegistry()
        self.calls = []

        def search(query, limit=3):
            self.calls.append((query, limit))
            return {"query": query, "limit": limit}

        self.registry.register_tool(
            "search_local",
            "Search local test data.",
            QUERY_SCHEMA,
            search,
        )

    def test_registered_metadata_does_not_expose_callable(self):
        tools = self.registry.get_tools()

        self.assertEqual(len(tools), 1)
        self.assertEqual(tools[0]["name"], "search_local")
        self.assertNotIn("callable", tools[0])

    def test_execute_tool_validates_and_invokes_callable(self):
        result = self.registry.execute_tool(
            "search_local",
            {"query": "project", "limit": 2},
        )

        self.assertEqual(result, {"query": "project", "limit": 2})
        self.assertEqual(self.calls, [("project", 2)])

    def test_missing_extra_and_wrong_type_arguments_are_rejected(self):
        invalid_arguments = [
            {},
            {"query": "project", "unexpected": True},
            {"query": "project", "limit": "2"},
            {"query": "", "limit": 2},
            {"query": "project", "limit": 6},
        ]

        for arguments in invalid_arguments:
            with self.subTest(arguments=arguments):
                with self.assertRaises(ToolArgumentError):
                    self.registry.execute_tool("search_local", arguments)

        self.assertEqual(self.calls, [])

    def test_unknown_tool_is_rejected(self):
        with self.assertRaises(ToolNotFoundError):
            self.registry.execute_tool("run_shell", {})

    def test_callable_exception_is_wrapped(self):
        registry = ToolRegistry()

        def broken_tool():
            raise OSError("unavailable")

        registry.register_tool(
            "broken_tool",
            "Fail for testing.",
            {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
            broken_tool,
        )

        with self.assertRaisesRegex(ToolExecutionError, "could not complete"):
            registry.execute_tool("broken_tool", {})


if __name__ == "__main__":
    unittest.main()
