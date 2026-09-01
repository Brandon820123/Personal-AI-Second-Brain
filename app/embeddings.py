"""Generate text embeddings with the local Ollama server."""

import requests


OLLAMA_EMBED_URL = "http://127.0.0.1:11434/api/embed"
EMBEDDING_MODEL = "qwen3-embedding:0.6b"
DEFAULT_EMBEDDING_BATCH_SIZE = 32


class EmbeddingError(RuntimeError):
    """Report a clear problem while generating local embeddings."""


def _request_embedding_batch(texts):
    """Send one already-sized text batch to local Ollama."""
    try:
        response = requests.post(
            OLLAMA_EMBED_URL,
            json={"model": EMBEDDING_MODEL, "input": texts},
            timeout=(5, 300),
        )
        response.raise_for_status()
    except requests.exceptions.ConnectionError as error:
        raise EmbeddingError(
            "Could not connect to local Ollama. Please start Ollama and try again."
        ) from error
    except requests.exceptions.Timeout as error:
        raise EmbeddingError(
            "Local Ollama took too long to generate embeddings."
        ) from error
    except requests.exceptions.HTTPError as error:
        try:
            ollama_message = response.json().get("error", str(error))
        except ValueError:
            ollama_message = str(error)

        raise EmbeddingError(f"Local Ollama embedding error: {ollama_message}") from error
    except requests.exceptions.RequestException as error:
        raise EmbeddingError(
            f"Could not request embeddings from local Ollama: {error}"
        ) from error

    try:
        embeddings = response.json()["embeddings"]
    except (ValueError, KeyError, TypeError) as error:
        raise EmbeddingError(
            "Local Ollama returned an unexpected embedding response."
        ) from error

    if len(embeddings) != len(texts) or any(not embedding for embedding in embeddings):
        raise EmbeddingError(
            "Local Ollama did not return one embedding for each input text."
        )

    return embeddings


def generate_embeddings(texts, batch_size=DEFAULT_EMBEDDING_BATCH_SIZE):
    """Return local embeddings without sending an oversized Ollama request."""
    if not texts:
        return []

    if batch_size <= 0:
        raise ValueError("Embedding batch size must be greater than zero.")

    if any(not isinstance(text, str) or not text.strip() for text in texts):
        raise ValueError("Each text to embed must be a non-empty string.")

    embeddings = []

    for start in range(0, len(texts), batch_size):
        embeddings.extend(_request_embedding_batch(texts[start : start + batch_size]))

    return embeddings


def generate_query_embedding(query):
    """Return a local embedding for one search query."""
    if not isinstance(query, str) or not query.strip():
        raise ValueError("Search query must be a non-empty string.")

    return generate_embeddings([query])[0]
