"""Rank document chunks by semantic similarity to a search query."""

import logging
import os
from pathlib import Path
import re

import numpy as np

try:
    from .embeddings import generate_query_embedding
except ImportError:
    from embeddings import generate_query_embedding


def cosine_similarity(first_embedding, second_embedding):
    """Return the cosine similarity between two embedding vectors."""
    first_vector = np.asarray(first_embedding, dtype=float)
    second_vector = np.asarray(second_embedding, dtype=float)

    if first_vector.ndim != 1 or second_vector.ndim != 1:
        raise ValueError("Cosine similarity expects two one-dimensional vectors.")

    if first_vector.shape != second_vector.shape:
        raise ValueError("Embedding vectors must have the same length.")

    denominator = np.linalg.norm(first_vector) * np.linalg.norm(second_vector)

    if denominator == 0:
        return 0.0

    return float(np.dot(first_vector, second_vector) / denominator)


def semantic_search(query, chunks, chunk_embeddings, top_k=3):
    """Return the most relevant chunks as ``(chunk, score)`` pairs."""
    if not query or not query.strip():
        raise ValueError("Search query must not be empty.")

    if len(chunks) != len(chunk_embeddings):
        raise ValueError("Each document chunk must have one embedding.")

    if top_k <= 0:
        raise ValueError("Number of results must be greater than zero.")

    if not chunks:
        return []

    query_embedding = generate_query_embedding(query)
    scored_chunks = [
        (chunk, cosine_similarity(query_embedding, embedding))
        for chunk, embedding in zip(chunks, chunk_embeddings)
    ]
    scored_chunks.sort(key=lambda result: result[1], reverse=True)

    return scored_chunks[:top_k]


def search_knowledge(query, file_type=None, top_k=20):
    """Query the existing local index without generating an AI answer."""
    if __package__:
        from .vector_store import VectorStore
        from .file_scanner import FileScannerError, load_scanner_config
    else:
        from vector_store import VectorStore
        from file_scanner import FileScannerError, load_scanner_config
    if not query or not query.strip():
        raise ValueError("Search query must not be empty.")
    if top_k <= 0:
        raise ValueError("Number of results must be greater than zero.")
    if file_type not in (None, ".pdf", ".docx", ".txt", ".md"):
        raise ValueError("Unsupported file type.")
    store = VectorStore()
    stored_count = store.count()
    if not stored_count:
        return []
    try:
        roots = [Path(root).expanduser().resolve()
                 for root in load_scanner_config()["watch_folders"]]
    except (FileScannerError, OSError, ValueError):
        logging.getLogger(__name__).warning("Search is using project-relative paths: source settings unavailable.")
        roots = []
    project_root = Path(__file__).resolve().parent.parent
    embedding = generate_query_embedding(query)
    candidate_count = min(stored_count, max(200, top_k))
    while True:
        candidates = store.search(embedding, top_k=candidate_count, file_type=file_type)
        unique = {}
        for result in candidates:
            if not any(character.isalpha() for character in result["text"]):
                continue
            metadata = result["metadata"]
            path = Path(metadata.get("source_path") or metadata.get("source_filename", ""))
            key = (metadata.get("content_hash") or os.path.normcase(str(path)),
                   metadata.get("page_number"), " ".join(result["text"].split()))
            # Prefer an existing authorized source over a manually imported copy.
            priority = (path.is_file(), any(path.is_relative_to(root) for root in roots))
            previous = unique.get(key)
            if previous is None or priority > previous[0]:
                unique[key] = (priority, result)
        results = sorted((item[1] for item in unique.values()), key=lambda item: item["distance"])
        if len(results) >= top_k or len(candidates) < candidate_count or candidate_count == stored_count:
            break
        candidate_count = min(stored_count, candidate_count * 2)
    results = results[:top_k]
    for result in results:
        metadata = result["metadata"]
        path = Path(metadata.get("source_path", metadata.get("source_filename", "")))
        try:
            result["relative_path"] = Path(os.path.relpath(path, project_root)).as_posix()
        except ValueError:
            result["relative_path"] = path.as_posix()
        for root in sorted(roots, key=lambda item: len(item.parts), reverse=True):
            try:
                result["relative_path"] = path.relative_to(root).as_posix()
                break
            except ValueError:
                continue
        result["score"] = 1.0 - result["distance"]
    return results


def load_search_context(result):
    """Read original text on a worker, retaining the indexed hit if unavailable."""
    if __package__:
        from .document_loader import load_document_pages
    else:
        from document_loader import load_document_pages
    metadata = result["metadata"]
    hit = result["text"]
    try:
        page_number = metadata.get("page_number")
        pages = load_document_pages(metadata["source_path"], page_number=page_number)
        text = "\n\n".join(page["text"] for page in pages
                           if page_number is None or page["page_number"] == page_number)
        if not text:
            raise ValueError("Indexed page no longer exists.")
        # Keep rendering bounded even for very large text documents.
        position = text.find(hit)
        if position < 0:
            # Chunk overlap may normalize line breaks to spaces.
            match = re.search(r"\s+".join(re.escape(word) for word in hit.split()), text)
            if match is None:
                return "原文与索引片段不一致，请同步知识库。\n\n索引片段：\n" + hit
            position = match.start()
        start = max(0, position - 2500)
        end = min(len(text), position + len(hit) + 2500)
        return ("…\n" if start else "") + text[start:end] + ("\n…" if end < len(text) else "")
    except (OSError, ValueError, KeyError) as error:
        return f"无法读取当前原文：{error}\n\n保留的索引片段：\n{hit}"
