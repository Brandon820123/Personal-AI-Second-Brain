"""Offscreen task UI acceptance tests with actual Qt workers and fake tools."""

import os
import time
import threading
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QThread, Qt, Slot
from PySide6.QtGui import QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from app import gui
from app.agent_core import AgentCore
from app.agent_task_observer import AgentTaskObserver
from app.agent_task_store import AgentTaskStore
from app.tool_registry import ToolConfirmationRequiredError, ToolRegistry
from app.ui.agent_task_panel import AgentTaskPanel


class FixedPlanner:
    def __init__(self, names):
        self.names = names

    def plan(self, goal, tools, context):
        return {"goal": goal, "steps": [
            {"tool": name, "arguments": {"title": "Physics", "filename": "physics_review.md"}}
            for name in self.names
        ]}


def make_agent(names, writes, fail=False):
    registry = ToolRegistry()
    schema = {"type": "object", "properties": {
        "title": {"type": "string"}, "filename": {"type": "string"},
    }, "required": ["title", "filename"], "additionalProperties": False}

    def read(**kwargs):
        if fail:
            raise RuntimeError("Physics source unavailable")
        return {"summary": "Newton's laws", "source": "Physics.md"}

    for name in ("search_knowledge", "summarize_knowledge"):
        registry.register_tool(name, name, schema, read)
    registry.register_tool("create_note", "Create note", schema,
                           lambda **kwargs: writes.append(kwargs) or {"path": "data/notes/physics_review.md"},
                           read_only=False, requires_confirmation=True)
    return AgentCore(registry, FixedPlanner(names))


class TrackingWindow(gui.MainWindow):
    def __init__(self, **kwargs):
        self.task_snapshots = []
        self.task_threads = []
        super().__init__(**kwargs)

    @Slot(object)
    def _agent_task_updated(self, snapshot):
        self.task_snapshots.append(snapshot)
        self.task_threads.append(threading.get_ident())
        super()._agent_task_updated(snapshot)


class AgentTaskGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        # Windows' offscreen platform does not enumerate installed fonts.
        for filename in ("msyh.ttc", "seguisym.ttf"):
            font = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / filename
            if font.exists():
                QFontDatabase.addApplicationFont(str(font))

    def setUp(self):
        self.writes = []
        self.task_directory = tempfile.TemporaryDirectory()
        self.task_store = AgentTaskStore(Path(self.task_directory.name) / "tasks.db")
        self.patches = [
            patch("app.ai_service.prepare_memory_messages", return_value=[]),
            patch("app.ai_service.finalize_chat_memory"),
            patch("app.ai_service._stream_ollama", side_effect=lambda messages, callback: callback("Final answer")),
        ]
        for item in self.patches:
            item.start()
        store = Mock()
        store.context.return_value = []
        self.window = TrackingWindow(conversation_store=store, task_store=self.task_store)
        # Disable application startup, not task workers; no DB, network or audio.
        self.window._startup_started = True
        self.window.conversation_id = "test-conversation"
        self.window._begin_streaming_speech = Mock()
        self.window._finish_streaming_speech = Mock()
        self.window._set_chat_busy(False)
        self.window.chat_mode.setCurrentIndex(0)
        self.window.show()
        self.snapshots = self.window.task_snapshots
        self.callback_threads = self.window.task_threads

    def wait_until(self, predicate):
        deadline = time.monotonic() + 5
        while not predicate() and time.monotonic() < deadline:
            QTest.qWait(10)
        self.assertTrue(predicate(), "Qt task did not settle within 5 seconds")

    def tearDown(self):
        self.wait_until(lambda: not self.window.worker_threads)
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        for item in reversed(self.patches):
            item.stop()
        self.task_directory.cleanup()

    def send(self, text, names, fail=False):
        self.agent = make_agent(names, self.writes, fail)
        with patch("app.gui.AgentCore", return_value=self.agent):
            self.window.message_input.setPlainText(text)
            self.window.send_message()
            self.wait_until(lambda: not self.window.worker_threads)

    def test_a_simple_chat_has_no_task_panel(self):
        self.send("What is GPU?", [])
        self.assertIsNone(self.window.current_task_panel)
        self.assertEqual(self.window.findChildren(AgentTaskPanel), [])
        self.assertEqual(self.snapshots, [])
        self.assertEqual(self.task_store.list_tasks(), [])

    def test_stop_button_cancels_waiting_task_and_history_is_readable(self):
        self.send("整理资料并创建笔记", ["search_knowledge", "create_note"])
        panel = self.window.current_task_panel
        self.assertTrue(panel.stop_button.isVisible())
        QTest.mouseClick(panel.stop_button, Qt.MouseButton.LeftButton)
        self.wait_until(lambda: not self.window.worker_threads)
        self.assertEqual(self.writes, [])
        self.assertEqual(panel.heading.text(), "✕ Task Cancelled")
        self.assertFalse(panel.stop_button.isVisible())
        self.window.task_history_button.click()
        self.wait_until(lambda: not self.window.worker_threads)
        history = self.window.task_history_dialog
        self.assertEqual(history.tasks.count(), 1)
        self.assertIn("CANCELLED", history.details.toPlainText())
        self.assertIn("Physics.md", history.details.toPlainText())

    def test_stop_button_during_execution_waits_for_safe_step(self):
        agent = make_agent(["search_knowledge", "create_note"], self.writes)
        entered, release = threading.Event(), threading.Event()
        original = agent.registry.execute_tool
        def blocked(*args, **kwargs):
            entered.set()
            if not release.wait(5):
                raise RuntimeError("Test step timed out")
            return original(*args, **kwargs)
        agent.registry.execute_tool = blocked
        with patch("app.gui.AgentCore", return_value=agent):
            self.window.message_input.setPlainText("整理资料并创建笔记")
            self.window.send_message()
            try:
                self.wait_until(lambda: entered.is_set() and self.window.current_task_panel is not None)
                panel = self.window.current_task_panel
                panel.stop_button.click()
                self.assertTrue(self.window.current_task_control.cancelled)
                self.assertTrue(self.window.chat_busy)
            finally:
                release.set()
            self.wait_until(lambda: not self.window.worker_threads)
        self.assertEqual(panel.snapshot["state"], "CANCELLED")
        self.assertEqual(self.writes, [])
        self.assertEqual(self.task_store.list_tasks()[0]["status"], "CANCELLED")

    def test_startup_recovers_history_before_loading_conversation(self):
        self.task_store.save_snapshot({"task_id": "previous-run", "state": "EXECUTING",
            "plan": {"goal": "Physics", "steps": []}}, "fairy")
        loaded = []
        def load_conversation():
            loaded.append(self.task_store.get_task("previous-run")["status"])
        self.window._initialize_conversation = load_conversation
        self.window._initialize_task_history()
        self.wait_until(lambda: not self.window.worker_threads)
        self.assertEqual(loaded, ["INTERRUPTED"])

    def test_b_read_task_signals_and_retained_results(self):
        self.send("搜索 Physics 资料并总结", ["search_knowledge", "summarize_knowledge"])
        states = [item["state"] for item in self.snapshots]
        self.assertEqual(states[0], "PLANNING")
        self.assertIn("EXECUTING", states)
        self.assertEqual(states[-1], "COMPLETE")
        self.assertTrue(all(thread == threading.get_ident() for thread in self.callback_threads))
        self.assertTrue(any(any(step["status"] == "running" for step in item["plan"]["steps"])
                            for item in self.snapshots))
        panel = self.window.current_task_panel
        self.assertTrue(panel.isVisible())
        self.assertEqual(panel.heading.text(), "✓ Task Complete")
        self.assertIn("Physics.md", panel.details.toPlainText())
        self.assertTrue(all(step["status"] == "completed" for step in panel.snapshot["plan"]["steps"]))
        self.assertEqual(self.snapshots[0]["plan"]["steps"], [])
        self.assertFalse(self.window.chat_busy)

    def test_c_confirmation_buttons_resume_existing_core_once(self):
        self.send("整理资料并创建笔记", ["search_knowledge", "create_note"])
        panel = self.window.current_task_panel
        self.assertEqual(self.writes, [])
        self.assertEqual(panel.snapshot["state"], "WAITING_CONFIRMATION")
        self.assertIn("data/notes/physics_review.md", panel.confirmation_label.text())
        self.assertTrue(panel.confirm_button.isEnabled())
        self.assertTrue(self.window.chat_busy)
        QTest.mouseClick(panel.confirm_button, Qt.MouseButton.LeftButton)
        QTest.mouseClick(panel.confirm_button, Qt.MouseButton.LeftButton)
        self.wait_until(lambda: not self.window.worker_threads)
        self.assertEqual(len(self.writes), 1)
        self.assertEqual(panel.heading.text(), "✓ Task Complete")

    def test_d_cancel_does_not_write(self):
        self.send("整理资料并创建笔记", ["search_knowledge", "create_note"])
        panel = self.window.current_task_panel
        QTest.mouseClick(panel.cancel_button, Qt.MouseButton.LeftButton)
        self.wait_until(lambda: not self.window.worker_threads)
        self.assertEqual(self.writes, [])
        self.assertEqual(panel.snapshot["state"], "CANCELLED")
        self.assertFalse(panel.confirm_button.isVisible())
        self.assertIn("取消", panel.reason_label.text())

    def test_e_failure_is_visible_and_does_not_crash(self):
        self.send("搜索 Physics 资料并总结", ["search_knowledge", "create_note"], fail=True)
        panel = self.window.current_task_panel
        self.assertEqual(panel.heading.text(), "⚠ Task Failed")
        self.assertIn("Physics source unavailable", panel.reason_label.text())
        self.assertNotIn("Traceback", panel.reason_label.text())
        self.assertFalse(self.window.chat_busy)
        self.assertEqual(self.writes, [])

    def test_second_write_gets_new_buttons_and_old_panel_is_retained(self):
        self.send("整理资料并创建笔记", ["create_note", "create_note"])
        panel = self.window.current_task_panel
        first_id = panel.confirmation_id
        QTest.mouseClick(panel.confirm_button, Qt.MouseButton.LeftButton)
        self.wait_until(lambda: not self.window.worker_threads)
        self.assertNotEqual(first_id, panel.confirmation_id)
        self.assertTrue(panel.confirm_button.isEnabled())
        self.window._resolve_agent_confirmation(first_id, True)
        self.assertEqual(len(self.writes), 1)
        QTest.mouseClick(panel.cancel_button, Qt.MouseButton.LeftButton)
        self.wait_until(lambda: not self.window.worker_threads)
        self.send("What is GPU?", [])
        self.assertIsNone(self.window.current_task_panel)
        self.assertTrue(panel.isVisible())
        self.assertEqual(len(self.writes), 1)

    def test_observer_restores_registry_and_never_grants_permission(self):
        agent = make_agent(["create_note"], self.writes)
        registry = agent.registry
        with AgentTaskObserver(agent, lambda snapshot: None) as observer:
            result = agent.run("整理资料并创建笔记", on_state=observer.state_changed)
            with self.assertRaises(ToolConfirmationRequiredError):
                agent.registry.execute_tool("create_note", {"title": "x", "filename": "x.md"})
        self.assertIs(agent.registry, registry)
        self.assertIsNotNone(result["pending_confirmation"])
        self.assertEqual(self.writes, [])

    def test_plain_text_errors_and_confirmation_readiness(self):
        panel = AgentTaskPanel()
        self.addCleanup(panel.deleteLater)
        panel.update_task({
            "plan": {"goal": "<b>Physics</b>", "steps": []},
            "state": "WAITING_CONFIRMATION", "pending_confirmation": {
                "tool": "create_note", "confirmation_id": "one-use",
                "arguments": {"title": "Physics", "filename": "physics_review"},
            },
        })
        self.assertFalse(panel.confirm_button.isEnabled())
        panel.enable_confirmation("stale-id")
        self.assertFalse(panel.confirm_button.isEnabled())
        self.assertEqual(panel.goal_label.textFormat(), Qt.TextFormat.PlainText)
        self.assertIn("physics_review.md", panel.confirmation_label.text())
        panel.fail("Traceback (most recent call last):\n  internal details\nRuntimeError: source unavailable")
        self.assertEqual(panel.reason_label.text(), "RuntimeError: source unavailable")
        self.assertNotIn("Traceback", panel.details.toPlainText())
