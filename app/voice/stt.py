"""Lazy, local faster-whisper speech recognition."""

from pathlib import Path


class SpeechToTextError(RuntimeError):
    """A concise, user-facing local transcription failure."""


class LocalSpeechToText:
    """Load a CPU INT8 Whisper model only when transcription is requested."""

    def __init__(self, model_name="tiny"):
        self.model_name = str(model_name).strip() or "tiny"
        self._model = None

    @property
    def is_loaded(self):
        return self._model is not None

    def _load_model(self):
        if self._model is not None:
            return self._model

        try:
            from faster_whisper import WhisperModel
        except (ImportError, OSError) as error:
            raise SpeechToTextError(
                "faster-whisper 不可用，无法进行本地语音转写。"
            ) from error

        try:
            self._model = WhisperModel(
                self.model_name,
                device="cpu",
                compute_type="int8",
                local_files_only=True,
            )
        except Exception as error:
            raise SpeechToTextError(
                f"本地 STT 模型“{self.model_name}”未安装或无法加载。"
                "请先把 faster-whisper 模型下载到本机缓存。"
            ) from error

        return self._model

    def transcribe(self, audio_path, language=None, delete_after=True):
        """Transcribe one local WAV and remove it after the attempt by default."""
        path = Path(audio_path)

        if not path.is_file():
            raise SpeechToTextError(f"临时录音不存在：{path}")

        try:
            model = self._load_model()
            segments, information = model.transcribe(
                str(path),
                language=language,
                beam_size=1,
                vad_filter=True,
            )
            transcript = " ".join(segment.text.strip() for segment in segments).strip()

            if not transcript:
                raise SpeechToTextError("没有识别到清晰语音，请重试并靠近麦克风。")

            detected_language = getattr(information, "language", None)
            return {"text": transcript, "language": detected_language}
        except SpeechToTextError:
            raise
        except Exception as error:
            raise SpeechToTextError(f"本地语音转写失败：{error}") from error
        finally:
            if delete_after:
                path.unlink(missing_ok=True)

    def unload(self):
        """Release the loaded model reference when voice mode is disabled."""
        self._model = None
