"""Memory administration widgets for the desktop interface."""

from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

try:
    from ..memory_manager import MemoryManager
except ImportError:
    from memory_manager import MemoryManager


MEMORY_TYPE_LABELS = {
    "personal": "Personal",
    "project": "Project",
    "conversation": "Conversation",
}
MEMORY_PAGE_SIZE = 50


class MemoryEditorDialog(QDialog):
    """Collect the editable fields for one manual memory."""

    def __init__(self, memory=None, parent=None):
        super().__init__(parent)
        self.memory = memory or {}
        self.setWindowTitle("编辑记忆" if memory else "添加记忆")
        self.setMinimumWidth(520)

        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setSpacing(12)

        self.content_edit = QPlainTextEdit()
        self.content_edit.setObjectName("memoryContentEdit")
        self.content_edit.setPlaceholderText("输入具有长期价值的信息…")
        self.content_edit.setMinimumHeight(140)
        self.content_edit.setPlainText(self.memory.get("content", ""))
        form.addRow("内容", self.content_edit)

        self.type_combo = QComboBox()
        for memory_type, label in MEMORY_TYPE_LABELS.items():
            self.type_combo.addItem(label, memory_type)
        selected_type = self.memory.get("memory_type", "personal")
        selected_index = self.type_combo.findData(selected_type)
        self.type_combo.setCurrentIndex(max(selected_index, 0))
        form.addRow("类型", self.type_combo)

        self.importance_spin = QSpinBox()
        self.importance_spin.setRange(1, 5)
        self.importance_spin.setValue(self.memory.get("importance", 3))
        self.importance_spin.setToolTip("1 表示较低，5 表示最高")
        form.addRow("Importance", self.importance_spin)
        layout.addLayout(form)

        self.error_label = QLabel("")
        self.error_label.setObjectName("memoryDialogError")
        self.error_label.setWordWrap(True)
        layout.addWidget(self.error_label)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self):
        """Return normalized values accepted by MemoryManager."""
        return {
            "content": self.content_edit.toPlainText().strip(),
            "memory_type": self.type_combo.currentData(),
            "importance": self.importance_spin.value(),
        }

    def _validate_and_accept(self):
        if not self.values()["content"]:
            self.error_label.setText("请输入记忆内容。")
            return
        self.accept()


