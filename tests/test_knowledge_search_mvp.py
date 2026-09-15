"""Search filtering, source context, and Qt worker responsiveness checks."""

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QThread, QTimer
from PySide6.QtWidgets import QApplication
from pypdf import PdfWriter

from app import gui
from app.semantic_search import load_search_context, search_knowledge
from app.file_scanner import FileScannerError
from app.document_loader import load_document_pages
from app.vector_store import VectorStore
from tests.test_document_loader import add_text_page


class SearchMvpTests(unittest.TestCase):
    def test_search_deduplicates_copies_and_survives_invalid_source_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "sources"
            source.mkdir()
            store = VectorStore(database_path=root / "db")
            try:
                for path in (root / "copy.pdf", source / "original.pdf"):
                    path.write_bytes(b"test")
                    store.store_document(path, ["Physics concepts", "31"],
                                         [[1., 0.], [1., 0.]],
                                         [{"page_number": 1}, {"page_number": 2}])
                with (patch("app.vector_store.VectorStore", return_value=store),
                      patch("app.semantic_search.generate_query_embedding", return_value=[1., 0.]),
                      patch("app.file_scanner.load_scanner_config",
                            return_value={"watch_folders": [str(source)]})):
                    hits = search_knowledge("physics")
                    self.assertEqual(len(hits), 1)
                    self.assertEqual(hits[0]["relative_path"], "original.pdf")
                    self.assertAlmostEqual(hits[0]["score"], 1.)
                    self.assertEqual(search_knowledge("physics", file_type=".docx"), [])
                    with patch("app.file_scanner.load_scanner_config",
                               side_effect=FileScannerError("invalid JSON")):
                        self.assertEqual(len(search_knowledge("physics")), 1)
            finally:
                store.client.close()

    def test_chroma_filters_before_ranking(self):
        with tempfile.TemporaryDirectory() as directory:
            store = VectorStore(database_path=Path(directory) / "db")
            try:
                for extension, vector in (("pdf", [1., 0.]), ("docx", [.8, .2]),
                                          ("txt", [.7, .3]), ("md", [.6, .4])):
                    store.store_document(Path(directory) / f"file.{extension}",
                                         ["test text"], [vector])
                for extension in ("pdf", "docx", "txt", "md"):
                    results = store.search([1., 0.], top_k=1, file_type=f".{extension}")
                    self.assertEqual(results[0]["metadata"]["file_type"], f".{extension}")
            finally:
                store.client.close()

    def test_original_context_whitespace_missing_file_and_pdf_page(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "notes.txt"
            path.write_text("Before\n\nmatched\nwords\n\nAfter", encoding="utf-8")
            result = {"metadata": {"source_path": str(path)}, "text": "matched words"}
            self.assertIn("After", load_search_context(result))
            path.unlink()
            self.assertIn("保留的索引片段", load_search_context(result))
            pdf = Path(directory) / "pages.pdf"
            writer = PdfWriter()
            add_text_page(writer, "first page")
            add_text_page(writer, "second page")
            writer.write(pdf)
            pages = load_document_pages(pdf, page_number=2)
            self.assertEqual(len(pages), 1)
            self.assertEqual(pages[0]["page_number"], 2)
            self.assertIn("second page", pages[0]["text"])
            pdf.write_bytes(b"not a PDF")
            result["metadata"]["source_path"] = str(pdf)
            self.assertIn("保留的索引片段", load_search_context(result))

    def test_search_and_context_keep_event_loop_responsive(self):
        app = QApplication.instance() or QApplication([])
        window = gui.MainWindow()
        result = {"metadata": {"source_filename": "notes.md", "source_path": "D:/notes.md",
                               "file_type": ".md"}, "text": "matched text",
                  "relative_path": "notes.md", "score": .8}
        ticks = []
        timer = QTimer()
        timer.setInterval(10)
        timer.timeout.connect(lambda: ticks.append(time.monotonic()))
        timer.start()

        def slow_result(*args, **kwargs):
            self.assertNotEqual(QThread.currentThread(), app.thread())
            time.sleep(.15)
            return [result]

        def slow_context(*args):
            self.assertNotEqual(QThread.currentThread(), app.thread())
            time.sleep(.15)
            return "before matched text after"

        def drain():
            deadline = time.monotonic() + 5
            while window.worker_threads and time.monotonic() < deadline:
                app.processEvents()
                time.sleep(.005)
            self.assertFalse(window.worker_threads)

        try:
            with patch("app.gui.search_knowledge", side_effect=slow_result):
                window.knowledge_search.setText("math")
                window.search_knowledge_documents()
                drain()
            self.assertEqual(window.search_table.rowCount(), 1)
            self.assertGreater(len(ticks), 5)
            ticks.clear()
            with patch("app.gui.load_search_context", side_effect=slow_context):
                window.search_table.selectRow(0)
                drain()
            self.assertIn("before matched text after", window.search_context.toPlainText())
            self.assertGreater(len(ticks), 5)
            self.assertEqual(window.knowledge_tabs.tabText(1), "知识来源")
            with patch("app.gui.search_knowledge", side_effect=RuntimeError("offline")):
                window.search_knowledge_documents()
                drain()
            self.assertIn("offline", window.search_status.text())
            self.assertTrue(window.search_button.isEnabled())
            with patch("app.gui.search_knowledge", return_value=[]):
                window.search_knowledge_documents()
                drain()
            self.assertEqual(window.search_table.rowCount(), 0)
            self.assertIn("没有匹配", window.search_status.text())

            # Source operations serialize writes but keep the GUI event loop alive.
            window.scanner_settings["watch_folders"] = ["D:/Learning_profile"]
            for operation, entry in (("scan_authorized_sources", window.scan_knowledge_sources),
                                     ("synchronize_authorized_sources", window.sync_knowledge_sources)):
                ticks.clear()
                def fail_slowly(**kwargs):
                    self.assertNotEqual(QThread.currentThread(), app.thread())
                    time.sleep(.15)
                    raise ValueError("unreadable source")
                with patch(f"app.gui.{operation}", side_effect=fail_slowly) as worker:
                    entry()
                    entry()
                    self.assertFalse(window.import_button.isEnabled())
                    drain()
                    self.assertEqual(worker.call_count, 1)
                self.assertGreater(len(ticks), 5)
                self.assertIn("unreadable source", window.source_progress.text())
                self.assertTrue(window.import_button.isEnabled())
        finally:
            timer.stop()
            drain()
            window.close()
            window.deleteLater()
            app.processEvents()


if __name__ == "__main__":
    unittest.main()
