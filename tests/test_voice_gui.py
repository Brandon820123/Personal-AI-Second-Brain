"""Offscreen integration tests for the optional desktop voice layer."""

import copy
import gc
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from app import gui
from app.ui import PersonaState
from app.voice.settings import DEFAULT_VOICE_SETTINGS, validate_voice_settings


class FakeRecorder:
    def __init__(self):
        self.is_recording = False
        self.cancelled = False

    def start(self, device=None):
        del device
        self.is_recording = True

    def stop(self):
        self.is_recording = False
        return Path("private-recording.wav")

    def cancel(self):
        self.cancelled = True
        self.is_recording = False


class FakeSpeechQueue:
    def __init__(self, window, drain_on_finish=True):
        self.window = window
        self.drain_on_finish = drain_on_finish
        self.session_id = 0
        self.active = False
        self.sentences = []
        self.cancelled = False
        self.prepared_languages = []

    def start_session(self):
        self.session_id += 1
        self.cancelled = False
        return self.session_id

    def enqueue(self, session_id, text, profile, language_id, device=None):
        self.sentences.append((text, profile, language_id, device))
        self.active = True
        self.window._speech_activity_changed(session_id, True)
        self.window._speech_started(
            session_id,
            len(self.sentences) - 1,
            text,
            0.0,
            "fake-local",
        )
        return True

    def prepare_voice(self, session_id, profile, language_id):
        del session_id, profile
        self.prepared_languages.append(language_id)
        return True

    def finish_session(self, session_id):
        if self.drain_on_finish:
            self.active = False
            self.window._speech_activity_changed(session_id, False)
            self.window._speech_queue_drained(session_id)

    def is_active(self, session_id=None):
        del session_id
        return self.active

    def cancel(self):
        self.cancelled = True
        self.active = False

    def shutdown(self, wait=False):
        del wait
        self.cancel()


class VoiceGuiTests(unittest.TestCase):
    """Verify OFF privacy, explicit capture, and SPEAKING lifecycle."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        settings = copy.deepcopy(DEFAULT_VOICE_SETTINGS)
        self.patches = (
            patch.object(gui.MainWindow, "_run_health_check"),
            patch("app.gui.list_documents", return_value=[]),
            patch("app.gui.get_voice_settings", return_value=settings),
            patch(
                "app.gui.save_voice_settings",
                side_effect=lambda values: validate_voice_settings(values),
            ),
        )

        for active_patch in self.patches:
            active_patch.start()

        self.window = gui.MainWindow()

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()

        for active_patch in reversed(self.patches):
            active_patch.stop()

        gc.collect()

    def test_voice_off_keeps_all_resources_uninitialized(self):
        self.assertEqual(self.window.voice_master_button.text(), "VOICE · OFF")
        self.assertFalse(self.window.microphone_button.isEnabled())
        self.assertIsNone(self.window.voice_recorder)
        self.assertIsNone(self.window.stt_engine)
        self.assertIsNone(self.window.tts_engine)
        self.assertIsNone(self.window.audio_player)
        self.assertIsNone(self.window.speech_queue)
        self.assertTrue(self.window.recording_indicator.isHidden())

    def test_click_to_record_then_transcribe_only_fills_input(self):
        self.window.voice_settings["enabled"] = True
        self.window._sync_voice_controls()

        def fake_worker(operation, *arguments, **callbacks):
            callbacks["on_success"]({"text": "你好 Delamain", "language": "zh"})
            callbacks["on_finished"]()

        with patch("app.gui.MicrophoneRecorder", FakeRecorder):
            self.window.toggle_microphone_recording()
            self.assertTrue(self.window.recording_indicator.isVisibleTo(self.window))
            self.assertEqual(self.window.voice_capture_panel.state, PersonaState.LISTENING)

            with patch.object(self.window, "_run_worker", side_effect=fake_worker):
                self.window.toggle_microphone_recording()

        self.assertEqual(self.window.message_input.toPlainText(), "你好 Delamain")
        self.assertEqual(self.window.user_message_count, 0)
        self.assertTrue(self.window.recording_indicator.isHidden())

    def test_streamed_sentences_speak_before_full_text_finishes(self):
        self.window.voice_settings.update(
            {"enabled": True, "output_enabled": True, "auto_playback": True}
        )
        self.window._sync_voice_controls()
        fake_queue = FakeSpeechQueue(self.window)
        self.window.speech_queue = fake_queue
        state_during_stream = []

        def fake_worker(operation, message, **callbacks):
            del operation, message
            callbacks["on_state"]("thinking")
            callbacks["on_token"]("RAG 可以理解成先查资料再回答。")
            state_during_stream.append(self.window.current_ai_panel.state)
            callbacks["on_token"]("系统首先从知识库中检索相关内容。")
            callbacks["on_success"](None)
            callbacks["on_finished"]()

        self.window.message_input.setPlainText("简单解释 RAG。")

        with patch.object(self.window, "_run_worker", side_effect=fake_worker):
            self.window.send_message()

        panel = self.window.current_ai_panel
        self.assertEqual(state_during_stream, [PersonaState.SPEAKING])
        self.assertEqual(
            [sentence[0] for sentence in fake_queue.sentences],
            [
                "RAG 可以理解成先查资料再回答。",
                "系统首先从知识库中检索相关内容。",
            ],
        )
        self.assertEqual(fake_queue.prepared_languages, ["zh-CN"])
        self.assertIn(PersonaState.SPEAKING, panel.state_history)
        self.assertEqual(panel.state, PersonaState.COMPLETE)

    def test_microphone_interrupts_speech_but_not_streamed_text(self):
        self.window.voice_settings.update(
            {"enabled": True, "input_enabled": True, "output_enabled": True}
        )
        self.window._sync_voice_controls()
        panel = self.window._add_persona_panel(
            self.window.active_persona,
            PersonaState.RESPONDING,
            "已经生成的文字。",
        )
        self.window.current_ai_panel = panel
        self.window.chat_busy = True
        fake_queue = FakeSpeechQueue(self.window, drain_on_finish=False)
        self.window.speech_queue = fake_queue
        self.window._begin_streaming_speech(panel)
        self.window._queue_streamed_sentences(["正在播放的第一句话。"])
        self.window._update_voice_action_availability()

        self.assertTrue(self.window.microphone_button.isEnabled())

        with patch("app.gui.MicrophoneRecorder", FakeRecorder):
            self.window.toggle_microphone_recording()

        self.assertTrue(fake_queue.cancelled)
        self.assertEqual(panel._text, "已经生成的文字。")
        self.assertEqual(self.window.voice_capture_panel.state, PersonaState.LISTENING)
        self.assertIsNone(self.window.streaming_speech_context)

    def test_switching_voice_off_cancels_active_capture(self):
        self.window.voice_settings["enabled"] = True
        self.window._sync_voice_controls()
        recorder = FakeRecorder()
        recorder.start()
        self.window.voice_recorder = recorder

        self.window.voice_enabled_checkbox.setChecked(False)

        self.assertTrue(recorder.cancelled)
        self.assertFalse(self.window.voice_settings["enabled"])
        self.assertEqual(self.window.voice_master_button.text(), "VOICE · OFF")


if __name__ == "__main__":
    unittest.main()
