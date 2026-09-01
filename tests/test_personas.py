"""Tests for local persona definitions and persistence."""

import json
import tempfile
import unittest
from pathlib import Path

import app.personas as personas_module
from app.personas import (
    DEFAULT_PERSONA_ID,
    LOCAL_MODEL_DESCRIPTION,
    build_persona_instruction,
    get_active_persona,
    get_persona,
    list_personas,
    switch_persona,
)


class PersonaTests(unittest.TestCase):
    """Check persona loading, validation, switching, and local persistence."""

    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.settings_path = (
            Path(self.temporary_directory.name) / "config" / "persona.json"
        )

    def tearDown(self):
        personas_module._active_persona = None
        personas_module._active_settings_path = None
        self.temporary_directory.cleanup()

    def test_loads_all_personas_with_required_configuration(self):
        required_fields = {
            "id",
            "display_name",
            "system_prompt",
            "style_description",
            "preferred_response_length",
            "greeting",
            "ui_theme_id",
            "voice_id",
        }

        personas = list_personas()

        self.assertEqual(
            [persona["id"] for persona in personas],
            ["delamain", "fairy", "neutral"],
        )
        self.assertTrue(
            all(required_fields.issubset(persona) for persona in personas)
        )
        self.assertIn("冷静、专业、简洁", personas[0]["style_description"])
        self.assertIn("聪明、可靠、专业", personas[1]["style_description"])
        self.assertIn("直接、简洁、客观", personas[2]["style_description"])
        self.assertTrue(all("您" in persona["greeting"] or "你好" in persona["greeting"]
                            for persona in personas))

    def test_defaults_to_delamain_when_no_setting_exists(self):
        active_persona = get_active_persona(self.settings_path, reload=True)

        self.assertEqual(active_persona["id"], DEFAULT_PERSONA_ID)
        self.assertFalse(self.settings_path.exists())

    def test_switches_persona_and_persists_after_restart(self):
        selected_persona = switch_persona("fairy", self.settings_path)
        saved_settings = json.loads(self.settings_path.read_text(encoding="utf-8"))

        self.assertEqual(selected_persona["id"], "fairy")
        self.assertEqual(saved_settings, {"active_persona": "fairy"})

        personas_module._active_persona = None
        personas_module._active_settings_path = None
        restored_persona = get_active_persona(self.settings_path)

        self.assertEqual(restored_persona["id"], "fairy")

    def test_rejects_invalid_persona_without_writing_a_setting(self):
        with self.assertRaisesRegex(ValueError, "Unknown persona"):
            switch_persona("unknown", self.settings_path)

        self.assertFalse(self.settings_path.exists())

    def test_returns_copies_of_persona_configuration(self):
        persona = get_persona("neutral")
        persona["display_name"] = "Changed"

        self.assertEqual(get_persona("neutral")["display_name"], "Neutral")

    def test_fairy_prioritizes_accuracy_and_uses_humor_selectively(self):
        fairy = get_persona("fairy")
        prompt = fairy["system_prompt"]

        self.assertIn("认真对待任务，但不总把自己太当回事", prompt)
        self.assertIn("始终先清楚、准确地回答用户的实际问题", prompt)
        self.assertIn("不要在每次回答中强行制造笑点", prompt)
        self.assertIn("严肃、敏感、学术", prompt)
        self.assertIn("RAG grounding", prompt)
        self.assertIn("引用、来源准确性或错误报告", prompt)
        self.assertIn("自然、现代、简洁", prompt)
        self.assertIn("避免幼稚表达", prompt)
        self.assertEqual(fairy["ui_theme_id"], "spark")

    def test_each_persona_identity_overrides_model_self_identification(self):
        for persona_id in ("fairy", "delamain", "neutral"):
            persona = get_persona(persona_id)
            instruction = build_persona_instruction(persona)

            self.assertIn(
                f"身份是 {persona['display_name']}",
                instruction,
            )
            self.assertIn("Persona 身份优先于预训练模型的默认自我识别", instruction)
            self.assertIn("你是谁", instruction)
            self.assertIn("你叫什么", instruction)
            self.assertIn("who are you", instruction)
            self.assertIn("what are you", instruction)
            self.assertIn("直接回答", instruction)
            self.assertIn("身份回答不得提及 Qwen", instruction)
            self.assertIn("必须清楚区分 Persona 与推理引擎", instruction)

    def test_backend_identity_is_disclosed_only_when_explicitly_requested(self):
        instruction = build_persona_instruction(get_persona("fairy"))

        self.assertIn("只有当用户明确询问底层模型", instruction)
        self.assertIn(LOCAL_MODEL_DESCRIPTION, instruction)
        self.assertIn("绝不能声称 Persona 本身就是 Qwen3.5", instruction)
        self.assertIn("Fairy 是 Persona 专名", instruction)
        self.assertLess(
            instruction.index("Persona style:"),
            instruction.index("关键身份规则"),
        )


if __name__ == "__main__":
    unittest.main()
