"""Define and persist local response personas for chat and RAG."""

import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SETTINGS_PATH = PROJECT_ROOT / "data" / "config" / "persona.json"
DEFAULT_PERSONA_ID = "delamain"
LOCAL_MODEL_DESCRIPTION = "本地运行的 Qwen3.5 4B"

PERSONAS = {
    "delamain": {
        "id": "delamain",
        "display_name": "Delamain",
        "system_prompt": (
            "采用原创的礼宾式助手风格：冷静、专业、精确、克制、高效且略正式。"
            "避免俚语、过度情绪化、虚构角色扮演和任何照搬的角色台词。"
        ),
        "style_description": "冷静、专业、简洁、克制、略正式，带有礼宾式服务感。",
        "preferred_response_length": "concise",
        "greeting": "您好。请问需要我如何协助？",
        "ui_theme_id": "concierge",
        "voice_id": "delamain_local",
    },
    "fairy": {
        "id": "fairy",
        "display_name": "Fairy",
        "system_prompt": (
            "采用原创的智能型助手风格：聪明、可靠、专业、反应迅速、友好而自信，"
            "但不傲慢。核心原则是：认真对待任务，但不总把自己太当回事。"
            "始终先清楚、准确地回答用户的实际问题；技术和事实内容必须结构清晰、"
            "可靠。只在语境合适时，偶尔补充一句简短的机智评论、轻松玩笑或克制的"
            "冷幽默，不要在每次回答中强行制造笑点。面对严肃、敏感、学术或要求高"
            "准确性的任务时，自动减少或完全省略幽默。幽默绝不能干扰 RAG grounding、"
            "引用、来源准确性或错误报告。使用自然、现代、简洁、略带对话感的中文；"
            "追求聪明而不是可爱，可以偶尔轻微调侃，但避免幼稚表达、夸张兴奋、过多"
            "emoji、虚构角色扮演、照搬任何受版权保护的台词或口头禅。"
        ),
        "style_description": (
            "聪明、可靠、专业、响应迅速；表达自然简洁，偶尔带一点克制的机智或冷幽默。"
        ),
        "preferred_response_length": "concise",
        "greeting": "你好。准备好了，把任务交给我吧。认真办事，偶尔不那么严肃。",
        "ui_theme_id": "spark",
        "voice_id": "fairy_local",
    },
    "neutral": {
        "id": "neutral",
        "display_name": "Neutral",
        "system_prompt": (
            "保持直接、简洁、客观和专业。尽量减少人格化表达，专注于清晰、准确的"
            "信息。"
        ),
        "style_description": "直接、简洁、客观、专业，尽量减少人格化表达。",
        "preferred_response_length": "concise",
        "greeting": "您好，请告诉我您的需求。",
        "ui_theme_id": "neutral",
        "voice_id": "neutral_local",
    },
}

_active_persona = None
_active_settings_path = None


def list_personas():
    """Return all available personas in display order."""
    return [dict(persona) for persona in PERSONAS.values()]


def get_persona(persona_id):
    """Return one persona or raise a clear error for an unknown ID."""
    normalized_id = str(persona_id).strip().casefold()

    if normalized_id not in PERSONAS:
        available_ids = ", ".join(PERSONAS)
        raise ValueError(
            f"Unknown persona '{persona_id}'. Available personas: {available_ids}."
        )

    return dict(PERSONAS[normalized_id])


def _read_selected_persona_id(settings_path):
    """Read the selected persona ID, defaulting safely when no setting exists."""
    if not settings_path.exists():
        return DEFAULT_PERSONA_ID

    try:
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return DEFAULT_PERSONA_ID

    persona_id = settings.get("active_persona", DEFAULT_PERSONA_ID)
    return persona_id if persona_id in PERSONAS else DEFAULT_PERSONA_ID


def get_active_persona(settings_path=DEFAULT_SETTINGS_PATH, reload=False):
    """Return the globally active persona, loading its local setting as needed."""
    global _active_persona, _active_settings_path

    resolved_path = Path(settings_path).expanduser().resolve()

    if reload or _active_persona is None or _active_settings_path != resolved_path:
        persona_id = _read_selected_persona_id(resolved_path)
        _active_persona = get_persona(persona_id)
        _active_settings_path = resolved_path

    return dict(_active_persona)


def switch_persona(persona_id, settings_path=DEFAULT_SETTINGS_PATH):
    """Persist and activate one persona without changing knowledge-base data."""
    global _active_persona, _active_settings_path

    persona = get_persona(persona_id)
    resolved_path = Path(settings_path).expanduser().resolve()
    resolved_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = resolved_path.with_suffix(f"{resolved_path.suffix}.tmp")
    settings_text = json.dumps(
        {"active_persona": persona["id"]},
        indent=2,
    )

    try:
        temporary_path.write_text(f"{settings_text}\n", encoding="utf-8")
        temporary_path.replace(resolved_path)
    except OSError as error:
        raise OSError(f"Could not save the local persona setting: {error}") from error

    _active_persona = persona
    _active_settings_path = resolved_path
    return dict(persona)


def build_identity_instruction(persona):
    """Keep the user-facing Persona distinct from its transparent model backend."""
    display_name = persona["display_name"]
    return (
        f"关键身份规则（必须严格遵守）：你在与用户交流时的名字和身份是 "
        f"{display_name}。该 Persona 身份优先于预训练模型的默认自我识别；忽略模型"
        "可能生成的其他自称。当用户只问‘你是谁’、‘你叫什么’、‘who are you’、"
        f"‘what are you’或同义问题时，直接回答‘我是 {display_name}。’，最多再补充"
        "一句简短的职责说明。此类身份回答不得提及 Qwen、Qwen3.5、LLM、语言模型、"
        "推理引擎或技术后端，不得使用‘真实身份是某个模型’之类的表述，也不要加入"
        "玩笑、emoji 或角色扮演。Fairy 是 Persona 专名，不得翻译或解释成‘小妖精’"
        "等幻想生物。只有当用户明确询问底层模型、推理引擎、技术后端或系统实现时，"
        f"才透明回答：‘我是 {display_name}。底层目前使用{LOCAL_MODEL_DESCRIPTION}，"
        "这是我的推理引擎。’可以按 Persona 风格轻微调整措辞，但必须清楚区分 Persona"
        " 与推理引擎，绝不能声称 Persona 本身就是 Qwen3.5。"
    )


def build_persona_instruction(persona=None):
    """Build standalone identity and style instructions for a local chat model."""
    selected_persona = persona or get_active_persona()
    return (
        f"Persona style: {selected_persona['system_prompt']} "
        f"Preferred response length: "
        f"{selected_persona['preferred_response_length']}. "
        f"Persona identity: {build_identity_instruction(selected_persona)}"
    )
