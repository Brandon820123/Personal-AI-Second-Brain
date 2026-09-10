"""Presentation-only observation of an Agent, without changing execution policy."""

import copy
import logging

try:
    from .agent_task_control import TaskCancellationError
    from .agent_task_store import summary
except ImportError:
    from agent_task_control import TaskCancellationError
    from agent_task_store import summary


LOGGER = logging.getLogger(__name__)
STEP_STATES = {
    "pending": "pending", "complete": "completed", "error": "failed",
    "pending_confirmation": "waiting_confirmation", "cancelled": "cancelled",
}


class AgentTaskObserver:
    """Copy worker-owned plan data at existing state and tool-call boundaries."""

    def __init__(self, agent, on_task=None, on_state=lambda state: None, store=None, persona="unknown"):
        self.agent = agent
        self.on_task = on_task
        self.on_state = on_state
        self.registry = None
        self.store = store
        self.persona = persona
        self.execution = None

    def __enter__(self):
        if self.on_task is not None or self.store is not None:
            self.registry = self.agent.registry
            self.agent.registry = _ObservedRegistry(self.registry, self)
        return self

    def __exit__(self, *exc):
        if self.registry is not None:
            self.agent.registry = self.registry

    def state_changed(self, state):
        self.on_state(state)
        self.publish()

    def publish(self, step_status=None, result=None, error=None):
        execution = self.execution or getattr(self.agent, "_plan_execution", None)
        if execution is None or (step_status is not None and execution.state in {"COMPLETE", "FAILED", "CANCELLED"}):
            return
        self.execution = execution
        snapshot = execution.result()
        snapshot.pop("context", None)
        snapshot["current_step"] = execution.index
        control = getattr(self.agent, "_task_control", None)
        if control is not None:
            snapshot["task_id"] = control.task_id
        for index, step in enumerate(snapshot["plan"]["steps"]):
            step["status"] = STEP_STATES.get(step["status"], "pending")
            records = [record for record in snapshot["tool_calls"] if record.get("step_index") == index]
            if records:
                step["result"] = records[-1].get("result")
                step["error"] = records[-1].get("error")
        if step_status and execution.index < len(snapshot["plan"]["steps"]):
            step = snapshot["plan"]["steps"][execution.index]
            step["status"] = step_status
            if result is not None:
                step["result"] = result
                step.pop("error", None)
            if error is not None:
                step["error"] = str(error)
        if self.store is not None and control is not None:
            try:
                self.store.save_snapshot(snapshot, self.persona)
            except Exception:
                snapshot["history_warning"] = "任务历史保存失败。"
                LOGGER.warning("Agent task history could not be saved.")
        # The GUI receives an independent snapshot, never a live plan or registry.
        try:
            if self.on_task is not None:
                self.on_task(copy.deepcopy(snapshot))
        except Exception:
            LOGGER.warning("Agent task UI update was unavailable.")


class _ObservedRegistry:
    """Forward every permission check and call unchanged to the real registry."""

    def __init__(self, registry, observer):
        self.registry = registry
        self.observer = observer

    def __getattr__(self, name):
        return getattr(self.registry, name)

    def execute_tool(self, name, arguments, *, confirmed=False):
        self.observer.publish("running")
        execution = self.observer.execution
        if execution is not None and execution.state in {"COMPLETE", "FAILED", "CANCELLED"}:
            raise TaskCancellationError("任务已结束，未执行此步骤。")
        control = getattr(self.observer.agent, "_task_control", None)
        if control is not None and not control.begin_step():
            raise TaskCancellationError("任务已取消，未执行此步骤。")
        try:
            result = self.registry.execute_tool(name, arguments, confirmed=confirmed)
        except Exception as error:
            self.observer.publish("failed", error=error)
            raise
        # Bound the presentation summary; return the original value for Core validation.
        self.observer.publish("completed", result=summary(result))
        return result
