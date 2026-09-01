"""Reusable animated Persona dialogue panel for streamed AI responses."""

import re
from enum import Enum

from PySide6.QtCore import (
    QEasingCurve,
    QParallelAnimationGroup,
    QPoint,
    QPropertyAnimation,
    Qt,
)
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .avatar_widget import AvatarAnimationMode, PersonaAvatarWidget


class PersonaState(str, Enum):
    """Presentation-only states shared by every Persona dialogue panel."""

    IDLE = "idle"
    LISTENING = "listening"
    SEARCHING = "searching"
    THINKING = "thinking"
    RESPONDING = "responding"
    SPEAKING = "speaking"
    COMPLETE = "complete"
    ERROR = "error"


STATUS_TEXT = {
    "delamain": {
        PersonaState.IDLE: "系统待命",
        PersonaState.LISTENING: "正在接收请求",
        PersonaState.SEARCHING: "正在检索知识库",
        PersonaState.THINKING: "正在分析",
        PersonaState.RESPONDING: "正在生成响应",
        PersonaState.SPEAKING: "正在输出语音",
        PersonaState.COMPLETE: "处理完成",
        PersonaState.ERROR: "处理异常",
    },
    "fairy": {
        PersonaState.IDLE: "随时可以开始",
        PersonaState.LISTENING: "我在听",
        PersonaState.SEARCHING: "正在查找相关资料",
        PersonaState.THINKING: "正在梳理思路",
        PersonaState.RESPONDING: "已找到相关内容",
        PersonaState.SPEAKING: "正在说话",
        PersonaState.COMPLETE: "处理完成",
        PersonaState.ERROR: "遇到问题",
    },
    "neutral": {
        PersonaState.IDLE: "等待",
        PersonaState.LISTENING: "处理中",
        PersonaState.SEARCHING: "检索中",
        PersonaState.THINKING: "处理中",
        PersonaState.RESPONDING: "生成中",
        PersonaState.SPEAKING: "播放中",
        PersonaState.COMPLETE: "完成",
        PersonaState.ERROR: "错误",
    },
}


def parse_source_groups(source_text):
    """Parse and group the existing RAG source format without internal IDs."""
    groups = {}
    source_pattern = re.compile(
        r"^\[\d+\]\s+(.+?)(?:\s+-\s+page\s+(\d+))?"
        r"\s+-\s+chunk\s+([^\s]+)\s*$",
        re.IGNORECASE,
    )

    for line in str(source_text).splitlines():
        match = source_pattern.match(line.strip())

        if not match:
            continue

        filename, page_number, chunk_index = match.groups()
        details = groups.setdefault(filename, [])

        if page_number is not None:
            details.append(f"Page {page_number}")
        else:
            details.append(f"Chunk {chunk_index}")

    return [(filename, details) for filename, details in groups.items()]


