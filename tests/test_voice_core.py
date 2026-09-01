"""Unit tests for private recording, lazy STT, and local TTS safeguards."""

import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from app.voice.recorder import MicrophoneRecorder
from app.voice.stt import LocalSpeechToText
from app.voice.streaming import StreamingSentenceSegmenter, ThreadedSpeechQueue
from app.voice.tts import LocalPiperTextToSpeech, TextToSpeechError, select_voice_id


class FakeInputStream:
    def __init__(self, callback, **unused_arguments):
        self.callback = callback
        self.closed = False

    def start(self):
        samples = np.full((320, 1), 0.1, dtype=np.float32)
        self.callback(samples, len(samples), None, None)

    def stop(self):
        return None

    def close(self):
        self.closed = True


class FakeSoundDevice:
    def InputStream(self, **arguments):
        return FakeInputStream(**arguments)


class VoiceCoreTests(unittest.TestCase):
    """Verify voice resources stay lazy and temporary recordings are removed."""

    def test_recorder_does_not_open_microphone_until_start(self):
        recorder = MicrophoneRecorder()

        self.assertFalse(recorder.is_recording)
        self.assertEqual(recorder.frames, [])

    def test_explicit_recording_creates_one_local_temporary_wav(self):
        with tempfile.TemporaryDirectory() as temp_directory:
            recorder = MicrophoneRecorder(temp_directory=temp_directory)

            with patch(
                "app.voice.recorder._load_sounddevice",
                return_value=FakeSoundDevice(),
            ):
                recorder.start()
                audio_path = recorder.stop()

            self.assertTrue(audio_path.is_file())
            self.assertEqual(audio_path.parent, Path(temp_directory))
            audio_path.unlink()

    def test_stt_is_lazy_cpu_int8_and_deletes_temporary_audio(self):
        with tempfile.TemporaryDirectory() as temp_directory:
            audio_path = Path(temp_directory) / "input.wav"
            audio_path.write_bytes(b"private audio")
            engine = LocalSpeechToText("tiny")
            fake_model = unittest.mock.Mock()
            fake_model.transcribe.return_value = (
                [SimpleNamespace(text=" 你好 "), SimpleNamespace(text=" Delamain ")],
                SimpleNamespace(language="zh"),
            )
            engine._model = fake_model

            result = engine.transcribe(audio_path)

            self.assertEqual(result["text"], "你好 Delamain")
            self.assertEqual(result["language"], "zh")
            self.assertFalse(audio_path.exists())
            fake_model.transcribe.assert_called_once_with(
                str(audio_path),
                language=None,
                beam_size=1,
                vad_filter=True,
            )

    def test_tts_voice_selection_is_language_and_persona_profile_specific(self):
        profile = {
            "zh-CN": "fairy-zh",
            "en": "fairy-en",
            "rate": 1.07,
            "volume": 0.95,
        }

        self.assertEqual(select_voice_id(profile, "zh-CN"), "fairy-zh")
        self.assertEqual(select_voice_id(profile, "en"), "fairy-en")
        self.assertIsNone(select_voice_id(profile, "same-as-user"))

    def test_missing_piper_voice_has_readable_optional_error(self):
        with tempfile.TemporaryDirectory() as temp_directory:
            engine = LocalPiperTextToSpeech(model_directory=temp_directory)

            with self.assertRaisesRegex(TextToSpeechError, "未安装"):
                engine.synthesize(
                    "测试",
                    {"zh-CN": "missing-voice", "rate": 1.0, "volume": 1.0},
                    "zh-CN",
                )

    def test_tts_prepare_warms_and_reuses_the_selected_voice(self):
        engine = LocalPiperTextToSpeech()
        fake_voice = object()

        with patch.object(engine, "_load_voice", return_value=fake_voice) as load:
            voice_id = engine.prepare(
                {"zh-CN": "fairy-zh", "en": "fairy-en"},
                "zh-CN",
            )

        self.assertEqual(voice_id, "fairy-zh")
        load.assert_called_once_with("fairy-zh")

    def test_streamed_sentence_segmentation_handles_mixed_boundaries(self):
        segmenter = StreamingSentenceSegmenter()

        self.assertEqual(segmenter.feed("好。RAG 可以理解成先查"), [])
        self.assertEqual(
            segmenter.feed("资料再回答。系统首先从知识库中"),
            ["好。RAG 可以理解成先查资料再回答。"],
        )
        self.assertEqual(
            segmenter.feed("检索。Version 3.5 is ready."),
            ["系统首先从知识库中检索。"],
        )
        self.assertEqual(
            segmenter.feed(" Next step?"),
            ["Version 3.5 is ready.", "Next step?"],
        )
        self.assertEqual(segmenter.finish(), [])

        initials = StreamingSentenceSegmenter()
        self.assertEqual(initials.feed("The U.S. system retrieves context."), [])
        self.assertEqual(
            initials.finish(),
            ["The U.S. system retrieves context."],
        )

    def test_fifo_queue_prepares_next_sentence_without_overlapping_playback(self):
        with tempfile.TemporaryDirectory() as temp_directory:
            synthesized = []
            played = []
            timings = {}
            drained = threading.Event()
            player_lock = threading.Lock()
            concurrent_playback = 0
            maximum_playback = 0

            class FakeTTS:
                def synthesize(self, text, profile, language_id):
                    del profile, language_id
                    synthesized.append(text)
                    timings[f"synth-{text}"] = time.perf_counter()
                    path = Path(temp_directory) / f"{len(synthesized)}.wav"
                    path.write_bytes(b"wav")
                    return {"path": path, "voice_id": "fake-local"}

                def unload(self):
                    return None

            class FakePlayer:
                def play(self, path, device=None, cancel_event=None):
                    nonlocal concurrent_playback, maximum_playback
                    del device

                    with player_lock:
                        concurrent_playback += 1
                        maximum_playback = max(maximum_playback, concurrent_playback)

                    item_number = Path(path).stem
                    played.append(item_number)
                    timings[f"play-start-{item_number}"] = time.perf_counter()
                    cancel_event.wait(0.08)
                    timings[f"play-end-{item_number}"] = time.perf_counter()

                    with player_lock:
                        concurrent_playback -= 1

                    Path(path).unlink(missing_ok=True)
                    return not cancel_event.is_set()

                def stop(self):
                    return None

                def unload(self):
                    return None

            speech_queue = ThreadedSpeechQueue(
                FakeTTS(),
                FakePlayer(),
                on_drained=lambda session_id: drained.set(),
            )
            session_id = speech_queue.start_session()
            profile = {"pause_ms": 0}
            speech_queue.enqueue(session_id, "first", profile, "en")
            speech_queue.enqueue(session_id, "second", profile, "en")
            speech_queue.finish_session(session_id)

            self.assertTrue(drained.wait(2.0))
            speech_queue.shutdown(wait=True)
            self.assertEqual(synthesized, ["first", "second"])
            self.assertEqual(played, ["1", "2"])
            self.assertEqual(maximum_playback, 1)
            self.assertLess(timings["synth-second"], timings["play-end-1"])

    def test_one_tts_failure_does_not_block_later_sentences(self):
        with tempfile.TemporaryDirectory() as temp_directory:
            played = []
            warnings = []
            drained = threading.Event()

            class FlakyTTS:
                def synthesize(self, text, profile, language_id):
                    del profile, language_id

                    if text == "bad sentence":
                        raise RuntimeError("one local sentence failed")

                    path = Path(temp_directory) / f"{text}.wav"
                    path.write_bytes(b"wav")
                    return {"path": path, "voice_id": "fake-local"}

                def unload(self):
                    return None

            class FakePlayer:
                def play(self, path, device=None, cancel_event=None):
                    del device, cancel_event
                    played.append(Path(path).stem)
                    Path(path).unlink(missing_ok=True)
                    return True

                def stop(self):
                    return None

                def unload(self):
                    return None

            speech_queue = ThreadedSpeechQueue(
                FlakyTTS(),
                FakePlayer(),
                on_warning=lambda session_id, message: warnings.append(message),
                on_drained=lambda session_id: drained.set(),
            )
            session_id = speech_queue.start_session()
            speech_queue.enqueue(session_id, "bad sentence", {}, "en")
            speech_queue.enqueue(session_id, "later sentence", {}, "en")
            speech_queue.finish_session(session_id)

            self.assertTrue(drained.wait(2.0))
            speech_queue.shutdown(wait=True)
            self.assertEqual(played, ["later sentence"])
            self.assertEqual(warnings, ["one local sentence failed"])

    def test_cancel_stops_current_audio_and_deletes_prepared_queue(self):
        with tempfile.TemporaryDirectory() as temp_directory:
            playback_started = threading.Event()
            player_stopped = threading.Event()
            played = []

            class FastTTS:
                def synthesize(self, text, profile, language_id):
                    del profile, language_id
                    path = Path(temp_directory) / f"{text}.wav"
                    path.write_bytes(b"wav")
                    return {"path": path, "voice_id": "fake-local"}

                def unload(self):
                    return None

            class InterruptiblePlayer:
                def play(self, path, device=None, cancel_event=None):
                    del device
                    played.append(Path(path).stem)
                    playback_started.set()
                    cancel_event.wait(2.0)
                    Path(path).unlink(missing_ok=True)
                    return False

                def stop(self):
                    player_stopped.set()

                def unload(self):
                    return None

            speech_queue = ThreadedSpeechQueue(FastTTS(), InterruptiblePlayer())
            session_id = speech_queue.start_session()

            for sentence in ("first", "second", "third"):
                speech_queue.enqueue(session_id, sentence, {}, "en")

            self.assertTrue(playback_started.wait(1.0))
            speech_queue.cancel()
            self.assertTrue(player_stopped.wait(1.0))
            time.sleep(0.05)
            speech_queue.shutdown(wait=True)

            self.assertEqual(played, ["first"])
            self.assertFalse(speech_queue.is_active(session_id))
            self.assertEqual(list(Path(temp_directory).glob("*.wav")), [])


if __name__ == "__main__":
    unittest.main()
