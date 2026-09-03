"""Tests for automatic scanner-to-knowledge-library synchronization."""

import gc
import tempfile
import unittest
from pathlib import Path

from app.chunker import chunk_document_pages
from app.document_importer import import_document_chunks
from app.document_loader import load_document_pages
from app.file_scanner import load_file_index
from app.knowledge_library import list_documents, reindex_document
from app.knowledge_sync import sync_new_documents
from app.vector_store import VectorStore


class FakeKnowledgeLibrary:
    def __init__(self):
        self.documents = []
        self.import_calls = []
        self.reindex_calls = []
        self.fail_names = set()

    def import_document(self, path, on_progress):
        source_path = Path(path).resolve()
        self.import_calls.append(source_path)

        if source_path.name in self.fail_names:
            raise ValueError("unsupported or unreadable test document")

        on_progress("Embedded test document")
        source_id = f"knowledge-{source_path.stem}"
        self.documents = [
            document
            for document in self.documents
            if Path(document["source_path"]).resolve() != source_path
        ]
        self.documents.append(
            {
                "source_id": source_id,
                "source_path": str(source_path),
                "filename": source_path.name,
            }
        )
        return 2

    def reindex_document(self, document, on_progress):
        self.reindex_calls.append(document["source_id"])
        on_progress("Re-embedded test document")
        return {"previous_chunk_count": 1, "stored_count": 2}

    def list_documents(self):
        return [dict(document) for document in self.documents]


class KnowledgeSyncTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name) / "Learning_profile"
        self.root.mkdir()
        self.config_path = Path(self.temporary_directory.name) / "scanner.json"
        self.index_path = Path(self.temporary_directory.name) / "file_index.json"
        self.library = FakeKnowledgeLibrary()
        self.logs = []

    def tearDown(self):
        self.temporary_directory.cleanup()

    def sync(self):
        return sync_new_documents(
            config_path=self.config_path,
            index_path=self.index_path,
            watch_folders=[self.root],
            import_function=self.library.import_document,
            reindex_function=self.library.reindex_document,
            list_documents_function=self.library.list_documents,
            log_function=self.logs.append,
        )

    def test_new_file_is_imported_and_indexed_state_is_persisted(self):
        document = self.root / "physics" / "Thermal Physics.pdf"
        document.parent.mkdir()
        document.write_bytes(b"test pdf")

        result = self.sync()

        self.assertEqual(self.library.import_calls, [document.resolve()])
        self.assertEqual(len(result["imported"]), 1)
        record = load_file_index(self.index_path)["files"][0]
        self.assertTrue(record["processed"])
        self.assertEqual(record["knowledge_id"], "knowledge-Thermal Physics")
        self.assertTrue(record["last_indexed"].endswith("+00:00"))
        self.assertIn("New document detected:", self.logs)
        self.assertIn("✓ Added to knowledge base", self.logs)

    def test_modified_file_uses_existing_safe_reindex_operation(self):
        document = self.root / "math" / "Calculus.txt"
        document.parent.mkdir()
        document.write_text("version one", encoding="utf-8")
        self.sync()
        self.logs.clear()
        document.write_text("version two", encoding="utf-8")

        result = self.sync()

        self.assertEqual(self.library.import_calls, [document.resolve()])
        self.assertEqual(self.library.reindex_calls, ["knowledge-Calculus"])
        self.assertEqual(len(result["reindexed"]), 1)
        self.assertIn("Modified document detected:", self.logs)
        self.assertIn("✓ Re-indexed in knowledge base", self.logs)

    def test_unchanged_processed_file_is_not_repeated(self):
        document = self.root / "notes.md"
        document.write_text("stable", encoding="utf-8")
        self.sync()
        import_count = len(self.library.import_calls)
        reindex_count = len(self.library.reindex_calls)

        result = self.sync()

        self.assertEqual(len(self.library.import_calls), import_count)
        self.assertEqual(len(self.library.reindex_calls), reindex_count)
        self.assertEqual([record["name"] for record in result["skipped"]], ["notes.md"])

    def test_one_failed_file_does_not_stop_later_documents(self):
        bad_document = self.root / "bad.docx"
        good_document = self.root / "good.txt"
        bad_document.write_bytes(b"broken")
        good_document.write_text("usable", encoding="utf-8")
        self.library.fail_names.add("bad.docx")

        result = self.sync()

        self.assertEqual([record["name"] for record in result["failed"]], ["bad.docx"])
        self.assertEqual([record["name"] for record in result["imported"]], ["good.txt"])
        records = {
            record["name"]: record for record in load_file_index(self.index_path)["files"]
        }
        self.assertFalse(records["bad.docx"]["processed"])
        self.assertIn("unsupported or unreadable", records["bad.docx"]["last_error"])
        self.assertTrue(records["good.txt"]["processed"])
        self.assertIn("Sync completed.", self.logs)

    def test_failed_file_is_retried_even_when_its_hash_is_unchanged(self):
        document = self.root / "retry.txt"
        document.write_text("retry me", encoding="utf-8")
        self.library.fail_names.add(document.name)
        first = self.sync()
        self.library.fail_names.clear()

        second = self.sync()

        self.assertEqual(len(first["failed"]), 1)
        self.assertEqual(len(second["imported"]), 1)
        self.assertEqual(len(self.library.import_calls), 2)
        self.assertIn("Pending document retry:", self.logs)

    def test_sync_uses_existing_pipeline_to_create_real_knowledge_records(self):
        document = self.root / "actual.txt"
        document.write_text("First topic.\n\nSecond topic.", encoding="utf-8")
        store = VectorStore(
            database_path=Path(self.temporary_directory.name) / "chroma",
            collection_name="knowledge_sync_integration",
        )

        def import_with_local_test_embeddings(path, on_progress):
            pages = load_document_pages(path)
            chunks, metadata = chunk_document_pages(pages)
            return import_document_chunks(
                path,
                chunks,
                metadata,
                vector_store=store,
                embedding_function=lambda texts: [[1.0, 0.0] for _ in texts],
                progress_function=on_progress,
            )

        def reindex_with_local_test_embeddings(record, on_progress):
            return reindex_document(
                record,
                vector_store=store,
                embedding_function=lambda texts: [[1.0, 0.0] for _ in texts],
                progress_function=on_progress,
            )

        try:
            result = sync_new_documents(
                config_path=self.config_path,
                index_path=self.index_path,
                watch_folders=[self.root],
                import_function=import_with_local_test_embeddings,
                reindex_function=reindex_with_local_test_embeddings,
                list_documents_function=lambda: list_documents(store),
                log_function=self.logs.append,
            )
            documents = list_documents(store)

            self.assertEqual(len(result["imported"]), 1)
            self.assertEqual(len(documents), 1)
            self.assertEqual(documents[0]["filename"], "actual.txt")
            self.assertGreater(documents[0]["chunk_count"], 0)
        finally:
            store.client.close()
            gc.collect()


if __name__ == "__main__":
    unittest.main()
