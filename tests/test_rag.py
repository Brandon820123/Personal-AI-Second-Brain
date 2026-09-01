"""Tests for grounded local knowledge-base question answering."""

import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from app.rag import (
    EMPTY_KNOWLEDGE_BASE_MESSAGE,
    INSUFFICIENT_CONTEXT_MESSAGE,
    answer_question,
    build_context,
    build_rag_system_messages,
    format_source_label,
    format_sources,
    select_relevant_results,
)
from app.personas import get_persona


def make_result(text, filename, chunk_index, distance, source_path=None):
    """Create one result shaped like a VectorStore search result."""
    return {
        "text": text,
        "metadata": {
            "source_filename": filename,
            "source_path": source_path or filename,
            "chunk_index": chunk_index,
        },
        "distance": distance,
    }


class FakeVectorStore:
    """Provide only the VectorStore behavior needed by RAG tests."""

    def __init__(self, results=None, stored_count=1):
        self.results = results or []
        self.stored_count = stored_count
        self.requested_top_k = None

    def count(self):
        return self.stored_count

    def search(self, query_embedding, top_k):
        self.requested_top_k = top_k
        return self.results[:top_k]


class RagFormattingTests(unittest.TestCase):
    """Check model context, source labels, and relevance filtering."""

    def test_builds_labelled_context_in_retrieval_order(self):
        results = [
            make_result("First fact.", "notes.txt", 2, 0.2),
            make_result("Second fact.", "guide.md", 7, 0.3),
        ]

        context = build_context(results)

        self.assertEqual(
            context,
            "[1] notes.txt - chunk 2\nFirst fact.\n\n"
            "[2] guide.md - chunk 7\nSecond fact.",
        )

    def test_formats_sources_without_duplicate_source_chunks(self):
        duplicate = make_result("Repeated fact.", "notes.txt", 2, 0.25)
        results = [
            make_result("First fact.", "notes.txt", 2, 0.2),
            duplicate,
            make_result("Second fact.", "guide.md", 7, 0.3),
        ]

        sources = format_sources(results)

        self.assertEqual(
            sources,
            "Sources:\n[1] notes.txt - chunk 2\n[2] guide.md - chunk 7",
        )

    def test_formats_pdf_source_with_page_number(self):
        metadata = {
            "source_filename": "Physics.pdf",
            "page_number": 12,
            "chunk_index": 3,
        }

        self.assertEqual(
            format_source_label(metadata),
            "Physics.pdf - page 12 - chunk 3",
        )

    def test_filters_chunks_below_the_relevance_threshold(self):
        results = [
            make_result("Relevant.", "notes.txt", 1, 0.4),
            make_result("Unrelated.", "notes.txt", 2, 0.7),
        ]

        relevant_results = select_relevant_results(results, minimum_score=0.45)

        self.assertEqual([result["text"] for result in relevant_results], ["Relevant."])
        self.assertAlmostEqual(relevant_results[0]["similarity"], 0.6)

    def test_grounding_rules_remain_separate_and_stronger_than_persona(self):
        messages = build_rag_system_messages(get_persona("fairy"))

        self.assertEqual(
            [message["role"] for message in messages],
            ["system", "system", "system"],
        )
        self.assertIn("Lower-priority response style", messages[0]["content"])
        self.assertIn("身份是 Fairy", messages[0]["content"])
        self.assertIn("原创的智能型助手风格", messages[0]["content"])
        self.assertIn("幽默绝不能干扰 RAG grounding", messages[0]["content"])
        self.assertIn("响应语言使用自动模式", messages[1]["content"])
        self.assertIn("明确指定始终具有最高优先级", messages[1]["content"])
        self.assertIn("must not alter retrieved text", messages[1]["content"])
        self.assertIn("higher priority than persona style", messages[2]["content"])
        self.assertIn("only the retrieved context", messages[2]["content"])
        self.assertIn("does not contain enough information", messages[2]["content"])
        self.assertIn("source citations", messages[2]["content"])
        self.assertIn("never invent a label or source", messages[2]["content"])
        self.assertNotIn("响应语言使用自动模式", messages[2]["content"])

    def test_rag_language_uses_question_not_retrieved_context(self):
        messages = build_rag_system_messages(
            get_persona("delamain"),
            user_message="Why does temperature rise?",
        )

        self.assertIn("本次回答使用英文", messages[1]["content"])
        self.assertIn("only the retrieved context", messages[2]["content"])


class RagSafeguardTests(unittest.TestCase):
    """Check that missing or weak knowledge never reaches the chat model."""

    @patch("app.rag.generate_query_embedding")
    @patch("app.rag.stream_grounded_answer")
    def test_empty_knowledge_base_stops_before_embedding(
        self,
        mock_stream_answer,
        mock_generate_embedding,
    ):
        store = FakeVectorStore(stored_count=0)
        output = io.StringIO()

        with redirect_stdout(output):
            answered = answer_question("Any question?", vector_store=store)

        self.assertFalse(answered)
        self.assertIn(EMPTY_KNOWLEDGE_BASE_MESSAGE, output.getvalue())
        mock_generate_embedding.assert_not_called()
        mock_stream_answer.assert_not_called()

    @patch("app.rag.generate_query_embedding", return_value=[1.0, 0.0])
    @patch("app.rag.stream_grounded_answer")
    def test_low_relevance_stops_before_chat_generation(
        self,
        mock_stream_answer,
        mock_generate_embedding,
    ):
        store = FakeVectorStore(
            results=[make_result("Unrelated text.", "notes.txt", 4, 0.7)]
        )
        output = io.StringIO()

        with redirect_stdout(output):
            answered = answer_question(
                "An unrelated question?",
                vector_store=store,
                minimum_score=0.45,
            )

        self.assertFalse(answered)
        self.assertIn(INSUFFICIENT_CONTEXT_MESSAGE, output.getvalue())
        self.assertEqual(store.requested_top_k, 4)
        mock_generate_embedding.assert_called_once_with("An unrelated question?")
        mock_stream_answer.assert_not_called()


if __name__ == "__main__":
    unittest.main()
