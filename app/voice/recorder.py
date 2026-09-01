"""Explicit click-to-start local microphone recording."""

import tempfile
import wave
from pathlib import Path

import numpy as np

from .settings import VOICE_TEMP_DIRECTORY


SAMPLE_RATE = 16000


class VoiceInputError(RuntimeError):
    """A concise, user-facing microphone failure."""


def _load_sounddevice():
    try:
        import sounddevice
    except (ImportError, OSError) as error:
        raise VoiceInputError(
            "本地音频组件不可用。请安装 sounddevice 并检查系统音频驱动。"
        ) from error

    return sounddevice


def list_audio_devices():
    """Return input and output devices without opening an audio stream."""
    sounddevice = _load_sounddevice()

    try:
        devices = sounddevice.query_devices()
    except Exception as error:
        raise VoiceInputError(f"无法读取本机音频设备：{error}") from error

    inputs = []
    outputs = []

    for index, device in enumerate(devices):
        name = str(device.get("name", f"设备 {index}"))

        if int(device.get("max_input_channels", 0)) > 0:
            inputs.append((index, name))
        if int(device.get("max_output_channels", 0)) > 0:
            outputs.append((index, name))

    return inputs, outputs


class MicrophoneRecorder:
    """Record mono audio only between explicit start and stop calls."""

    def __init__(self, sample_rate=SAMPLE_RATE, temp_directory=VOICE_TEMP_DIRECTORY):
        self.sample_rate = int(sample_rate)
        self.temp_directory = Path(temp_directory)
        self.stream = None
        self.frames = []
        self.callback_error = None

    @property
    def is_recording(self):
        return self.stream is not None

    def start(self, device=None):
        """Open the selected microphone; no stream exists before this call."""
        if self.is_recording:
            raise VoiceInputError("麦克风已经在录音。")

        sounddevice = _load_sounddevice()
        self.frames = []
        self.callback_error = None

        def audio_callback(indata, frames, time_info, status):
            del frames, time_info

            if status and self.callback_error is None:
                self.callback_error = str(status)

            self.frames.append(indata.copy())

        try:
            self.stream = sounddevice.InputStream(
                samplerate=self.sample_rate,
                channels=1,
                dtype="float32",
                device=device,
                callback=audio_callback,
            )
            self.stream.start()
        except Exception as error:
            self.stream = None
            raise VoiceInputError(
                "无法启动麦克风。请检查设备选择、系统权限和音频驱动。"
            ) from error

    def stop(self):
        """Stop capture, release the device, and write one private temporary WAV."""
        if not self.is_recording:
            raise VoiceInputError("当前没有正在进行的录音。")

        stream = self.stream
        self.stream = None

        try:
            stream.stop()
            stream.close()
        except Exception as error:
            raise VoiceInputError("停止录音时无法正常释放麦克风。") from error

        if not self.frames:
            raise VoiceInputError("没有录到可转写的声音。")

        samples = np.concatenate(self.frames, axis=0).reshape(-1)
        self.frames = []

        if samples.size == 0:
            raise VoiceInputError("没有录到可转写的声音。")

        pcm_data = (np.clip(samples, -1.0, 1.0) * 32767).astype(np.int16)
        self.temp_directory.mkdir(parents=True, exist_ok=True)
        temporary_file = tempfile.NamedTemporaryFile(
            prefix="voice-input-",
            suffix=".wav",
            dir=self.temp_directory,
            delete=False,
        )
        temporary_path = Path(temporary_file.name)
        temporary_file.close()

        try:
            with wave.open(str(temporary_path), "wb") as wav_file:
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)
                wav_file.setframerate(self.sample_rate)
                wav_file.writeframes(pcm_data.tobytes())
        except Exception:
            temporary_path.unlink(missing_ok=True)
            raise

        return temporary_path

    def cancel(self):
        """Immediately stop capture without retaining any audio."""
        stream = self.stream
        self.stream = None
        self.frames = []

        if stream is None:
            return

        try:
            stream.stop()
        finally:
            stream.close()
