"""Tests for local knowledge-folder discovery and change tracking."""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from app.file_scanner import (
    DEFAULT_IGNORED_FOLDERS,
    build_file_info,
    format_scan_report,
    load_file_index,
    load_scanner_config,
    scan_folder,
)


class FileScannerTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name) / "Learning_profile"
        self.root.mkdir()
        self.index_path = Path(self.temporary_directory.name) / "file_index.json"

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_recursively_scans_supported_files_and_ignores_other_types(self):
        physics = self.root / "physics"
        math = self.root / "math"
        ignored_git = self.root / ".git"
        ignored_cache = self.root / "__pycache__"

        for folder in (physics, math, ignored_git, ignored_cache):
            folder.mkdir()

        (physics / "mechanics.pdf").write_bytes(b"pdf bytes")
        (physics / "notes.txt").write_text("Newton", encoding="utf-8")
        (math / "algebra.md").write_text("# Algebra", encoding="utf-8")
        (math / "lesson.docx").write_bytes(b"docx bytes")
        (math / "diagram.png").write_bytes(b"image")
        (math / "archive.zip").write_bytes(b"archive")
        (ignored_git / "hidden.md").write_text("ignored", encoding="utf-8")
        (ignored_cache / "generated.txt").write_text("ignored", encoding="utf-8")

        result = scan_folder(self.root, index_path=self.index_path)

        self.assertEqual(len(result["files"]), 4)
        self.assertEqual(
            {record["extension"] for record in result["files"]},
            {".pdf", ".docx", ".txt", ".md"},
        )
        self.assertEqual(result["document_counts"], {"DOCX": 1, "MD": 1, "PDF": 1, "TXT": 1})
        self.assertEqual(result["folders"], ["math", "physics"])
        self.assertEqual(len(result["new_files"]), 4)
        self.assertEqual(result["errors"], [])

    def test_file_information_contains_sha256_and_required_fields(self):
        document = self.root / "physics.txt"
        content = b"mass and energy"
        document.write_bytes(content)

        info = build_file_info(document)

        self.assertEqual(
            set(info),
            {"path", "name", "extension", "size", "modified_time", "hash"},
        )
        self.assertEqual(info["name"], "physics.txt")
        self.assertEqual(info["size"], len(content))
        self.assertEqual(info["hash"], hashlib.sha256(content).hexdigest())

    def test_detects_new_modified_and_unchanged_files_by_hash(self):
        document = self.root / "course.md"
        document.write_text("version one", encoding="utf-8")

        first = scan_folder(self.root, index_path=self.index_path)
        second = scan_folder(self.root, index_path=self.index_path)
        document.write_text("version two", encoding="utf-8")
        third = scan_folder(self.root, index_path=self.index_path)

        self.assertEqual([record["name"] for record in first["new_files"]], ["course.md"])
        self.assertEqual(
            [record["name"] for record in second["unchanged_files"]],
            ["course.md"],
        )
        self.assertEqual(
            [record["name"] for record in third["modified_files"]],
            ["course.md"],
        )
        index = load_file_index(self.index_path)
        self.assertIsNotNone(index["last_scan_time"])
        self.assertFalse(index["files"][0]["processed"])
        self.assertIsNone(index["files"][0]["knowledge_id"])
        self.assertIsNone(index["files"][0]["last_indexed"])

    def test_report_contains_folder_counts_and_change_sections(self):
        folder = self.root / "Computer_Science"
        folder.mkdir()
        (folder / "python.txt").write_text("Python", encoding="utf-8")

        report = format_scan_report(scan_folder(self.root, index_path=self.index_path))

        self.assertIn("Scanning...", report)
        self.assertIn("- Computer_Science", report)
        self.assertIn("TXT: 1", report)
        self.assertIn("New files:\n- python.txt", report)

    def test_configuration_adds_all_mandatory_default_exclusions(self):
        config_path = Path(self.temporary_directory.name) / "scanner.json"
        config_path.write_text(
            json.dumps(
                {
                    "watch_folders": [self.root.as_posix()],
                    "ignored_folders": ["build"],
                }
            ),
            encoding="utf-8",
        )

        config = load_scanner_config(config_path)

        self.assertEqual(config["watch_folders"], [self.root.as_posix()])
        self.assertTrue(DEFAULT_IGNORED_FOLDERS.issubset(config["ignored_folders"]))
        self.assertIn("build", config["ignored_folders"])


if __name__ == "__main__":
    unittest.main()
