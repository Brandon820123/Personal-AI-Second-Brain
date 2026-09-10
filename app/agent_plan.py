"""Finite, in-memory plans with per-action approval and bounded context."""

import copy
import json
from importlib import import_module
import re

try:
    from .agent_task_control import TaskCancellationError
    from .agent_runtime import AgentTimeoutError, call_with_timeout
except ImportError:
    from agent_task_control import TaskCancellationError
    from agent_runtime import AgentTimeoutError, call_with_timeout


def needs_plan(request):
    """Recognize composed local tasks without planning ordinary questions."""
    read = re.search(r"搜索|查找|查询|检索|列出|查看|\b(?:search|find|query|list|look up)\b", request, re.I)
    synthesize = re.search(r"总结|整理|归纳|\b(?:summarize|organize)\b", request, re.I)
    write = re.search(r"创建|新建|更新|保存|完成|添加|\b(?:create|write|save|add|update|complete)\b", request, re.I)
    multiple_todos = re.search(r"[二三四五两2345].{0,4}(?:待办|任务)|\b(?:two|three|four|five|[2-5])\s+.*(?:todos?|to-dos?|tasks?)", request, re.I)
    return bool(sum(bool(value) for value in (read, synthesize, write)) >= 2
                or multiple_todos or (synthesize and re.search(r"资料|知识库|materials?|knowledge base", request, re.I)))



def _core():
    return import_module(".agent_core", __package__) if __package__ else import_module("agent_core")


