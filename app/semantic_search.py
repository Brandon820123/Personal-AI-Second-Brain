"""Rank document chunks by semantic similarity to a search query."""

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
