"""Offscreen tests for the Memory management page."""

import gc
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from app.memory_manager import MemoryManager
from app.ui.memory_page import MemoryPage


class MemoryPageTests(unittest.TestCase):
    """Verify listing, search, filtering, CRUD, refresh, and safe failures."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.manager = MemoryManager(
            db_path=Path(self.temporary_directory.name) / "memory.db"
        )
        self.personal = self.manager.add_memory(
            "我喜欢不加糖的咖啡。", "personal", 4, "chat:user"
        )
        self.project = self.manager.add_memory(
            "Atlas 项目下一步完成 Memory GUI。", "project", 5, "project:atlas"
        )
        self.conversation = self.manager.add_memory(
            "对话确认每周复盘 Atlas 里程碑。",
            "conversation",
            3,
            "summary:manual",
        )
        self.page = MemoryPage(self.manager)
        self.page.refresh_memories()

    def tearDown(self):
        self.page.close()
        self.page.deleteLater()
        self.app.processEvents()
        gc.collect()
        self.temporary_directory.cleanup()

    def _select_content(self, content):
        for row in range(self.page.memory_table.rowCount()):
            if self.page.memory_table.item(row, 0).text() == content:
                self.page.memory_table.selectRow(row)
                self.page.memory_table.setCurrentCell(row, 0)
                self.app.processEvents()
                return row
        self.fail(f"Memory row not found: {content}")

    def test_lists_memory_fields_and_complete_statistics(self):
        self.assertEqual(self.page.memory_table.rowCount(), 3)
        self.assertEqual(self.page.summary_values["total"].text(), "3")
        self.assertEqual(self.page.summary_values["personal"].text(), "1")
        self.assertEqual(self.page.summary_values["project"].text(), "1")
        self.assertEqual(self.page.summary_values["conversation"].text(), "1")

        row = self._select_content(self.project["content"])
        self.assertEqual(self.page.memory_table.item(row, 1).text(), "Project")
        self.assertEqual(self.page.memory_table.item(row, 2).text(), "5")
        self.assertTrue(self.page.memory_table.item(row, 3).text())
        self.assertEqual(self.page.memory_table.item(row, 4).text(), "project:atlas")
        self.assertIsNotNone(self.page.memory_table.verticalScrollBar())

    def test_search_calls_existing_retrieval_and_keeps_global_statistics(self):
        with patch.object(
            self.manager,
            "search_memories",
            wraps=self.manager.search_memories,
        ) as search:
            self.page.search_input.setText("咖啡偏好")

        self.assertEqual(self.page.memory_table.rowCount(), 1)
        self.assertEqual(
            self.page.memory_table.item(0, 0).text(), self.personal["content"]
        )
        self.assertEqual(self.page.summary_values["total"].text(), "3")
        search.assert_called_with("咖啡偏好", memory_type=None, limit=50)

    def test_type_filter_supports_all_three_memory_types(self):
        expected = {
            "personal": self.personal,
            "project": self.project,
            "conversation": self.conversation,
        }
        for memory_type, memory in expected.items():
            with self.subTest(memory_type=memory_type):
                self.page.type_filter.setCurrentIndex(
                    self.page.type_filter.findData(memory_type)
                )
                self.assertEqual(self.page.memory_table.rowCount(), 1)
                self.assertEqual(
                    self.page.memory_table.item(0, 0).text(), memory["content"]
                )

        self.page.type_filter.setCurrentIndex(0)
        self.assertEqual(self.page.memory_table.rowCount(), 3)

    @patch("app.ui.memory_page.MemoryEditorDialog")
    def test_add_uses_manager_and_refreshes_list_and_statistics(self, dialog_class):
        dialog = dialog_class.return_value
        dialog.exec.return_value = QDialog.DialogCode.Accepted
        dialog.values.return_value = {
            "content": "每月整理一次长期记忆。",
            "memory_type": "personal",
            "importance": 2,
        }

        self.page.add_memory()

        saved = self.manager.search_memories("长期记忆")
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]["source"], "gui:manual")
        self.assertEqual(self.page.memory_table.rowCount(), 4)
        self.assertEqual(self.page.summary_values["total"].text(), "4")
        self.assertEqual(self.page.summary_values["personal"].text(), "2")
        self.assertIn("已保存", self.page.memory_status.text())

    @patch("app.ui.memory_page.MemoryEditorDialog")
    def test_edit_updates_selected_memory_and_refreshes(self, dialog_class):
        self._select_content(self.project["content"])
        dialog = dialog_class.return_value
        dialog.exec.return_value = QDialog.DialogCode.Accepted
        dialog.values.return_value = {
            "content": "Atlas Memory GUI 已完成测试。",
            "memory_type": "conversation",
            "importance": 4,
        }

        self.page.edit_memory()

        updated = self.manager.get_memory(self.project["id"])
        self.assertEqual(updated["content"], "Atlas Memory GUI 已完成测试。")
        self.assertEqual(updated["memory_type"], "conversation")
        self.assertEqual(updated["importance"], 4)
        self.assertEqual(updated["source"], "project:atlas")
        self.assertEqual(self.page.summary_values["project"].text(), "0")
        self.assertEqual(self.page.summary_values["conversation"].text(), "2")
        self.assertEqual(self.page.memory_table.rowCount(), 3)

    @patch("app.ui.memory_page.QMessageBox.question")
    def test_delete_requires_confirmation_then_refreshes(self, question):
        question.return_value = QMessageBox.StandardButton.No
        self._select_content(self.personal["content"])
        self.page.delete_memory()
        self.assertIsNotNone(self.manager.get_memory(self.personal["id"]))

        question.return_value = QMessageBox.StandardButton.Yes
        self.page.delete_memory()

        self.assertIsNone(self.manager.get_memory(self.personal["id"]))
        self.assertEqual(self.page.memory_table.rowCount(), 2)
        self.assertEqual(self.page.summary_values["total"].text(), "2")
        self.assertEqual(self.page.summary_values["personal"].text(), "0")
        self.assertEqual(self.page.memory_status.text(), "记忆已删除。")

    def test_database_read_failure_is_displayed_without_exception_details(self):
        broken_manager = Mock()
        broken_manager.list_memories.side_effect = RuntimeError(
            "sqlite traceback and private path"
        )
        page = MemoryPage(broken_manager)

        self.assertFalse(page.refresh_memories())
        self.assertIn("无法读取本地记忆数据库", page.memory_status.text())
        self.assertNotIn("private path", page.memory_status.text())
        self.assertEqual(page.memory_table.rowCount(), 0)
        page.deleteLater()

    def test_action_errors_show_a_friendly_message_without_traceback(self):
        self._select_content(self.project["content"])
        self.manager.update_memory = Mock(side_effect=RuntimeError("private traceback"))
        dialog = Mock()
        dialog.exec.return_value = QDialog.DialogCode.Accepted
        dialog.values.return_value = {
            "content": "Updated",
            "memory_type": "project",
            "importance": 3,
        }
        with patch("app.ui.memory_page.MemoryEditorDialog", return_value=dialog), patch(
            "app.ui.memory_page.QMessageBox.warning"
        ) as warning:
            self.page.edit_memory()

        self.assertIn("无法保存修改", self.page.memory_status.text())
        self.assertNotIn("private traceback", self.page.memory_status.text())
        warning.assert_called_once()


if __name__ == "__main__":
    unittest.main()
