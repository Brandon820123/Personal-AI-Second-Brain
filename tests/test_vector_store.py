"""Tests for the local persistent Chroma vector store."""

import gc
import tempfile
import unittest
from pathlib import Path

from app.vector_store import VectorStore


class VectorStoreTests(unittest.TestCase):
    """Check local initialization, storage, querying, and persistence."""

    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "chroma"
        self.source_path = Path(self.temporary_directory.name) / "notes.md"
        self.source_path.write_text("Local test document.", encoding="utf-8")
        self.store = VectorStore(
            database_path=self.database_path,
            collection_name="test_knowledge_base",
        )
        self.open_stores = [self.store]

    def tearDown(self):
        for store in self.open_stores:
            store.client.close()

        self.store = None
        self.open_stores.clear()
        gc.collect()
        self.temporary_directory.cleanup()

    def test_initializes_persistent_local_collection(self):
        self.assertTrue(self.database_path.exists())
        self.assertEqual(self.store.collection.name, "test_knowledge_base")
        self.assertFalse(self.store.client.get_settings().anonymized_telemetry)
        self.assertEqual(self.store.count(), 0)

    def test_adds_chunks_with_source_metadata(self):
        chunks = ["Python dictionaries use keys.", "Tomatoes need sunlight."]
        embeddings = [[1.0, 0.0], [0.0, 1.0]]

        stored_count = self.store.store_document(
            self.source_path,
            chunks,
            embeddings,
        )
        records = self.store.collection.get(include=["documents", "metadatas"])

        self.assertEqual(stored_count, 2)
        self.assertEqual(self.store.count(), 2)
        self.assertCountEqual(records["documents"], chunks)
        self.assertTrue(
            all(
                metadata["source_filename"] == "notes.md"
                for metadata in records["metadatas"]
            )
        )
        self.assertTrue(
            all(
                metadata["file_type"] == ".md"
                for metadata in records["metadatas"]
            )
        )
        self.assertCountEqual(
            [metadata["chunk_index"] for metadata in records["metadatas"]],
            [0, 1],
        )

    def test_queries_stored_embeddings(self):
        self.store.store_document(
            self.source_path,
            ["Python dictionaries use keys.", "Tomatoes need sunlight."],
            [[1.0, 0.0], [0.0, 1.0]],
        )

        results = self.store.search([1.0, 0.0], top_k=2)

        self.assertEqual(results[0]["text"], "Python dictionaries use keys.")
        self.assertAlmostEqual(results[0]["distance"], 0.0, places=5)
        self.assertEqual(results[0]["metadata"]["chunk_index"], 0)

    def test_imports_pdf_chunks_with_page_metadata(self):
        pdf_path = Path(self.temporary_directory.name) / "physics.pdf"
        pdf_path.write_bytes(b"local test placeholder")

        stored_count = self.store.store_document(
            pdf_path,
            ["Inertia on page one.", "Gravity on page two."],
            [[1.0, 0.0], [0.0, 1.0]],
            chunk_metadata=[{"page_number": 1}, {"page_number": 2}],
        )
        self.store.store_document(
            pdf_path,
            ["Inertia on page one.", "Gravity on page two."],
            [[1.0, 0.0], [0.0, 1.0]],
            chunk_metadata=[{"page_number": 1}, {"page_number": 2}],
        )
        records = self.store.collection.get(include=["metadatas"])

        self.assertEqual(stored_count, 2)
        self.assertEqual(self.store.count(), 2)
        self.assertTrue(
            all(
                metadata["file_type"] == ".pdf"
                for metadata in records["metadatas"]
            )
        )
        self.assertCountEqual(
            [metadata["page_number"] for metadata in records["metadatas"]],
            [1, 2],
        )

    def test_duplicate_import_reuses_stable_ids(self):
        chunks = ["First chunk.", "Second chunk."]
        embeddings = [[1.0, 0.0], [0.0, 1.0]]

        self.store.store_document(self.source_path, chunks, embeddings)
        first_ids = set(self.store.collection.get(include=[])["ids"])
        self.store.store_document(self.source_path, chunks, embeddings)
        second_ids = set(self.store.collection.get(include=[])["ids"])

        self.assertEqual(self.store.count(), 2)
        self.assertEqual(first_ids, second_ids)

    def test_data_persists_across_client_reinitialization(self):
        self.store.store_document(
            self.source_path,
            ["Persistent private knowledge."],
            [[1.0, 0.0]],
        )

        reopened_store = VectorStore(
            database_path=self.database_path,
            collection_name="test_knowledge_base",
        )
        self.open_stores.append(reopened_store)
        results = reopened_store.search([1.0, 0.0], top_k=1)

        self.assertEqual(reopened_store.count(), 1)
        self.assertEqual(results[0]["text"], "Persistent private knowledge.")


if __name__ == "__main__":
    unittest.main()
