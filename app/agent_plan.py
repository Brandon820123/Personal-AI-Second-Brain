"""Finite, in-memory plans with per-action approval and bounded context."""

import copy
import json
from importlib import import_module
import re


def needs_plan(request):
    """Recognize composed local tasks without planning ordinary questions."""
    return bool(re.search(
        r"并|然后|接着|总结|整理|归纳|[二三四五两2345].{0,4}(?:待办|任务)|"
        r"\b(?:then|summarize|organize|and|three|two|four|five)\b",
        request, re.IGNORECASE,
    ))


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
        self.on_state(state)

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
        try:
            raw = self.owner.planner.plan(
                self.goal, self.owner.registry.get_tools(), self.context(),
            )
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
        except Exception as error:
            return self.fail(error)
        return self.advance()

    def fail(self, error):
        self.error = str(error)
        self.transition("FAILED")
        return self.result()

    def advance(self):
        self.transition("EXECUTING")
        while self.index < len(self.plan["steps"]):
            step = self.plan["steps"][self.index]
            if self.count >= self.owner.max_tool_calls:
                return self.fail("Tool call limit reached; remaining steps were not executed.")
            try:
                if "arguments" not in step:
                    decision = self.owner.planner.decide(
                        self.goal,
                        [self.owner.registry.get_tool(step["tool"])],
                        [self.context(), {"execute_step": self.index}],
                    )
                    name, arguments = self.owner._validate_decision(decision)
                    if name != step["tool"]:
                        raise ValueError("Step decision must use the planned tool.")
                    step["arguments"] = copy.deepcopy(arguments)
                metadata = self.owner.registry.validate_tool_call(step["tool"], step["arguments"])
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
            if self.state == "FAILED":
                return self.result()
            self.index += 1
        self.transition("COMPLETE")
        return self.result()

    def execute(self, step, confirmed):
        _bounded_json_value = _core()._bounded_json_value
        # Writes are never retried: an exception can follow a successful side effect.
        for _ in range(1 if confirmed else 2):
            if self.count >= self.owner.max_tool_calls:
                return self.fail("Tool call limit reached during retry.")
            self.count += 1
            step["attempts"] += 1
            record = {"tool": step["tool"], "arguments": copy.deepcopy(step["arguments"])}
            try:
                value = self.owner.registry.execute_tool(step["tool"], step["arguments"], confirmed=confirmed)
                record.update(status="ok", result=_bounded_json_value(value))
                self.calls.append(record)
                step["status"] = "complete"
                return
            except Exception as error:
                record.update(status="error", error=str(error))
                self.calls.append(record)
                step["status"] = "error"
        if confirmed or step.get("critical", True):
            self.fail(f"Critical step {self.index + 1} ({step['tool']}) failed: {record['error']}")

    def resume(self, pending, approved):
        self.pending = None
        step = self.plan["steps"][self.index]
        if not approved:
            step["status"] = "cancelled"
            return self.fail("User cancelled the remaining plan. Earlier completed actions remain saved.")
        # Use the private frozen payload, never the publicly returned preview.
        step["arguments"] = copy.deepcopy(pending["arguments"])
        self.transition("EXECUTING")
        self.execute(step, confirmed=True)
        if self.state == "FAILED":
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
