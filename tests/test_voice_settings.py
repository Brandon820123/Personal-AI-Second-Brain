"""Tests for local, default-OFF voice configuration persistence."""

import json
import tempfile
import unittest
from pathlib import Path

from app.voice.settings import (
    DEFAULT_PERSONA_VOICES,
    get_persona_voice_profile,
    get_voice_settings,
    save_voice_settings,
)


class VoiceSettingsTests(unittest.TestCase):
    """Keep optional voice state local, complete, and safely disabled by default."""

    def test_defaults_to_off_and_persists_without_initializing_audio(self):
        with tempfile.TemporaryDirectory() as temp_directory:
            settings_path = Path(temp_directory) / "voice.json"

            settings = get_voice_settings(settings_path)

            self.assertFalse(settings["enabled"])
            self.assertTrue(settings["input_enabled"])
            self.assertEqual(settings["stt_model"], "tiny")
            self.assertTrue(settings_path.is_file())

    def test_persists_devices_models_and_persona_voices(self):
        with tempfile.TemporaryDirectory() as temp_directory:
            settings_path = Path(temp_directory) / "voice.json"
            settings = get_voice_settings(settings_path)
            settings.update(
                {
                    "enabled": True,
                    "input_enabled": False,
                    "output_enabled": True,
                    "auto_playback": False,
                    "microphone_device": 2,
                    "speaker_device": 4,
                    "stt_model": "base",
                }
            )
            settings["persona_voices"]["fairy"]["en"] = "custom-fairy-en"

            save_voice_settings(settings, settings_path)
            restored = get_voice_settings(settings_path)

            self.assertTrue(restored["enabled"])
            self.assertFalse(restored["input_enabled"])
            self.assertFalse(restored["auto_playback"])
            self.assertEqual(restored["microphone_device"], 2)
            self.assertEqual(restored["speaker_device"], 4)
            self.assertEqual(restored["stt_model"], "base")
            self.assertEqual(
                restored["persona_voices"]["fairy"]["en"],
                "custom-fairy-en",
            )

    def test_repairs_invalid_config_and_keeps_profiles_separate(self):
        with tempfile.TemporaryDirectory() as temp_directory:
            settings_path = Path(temp_directory) / "voice.json"
            settings_path.write_text(
                json.dumps(
                    {
                        "enabled": "yes",
                        "microphone_device": "bad",
                        "persona_voices": {"fairy": {"rate": -1}},
                    }
                ),
                encoding="utf-8",
            )

            settings = get_voice_settings(settings_path)
            fairy = get_persona_voice_profile(settings, "fairy")
            delamain = get_persona_voice_profile(settings, "delamain")

            self.assertFalse(settings["enabled"])
            self.assertIsNone(settings["microphone_device"])
            self.assertEqual(fairy["rate"], DEFAULT_PERSONA_VOICES["fairy"]["rate"])
            self.assertNotEqual(fairy["profile_id"], delamain["profile_id"])


if __name__ == "__main__":
    unittest.main()
