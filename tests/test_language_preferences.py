"""Tests for the persisted global response-language preference."""

import json
import tempfile
import unittest
from pathlib import Path

import app.language_preferences as language_module
from app.language_preferences import (
    DEFAULT_LANGUAGE_ID,
    build_language_instruction,
    get_language_preference,
    infer_response_language,
    set_language_preference,
)


class LanguagePreferenceTests(unittest.TestCase):
    """Check automatic language behavior, persistence, and prompt rules."""

    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.settings_path = (
            Path(self.temporary_directory.name) / "config" / "language.json"
        )

    def tearDown(self):
        language_module._active_language = None
        language_module._active_settings_path = None
        self.temporary_directory.cleanup()

    def test_defaults_to_auto_with_chinese_fallback_and_persists_it(self):
        language = get_language_preference(self.settings_path, reload=True)
        saved_settings = json.loads(self.settings_path.read_text(encoding="utf-8"))

        self.assertEqual(language["id"], DEFAULT_LANGUAGE_ID)
        self.assertEqual(language["display_name"], "自动（简体中文回退）")
        self.assertEqual(saved_settings, {"response_language": "auto"})

    def test_restores_persisted_language_after_restart(self):
        set_language_preference("auto", self.settings_path)
        language_module._active_language = None
        language_module._active_settings_path = None

        restored_language = get_language_preference(self.settings_path)

        self.assertEqual(restored_language["id"], "auto")

    def test_language_instruction_allows_explicit_user_override(self):
        instruction = build_language_instruction(
            get_language_preference(self.settings_path)
        )

        self.assertIn("响应语言使用自动模式", instruction)
        self.assertIn("明确指定始终具有最高优先级", instruction)

    def test_language_instruction_matches_current_user_language(self):
        instruction = build_language_instruction(
            get_language_preference(self.settings_path)
        )

        self.assertIn("中文输入使用简体中文", instruction)
        self.assertIn("英文输入使用英文", instruction)
        self.assertIn("其他能够清楚识别的语言", instruction)
        self.assertIn("回退到简体中文", instruction)
        self.assertIn("不要根据检索到的上下文", instruction)
        self.assertIn("不得改变 Persona 名称或身份", instruction)

    def test_local_inference_handles_examples_and_explicit_overrides(self):
        self.assertEqual(infer_response_language("hello"), "en")
        self.assertEqual(infer_response_language("你好"), "zh-CN")
        self.assertEqual(infer_response_language("用英文解释 RAG"), "en")
        self.assertEqual(infer_response_language("Explain RAG in Chinese"), "zh-CN")
        self.assertEqual(infer_response_language("12345"), "zh-CN")

    def test_request_specific_instruction_overrides_chinese_prompt_examples(self):
        language = get_language_preference(self.settings_path)
        english_instruction = build_language_instruction(language, "hello")
        chinese_instruction = build_language_instruction(language, "你好")

        self.assertIn("本次回答使用英文", english_instruction)
        self.assertIn("系统说明使用中文", english_instruction)
        self.assertIn("本次回答使用简体中文", chinese_instruction)

    def test_language_instruction_preserves_natural_technical_terms(self):
        instruction = build_language_instruction(
            get_language_preference(self.settings_path)
        )

        for term in ("RAG", "Embedding", "ChromaDB", "API", "PDF", "Chunk", "LLM"):
            self.assertIn(term, instruction)

    def test_invalid_saved_language_falls_back_and_repairs_setting(self):
        self.settings_path.parent.mkdir(parents=True)
        self.settings_path.write_text(
            '{"response_language": "not-supported"}\n',
            encoding="utf-8",
        )

        language = get_language_preference(self.settings_path, reload=True)
        saved_settings = json.loads(self.settings_path.read_text(encoding="utf-8"))

        self.assertEqual(language["id"], "auto")
        self.assertEqual(saved_settings, {"response_language": "auto"})


if __name__ == "__main__":
    unittest.main()
