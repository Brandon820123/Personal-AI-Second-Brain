"""Tests for managing documents in the local knowledge library."""

import gc
import tempfile
import unittest
from pathlib import Path

from app.document_importer import DocumentImportError
from app.knowledge_library import delete_document, list_documents, reindex_document
from app.vector_store import VectorStore


class KnowledgeLibraryTests(unittest.TestCase):
    """Check listing, deletion isolation, and local re-indexing."""

    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary_directory.name)
        self.store = VectorStore(
            database_path=self.directory / "chroma",
            collection_name="test_knowledge_library",
        )

    def tearDown(self):
        self.store.client.close()
        self.store = None
        gc.collect()
        self.temporary_directory.cleanup()

    def _store_text_document(self, filename, chunks):
        source_path = self.directory / filename
        source_path.write_text("\n\n".join(chunks), encoding="utf-8")
        embeddings = [[1.0, float(index)] for index in range(len(chunks))]
        self.store.store_document(source_path, chunks, embeddings)
        return source_path

    def test_lists_unique_documents_and_counts_their_chunks(self):
        self._store_text_document("notes.txt", ["One.", "Two."])
        pdf_path = self.directory / "physics.pdf"
        pdf_path.write_bytes(b"test placeholder")
        self.store.store_document(
            pdf_path,
            ["Page one.", "Page two A.", "Page two B."],
            [[1.0, 0.0], [0.0, 1.0], [0.5, 0.5]],
            chunk_metadata=[
                {"page_number": 1},
                {"page_number": 2},
                {"page_number": 2},
            ],
        )

        documents = list_documents(self.store)

        self.assertEqual(len(documents), 2)
        documents_by_name = {
            document["filename"]: document for document in documents
        }
        self.assertEqual(documents_by_name["notes.txt"]["chunk_count"], 2)
        self.assertIsNone(documents_by_name["notes.txt"]["page_count"])
        self.assertEqual(documents_by_name["physics.pdf"]["chunk_count"], 3)
        self.assertEqual(documents_by_name["physics.pdf"]["page_count"], 2)

    def test_deletes_only_the_selected_document(self):
        notes_path = self._store_text_document("notes.txt", ["Delete me."])
        keep_path = self._store_text_document("keep.md", ["Keep me.", "Still here."])
        documents = list_documents(self.store)
        notes = next(
            document for document in documents if document["filename"] == notes_path.name
        )

        deleted_count = delete_document(notes["source_id"], self.store)
        remaining_documents = list_documents(self.store)

        self.assertEqual(deleted_count, 1)
        self.assertEqual(self.store.count(), 2)
        self.assertEqual(
            [document["filename"] for document in remaining_documents],
            [keep_path.name],
        )

    def test_reindexes_updated_source_file(self):
        source_path = self._store_text_document("notes.txt", ["Old knowledge."])
        old_ids = self.store.get_source_ids(
            list_documents(self.store)[0]["source_id"]
        )
        source_path.write_text(
            "Updated first topic.\n\nUpdated second topic.",
            encoding="utf-8",
        )
        document = list_documents(self.store)[0]

        result = reindex_document(
            document,
            vector_store=self.store,
            embedding_function=lambda texts: [[1.0, 0.0] for _ in texts],
            progress_function=lambda message: None,
        )
        records = self.store.collection.get(include=["documents", "metadatas"])

        self.assertEqual(result["previous_chunk_count"], 1)
        self.assertEqual(result["stored_count"], 2)
        self.assertEqual(self.store.count(), 2)
        self.assertCountEqual(
            records["documents"],
            ["Updated first topic.", "Updated second topic."],
        )
        self.assertFalse(old_ids & set(records["ids"]))
        self.assertTrue(
            all(metadata["source_path"] == str(source_path.resolve())
                for metadata in records["metadatas"])
        )

    def test_missing_source_file_stops_reindex_without_deleting_chunks(self):
        source_path = self._store_text_document("missing.txt", ["Still stored."])
        document = list_documents(self.store)[0]
        source_path.unlink()

        with self.assertRaisesRegex(FileNotFoundError, "no longer exists"):
            reindex_document(document, vector_store=self.store)

        self.assertEqual(self.store.count(), 1)

    def test_failed_reindex_keeps_previous_chunks(self):
        source_path = self._store_text_document("safe.txt", ["Old one.", "Old two."])
        document = list_documents(self.store)[0]
        old_ids = self.store.get_source_ids(document["source_id"])
        source_path.write_text("New one.\n\nNew two.", encoding="utf-8")

        def failed_embeddings(texts):
            raise ValueError("Embedding failed.")

        with self.assertRaises(DocumentImportError):
            reindex_document(
                document,
                vector_store=self.store,
                embedding_function=failed_embeddings,
                progress_function=lambda message: None,
            )

        self.assertEqual(self.store.get_source_ids(document["source_id"]), old_ids)
        self.assertEqual(self.store.count(), 2)


if __name__ == "__main__":
    unittest.main()
