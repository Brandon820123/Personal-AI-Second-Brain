"""Tests for safely batching requests to the local Ollama embedding API."""

import unittest
from unittest.mock import Mock, patch

from app.embeddings import generate_embeddings


class EmbeddingTests(unittest.TestCase):
    """Check that large input lists never become one oversized request."""

    @patch("app.embeddings.requests.post")
    def test_splits_large_input_into_local_ollama_batches(self, mock_post):
        request_sizes = []

        def fake_post(url, json, timeout):
            request_sizes.append(len(json["input"]))
            response = Mock()
            response.raise_for_status.return_value = None
            response.json.return_value = {
                "embeddings": [[1.0, 0.0] for _ in json["input"]]
            }
            return response

        mock_post.side_effect = fake_post

        embeddings = generate_embeddings(
            [f"Chunk {index}" for index in range(70)],
            batch_size=32,
        )

        self.assertEqual(request_sizes, [32, 32, 6])
        self.assertEqual(len(embeddings), 70)


if __name__ == "__main__":
    unittest.main()
