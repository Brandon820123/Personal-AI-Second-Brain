"""Read-only recent task history; all database work is requested through signals."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPlainTextEdit, QPushButton, QVBoxLayout

from .agent_task_panel import TOOL_LABELS


class AgentTaskHistory(QDialog):
    refresh_requested = Signal()
    task_selected = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Agent Tasks / Task History")
        self.resize(740, 520)
        self.selected_task_id = None
        layout = QVBoxLayout(self)
        header = QHBoxLayout()
        self.status_label = QLabel("最近 50 项任务")
        self.status_label.setTextFormat(Qt.TextFormat.PlainText)
        refresh = QPushButton("刷新")
        refresh.clicked.connect(self.refresh_requested)
        header.addWidget(self.status_label)
        header.addStretch()
        header.addWidget(refresh)
        layout.addLayout(header)
        self.tasks = QListWidget()
        self.tasks.currentItemChanged.connect(self._selected)
        layout.addWidget(self.tasks, 1)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        layout.addWidget(self.details, 2)

    def show_tasks(self, records):
        selected = self.selected_task_id
        self.tasks.clear()
        self.status_label.setText(f"最近 {len(records)} 项任务")
        for record in records:
            symbol = {"COMPLETE": "✓", "CANCELLED": "✕", "FAILED": "⚠", "INTERRUPTED": "⚠"}.get(record["status"], "→")
            item = QListWidgetItem(f"{symbol} {record['goal'][:100]}\n{record['status']} · {record['created_at']}")
            item.setData(Qt.ItemDataRole.UserRole, record["task_id"])
            self.tasks.addItem(item)
            if record["task_id"] == selected:
                self.tasks.setCurrentItem(item)
        if self.tasks.currentRow() < 0 and records:
            self.tasks.setCurrentRow(0)

    def _selected(self, item, previous):
        self.selected_task_id = item.data(Qt.ItemDataRole.UserRole) if item else None
        self.details.clear()
        if self.selected_task_id:
            self.task_selected.emit(self.selected_task_id)

    def show_task(self, task):
        if not task or task["task_id"] != self.selected_task_id:
            return
        lines = [f"Goal: {task['goal']}", f"Status: {task['status']}",
                 f"Persona: {task['persona']}", f"Created: {task['created_at']}",
                 f"Started: {task['started_at'] or '—'}", f"Completed: {task['completed_at'] or '—'}"]
        if task["error_message"]:
            lines.append(task["error_message"])
        for step in task["steps"]:
            lines.extend(["", f"{step['step_index'] + 1}. {TOOL_LABELS.get(step['tool'], step['tool'])} · {step['status']}",
                          f"{step['started_at'] or '—'} → {step['completed_at'] or '—'}",
                          step["result_summary"] or ""])
        self.details.setPlainText("\n".join(lines))
