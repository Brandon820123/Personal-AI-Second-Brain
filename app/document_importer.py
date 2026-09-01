"""Import document chunks into Chroma in small, resumable batches."""

import math
import time

try:
    from .embeddings import (
        DEFAULT_EMBEDDING_BATCH_SIZE,
        EmbeddingError,
        generate_embeddings,
    )
    from .vector_store import VectorStore
except ImportError:
    from embeddings import (
        DEFAULT_EMBEDDING_BATCH_SIZE,
        EmbeddingError,
        generate_embeddings,
    )
    from vector_store import VectorStore


DEFAULT_MAX_RETRIES = 2
DEFAULT_RETRY_DELAY_SECONDS = 2


class DocumentImportError(RuntimeError):
    """Report a clear failure during a batched local document import."""


def _print_progress(message):
    """Print import progress immediately, including when output is redirected."""
    print(message, flush=True)


def _batched_indexes(indexes, batch_size):
    """Yield small lists of chunk indexes in their original order."""
    for start in range(0, len(indexes), batch_size):
        yield indexes[start : start + batch_size]


def import_document_chunks(
    source_path,
    chunks,
    chunk_metadata,
    vector_store=None,
    batch_size=DEFAULT_EMBEDDING_BATCH_SIZE,
    max_retries=DEFAULT_MAX_RETRIES,
    retry_delay=DEFAULT_RETRY_DELAY_SECONDS,
    embedding_function=generate_embeddings,
    progress_function=_print_progress,
    force_reembed=False,
):
    """Embed and store missing chunks incrementally, then finalize the import."""
    if batch_size <= 0:
        raise ValueError("Embedding batch size must be greater than zero.")

    if max_retries < 0:
        raise ValueError("Maximum retries must be zero or more.")

    owns_vector_store = vector_store is None
    store = vector_store or VectorStore()

    try:
        records = store.prepare_document_records(
            source_path,
            chunks,
            chunk_metadata=chunk_metadata,
        )
        existing_ids = store.get_source_ids(records["source_id"])
        if force_reembed:
            missing_indexes = list(range(len(chunks)))
        else:
            missing_indexes = [
                chunk_index
                for chunk_index, chunk_id in enumerate(records["ids"])
                if chunk_id not in existing_ids
            ]
        already_stored = len(chunks) - len(missing_indexes)

        if already_stored:
            progress_function(
                f"Resuming import: {already_stored} / {len(chunks)} chunks "
                "already stored."
            )

        batch_count = math.ceil(len(missing_indexes) / batch_size)

        for batch_number, batch_indexes in enumerate(
            _batched_indexes(missing_indexes, batch_size),
            start=1,
        ):
            batch_chunks = [chunks[index] for index in batch_indexes]
            progress_function(
                f"Embedding batch {batch_number}/{batch_count} "
                f"({len(batch_chunks)} chunks)..."
            )

            for attempt in range(max_retries + 1):
                try:
                    batch_embeddings = embedding_function(batch_chunks)
                    break
                except (EmbeddingError, ValueError) as error:
                    if attempt == max_retries:
                        raise DocumentImportError(
                            f"Embedding batch {batch_number}/{batch_count} failed "
                            f"after {max_retries + 1} attempts: {error}"
                        ) from error

                    progress_function(
                        f"Embedding batch {batch_number}/{batch_count} failed: "
                        f"{error} Retrying ({attempt + 1}/{max_retries})..."
                    )
                    if retry_delay:
                        time.sleep(retry_delay)

            store.upsert_batch(
                [records["ids"][index] for index in batch_indexes],
                batch_chunks,
                batch_embeddings,
                [records["metadatas"][index] for index in batch_indexes],
            )
            stored_count = already_stored + min(
                batch_number * batch_size,
                len(missing_indexes),
            )
            progress_function(f"Embedded and stored {stored_count} / {len(chunks)} chunks.")

        current_ids = store.get_source_ids(records["source_id"])
        expected_ids = set(records["ids"])

        if not expected_ids.issubset(current_ids):
            missing_count = len(expected_ids - current_ids)
            raise DocumentImportError(
                f"Import stopped with {missing_count} chunk(s) still missing. "
                "Run the import again to resume."
            )

        store.finalize_document(records["source_id"], records["ids"])
        return len(records["ids"])
    finally:
        if owns_vector_store:
            store.client.close()