class PersonaDialoguePanel(QFrame):
    """Keep one Persona response, avatar, state, and citations together."""

    def __init__(
        self,
        persona,
        theme,
        state=PersonaState.IDLE,
        text="",
        sources=None,
        parent=None,
    ):
        super().__init__(parent)
        self.persona = dict(persona)
        self.persona_id = self.persona["id"]
        self.theme = dict(theme)
        self.state = PersonaState.IDLE
        self.state_history = []
        self._text = text
        self.source_groups = []
        self._appearance_started = False

        self.setObjectName("personaDialoguePanel")
        self.setMinimumWidth(500)
        self.setMaximumWidth(820)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        self._build_ui()
        self._apply_panel_style()
        self.set_state(state)

        if text:
            self.response_label.setText(text)
        if sources:
            self.set_sources(sources)

    def _build_ui(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(18, 16, 20, 18)
        root.setSpacing(16)

        self.avatar = PersonaAvatarWidget(self.persona_id, self.theme)
        root.addWidget(self.avatar, 0, Qt.AlignmentFlag.AlignTop)

        content = QVBoxLayout()
        content.setSpacing(10)
        header = QHBoxLayout()
        header.setSpacing(12)
        self.persona_label = QLabel(self.persona["display_name"].upper())
        self.persona_label.setObjectName("dialoguePersonaName")
        self.status_label = QLabel()
        self.status_label.setObjectName("dialogueStatus")
        header.addWidget(self.persona_label)
        header.addStretch(1)
        header.addWidget(self.status_label)
        content.addLayout(header)

        self.response_label = QLabel(self._text)
        self.response_label.setObjectName("dialogueResponse")
        self.response_label.setWordWrap(True)
        self.response_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        content.addWidget(self.response_label)

        self.sources_section = QWidget()
        self.sources_section.setObjectName("dialogueSourcesSection")
        self.sources_layout = QVBoxLayout(self.sources_section)
        self.sources_layout.setContentsMargins(0, 8, 0, 0)
        self.sources_layout.setSpacing(7)
        self.sources_section.hide()
        content.addWidget(self.sources_section)
        root.addLayout(content, 1)

    def _apply_panel_style(self):
        radius = self.theme["card_radius"]
        source_radius = self.theme["radius"]
        self.setStyleSheet(
            f"""
            #personaDialoguePanel {{
                background: {self.theme['surface']};
                border: 1px solid {self.theme['border']};
                border-left: 3px solid {self.theme['accent']};
                border-radius: {radius}px;
            }}
            #personaDialoguePanel[error="true"] {{
                border-left-color: {self.theme['error']};
            }}
            #dialoguePersonaName {{
                color: {self.theme['accent_bright']};
                font-size: 13px;
                font-weight: 750;
                letter-spacing: 2px;
            }}
            #dialogueStatus {{
                color: {self.theme['status_text']};
                font-size: 10px;
                font-weight: 700;
                letter-spacing: 1px;
            }}
            #dialogueStatus[error="true"] {{ color: {self.theme['error']}; }}
            #dialogueResponse {{
                color: {self.theme['text']};
                font-size: 14px;
                line-height: 1.4;
            }}
            #dialogueSourcesTitle {{
                color: {self.theme['accent_bright']};
                font-size: 10px;
                font-weight: 750;
                letter-spacing: 1px;
            }}
            #dialogueSourceCard {{
                background: {self.theme['surface_alt']};
                border-left: 2px solid {self.theme['accent']};
                border-radius: {source_radius}px;
            }}
            #dialogueSourceName {{
                color: {self.theme['text']};
                font-size: 12px;
                font-weight: 650;
            }}
            #dialogueSourceDetails {{
                color: {self.theme['muted']};
                font-size: 11px;
            }}
            """
        )

    def showEvent(self, event):
        super().showEvent(event)

        if not self._appearance_started:
            self._appearance_started = True
            self._start_appearance_animation()

    def _start_appearance_animation(self):
        """Fade in and rise a few pixels using lightweight Qt animation."""
        end_position = self.pos()
        start_position = end_position + QPoint(0, 6)
        self.move(start_position)
        self.opacity_effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self.opacity_effect)
        self.opacity_animation = QPropertyAnimation(
            self.opacity_effect,
            b"opacity",
            self,
        )
        self.opacity_animation.setDuration(200)
        self.opacity_animation.setStartValue(0.12)
        self.opacity_animation.setEndValue(1.0)
        self.opacity_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.position_animation = QPropertyAnimation(self, b"pos", self)
        self.position_animation.setDuration(200)
        self.position_animation.setStartValue(start_position)
        self.position_animation.setEndValue(end_position)
        self.position_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.appearance_animation = QParallelAnimationGroup(self)
        self.appearance_animation.addAnimation(self.opacity_animation)
        self.appearance_animation.addAnimation(self.position_animation)
        self.appearance_animation.start()

    def set_avatar_animation_enabled(self, enabled):
        """Delegate continuous-effect ownership to this panel's avatar."""
        self.avatar.set_continuous_animation_enabled(enabled)

    def set_avatar_animation_mode(self, mode):
        """Select this panel's working, standby, or historical avatar motion."""
        self.avatar.set_animation_mode(mode)

    def set_state(self, state):
        """Update status and avatar state while keeping the same panel instance."""
        normalized_state = state if isinstance(state, PersonaState) else PersonaState(state)
        self.state = normalized_state
        self.state_history.append(normalized_state)
        status_map = STATUS_TEXT.get(self.persona_id, STATUS_TEXT["neutral"])
        self.status_label.setText(status_map[normalized_state])
        is_error = normalized_state is PersonaState.ERROR
        self.setProperty("error", is_error)
        self.status_label.setProperty("error", is_error)
        self.style().unpolish(self)
        self.style().polish(self)
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)
        self.avatar.set_state(normalized_state)

    def append_text(self, text):
        """Append one streamed token without rebuilding the dialogue widget."""
        if self.state not in {
            PersonaState.RESPONDING,
            PersonaState.SPEAKING,
            PersonaState.ERROR,
        }:
            self.set_state(PersonaState.RESPONDING)

        self._text += text
        self.response_label.setText(self._text)

    def set_sources(self, source_text):
        """Add source cards inside this same historical response panel."""
        while self.sources_layout.count():
            item = self.sources_layout.takeAt(0)
            widget = item.widget()

            if widget:
                widget.deleteLater()

        self.source_groups = parse_source_groups(source_text)
        title_text = "SOURCE INDEX" if self.persona_id == "delamain" else "Sources"
        title = QLabel(title_text)
        title.setObjectName("dialogueSourcesTitle")
        self.sources_layout.addWidget(title)

        for filename, details in self.source_groups:
            card = QFrame()
            card.setObjectName("dialogueSourceCard")
            card_layout = QHBoxLayout(card)
            card_layout.setContentsMargins(11, 7, 11, 7)
            name_label = QLabel(filename)
            name_label.setObjectName("dialogueSourceName")
            detail_label = QLabel("  ·  ".join(details))
            detail_label.setObjectName("dialogueSourceDetails")
            card_layout.addWidget(name_label)
            card_layout.addStretch(1)
            card_layout.addWidget(detail_label)
            self.sources_layout.addWidget(card)

        self.sources_section.setVisible(bool(self.source_groups))

    def complete(self, keep_idle_animation=False):
        """Mark a successfully streamed panel complete and keep it in history."""
        self.set_state(PersonaState.COMPLETE)

        if keep_idle_animation and self.persona_id == "fairy":
            self.avatar.set_animation_mode(AvatarAnimationMode.IDLE_BREATHING)

    def set_error(self, message):
        """Show a concise failure in this panel instead of a Python traceback."""
        prefix = "\n\n" if self._text else ""
        self._text += f"{prefix}错误：{message}"
        self.response_label.setText(self._text)
        self.set_state(PersonaState.ERROR)
