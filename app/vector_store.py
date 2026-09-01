"""Store and search document chunks in a local persistent Chroma database."""

import hashlib
import json
from pathlib import Path

import chromadb
from chromadb.config import Settings


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATABASE_PATH = PROJECT_ROOT / "data" / "chroma"
COLLECTION_NAME = "personal_ai_knowledge_base"


class VectorStoreError(RuntimeError):
    """Report a clear problem while using the local knowledge database."""


class VectorStore:
    """A small wrapper around one local persistent Chroma collection."""

    def __init__(
        self,
        database_path=DEFAULT_DATABASE_PATH,
        collection_name=COLLECTION_NAME,
    ):
        try:
            self.database_path = Path(database_path).expanduser().resolve()
            self.database_path.mkdir(parents=True, exist_ok=True)

            settings = Settings(anonymized_telemetry=False)
            self.client = chromadb.PersistentClient(
                path=str(self.database_path),
                settings=settings,
            )
            self.collection = self.client.get_or_create_collection(
                name=collection_name,
                embedding_function=None,
                configuration={"hnsw": {"space": "cosine"}},
                metadata={
                    "description": "Private Personal AI local knowledge base",
                },
            )
        except Exception as error:
            raise VectorStoreError(
                f"Could not initialize the local knowledge base: {error}"
            ) from error

    def count(self):
        """Return the number of stored chunks."""
        try:
            return self.collection.count()
        except Exception as error:
            raise VectorStoreError(
                f"Could not read the local knowledge base: {error}"
            ) from error

    def store_document(
        self,
        source_path,
        chunks,
        embeddings,
        chunk_metadata=None,
    ):
        """Add or update all chunks from one document and return their count."""
        records = self.prepare_document_records(
            source_path,
            chunks,
            chunk_metadata=chunk_metadata,
        )

        if len(chunks) != len(embeddings):
            raise ValueError("Each document chunk must have one embedding.")

        self.upsert_batch(
            records["ids"],
            chunks,
            embeddings,
            records["metadatas"],
        )
        self.finalize_document(records["source_id"], records["ids"])
        return len(records["ids"])

    def prepare_document_records(
        self,
        source_path,
        chunks,
        chunk_metadata=None,
    ):
        """Build stable IDs and metadata before embedding document chunks."""
        if not chunks:
            raise ValueError("Cannot store a document without chunks.")

        if chunk_metadata is None:
            chunk_metadata = [{} for chunk in chunks]

        if len(chunks) != len(chunk_metadata):
            raise ValueError("Each document chunk must have one metadata record.")

        path = Path(source_path).expanduser().resolve()
        normalized_path = str(path).casefold()
        source_id = hashlib.sha256(normalized_path.encode("utf-8")).hexdigest()
        hash_input = json.dumps(
            {"chunks": chunks, "metadata": chunk_metadata},
            ensure_ascii=False,
            sort_keys=True,
        )
        content_hash = hashlib.sha256(hash_input.encode("utf-8")).hexdigest()
        chunk_ids = [
            f"{source_id}-{content_hash}-{chunk_index:06d}"
            for chunk_index in range(len(chunks))
        ]
        metadatas = []

        for chunk_index, extra_metadata in enumerate(chunk_metadata):
            metadata = {
                "source_filename": path.name,
                "source_path": str(path),
                "source_id": source_id,
                "chunk_index": chunk_index,
                "file_type": path.suffix.lower(),
                "content_hash": content_hash,
            }

            if extra_metadata.get("page_number") is not None:
                metadata["page_number"] = extra_metadata["page_number"]

            metadatas.append(metadata)

        return {
            "source_id": source_id,
            "content_hash": content_hash,
            "ids": chunk_ids,
            "metadatas": metadatas,
        }

    def get_source_ids(self, source_id):
        """Return all stored chunk IDs for one source document."""
        try:
            existing_records = self.collection.get(
                where={"source_id": source_id},
                include=[],
            )
            return set(existing_records["ids"])
        except Exception as error:
            raise VectorStoreError(
                f"Could not inspect stored document chunks: {error}"
            ) from error

    def get_all_metadata(self):
        """Return metadata for every chunk in the local collection."""
        try:
            records = self.collection.get(include=["metadatas"])
            return records["metadatas"] or []
        except Exception as error:
            raise VectorStoreError(
                f"Could not list knowledge-base documents: {error}"
            ) from error

    def delete_source(self, source_id):
        """Delete only the chunks belonging to one source document."""
        if not source_id:
            raise ValueError("A document source ID is required for deletion.")

        chunk_ids = self.get_source_ids(source_id)

        if not chunk_ids:
            return 0

        try:
            self.collection.delete(ids=list(chunk_ids))
        except Exception as error:
            raise VectorStoreError(
                f"Could not delete the selected document: {error}"
            ) from error

        return len(chunk_ids)

    def upsert_batch(self, chunk_ids, chunks, embeddings, metadatas):
        """Store one completed embedding batch in the local collection."""
        record_count = len(chunk_ids)

        if not record_count:
            return 0

        if not (
            record_count == len(chunks) == len(embeddings) == len(metadatas)
        ):
            raise ValueError("Batch IDs, chunks, embeddings, and metadata must match.")

        try:
            self.collection.upsert(
                ids=chunk_ids,
                documents=chunks,
                embeddings=embeddings,
                metadatas=metadatas,
            )

        except Exception as error:
            raise VectorStoreError(
                f"Could not store an embedding batch: {error}"
            ) from error

        return record_count

    def finalize_document(self, source_id, current_ids):
        """Remove older chunks only after the current import is complete."""
        try:
            existing_ids = self.get_source_ids(source_id)
            stale_ids = list(existing_ids - set(current_ids))

            if stale_ids:
                self.collection.delete(ids=stale_ids)
        except Exception as error:
            raise VectorStoreError(
                f"Could not finalize the imported document: {error}"
            ) from error

        return len(stale_ids)

    def search(self, query_embedding, top_k=3):
        """Return the nearest stored chunks with metadata and cosine distance."""
        if not query_embedding:
            raise ValueError("Query embedding must not be empty.")

        if top_k <= 0:
            raise ValueError("Number of results must be greater than zero.")

        try:
            stored_count = self.count()
            if stored_count == 0:
                return []

            query_results = self.collection.query(
                query_embeddings=[query_embedding],
                n_results=min(top_k, stored_count),
                include=["documents", "metadatas", "distances"],
            )
        except Exception as error:
            raise VectorStoreError(
                f"Could not search the local knowledge base: {error}"
            ) from error

        documents = query_results["documents"][0]
        metadatas = query_results["metadatas"][0]
        distances = query_results["distances"][0]

        return [
            {
                "text": document,
                "metadata": metadata,
                "distance": float(distance),
            }
            for document, metadata, distance in zip(
                documents,
                metadatas,
                distances,
            )
        ]
