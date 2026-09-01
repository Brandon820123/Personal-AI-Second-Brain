"""Lazy, fully local Piper text-to-speech synthesis."""

import tempfile
import wave
from pathlib import Path

from .settings import VOICE_MODEL_DIRECTORY, VOICE_TEMP_DIRECTORY


class TextToSpeechError(RuntimeError):
    """A concise, user-facing local synthesis failure."""


def select_voice_id(profile, language_id):
    """Select a configured Chinese or English Piper model for response text."""
    if language_id == "zh-CN":
        return profile.get("zh-CN")
    if language_id == "en":
        return profile.get("en")
    return None


class LocalPiperTextToSpeech:
    """Load only the requested Piper voice and cache it for later local use."""

    def __init__(
        self,
        model_directory=VOICE_MODEL_DIRECTORY,
        temp_directory=VOICE_TEMP_DIRECTORY,
    ):
        self.model_directory = Path(model_directory)
        self.temp_directory = Path(temp_directory)
        self._voices = {}

    @property
    def loaded_voice_ids(self):
        return tuple(self._voices)

    def _find_model_path(self, voice_id):
        if not voice_id:
            raise TextToSpeechError("当前回答语言没有配置本地 Piper 语音。")

        direct_path = self.model_directory / f"{voice_id}.onnx"

        if direct_path.is_file():
            return direct_path

        matches = list(self.model_directory.rglob(f"{voice_id}.onnx"))

        if matches:
            return matches[0]

        raise TextToSpeechError(
            f"本地 Piper 语音“{voice_id}”未安装。文字回答不受影响。"
        )

    def _load_voice(self, voice_id):
        if voice_id in self._voices:
            return self._voices[voice_id]

        model_path = self._find_model_path(voice_id)
        config_path = Path(f"{model_path}.json")

        if not config_path.is_file():
            raise TextToSpeechError(
                f"Piper 语音“{voice_id}”缺少对应的 .onnx.json 配置文件。"
            )

        try:
            from piper import PiperVoice
        except (ImportError, OSError) as error:
            raise TextToSpeechError("Piper TTS 不可用，无法生成本地语音。") from error

        try:
            voice = PiperVoice.load(
                str(model_path),
                config_path=str(config_path),
                use_cuda=False,
            )
        except Exception as error:
            raise TextToSpeechError(
                f"无法加载本地 Piper 语音“{voice_id}”：{error}"
            ) from error

        self._voices[voice_id] = voice
        return voice

    def synthesize(self, text, profile, language_id):
        """Generate a private temporary WAV without blocking text streaming."""
        content = str(text or "").strip()

        if not content:
            raise TextToSpeechError("没有可朗读的文字。")

        voice_id = select_voice_id(profile, language_id)

        if not voice_id:
            raise TextToSpeechError(
                "当前回答语言没有匹配的本地 Piper 语音。文字回答仍可正常使用。"
            )

        voice = self._load_voice(voice_id)

        try:
            from piper import SynthesisConfig
        except (ImportError, OSError) as error:
            raise TextToSpeechError("Piper TTS 配置组件不可用。") from error

        rate = max(0.25, float(profile.get("rate", 1.0)))
        volume = max(0.0, float(profile.get("volume", 1.0)))
        synthesis_config = SynthesisConfig(
            volume=volume,
            length_scale=1.0 / rate,
        )
        self.temp_directory.mkdir(parents=True, exist_ok=True)
        temporary_file = tempfile.NamedTemporaryFile(
            prefix="voice-output-",
            suffix=".wav",
            dir=self.temp_directory,
            delete=False,
        )
        output_path = Path(temporary_file.name)
        temporary_file.close()

        try:
            with wave.open(str(output_path), "wb") as wav_file:
                voice.synthesize_wav(
                    content,
                    wav_file,
                    syn_config=synthesis_config,
                )
        except Exception as error:
            output_path.unlink(missing_ok=True)
            raise TextToSpeechError(f"本地 Piper 语音生成失败：{error}") from error

        return {"path": output_path, "voice_id": voice_id}

    def prepare(self, profile, language_id):
        """Warm one configured local voice before the first sentence arrives."""
        voice_id = select_voice_id(profile, language_id)

        if not voice_id:
            raise TextToSpeechError(
                "当前回答语言没有匹配的本地 Piper 语音。文字回答仍可正常使用。"
            )

        self._load_voice(voice_id)
        return voice_id

    def unload(self):
        """Release cached voice models when the global voice switch is disabled."""
        self._voices.clear()
