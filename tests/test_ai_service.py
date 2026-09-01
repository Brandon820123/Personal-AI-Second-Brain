"""Tests for reusable desktop-facing local AI services."""

import unittest
from unittest.mock import Mock, patch

from app.ai_service import (
    AIServiceError,
    stream_knowledge_chat,
    stream_normal_chat,
)
from app.personas import get_persona


class AIServiceTests(unittest.TestCase):
    """Check streaming callbacks and grounded desktop RAG behavior."""

    @patch("app.ai_service._stream_ollama")
    def test_normal_chat_streams_with_persona_and_language_messages(self, mock_stream):
        tokens = []

        def fake_stream(messages, callback):
            callback("你")
            callback("好")

        mock_stream.side_effect = fake_stream
        states = []
        stream_normal_chat(
            "解释 RAG",
            tokens.append,
            persona=get_persona("delamain"),
            on_state=states.append,
        )
        messages = mock_stream.call_args.args[0]

        self.assertEqual(tokens, ["你", "好"])
        self.assertEqual(states, ["thinking"])
        self.assertEqual([message["role"] for message in messages], ["system", "system", "user"])
        self.assertIn("冷静", messages[0]["content"])
        self.assertIn("身份是 Delamain", messages[0]["content"])
        self.assertIn("本地运行的 Qwen3.5 4B", messages[0]["content"])
        self.assertIn("响应语言使用自动模式", messages[1]["content"])
        self.assertIn("本次回答使用简体中文", messages[1]["content"])

    @patch("app.ai_service.generate_query_embedding", return_value=[1.0, 0.0])
    def test_empty_knowledge_base_returns_ui_safe_error(self, mock_embedding):
        store = Mock()
        store.count.return_value = 0
        store.client = Mock()

        with patch("app.ai_service.VectorStore", return_value=store):
            with self.assertRaisesRegex(AIServiceError, "知识库为空"):
                stream_knowledge_chat("问题", lambda token: None)

        mock_embedding.assert_not_called()
        store.client.close.assert_called_once()

    @patch("app.ai_service._stream_ollama")
    @patch("app.ai_service.generate_query_embedding", return_value=[1.0, 0.0])
    def test_knowledge_chat_returns_pdf_sources(self, mock_embedding, mock_stream):
        store = Mock()
        store.count.return_value = 1
        store.search.return_value = [
            {
                "text": "温室气体吸收地表辐射。",
                "metadata": {
                    "source_filename": "Textbook.pdf",
                    "source_path": "Textbook.pdf",
                    "page_number": 191,
                    "chunk_index": 526,
                },
                "distance": 0.2,
            }
        ]
        store.client = Mock()

        def fake_stream(messages, callback):
            callback("温室气体会吸收辐射。")

        mock_stream.side_effect = fake_stream
        tokens = []
        states = []

        with patch("app.ai_service.VectorStore", return_value=store):
            sources = stream_knowledge_chat(
                "为什么会升温？",
                tokens.append,
                on_state=states.append,
            )

        self.assertEqual(tokens, ["温室气体会吸收辐射。"])
        self.assertEqual(states, ["searching", "thinking"])
        self.assertIn("Textbook.pdf - page 191 - chunk 526", sources)
        messages = mock_stream.call_args.args[0]
        self.assertIn("only the retrieved context", messages[-2]["content"])
        self.assertEqual(messages[-1]["role"], "user")


if __name__ == "__main__":
    unittest.main()