class MemoryPage(QWidget):
    """Show searchable Memory statistics and CRUD controls."""

    operation_completed = Signal(str)

    def __init__(self, memory_manager=None, parent=None):
        super().__init__(parent)
        self.memory_manager = memory_manager or MemoryManager()
        self.memories = []
        self.filtered_memories = []
        self._build_ui()

    def _build_ui(self):
        self.setObjectName("pageRoot")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(34, 28, 34, 28)
        layout.setSpacing(14)

        title = QLabel("Memory")
        title.setObjectName("pageTitle")
        subtitle = QLabel("管理保存在本机的长期偏好、项目记录和重要对话摘要。")
        subtitle.setObjectName("mutedLabel")
        layout.addWidget(title)
        layout.addWidget(subtitle)

        summary_grid = QGridLayout()
        summary_grid.setHorizontalSpacing(9)
        self.summary_values = {}
        summaries = (
            ("total", "记忆总数"),
            ("personal", "Personal"),
            ("project", "Project"),
            ("conversation", "Conversation"),
        )
        for column, (key, caption_text) in enumerate(summaries):
            card = QFrame()
            card.setObjectName("memorySummaryCard")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(14, 10, 14, 10)
            card_layout.setSpacing(2)
            value = QLabel("0")
            value.setObjectName("memorySummaryValue")
            caption = QLabel(caption_text)
            caption.setObjectName("mutedLabel")
            card_layout.addWidget(value)
            card_layout.addWidget(caption)
            summary_grid.addWidget(card, 0, column)
            self.summary_values[key] = value
        layout.addLayout(summary_grid)

        controls = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setObjectName("memorySearchInput")
        self.search_input.setPlaceholderText("搜索记忆...")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.textChanged.connect(lambda text: self.refresh_memories())
        controls.addWidget(self.search_input, 1)

        self.type_filter = QComboBox()
        self.type_filter.setObjectName("memoryTypeFilter")
        self.type_filter.addItem("全部", None)
        for memory_type, label in MEMORY_TYPE_LABELS.items():
            self.type_filter.addItem(label, memory_type)
        self.type_filter.currentIndexChanged.connect(
            lambda index: self.refresh_memories()
        )
        controls.addWidget(self.type_filter)

        self.add_button = QPushButton("＋ 添加记忆")
        self.add_button.setObjectName("primaryButton")
        self.add_button.clicked.connect(self.add_memory)
        controls.addWidget(self.add_button)
        layout.addLayout(controls)

        self.memory_table = QTableWidget(0, 5)
        self.memory_table.setObjectName("memoryTable")
        self.memory_table.setAlternatingRowColors(True)
        self.memory_table.setHorizontalHeaderLabels(
            ["内容", "类型", "Importance", "更新时间", "来源"]
        )
        self.memory_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        self.memory_table.setSelectionMode(
            QTableWidget.SelectionMode.SingleSelection
        )
        self.memory_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.memory_table.verticalHeader().setVisible(False)
        self.memory_table.itemSelectionChanged.connect(self._sync_action_buttons)
        self.memory_table.itemDoubleClicked.connect(lambda item: self.edit_memory())
        header = self.memory_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in range(1, 5):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.memory_table, 1)

        footer = QHBoxLayout()
        self.memory_status = QLabel("打开 Memory 页面后将读取本地记忆。")
        self.memory_status.setObjectName("mutedLabel")
        self.memory_status.setWordWrap(True)
        footer.addWidget(self.memory_status, 1)
        self.edit_button = QPushButton("编辑")
        self.edit_button.clicked.connect(self.edit_memory)
        self.delete_button = QPushButton("删除")
        self.delete_button.setObjectName("dangerButton")
        self.delete_button.clicked.connect(self.delete_memory)
        footer.addWidget(self.edit_button)
        footer.addWidget(self.delete_button)
        layout.addLayout(footer)
        self._sync_action_buttons()

    def _load_all_memories(self):
        records = []
        offset = 0
        while True:
            page = self.memory_manager.list_memories(
                limit=MEMORY_PAGE_SIZE,
                offset=offset,
            )
            records.extend(page)
            if len(page) < MEMORY_PAGE_SIZE:
                break
            offset += len(page)
        return records

    def refresh_memories(self, status_message=None):
        """Reload statistics and the active search/type view."""
        try:
            self.memories = self._load_all_memories()
            query = self.search_input.text().strip()
            memory_type = self.type_filter.currentData()
            if query:
                self.filtered_memories = self.memory_manager.search_memories(
                    query,
                    memory_type=memory_type,
                    limit=MEMORY_PAGE_SIZE,
                )
            else:
                self.filtered_memories = [
                    memory for memory in self.memories
                    if memory_type is None or memory["memory_type"] == memory_type
                ]
        except Exception:
            self.memories = []
            self.filtered_memories = []
            self._render_summary()
            self._render_table()
            self.memory_status.setText(
                "无法读取本地记忆数据库，请检查文件权限后重试。"
            )
            return False

        self._render_summary()
        self._render_table()
        if status_message is None:
            self.memory_status.setText(
                f"显示 {len(self.filtered_memories)} 条，共 {len(self.memories)} 条记忆。"
            )
        else:
            self.memory_status.setText(status_message)
            self.operation_completed.emit(status_message)
        return True

    def _render_summary(self):
        counts = {
            memory_type: sum(
                memory["memory_type"] == memory_type for memory in self.memories
            )
            for memory_type in MEMORY_TYPE_LABELS
        }
        self.summary_values["total"].setText(str(len(self.memories)))
        for memory_type, count in counts.items():
            self.summary_values[memory_type].setText(str(count))

    def _render_table(self):
        self.memory_table.clearContents()
        self.memory_table.setRowCount(len(self.filtered_memories))
        for row, memory in enumerate(self.filtered_memories):
            values = (
                memory["content"],
                MEMORY_TYPE_LABELS.get(memory["memory_type"], memory["memory_type"]),
                str(memory["importance"]),
                self._format_timestamp(memory["updated_at"]),
                memory["source"],
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, memory["id"])
                self.memory_table.setItem(row, column, item)
        self._sync_action_buttons()

    @staticmethod
    def _format_timestamp(timestamp):
        try:
            parsed = datetime.fromisoformat(timestamp)
        except (TypeError, ValueError):
            return str(timestamp)
        return parsed.astimezone().strftime("%Y-%m-%d %H:%M")

    def _selected_memory(self):
        row = self.memory_table.currentRow()
        if row < 0:
            return None
        item = self.memory_table.item(row, 0)
        memory_id = item.data(Qt.ItemDataRole.UserRole) if item else None
        return next(
            (memory for memory in self.filtered_memories if memory["id"] == memory_id),
            None,
        )

    def _sync_action_buttons(self):
        enabled = self._selected_memory() is not None
        self.edit_button.setEnabled(enabled)
        self.delete_button.setEnabled(enabled)

    def add_memory(self):
        dialog = MemoryEditorDialog(parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            memory = self.memory_manager.add_memory(
                **dialog.values(), source="gui:manual"
            )
        except Exception:
            self._show_operation_error("无法添加记忆，请检查内容和数据库状态。")
            return
        self.refresh_memories(f"已保存 {MEMORY_TYPE_LABELS[memory['memory_type']]} 记忆。")

    def edit_memory(self):
        memory = self._selected_memory()
        if memory is None:
            return
        dialog = MemoryEditorDialog(memory, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            updated = self.memory_manager.update_memory(memory["id"], **dialog.values())
        except Exception:
            self._show_operation_error("无法保存修改，请检查内容和数据库状态。")
            return
        if updated is None:
            self._show_operation_error("该记忆已不存在，列表将重新载入。")
            self.refresh_memories()
            return
        self.refresh_memories("记忆已更新。")

    def delete_memory(self):
        memory = self._selected_memory()
        if memory is None:
            return
        answer = QMessageBox.question(
            self,
            "删除记忆",
            "确定删除这条本地记忆？此操作无法撤销。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            deleted = self.memory_manager.delete_memory(memory["id"])
        except Exception:
            self._show_operation_error("无法删除记忆，请检查数据库状态。")
            return
        self.refresh_memories("记忆已删除。" if deleted else "该记忆已不存在。")

    def _show_operation_error(self, message):
        self.memory_status.setText(message)
        QMessageBox.warning(self, "Memory 操作失败", message)
