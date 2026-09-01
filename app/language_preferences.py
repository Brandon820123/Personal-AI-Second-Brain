"""Persist and describe the global response-language preference."""

import json
import re
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SETTINGS_PATH = PROJECT_ROOT / "data" / "config" / "language.json"
DEFAULT_LANGUAGE_ID = "auto"

LANGUAGES = {
    "auto": {
        "id": "auto",
        "display_name": "自动（简体中文回退）",
        "instruction": (
            "响应语言使用自动模式。首先检查用户在当前请求中是否明确指定回答语言；"
            "明确指定始终具有最高优先级，即使它与输入文本语言不同。否则，根据用户"
            "当前请求本身的主要语言回答：中文输入使用简体中文，英文输入使用英文，"
            "其他能够清楚识别的语言在可行时使用同一种语言。不要根据检索到的上下文、"
            "引用内容或历史消息的语言改变判断。对于语言不明确、过短且无法可靠识别、"
            "或多语言混合但没有明显主语言的请求，回退到简体中文。语言选择不得改变 "
            "Persona 名称或身份。不要生硬翻译技术术语；RAG、Embedding、ChromaDB、"
            "API、PDF、Chunk、LLM 等术语在自然的情况下可以保留英文，并可在有助于"
            "理解时用回答语言解释。不要改写引用原文、文件名、路径、标识符或来源元数据。"
        ),
    },
}

_active_language = None
_active_settings_path = None


def get_language(language_id):
    """Return one supported language preference."""
    normalized_id = str(language_id).strip()

    if normalized_id not in LANGUAGES:
        available_ids = ", ".join(LANGUAGES)
        raise ValueError(
            f"Unknown language preference '{language_id}'. "
            f"Available languages: {available_ids}."
        )

    return dict(LANGUAGES[normalized_id])


def _write_language_setting(language_id, settings_path):
    """Atomically write the selected language to a local JSON file."""
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = settings_path.with_suffix(f"{settings_path.suffix}.tmp")
    settings_text = json.dumps(
        {"response_language": language_id},
        indent=2,
        ensure_ascii=False,
    )

    try:
        temporary_path.write_text(f"{settings_text}\n", encoding="utf-8")
        temporary_path.replace(settings_path)
    except OSError as error:
        raise OSError(f"Could not save the local language setting: {error}") from error


def get_language_preference(settings_path=DEFAULT_SETTINGS_PATH, reload=False):
    """Return automatic response language with a Simplified Chinese fallback."""
    global _active_language, _active_settings_path

    resolved_path = Path(settings_path).expanduser().resolve()

    if reload or _active_language is None or _active_settings_path != resolved_path:
        language_id = DEFAULT_LANGUAGE_ID
        should_write_setting = not resolved_path.exists()

        if resolved_path.exists():
            try:
                settings = json.loads(resolved_path.read_text(encoding="utf-8"))
                saved_language_id = settings.get("response_language")

                if saved_language_id in LANGUAGES:
                    language_id = saved_language_id
                else:
                    should_write_setting = True
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                language_id = DEFAULT_LANGUAGE_ID
                should_write_setting = True

        if should_write_setting:
            _write_language_setting(language_id, resolved_path)

        _active_language = get_language(language_id)
        _active_settings_path = resolved_path

    return dict(_active_language)


def set_language_preference(language_id, settings_path=DEFAULT_SETTINGS_PATH):
    """Persist and activate a supported global response language."""
    global _active_language, _active_settings_path

    language = get_language(language_id)
    resolved_path = Path(settings_path).expanduser().resolve()
    _write_language_setting(language["id"], resolved_path)
    _active_language = language
    _active_settings_path = resolved_path
    return dict(language)


def infer_response_language(user_message):
    """Infer a response-language hint locally without calling an external service."""
    message = str(user_message or "").strip()
    lowered_message = message.casefold()
    explicit_targets = []

    explicit_patterns = {
        "en": (
            r"(?:请用|使用|用)\s*(?:英文|英语)",
            r"\b(?:in|using|use)\s+english\b",
            r"\b(?:answer|respond|write)\s+(?:to\s+me\s+)?in\s+english\b",
        ),
        "zh-CN": (
            r"(?:请用|使用|用)\s*(?:简体中文|中文|汉语)",
            r"\b(?:in|using|use)\s+(?:simplified\s+)?chinese\b",
            r"\b(?:answer|respond|write)\s+(?:to\s+me\s+)?in\s+chinese\b",
        ),
    }

    for language_id, patterns in explicit_patterns.items():
        for pattern in patterns:
            for match in re.finditer(pattern, lowered_message):
                explicit_targets.append((match.start(), language_id))

    if explicit_targets:
        return max(explicit_targets)[1]

    chinese_count = len(re.findall(r"[\u3400-\u9fff]", message))
    english_count = len(re.findall(r"[A-Za-z]", message))
    other_letter_count = sum(
        character.isalpha()
        and not ("\u3400" <= character <= "\u9fff")
        and not character.isascii()
        for character in message
    )

    if chinese_count:
        return "zh-CN"
    if english_count:
        return "en"
    if other_letter_count:
        return "same-as-user"
    return "zh-CN"


def build_language_instruction(language=None, user_message=None):
    """Build a centralized automatic-language instruction for one request."""
    selected_language = language or get_language_preference()
    instruction = f"全局语言偏好：{selected_language['instruction']}"

    if user_message is None:
        return instruction

    inferred_language = infer_response_language(user_message)
    language_hints = {
        "en": (
            "本地语言判断：本次回答使用英文。即使 Persona 身份示例或系统说明使用"
            "中文，也必须将回答自然地表达为英文；Persona 名称保持不变。"
        ),
        "zh-CN": "本地语言判断：本次回答使用简体中文；Persona 名称保持不变。",
        "same-as-user": (
            "本地语言判断：本次输入是可识别的其他语言；在可行时使用同一种语言回答。"
        ),
    }
    return f"{instruction} {language_hints[inferred_language]}"