class PlanExecution:
    """Own a single task across confirmation pauses; never replan in a loop."""

    def __init__(self, owner, goal, recent, memory, on_state):
        self.owner = owner
        self.goal = goal
        self.recent = copy.deepcopy((recent or [])[-6:])
        self.memory = copy.deepcopy(memory or [])
        self.on_state = on_state
        self.plan = {"goal": goal, "steps": []}
        self.calls = []
        self.index = 0
        self.count = 0
        self.error = None
        self.pending = None
        self.state = "PLANNING"

    def transition(self, state):
        self.state = state
        self.owner.state = state
        _core().LOGGER.debug("Task state -> %s", state)
        try:
            self.on_state(state)
        except Exception:
            _core().LOGGER.warning("Task state listener was unavailable.")

    def context(self):
        # All reference blocks are bounded independently; keep the goal and plan.
        _truncate_text = _core()._truncate_text
        return {
            "original_goal": self.goal,
            "current_plan": copy.deepcopy(self.plan),
            "completed_step_results": copy.deepcopy(self.calls),
            "recent_conversation": _truncate_text(json.dumps(self.recent, ensure_ascii=False), 6000),
            "relevant_memory": _truncate_text(json.dumps(self.memory, ensure_ascii=False), 2400),
        }

    def start(self):
        self.transition("PLANNING")
        if self.cancel_if_requested():
            return self.result()
        try:
            raw = call_with_timeout(
                lambda: self.owner.planner.plan(self.goal, self.owner.registry.get_tools(), self.context()),
                self.owner.tool_timeout, "Agent planning",
            )
            if self.cancel_if_requested():
                return self.result()
            if not isinstance(raw, dict) or set(raw) != {"goal", "steps"}:
                raise ValueError("Plan must contain goal and steps.")
            if len(json.dumps(raw, ensure_ascii=False)) > 24000:
                raise ValueError("Plan exceeds the context size limit.")
            steps = raw["steps"]
            if not isinstance(raw["goal"], str) or not isinstance(steps, list) or not 1 <= len(steps) <= 5:
                raise ValueError("A plan must contain 1 to 5 steps.")
            for step in steps:
                if not isinstance(step, dict) or set(step) - {"tool", "arguments", "instruction", "critical", "status"}:
                    raise ValueError("Invalid plan step.")
                if not self.owner.registry.get_tool(step.get("tool")):
                    raise ValueError("Plan contains an unregistered tool.")
                if type(step.get("critical", True)) is not bool:
                    raise ValueError("Step critical flag must be boolean.")
                if not isinstance(step.get("instruction", ""), str):
                    raise ValueError("Step instruction must be text.")
                if "arguments" in step:
                    self.owner.registry.validate_tool_call(step["tool"], step["arguments"])
                step.update(status="pending", attempts=0)
            self.plan["steps"] = copy.deepcopy(steps)
            _core().LOGGER.debug("Plan -> %s", json.dumps(self.plan, ensure_ascii=False))
        except Exception as error:
            return self.fail(error)
        return self.advance()

    def fail(self, error):
        if self.cancel_if_requested():
            return self.result()
        self.error = str(error)
        self.pending = None
        self.owner._pending_actions.clear()
        self.transition("FAILED")
        return self.result()

    def cancel_if_requested(self):
        """Stop only at safe execution boundaries; leave planning policy unchanged."""
        control = getattr(self.owner, "_task_control", None)
        if control is None or not control.cancelled:
            return False
        self.pending = None
        self.owner._pending_actions.clear()
        for step in self.plan["steps"]:
            if step["status"] in {"pending", "pending_confirmation"}:
                step["status"] = "cancelled"
        self.error = "任务已取消；此前已完成的操作仍保留。"
        if self.state != "CANCELLED":
            self.transition("CANCELLED")
        return True

    def advance(self):
        if self.cancel_if_requested():
            return self.result()
        self.transition("EXECUTING")
        while self.index < len(self.plan["steps"]):
            if self.cancel_if_requested():
                return self.result()
            step = self.plan["steps"][self.index]
            try:
                if "arguments" not in step:
                    context = [self.context(), {"execute_step": self.index}]
                    decision = call_with_timeout(
                        lambda: self.owner.planner.decide(self.goal,
                            [self.owner.registry.get_tool(step["tool"])], context),
                        self.owner.tool_timeout, "Step arguments",
                    )
                    name, arguments = self.owner._validate_decision(decision)
                    if name != step["tool"]:
                        raise ValueError("Step decision must use the planned tool.")
                    step["arguments"] = copy.deepcopy(arguments)
                if self.cancel_if_requested():
                    return self.result()
                metadata = self.owner.registry.validate_tool_call(step["tool"], step["arguments"])
                cached = self.owner.cached_result(step["tool"], step["arguments"])
                if cached is not None:
                    step["status"] = "complete"
                    step["result"] = cached["result"]
                    self.calls.append({"tool": step["tool"], "arguments": copy.deepcopy(step["arguments"]),
                                       "status": "ok", "result": cached["result"], "cached": True,
                                       "step_index": self.index})
                    _core().LOGGER.debug("Tool result -> cached | %s", step["tool"])
                    self.transition("EXECUTING")
                    self.index += 1
                    continue
                if self.count >= self.owner.max_tool_calls:
                    return self.fail("Tool call limit reached; remaining steps were not executed.")
                if metadata["requires_confirmation"]:
                    self.pending = self.owner._create_pending_action(
                        step["tool"], step["arguments"], metadata["description"],
                    )
                    step["status"] = "pending_confirmation"
                    self.transition("WAITING_CONFIRMATION")
                    return self.result()
                self.execute(step, confirmed=False)
            except Exception as error:
                step["status"] = "error"
                return self.fail(error)
            if self.cancel_if_requested() or self.state == "FAILED":
                return self.result()
            self.index += 1
        if self.cancel_if_requested():
            return self.result()
        self.transition("COMPLETE")
        return self.result()

    def execute(self, step, confirmed):
        _bounded_json_value = _core()._bounded_json_value
        # Writes are never retried: an exception can follow a successful side effect.
        for _ in range(1 if confirmed else 2):
            control = getattr(self.owner, "_task_control", None)
            if control is not None and not control.begin_step():
                self.cancel_if_requested()
                return
            if self.count >= self.owner.max_tool_calls:
                return self.fail("Tool call limit reached during retry.")
            self.count += 1
            step["attempts"] += 1
            record = {"tool": step["tool"], "arguments": copy.deepcopy(step["arguments"]), "step_index": self.index}
            try:
                _core().LOGGER.debug("Tool call -> %s | %s", step["tool"], step["arguments"])
                value = self.owner.execute_selected_tool(step["tool"], step["arguments"], confirmed=confirmed)
                record.update(status="ok", result=_bounded_json_value(value))
                self.calls.append(record)
                step["status"] = "complete"
                step["result"] = result = record["result"]
                _core().LOGGER.debug("Tool result -> %s | %s", step["tool"], _core()._log_preview(result))
                return
            except Exception as error:
                if isinstance(error, TaskCancellationError):
                    step["status"] = "cancelled"
                    self.cancel_if_requested()
                    return
                record.update(status="error", error=str(error))
                self.calls.append(record)
                step["status"] = "error"
                if isinstance(error, AgentTimeoutError):
                    self.fail(error)
                    return
        if confirmed or step.get("critical", True):
            self.fail(f"Critical step {self.index + 1} ({step['tool']}) failed: {record['error']}")

    def resume(self, pending, approved):
        control = getattr(self.owner, "_task_control", None)
        if not approved and control is not None:
            control.request_cancel()
        if self.cancel_if_requested():
            return self.result()
        self.pending = None
        step = self.plan["steps"][self.index]
        if not approved:
            step["status"] = "cancelled"
            return self.fail("User cancelled the remaining plan. Earlier completed actions remain saved.")
        # Use the private frozen payload, never the publicly returned preview.
        step["arguments"] = copy.deepcopy(pending["arguments"])
        self.transition("EXECUTING")
        self.execute(step, confirmed=True)
        if self.cancel_if_requested() or self.state == "FAILED":
            return self.result()
        self.index += 1
        return self.advance()

    def result(self):
        _format_agent_context = _core()._format_agent_context
        return copy.deepcopy({
            "plan": self.plan, "state": self.state, "tool_calls": self.calls,
            "context": _format_agent_context(self.calls, self.error),
            "error": self.error, "pending_confirmation": self.pending,
            "limit_reached": self.count >= self.owner.max_tool_calls,
        })
