"""Compact system task panel, independent of Persona and persistence."""

import copy
import json

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton,
    QSizePolicy, QVBoxLayout,
)


TOOL_LABELS = {
    "search_knowledge": "搜索相关知识", "summarize_knowledge": "整理重点",
    "search_memory": "检索相关记忆", "list_knowledge_files": "列出知识文件",
    "scan_knowledge_sources": "查看知识来源", "create_note": "创建复习笔记",
    "create_todo": "创建待办", "complete_todo": "完成待办",
    "list_todos": "查看待办", "update_memory": "更新记忆",
}
STEP_SYMBOLS = {
    "pending": "○", "running": "→", "completed": "✓",
    "failed": "⚠", "waiting_confirmation": "◇", "cancelled": "✕", "interrupted": "⚠",
}


def short_reason(value):
    """Show a concise plain-text reason, never a multi-line traceback."""
    lines = str(value or "任务未能完成。").strip().splitlines()
    return (lines[-1] if lines else "任务未能完成。")[:240]


class AgentTaskPanel(QFrame):
    """Render immutable snapshots; buttons emit only a one-use confirmation ID."""

    confirmation_requested = Signal(str, bool)
    stop_requested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("agentTaskPanel")
        self.setMinimumWidth(380)
        self.setMaximumWidth(740)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        self.snapshot = {}
        self.confirmation_id = None
        self.stopping = False
        self.submitted_confirmations = set()
        self.setStyleSheet("""
            QFrame#agentTaskPanel { background: #101b28; border: 1px solid #2c6680;
                border-left: 3px solid #52d6ec; border-radius: 6px; }
            QFrame#agentTaskPanel QLabel { color: #dcebf5; background: transparent; border: none; }
            QFrame#agentTaskPanel QPushButton { background: #132b3b; color: #8be9f7;
                border: 1px solid #2c6680; border-radius: 4px; padding: 5px 12px; }
            QFrame#agentTaskPanel QPushButton:disabled { color: #718695; border-color: #263b4a; }
            QFrame#agentTaskPanel QPlainTextEdit { background: #0b1420; color: #dcebf5;
                border: 1px solid #223b52; border-radius: 4px; }
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(6)
        self.heading = self._label("Agent Task  ·  SYSTEM")
        self.goal_label = self._label("")
        self.steps_label = self._label("")
        self.status_label = self._label("")
        self.reason_label = self._label("")
        for label in (self.heading, self.steps_label):
            font = label.font()
            font.setFamilies(["Microsoft YaHei UI", "Segoe UI Symbol", "sans-serif"])
            label.setFont(font)
        for label in (self.heading, self.goal_label, self.steps_label, self.status_label, self.reason_label):
            layout.addWidget(label)
        self.confirmation_label = self._label("")
        layout.addWidget(self.confirmation_label)
        buttons = QHBoxLayout()
        self.confirm_button = QPushButton("确认执行")
        self.cancel_button = QPushButton("取消")
        self.confirm_button.clicked.connect(lambda: self._choose(True))
        self.cancel_button.clicked.connect(lambda: self._choose(False))
        buttons.addWidget(self.confirm_button)
        buttons.addWidget(self.cancel_button)
        self.stop_button = QPushButton("停止任务")
        self.stop_button.clicked.connect(self._stop)
        self.stop_button.hide()
        buttons.addWidget(self.stop_button)
        buttons.addStretch()
        layout.addLayout(buttons)
        self.details_button = QPushButton("查看步骤结果 / 操作参数")
        self.details_button.setCheckable(True)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setMaximumHeight(150)
        self.details.hide()
        self.details_button.toggled.connect(self.details.setVisible)
        layout.addWidget(self.details_button)
        layout.addWidget(self.details)
        self._show_confirmation(False)
        self.hide()

    def _label(self, text):
        label = QLabel(text)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        return label

    @Slot(object)
    def update_task(self, snapshot):
        if self.snapshot.get("state") in {"COMPLETE", "FAILED", "CANCELLED", "INTERRUPTED"}:
            return
        previous_id = self.confirmation_id
        was_ready = self.confirm_button.isEnabled()
        self.snapshot = copy.deepcopy(snapshot)
        snapshot = self.snapshot
        for record in snapshot.get("tool_calls", []) + snapshot.get("plan", {}).get("steps", []):
            if record.get("error"):
                record["error"] = short_reason(record["error"])
        plan = snapshot.get("plan", {})
        state = snapshot.get("state", "PLANNING")
        self.goal_label.setText("Goal: " + str(plan.get("goal", ""))[:500])
        steps = plan.get("steps", [])[:5]
        self.steps_label.setText("\n".join(
            f"{STEP_SYMBOLS.get(step.get('status'), '○')} "
            f"{TOOL_LABELS.get(step.get('tool'), step.get('tool', 'Step'))}"
            for step in steps
        ) or ("正在生成任务计划…" if state == "PLANNING" else "未执行任何步骤。"))
        completed = sum(step.get("status") == "completed" for step in steps)
        self.status_label.setText(f"Status: {state}  ·  {completed}/{len(steps)}")
        self.heading.setText({"COMPLETE": "✓ Task Complete", "FAILED": "⚠ Task Failed",
                              "CANCELLED": "✕ Task Cancelled", "INTERRUPTED": "⚠ Task Interrupted"}.get(
            state, "Agent Task  ·  SYSTEM",
        ))
        reason = snapshot.get("error") or snapshot.get("history_warning")
        self.reason_label.setVisible(bool(reason))
        self.reason_label.setText(short_reason(reason) if reason else "")
        self.stop_button.setVisible(state in {"PLANNING", "EXECUTING", "WAITING_CONFIRMATION"})
        self.stop_button.setEnabled(not self.stopping)
        if self.stopping:
            self.stop_button.setText("正在停止…")
        pending = snapshot.get("pending_confirmation") if state == "WAITING_CONFIRMATION" else None
        self.confirmation_id = pending.get("confirmation_id") if pending else None
        self._show_confirmation(bool(pending))
        if pending and previous_id == self.confirmation_id and was_ready:
            self.enable_confirmation(self.confirmation_id)
        if pending:
            arguments = pending.get("arguments", {})
            if pending["tool"] == "create_note":
                filename = arguments.get("filename")
                if filename and not filename.lower().endswith(".md"):
                    filename += ".md"
                target = f"data/notes/{filename}" if filename else "data/notes/（执行时分配文件名）"
                preview = f"创建文件：{target}\n标题：{arguments.get('title', '')}"
            else:
                preview = TOOL_LABELS.get(pending["tool"], pending["tool"])
                preview += "\n" + json.dumps(arguments, ensure_ascii=False)[:600]
            self.confirmation_label.setText("Agent 请求执行以下操作：\n" + preview[:850])
        details = {"steps": steps, "results": snapshot.get("tool_calls", [])}
        if pending:
            details["confirmation"] = pending
        self.details.setPlainText(json.dumps(details, ensure_ascii=False, indent=2)[:24000])
        self.show()

    def _show_confirmation(self, visible):
        self.confirmation_label.setVisible(visible)
        for button in (self.confirm_button, self.cancel_button):
            button.setVisible(visible)
            button.setEnabled(False)

    def enable_confirmation(self, confirmation_id):
        """Called only after the worker has returned the pending action."""
        if (confirmation_id == self.confirmation_id and confirmation_id and not self.stopping
                and confirmation_id not in self.submitted_confirmations):
            self.confirm_button.setEnabled(True)
            self.cancel_button.setEnabled(True)

    def _choose(self, approved):
        if not self.confirmation_id or not self.confirm_button.isEnabled():
            return
        self.confirm_button.setEnabled(False)
        self.cancel_button.setEnabled(False)
        self.submitted_confirmations.add(self.confirmation_id)
        self.confirmation_requested.emit(self.confirmation_id, approved)

    def fail(self, reason):
        snapshot = dict(self.snapshot, state="FAILED", error=short_reason(reason), pending_confirmation=None)
        self.update_task(snapshot)

    def _stop(self):
        task_id = self.snapshot.get("task_id")
        if self.stopping or not task_id:
            return
        self.stopping = True
        self.stop_button.setEnabled(False)
        self.stop_button.setText("正在停止…")
        self.confirm_button.setEnabled(False)
        self.cancel_button.setEnabled(False)
        self.stop_requested.emit(task_id)
