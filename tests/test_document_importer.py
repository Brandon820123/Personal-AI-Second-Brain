"""Tests for batched and resumable document importing."""

import gc
import tempfile
import unittest
from pathlib import Path

from app.document_importer import DocumentImportError, import_document_chunks
from app.embeddings import EmbeddingError
from app.vector_store import VectorStore


class DocumentImporterTests(unittest.TestCase):
    """Check batching, retries, incremental storage, and resume behavior."""

    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary_directory.name)
        self.source_path = self.directory / "large-notes.txt"
        self.source_path.write_text("Local test document.", encoding="utf-8")
        self.store = VectorStore(
            database_path=self.directory / "chroma",
            collection_name="test_batched_import",
        )

    def tearDown(self):
        self.store.client.close()
        self.store = None
        gc.collect()
        self.temporary_directory.cleanup()

    def _chunks(self, count):
        return [f"Chunk number {index}." for index in range(count)]

    def _metadata(self, count):
        return [{"page_number": index + 1} for index in range(count)]

    def test_embeds_and_stores_in_configured_batches(self):
        chunks = self._chunks(70)
        batch_lengths = []
        progress_messages = []

        def fake_embeddings(batch):
            batch_lengths.append(len(batch))
            return [[float(len(text)), 1.0] for text in batch]

        stored_count = import_document_chunks(
            self.source_path,
            chunks,
            self._metadata(70),
            vector_store=self.store,
            batch_size=32,
            embedding_function=fake_embeddings,
            progress_function=progress_messages.append,
        )

        self.assertEqual(batch_lengths, [32, 32, 6])
        self.assertEqual(stored_count, 70)
        self.assertEqual(self.store.count(), 70)
        self.assertIn("Embedding batch 3/3 (6 chunks)...", progress_messages)
        self.assertIn("Embedded and stored 70 / 70 chunks.", progress_messages)

    def test_retries_a_failed_embedding_batch(self):
        attempts = 0
        progress_messages = []

        def flaky_embeddings(batch):
            nonlocal attempts
            attempts += 1

            if attempts == 1:
                raise EmbeddingError("Temporary local runner failure.")

            return [[1.0, float(index)] for index, text in enumerate(batch)]

        import_document_chunks(
            self.source_path,
            self._chunks(2),
            self._metadata(2),
            vector_store=self.store,
            max_retries=2,
            retry_delay=0,
            embedding_function=flaky_embeddings,
            progress_function=progress_messages.append,
        )

        self.assertEqual(attempts, 2)
        self.assertEqual(self.store.count(), 2)
        self.assertTrue(any("Retrying (1/2)" in message for message in progress_messages))

    def test_failed_import_keeps_batches_and_resumes_missing_chunks(self):
        chunks = self._chunks(5)
        metadata = self._metadata(5)
        failed_calls = 0

        def fail_second_batch(batch):
            nonlocal failed_calls
            failed_calls += 1

            if failed_calls > 1:
                raise EmbeddingError("Local runner unavailable.")

            return [[1.0, float(index)] for index, text in enumerate(batch)]

        with self.assertRaisesRegex(DocumentImportError, "batch 2/3 failed"):
            import_document_chunks(
                self.source_path,
                chunks,
                metadata,
                vector_store=self.store,
                batch_size=2,
                max_retries=1,
                retry_delay=0,
                embedding_function=fail_second_batch,
                progress_function=lambda message: None,
            )

        self.assertEqual(self.store.count(), 2)
        resumed_batches = []
        progress_messages = []

        def resumed_embeddings(batch):
            resumed_batches.append(list(batch))
            return [[2.0, float(index)] for index, text in enumerate(batch)]

        stored_count = import_document_chunks(
            self.source_path,
            chunks,
            metadata,
            vector_store=self.store,
            batch_size=2,
            embedding_function=resumed_embeddings,
            progress_function=progress_messages.append,
        )

        self.assertEqual(stored_count, 5)
        self.assertEqual(self.store.count(), 5)
        self.assertEqual(resumed_batches, [chunks[2:4], chunks[4:5]])
        self.assertIn("Resuming import: 2 / 5 chunks already stored.", progress_messages)

    def test_failed_reimport_keeps_previous_complete_version(self):
        old_chunks = ["Old chunk one.", "Old chunk two."]
        metadata = self._metadata(2)

        import_document_chunks(
            self.source_path,
            old_chunks,
            metadata,
            vector_store=self.store,
            embedding_function=lambda texts: [[1.0, 0.0] for _ in texts],
            progress_function=lambda message: None,
        )
        old_records = self.store.prepare_document_records(
            self.source_path,
            old_chunks,
            metadata,
        )
        failed_calls = 0

        def fail_second_batch(batch):
            nonlocal failed_calls
            failed_calls += 1

            if failed_calls == 2:
                raise EmbeddingError("Temporary local runner failure.")

            return [[1.0, 0.0] for _ in batch]

        new_chunks = ["New chunk one.", "New chunk two."]

        with self.assertRaises(DocumentImportError):
            import_document_chunks(
                self.source_path,
                new_chunks,
                metadata,
                vector_store=self.store,
                batch_size=1,
                max_retries=0,
                embedding_function=fail_second_batch,
                progress_function=lambda message: None,
            )

        stored_ids = self.store.get_source_ids(old_records["source_id"])
        self.assertTrue(set(old_records["ids"]).issubset(stored_ids))
        self.assertEqual(self.store.count(), 3)

        import_document_chunks(
            self.source_path,
            new_chunks,
            metadata,
            vector_store=self.store,
            batch_size=1,
            embedding_function=lambda texts: [[1.0, 0.0] for _ in texts],
            progress_function=lambda message: None,
        )

        current_ids = self.store.get_source_ids(old_records["source_id"])
        self.assertEqual(self.store.count(), 2)
        self.assertFalse(set(old_records["ids"]) & current_ids)


if __name__ == "__main__":
    unittest.main()
