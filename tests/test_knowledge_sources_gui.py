"""Offscreen tests for the Knowledge Sources management interface."""

import gc
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox

from app import gui


class KnowledgeSourcesGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name) / "Learning_profile"
        self.root.mkdir()
        self.records = []

        for position, (folder, filename, status) in enumerate(
            (
                ("Business_Management", "plan.md", "indexed"),
                ("Computer_Science", "python.txt", "new"),
                ("math", "Calculus.docx", "modified"),
                ("physics", "broken.pdf", "failed"),
            ),
            start=1,
        ):
            directory = self.root / folder
            directory.mkdir()
            document = directory / filename
            document.write_text(f"document {position}", encoding="utf-8")
            self.records.append(
                {
                    "path": document.resolve().as_posix(),
                    "name": filename,
                    "extension": document.suffix.lower(),
                    "size": document.stat().st_size,
                    "modified_time": document.stat().st_mtime,
                    "hash": str(position),
                    "processed": status == "indexed",
                    "knowledge_id": "knowledge-plan" if status == "indexed" else None,
                    "last_indexed": (
                        "2026-09-04T06:00:00+00:00" if status == "indexed" else None
                    ),
                    "scan_status": status,
                    "last_error": "Could not parse PDF" if status == "failed" else None,
                }
            )

        self.scanner_config = {
            "watch_folders": [self.root.resolve().as_posix()],
            "ignored_folders": [".git", "__pycache__", "node_modules", "venv"],
            "scan_on_startup": False,
        }
        self.file_index = {
            "version": 1,
            "last_scan_time": "2026-09-04T07:00:00+00:00",
            "scans": {self.root.resolve().as_posix(): "2026-09-04T07:00:00+00:00"},
            "files": self.records,
        }
        self.patches = (
            patch.object(gui.MainWindow, "_run_health_check"),
            patch.object(gui.MainWindow, "_sync_cloud_files"),
            patch("app.gui.list_documents", return_value=[]),
            patch("app.gui.load_scanner_config", return_value=self.scanner_config),
            patch("app.gui.load_file_index", return_value=self.file_index),
        )

        for active_patch in self.patches:
            active_patch.start()

        self.window = gui.MainWindow()

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()

        for active_patch in reversed(self.patches):
            active_patch.stop()

        self.temporary_directory.cleanup()
        gc.collect()

    def test_sources_page_shows_authorized_folder_summary_hierarchy_and_files(self):
        self.assertEqual(self.window.knowledge_tabs.count(), 2)
        self.assertEqual(self.window.knowledge_tabs.tabText(1), "知识来源")
        self.assertEqual(self.window.source_folder_table.rowCount(), 1)
        self.assertEqual(
            self.window.source_folder_table.item(0, 0).text(),
            self.root.resolve().as_posix(),
        )
        self.assertEqual(self.window.source_summary_values["folders"].text(), "1")
        self.assertEqual(self.window.source_summary_values["files"].text(), "4")
        self.assertEqual(self.window.source_summary_values["indexed"].text(), "1")
        self.assertEqual(self.window.source_summary_values["pending"].text(), "2")
        self.assertEqual(self.window.source_summary_values["failed"].text(), "1")
        self.assertEqual(self.window.source_file_table.rowCount(), 4)
        hierarchy = self.window.source_hierarchy.toPlainText()

        for folder in ("Business_Management", "Computer_Science", "math", "physics"):
            self.assertIn(folder, hierarchy)

    def test_filename_search_and_status_filter_hide_non_matching_rows(self):
        self.window.source_search.setText("Calculus")
        visible_names = [
            self.window.source_file_table.item(row, 0).text()
            for row in range(self.window.source_file_table.rowCount())
            if not self.window.source_file_table.isRowHidden(row)
        ]
        self.assertEqual(visible_names, ["Calculus.docx"])

        self.window.source_search.clear()
        self.window.source_status_filter.setCurrentText("失败")
        visible_names = [
            self.window.source_file_table.item(row, 0).text()
            for row in range(self.window.source_file_table.rowCount())
            if not self.window.source_file_table.isRowHidden(row)
        ]
        self.assertEqual(visible_names, ["broken.pdf"])

    def test_add_and_remove_are_explicit_and_removal_keeps_source_file(self):
        added_folder = Path(self.temporary_directory.name) / "New_Source"
        added_folder.mkdir()

        with (
            patch.object(
                gui.QFileDialog,
                "getExistingDirectory",
                return_value=str(added_folder),
            ),
            patch("app.gui.add_watch_folder", return_value=True) as add_folder,
        ):
            self.window.choose_knowledge_source_folder()

        add_folder.assert_called_once_with(str(added_folder))
        source_file = Path(self.records[0]["path"])

        with (
            patch.object(
                gui.QMessageBox,
                "question",
                return_value=QMessageBox.StandardButton.Yes,
            ),
            patch("app.gui.remove_watch_folder", return_value=True) as remove_folder,
        ):
            self.window.remove_knowledge_source(self.root.as_posix())

        remove_folder.assert_called_once_with(self.root.as_posix())
        self.assertTrue(source_file.is_file())

    def test_scan_and_sync_actions_use_worker_callbacks_and_restore_buttons(self):
        scan_result = {
            "results": [],
            "errors": [],
            "file_count": 115,
            "new_count": 3,
            "modified_count": 2,
            "unchanged_count": 110,
        }

        def finish_scan(operation, **callbacks):
            self.assertIs(operation, gui.scan_authorized_sources)
            callbacks["on_progress"]("正在扫描：测试目录")
            callbacks["on_success"](scan_result)
            callbacks["on_finished"]()

        with patch.object(self.window, "_run_worker", side_effect=finish_scan):
            self.window.scan_knowledge_sources()

        self.assertIn("发现文件 115", self.window.source_progress.text())
        self.assertFalse(self.window.source_operation_busy)
        self.assertTrue(self.window.scan_sources_button.isEnabled())

        sync_result = {
            "imported": [{"name": "new.txt"}],
            "reindexed": [{"name": "changed.md"}],
            "skipped": [],
            "failed": [],
        }

        def finish_sync(operation, **callbacks):
            self.assertIs(operation, gui.synchronize_authorized_sources)
            callbacks["on_progress"]("Progress: 1 / 2")
            callbacks["on_success"](sync_result)
            callbacks["on_finished"]()

        with patch.object(self.window, "_run_worker", side_effect=finish_sync):
            self.window.sync_knowledge_sources()

        self.assertIn("新增 1，更新 1", self.window.source_progress.text())
        self.assertIn("Progress: 1 / 2", self.window.source_activity_log.toPlainText())

    def test_startup_scan_toggle_persists_without_starting_sync(self):
        updated = {**self.scanner_config, "scan_on_startup": True}

        with patch("app.gui.set_scan_on_startup", return_value=updated) as save_setting:
            self.window.scan_on_startup_checkbox.setChecked(True)

        save_setting.assert_called_once_with(True)
        self.assertTrue(self.window.scanner_settings["scan_on_startup"])


if __name__ == "__main__":
    unittest.main()
