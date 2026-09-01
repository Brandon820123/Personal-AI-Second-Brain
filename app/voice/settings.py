"""Persist optional voice settings outside the knowledge database."""

import copy
import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_VOICE_SETTINGS_PATH = PROJECT_ROOT / "data" / "config" / "voice.json"
VOICE_MODEL_DIRECTORY = PROJECT_ROOT / "data" / "voice" / "models"
VOICE_TEMP_DIRECTORY = PROJECT_ROOT / "data" / "voice" / "temp"

DEFAULT_PERSONA_VOICES = {
    "delamain": {
        "profile_id": "delamain_local",
        "zh-CN": "zh_CN-huayan-medium",
        "en": "en_US-ryan-low",
        "rate": 0.90,
        "volume": 0.95,
        "pause_ms": 260,
    },
    "fairy": {
        "profile_id": "fairy_local",
        "zh-CN": "zh_CN-huayan-medium",
        "en": "en_US-lessac-low",
        "rate": 1.07,
        "volume": 0.95,
        "pause_ms": 100,
    },
    "neutral": {
        "profile_id": "neutral_local",
        "zh-CN": "zh_CN-huayan-medium",
        "en": "en_US-amy-low",
        "rate": 1.00,
        "volume": 0.90,
        "pause_ms": 160,
    },
}

DEFAULT_VOICE_SETTINGS = {
    "enabled": False,
    "input_enabled": True,
    "output_enabled": True,
    "auto_playback": True,
    "recording_mode": "push_to_toggle",
    "microphone_device": None,
    "speaker_device": None,
    "stt_model": "tiny",
    "persona_voices": DEFAULT_PERSONA_VOICES,
}


def _clean_optional_device(value):
    """Keep an optional sounddevice index or the system-default marker."""
    if value is None or isinstance(value, int):
        return value
    return None


def _validated_profile(persona_id, saved_profile):
    """Merge one saved voice profile with safe local defaults."""
    profile = copy.deepcopy(DEFAULT_PERSONA_VOICES[persona_id])

    if not isinstance(saved_profile, dict):
        return profile

    for key in ("profile_id", "zh-CN", "en"):
        value = saved_profile.get(key)

        if isinstance(value, str) and value.strip():
            profile[key] = value.strip()

    for key in ("rate", "volume"):
        value = saved_profile.get(key)

        if isinstance(value, (int, float)) and value > 0:
            profile[key] = float(value)

    pause_ms = saved_profile.get("pause_ms")

    if isinstance(pause_ms, (int, float)) and pause_ms >= 0:
        profile["pause_ms"] = float(pause_ms)

    return profile


def validate_voice_settings(settings):
    """Return a complete settings dictionary even for old or damaged config."""
    saved = settings if isinstance(settings, dict) else {}
    validated = copy.deepcopy(DEFAULT_VOICE_SETTINGS)

    for key in ("enabled", "input_enabled", "output_enabled", "auto_playback"):
        if isinstance(saved.get(key), bool):
            validated[key] = saved[key]

    validated["microphone_device"] = _clean_optional_device(
        saved.get("microphone_device")
    )
    validated["speaker_device"] = _clean_optional_device(
        saved.get("speaker_device")
    )

    stt_model = saved.get("stt_model")

    if isinstance(stt_model, str) and stt_model.strip():
        validated["stt_model"] = stt_model.strip()

    saved_profiles = saved.get("persona_voices", {})
    validated["persona_voices"] = {
        persona_id: _validated_profile(
            persona_id,
            saved_profiles.get(persona_id) if isinstance(saved_profiles, dict) else None,
        )
        for persona_id in DEFAULT_PERSONA_VOICES
    }
    return validated


def save_voice_settings(settings, settings_path=DEFAULT_VOICE_SETTINGS_PATH):
    """Atomically persist voice settings and return the normalized values."""
    normalized = validate_voice_settings(settings)
    resolved_path = Path(settings_path).expanduser().resolve()
    resolved_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = resolved_path.with_suffix(f"{resolved_path.suffix}.tmp")
    settings_text = json.dumps(normalized, ensure_ascii=False, indent=2)

    try:
        temporary_path.write_text(f"{settings_text}\n", encoding="utf-8")
        temporary_path.replace(resolved_path)
    except OSError as error:
        raise OSError(f"无法保存本地语音设置：{error}") from error

    return normalized


def get_voice_settings(settings_path=DEFAULT_VOICE_SETTINGS_PATH):
    """Load local voice settings, creating the default-OFF config if absent."""
    resolved_path = Path(settings_path).expanduser().resolve()

    if not resolved_path.exists():
        return save_voice_settings(DEFAULT_VOICE_SETTINGS, resolved_path)

    try:
        saved = json.loads(resolved_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return save_voice_settings(DEFAULT_VOICE_SETTINGS, resolved_path)

    normalized = validate_voice_settings(saved)

    if normalized != saved:
        return save_voice_settings(normalized, resolved_path)

    return normalized


def get_persona_voice_profile(settings, persona_id):
    """Return one Persona profile without sharing mutable config state."""
    normalized = validate_voice_settings(settings)
    profiles = normalized["persona_voices"]
    return copy.deepcopy(profiles.get(persona_id, profiles["neutral"]))


def list_installed_voice_ids(model_directory=VOICE_MODEL_DIRECTORY):
    """List Piper model IDs already installed in the local model directory."""
    directory = Path(model_directory)

    if not directory.exists():
        return []

    return sorted(path.stem for path in directory.rglob("*.onnx"))
