"""Task persistence, safe stop boundaries, and restart recovery tests."""

from pathlib import Path
import tempfile
import threading
import unittest

from app.agent_core import AgentConfirmationError
from app.agent_task_control import AgentTaskControl
from app.agent_task_observer import AgentTaskObserver
from app.agent_task_store import AgentTaskStore, INTERRUPTED_MESSAGE
from tests.test_agent_task_ui import make_agent


class TaskHistoryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = AgentTaskStore(Path(self.directory.name) / "tasks.db")
        self.control = AgentTaskControl()
        self.writes = []
        self.events = []

    def run_agent(self, agent):
        agent._task_control = self.control
        with AgentTaskObserver(agent, self.events.append, store=self.store, persona="fairy") as observer:
            return agent.run("整理 Physics 资料并创建笔记", on_state=observer.state_changed)

    def test_complete_and_step_results_are_saved(self):
        agent = make_agent(["search_knowledge", "summarize_knowledge"], self.writes)
        result = self.run_agent(agent)
        record = self.store.get_task(self.control.task_id)
        self.assertEqual(result["state"], record["status"])
        self.assertEqual(record["status"], "COMPLETE")
        self.assertEqual(record["persona"], "fairy")
        self.assertEqual(record["total_steps"], 2)
        self.assertTrue(record["started_at"] and record["completed_at"])
        self.assertTrue(all(step["started_at"] and step["completed_at"] for step in record["steps"]))
        self.assertIn("Physics.md", record["steps"][0]["result_summary"])

    def test_failed_task_saves_error(self):
        result = self.run_agent(make_agent(["search_knowledge"], self.writes, fail=True))
        record = self.store.get_task(self.control.task_id)
        self.assertEqual(record["status"], "FAILED")
        self.assertIn("Physics source unavailable", record["error_message"])
        self.assertEqual(len(result["tool_calls"]), 2)

    def test_cancel_while_tool_runs_keeps_current_result_and_stops_next(self):
        agent = make_agent(["search_knowledge", "create_note"], self.writes)
        started, release = threading.Event(), threading.Event()
        original = agent.registry.execute_tool

        def blocked(*args, **kwargs):
            started.set()
            if not release.wait(5):
                raise RuntimeError("Test step timed out")
            return original(*args, **kwargs)

        agent.registry.execute_tool = blocked
        results = []
        thread = threading.Thread(target=lambda: results.append(self.run_agent(agent)))
        thread.start()
        try:
            self.assertTrue(started.wait(5))
            record = self.store.get_task(self.control.task_id)
            self.assertEqual(record["status"], "EXECUTING")
            self.assertEqual(record["steps"][0]["status"], "running")
            self.control.request_cancel()
        finally:
            release.set()
            thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(results[0]["state"], "CANCELLED")
        self.assertEqual(self.writes, [])
        self.assertEqual(len(results[0]["tool_calls"]), 1)
        record = self.store.get_task(self.control.task_id)
        self.assertEqual(record["steps"][0]["status"], "completed")
        self.assertEqual(record["steps"][1]["status"], "cancelled")

    def test_cancel_during_planning_is_persisted_before_any_tool(self):
        agent = make_agent(["create_note"], self.writes)
        original = agent.planner.plan

        def plan(*args):
            self.assertEqual(self.store.get_task(self.control.task_id)["status"], "PLANNING")
            self.control.request_cancel()
            return original(*args)

        agent.planner.plan = plan
        self.assertEqual(self.run_agent(agent)["state"], "CANCELLED")
        self.assertEqual(self.writes, [])

    def test_cancel_waiting_invalidates_confirmation_even_if_approved(self):
        agent = make_agent(["create_note"], self.writes)
        result = self.run_agent(agent)
        pending_id = result["pending_confirmation"]["confirmation_id"]
        self.assertEqual(self.store.get_task(self.control.task_id)["status"], "WAITING_CONFIRMATION")
        self.control.request_cancel()
        with AgentTaskObserver(agent, self.events.append, store=self.store) as observer:
            agent._plan_execution.on_state = observer.state_changed
            result = agent.confirm_action(pending_id, True)
        self.assertEqual(result["state"], "CANCELLED")
        self.assertEqual(self.writes, [])
        with self.assertRaises(AgentConfirmationError):
            agent.confirm_action(pending_id, True)
        self.assertEqual(self.store.get_task(self.control.task_id)["status"], "CANCELLED")

    def test_restart_interrupts_active_tasks_and_keeps_terminal_history(self):
        states = ["PLANNING", "EXECUTING", "WAITING_CONFIRMATION", "COMPLETE", "FAILED", "CANCELLED"]
        for state in states:
            self.store.save_snapshot({"task_id": state, "state": state,
                "plan": {"goal": "Physics", "steps": [{"tool": "search_knowledge", "status": "running"}]}}, "delamain")
        reopened = AgentTaskStore(self.store.db_path)
        self.assertEqual(reopened.recover_interrupted(), 3)
        self.assertEqual(reopened.recover_interrupted(), 0)
        for state in states:
            task = reopened.get_task(state)
            self.assertEqual(task["status"], "INTERRUPTED" if state in states[:3] else state)
            if state in states[:3]:
                self.assertEqual(task["error_message"], INTERRUPTED_MESSAGE)
                self.assertEqual(task["steps"][0]["status"], "interrupted")
        self.assertEqual(len(reopened.list_tasks()), 6)

    def test_terminal_history_cannot_be_overwritten_by_stale_progress(self):
        snapshot = {"task_id": "terminal", "state": "CANCELLED", "plan": {"goal": "Physics", "steps": []}}
        self.store.save_snapshot(snapshot, "fairy")
        self.store.save_snapshot(dict(snapshot, state="EXECUTING"), "fairy")
        self.assertEqual(self.store.get_task("terminal")["status"], "CANCELLED")

    def test_read_retry_does_not_continue_after_cancel(self):
        agent = make_agent(["search_knowledge", "create_note"], self.writes)
        calls = []

        def fail(*args, **kwargs):
            calls.append(1)
            self.control.request_cancel()
            raise RuntimeError("source unavailable")

        agent.registry.execute_tool = fail
        self.assertEqual(self.run_agent(agent)["state"], "CANCELLED")
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.writes, [])

    def test_cancel_at_write_admission_still_prevents_callable(self):
        agent = make_agent(["create_note"], self.writes)
        result = self.run_agent(agent)

        def stop_on_running(snapshot):
            if snapshot["plan"]["steps"][0]["status"] == "running":
                self.control.request_cancel()

        with AgentTaskObserver(agent, stop_on_running, store=self.store) as observer:
            agent._plan_execution.on_state = observer.state_changed
            result = agent.confirm_action(result["pending_confirmation"]["confirmation_id"], True)
        self.assertEqual(result["state"], "CANCELLED")
        self.assertEqual(self.writes, [])

    def test_history_errors_do_not_disable_cancellation(self):
        agent = make_agent(["create_note"], self.writes)
        agent._task_control = self.control
        self.control.request_cancel()
        class BrokenStore:
            def save_snapshot(self, *args):
                raise OSError("disk unavailable")
        with AgentTaskObserver(agent, self.events.append, store=BrokenStore()) as observer:
            result = agent.run("整理资料并创建笔记", on_state=observer.state_changed)
        self.assertEqual(result["state"], "CANCELLED")
        self.assertIn("history_warning", self.events[-1])
        self.assertEqual(self.writes, [])
