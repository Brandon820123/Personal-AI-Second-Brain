"""Tests for cosine similarity and semantic result ranking."""

import unittest
from unittest.mock import patch

from app.semantic_search import cosine_similarity, semantic_search


class CosineSimilarityTests(unittest.TestCase):
    """Check basic cosine similarity calculations."""

    def test_identical_vectors_have_full_similarity(self):
        self.assertAlmostEqual(cosine_similarity([1, 2], [1, 2]), 1.0)

    def test_perpendicular_vectors_have_zero_similarity(self):
        self.assertAlmostEqual(cosine_similarity([1, 0], [0, 1]), 0.0)


class SemanticSearchTests(unittest.TestCase):
    """Check that chunks are returned from most to least relevant."""

    @patch("app.semantic_search.generate_query_embedding", return_value=[1, 0])
    def test_ranks_and_limits_results(self, _mock_query_embedding):
        chunks = ["unrelated", "most relevant", "partly relevant", "opposite"]
        embeddings = [[0, 1], [1, 0], [1, 1], [-1, 0]]

        results = semantic_search("test query", chunks, embeddings, top_k=3)

        self.assertEqual(
            [chunk for chunk, _score in results],
            ["most relevant", "partly relevant", "unrelated"],
        )
        self.assertGreater(results[0][1], results[1][1])
        self.assertGreater(results[1][1], results[2][1])


if __name__ == "__main__":
    unittest.main()
