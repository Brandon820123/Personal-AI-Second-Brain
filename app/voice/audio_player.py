"""Local WAV playback with an immediate stop operation."""

import threading
import wave
from pathlib import Path

import numpy as np


class AudioPlaybackError(RuntimeError):
    """A concise, user-facing local audio output failure."""


class LocalAudioPlayer:
    """Play one generated WAV locally and support stop from the GUI thread."""

    def __init__(self):
        self._sounddevice = None
        self._stop_requested = threading.Event()
        self._playing = False

    @property
    def is_playing(self):
        return self._playing

    def _load_sounddevice(self):
        if self._sounddevice is not None:
            return self._sounddevice

        try:
            import sounddevice
        except (ImportError, OSError) as error:
            raise AudioPlaybackError(
                "本地音频输出组件不可用。请安装 sounddevice 并检查音频驱动。"
            ) from error

        self._sounddevice = sounddevice
        return sounddevice

    def play(
        self,
        audio_path,
        device=None,
        delete_after=True,
        cancel_event=None,
    ):
        """Play a PCM WAV in a worker thread and delete its temporary file."""
        path = Path(audio_path)

        if not path.is_file():
            raise AudioPlaybackError("待播放的本地语音文件不存在。")

        try:
            with wave.open(str(path), "rb") as wav_file:
                channels = wav_file.getnchannels()
                sample_width = wav_file.getsampwidth()
                sample_rate = wav_file.getframerate()
                frame_data = wav_file.readframes(wav_file.getnframes())

            if sample_width != 2:
                raise AudioPlaybackError("当前只支持播放 16-bit PCM 本地语音。")

            samples = np.frombuffer(frame_data, dtype=np.int16)

            if channels > 1:
                samples = samples.reshape(-1, channels)

            sounddevice = self._load_sounddevice()
            self._stop_requested.clear()

            if cancel_event is not None and cancel_event.is_set():
                return False

            self._playing = True
            sounddevice.play(samples, sample_rate, device=device, blocking=False)

            if cancel_event is not None and cancel_event.is_set():
                sounddevice.stop()

            sounddevice.wait()
            return not self._stop_requested.is_set() and not (
                cancel_event is not None and cancel_event.is_set()
            )
        except AudioPlaybackError:
            raise
        except Exception as error:
            raise AudioPlaybackError(f"本地语音播放失败：{error}") from error
        finally:
            self._playing = False

            if delete_after:
                path.unlink(missing_ok=True)

    def stop(self):
        """Immediately stop active playback without opening an output device."""
        self._stop_requested.set()

        if self._sounddevice is not None:
            try:
                self._sounddevice.stop()
            except Exception:
                pass

    def unload(self):
        """Stop playback and release the imported audio backend reference."""
        self.stop()
        self._sounddevice = None
