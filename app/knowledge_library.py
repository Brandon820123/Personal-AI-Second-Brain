"""Inspect and manage documents in the local Chroma knowledge library."""

from pathlib import Path

try:
    from .chunker import chunk_document_pages
    from .document_importer import import_document_chunks
    from .document_loader import load_document_pages
    from .embeddings import generate_embeddings
    from .vector_store import VectorStore
except ImportError:
    from chunker import chunk_document_pages
    from document_importer import import_document_chunks
    from document_loader import load_document_pages
    from embeddings import generate_embeddings
    from vector_store import VectorStore


def list_documents(vector_store=None):
    """Return one summary for each unique imported source document."""
    owns_vector_store = vector_store is None
    store = vector_store or VectorStore()

    try:
        documents_by_source = {}

        for metadata in store.get_all_metadata():
            source_id = metadata.get("source_id")

            if not source_id:
                continue

            if source_id not in documents_by_source:
                documents_by_source[source_id] = {
                    "source_id": source_id,
                    "filename": metadata.get("source_filename", "Unknown document"),
                    "source_path": metadata.get("source_path", ""),
                    "file_type": metadata.get("file_type", ""),
                    "chunk_count": 0,
                    "page_numbers": set(),
                }

            document = documents_by_source[source_id]
            document["chunk_count"] += 1

            if metadata.get("page_number") is not None:
                document["page_numbers"].add(metadata["page_number"])

        documents = []

        for document in documents_by_source.values():
            page_count = len(document.pop("page_numbers"))
            document["page_count"] = page_count or None
            documents.append(document)

        return sorted(
            documents,
            key=lambda document: (
                document["filename"].casefold(),
                document["source_path"].casefold(),
            ),
        )
    finally:
        if owns_vector_store:
            store.client.close()


def delete_document(source_id, vector_store=None):
    """Delete all chunks for one document and return the number removed."""
    owns_vector_store = vector_store is None
    store = vector_store or VectorStore()

    try:
        return store.delete_source(source_id)
    finally:
        if owns_vector_store:
            store.client.close()


def reindex_document(
    document,
    vector_store=None,
    embedding_function=generate_embeddings,
    progress_function=print,
):
    """Reload, re-chunk, and replace one stored document locally."""
    source_path = Path(document["source_path"]).expanduser()

    if not source_path.exists() or not source_path.is_file():
        raise FileNotFoundError(
            f"Cannot re-index '{document['filename']}' because its source file "
            f"no longer exists: {source_path}"
        )

    document_pages = load_document_pages(source_path)
    chunks, chunk_metadata = chunk_document_pages(document_pages)

    if not chunks:
        raise ValueError("Cannot re-index a document that contains no text.")

    owns_vector_store = vector_store is None
    store = vector_store or VectorStore()

    try:
        previous_chunk_count = len(store.get_source_ids(document["source_id"]))
        stored_count = import_document_chunks(
            source_path,
            chunks,
            chunk_metadata,
            vector_store=store,
            embedding_function=embedding_function,
            progress_function=progress_function,
            force_reembed=True,
        )
        return {
            "previous_chunk_count": previous_chunk_count,
            "stored_count": stored_count,
        }
    finally:
        if owns_vector_store:
            store.client.close()
