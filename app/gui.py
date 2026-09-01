"""PySide6 desktop interface for the local Private Personal AI."""

import sys
import time
from pathlib import Path

from PySide6.QtCore import QLineF, QPointF, QRectF, QObject, QThread, Qt, Signal, Slot
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

try:
    from .ai_service import (
        check_ollama,
        import_document,
        reindex_library_document,
        stream_knowledge_chat,
        stream_normal_chat,
    )
    from .knowledge_library import delete_document, list_documents
    from .language_preferences import get_language_preference, infer_response_language
    from .personas import get_active_persona, list_personas, switch_persona
    from .ui import (
        AvatarAnimationMode,
        PersonaAvatarWidget,
        PersonaDialoguePanel,
        PersonaState,
    )
    from .ui_themes import build_stylesheet, get_theme
    from .voice.audio_player import LocalAudioPlayer
    from .voice.recorder import MicrophoneRecorder, list_audio_devices
    from .voice.settings import (
        get_persona_voice_profile,
        get_voice_settings,
        list_installed_voice_ids,
        save_voice_settings,
    )
    from .voice.stt import LocalSpeechToText
    from .voice.streaming import StreamingSentenceSegmenter, ThreadedSpeechQueue
    from .voice.tts import LocalPiperTextToSpeech
except ImportError:
    from ai_service import (
        check_ollama,
        import_document,
        reindex_library_document,
        stream_knowledge_chat,
        stream_normal_chat,
    )
    from knowledge_library import delete_document, list_documents
    from language_preferences import get_language_preference, infer_response_language
    from personas import get_active_persona, list_personas, switch_persona
    from ui import (
        AvatarAnimationMode,
        PersonaAvatarWidget,
        PersonaDialoguePanel,
        PersonaState,
    )
    from ui_themes import build_stylesheet, get_theme
    from voice.audio_player import LocalAudioPlayer
    from voice.recorder import MicrophoneRecorder, list_audio_devices
    from voice.settings import (
        get_persona_voice_profile,
        get_voice_settings,
        list_installed_voice_ids,
        save_voice_settings,
    )
    from voice.stt import LocalSpeechToText
    from voice.streaming import StreamingSentenceSegmenter, ThreadedSpeechQueue
    from voice.tts import LocalPiperTextToSpeech


APP_TITLE = "Private Personal AI"
DOCUMENT_FILTER = "Documents (*.txt *.md *.pdf)"


class BackgroundWorker(QObject):
    """Run one blocking local operation outside the Qt GUI thread."""

    token = Signal(str)
    progress = Signal(str)
    state_changed = Signal(str)
    succeeded = Signal(object)
    failed = Signal(str)
    finished = Signal()

    def __init__(
        self,
        operation,
        *args,
        use_token_callback=False,
        use_progress_callback=False,
        use_state_callback=False,
        **kwargs,
    ):
        super().__init__()
        self.operation = operation
        self.args = args
        self.kwargs = kwargs
        self.use_token_callback = use_token_callback
        self.use_progress_callback = use_progress_callback
        self.use_state_callback = use_state_callback

    @Slot()
    def run(self):
        try:
            callback_kwargs = dict(self.kwargs)

            if self.use_token_callback:
                callback_kwargs["on_token"] = self.token.emit

            if self.use_progress_callback:
                callback_kwargs["on_progress"] = self.progress.emit

            if self.use_state_callback:
                callback_kwargs["on_state"] = self.state_changed.emit

            result = self.operation(*self.args, **callback_kwargs)
            self.succeeded.emit(result)
        except Exception as error:
            self.failed.emit(str(error))
        finally:
            self.finished.emit()


class SpeechQueueBridge(QObject):
    """Deliver Python speech-thread callbacks safely to the Qt GUI thread."""

    activity_changed = Signal(int, bool)
    speech_started = Signal(int, int, str, float, str)
    warning = Signal(int, str)
    drained = Signal(int)


class AIMessageWidget(QFrame):
    """Display one message bubble that can receive streamed text."""

    def __init__(self, role, title, text="", parent=None):
        super().__init__(parent)
        self.role = role
        self.setObjectName("userBubble" if role == "user" else "aiBubble")
        self.setMinimumWidth(260)
        self.setMaximumWidth(740)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        self._text = text

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(6)

        heading = QLabel(title)
        heading.setObjectName("bubbleTitle")
        body = QLabel(text)
        body.setObjectName("bubbleBody")
        body.setWordWrap(True)
        body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(heading)
        layout.addWidget(body)
        self.body = body

    def append_text(self, text):
        self._text += text
        self.body.setText(self._text)


class MessageRow(QWidget):
    """Align user cards right and assistant cards left without filling the row."""

    def __init__(self, message, role, parent=None):
        super().__init__(parent)
        self.role = role
        self.message = message
        self.setObjectName("messageRow")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.setSpacing(0)

        if role == "user":
            layout.addStretch(1)
            layout.addWidget(message, 0, Qt.AlignmentFlag.AlignRight)
        else:
            layout.addWidget(message, 0, Qt.AlignmentFlag.AlignLeft)
            layout.addStretch(1)


class PersonaIdleWidget(QWidget):
    """Paint a lightweight, static idle visualization for the active Persona."""

    def __init__(self, theme, parent=None):
        super().__init__(parent)
        self.theme = theme
        self.setMinimumHeight(190)
        self.setMaximumHeight(250)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def set_theme(self, theme):
        self.theme = theme
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        accent = QColor(self.theme["accent"])
        muted = QColor(self.theme["muted"])
        center = QPointF(self.width() / 2, self.height() / 2 - 8)
        kind = self.theme["idle_kind"]

        accent.setAlpha(115)
        painter.setPen(QPen(accent, 1.4))

        if kind == "geometry":
            for size in (48, 74, 104):
                painter.drawRect(
                    QRectF(center.x() - size / 2, center.y() - size / 2, size, size)
                )
            painter.drawLine(QLineF(center.x() - 70, center.y(), center.x() + 70, center.y()))
            painter.drawLine(QLineF(center.x(), center.y() - 70, center.x(), center.y() + 70))
        elif kind == "waves":
            for offset, span in ((0, 210), (12, 190), (24, 165)):
                painter.drawArc(
                    QRectF(
                        center.x() - 68 + offset / 2,
                        center.y() - 40 + offset / 3,
                        136 - offset,
                        80 - offset / 2,
                    ),
                    -15 * 16,
                    span * 16,
                )
            for x_offset in (-72, -48, 48, 72):
                painter.drawEllipse(QPointF(center.x() + x_offset, center.y()), 2.2, 2.2)
        else:
            painter.drawEllipse(QRectF(center.x() - 29, center.y() - 29, 58, 58))
            painter.drawLine(QLineF(center.x() - 62, center.y(), center.x() - 36, center.y()))
            painter.drawLine(QLineF(center.x() + 36, center.y(), center.x() + 62, center.y()))

        muted.setAlpha(150)
        painter.setPen(QPen(muted, 1))
        label_rect = QRectF(0, center.y() + 75, self.width(), 24)
        painter.drawText(
            label_rect,
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
            self.theme["idle_label"],
        )


class MainWindow(QMainWindow):
    """Desktop shell for chat, knowledge, persona, and settings pages."""

    def __init__(self):
        super().__init__()
        self.active_persona = get_active_persona(reload=True)
        self.active_language = get_language_preference(reload=True)
        self.voice_settings = get_voice_settings()
        self.current_theme = get_theme(self.active_persona["id"])
        self.documents = []
        self.worker_threads = set()
        self.current_ai_panel = None
        self.latest_completed_fairy_panel = None
        self.current_chat_mode = None
        self.user_message_count = 0
        self.voice_recorder = None
        self.stt_engine = None
        self.tts_engine = None
        self.audio_player = None
        self.voice_capture_panel = None
        self.voice_capture_row = None
        self.voice_playback_panel = None
        self.voice_operation_id = 0
        self.speech_queue = None
        self.streaming_speech_context = None
        self.first_speech_latencies_ms = []
        self._measured_speech_sessions = set()
        self._voice_warning_session = None
        self._updating_voice_controls = False
        self.chat_busy = False
        self.voice_transcribing = False
        self.speech_queue_bridge = SpeechQueueBridge(self)
        self.speech_queue_bridge.activity_changed.connect(
            self._speech_activity_changed
        )
        self.speech_queue_bridge.speech_started.connect(self._speech_started)
        self.speech_queue_bridge.warning.connect(self._speech_warning)
        self.speech_queue_bridge.drained.connect(self._speech_queue_drained)

        self.setWindowTitle(APP_TITLE)
        self.setFont(QFont("Microsoft YaHei UI", 10))
        self.resize(1240, 820)
        self.setMinimumSize(980, 680)
        self._build_ui()
        self._sync_voice_controls()
        self._update_persona_display(add_greeting=True)
        self.refresh_library()
        self._run_health_check()

        if self.voice_settings["enabled"]:
            self._refresh_audio_devices()

    def _build_ui(self):
        root = QWidget()
        root.setObjectName("applicationRoot")
        root_layout = QHBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        root_layout.addWidget(self._build_sidebar())

        self.pages = QStackedWidget()
        self.pages.addWidget(self._build_chat_page())
        self.pages.addWidget(self._build_knowledge_page())
        self.pages.addWidget(self._build_persona_page())
        self.pages.addWidget(self._build_settings_page())
        root_layout.addWidget(self.pages, 1)
        self.setCentralWidget(root)

    def _build_sidebar(self):
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(220)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(22, 26, 22, 22)
        layout.setSpacing(10)

        brand = QLabel("PRIVATE AI")
        brand.setObjectName("brand")
        subtitle = QLabel("LOCAL SECOND BRAIN")
        subtitle.setObjectName("brandSubtitle")
        layout.addWidget(brand)
        layout.addWidget(subtitle)
        layout.addSpacing(26)

        self.navigation_group = QButtonGroup(self)
        self.navigation_group.setExclusive(True)

        for index, label in enumerate(("Chat", "Knowledge", "Persona", "Settings")):
            button = QPushButton(label)
            button.setCheckable(True)
            button.setObjectName("navButton")
            button.clicked.connect(lambda checked=False, page=index: self._show_page(page))
            self.navigation_group.addButton(button)
            layout.addWidget(button)

            if index == 0:
                button.setChecked(True)

        layout.addStretch()

        status_title = QLabel("SYSTEM STATUS")
        status_title.setObjectName("sectionEyebrow")
        layout.addWidget(status_title)

        badges = QVBoxLayout()
        badges.setSpacing(6)
        self.local_badge = QLabel("LOCAL")
        self.private_badge = QLabel("PRIVATE")
        self.gpu_badge = QLabel("GPU: CONFIGURED")

        for badge in (self.local_badge, self.private_badge, self.gpu_badge):
            badge.setObjectName("statusBadge")
            badges.addWidget(badge)

        layout.addLayout(badges)
        self.ollama_status = QLabel("正在检查 Ollama…")
        self.ollama_status.setObjectName("mutedLabel")
        self.ollama_status.setWordWrap(True)
        layout.addWidget(self.ollama_status)
        return sidebar

    def _build_chat_page(self):
        page = QWidget()
        page.setObjectName("pageRoot")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(34, 28, 34, 28)
        layout.setSpacing(18)

        header = QHBoxLayout()
        title_box = QVBoxLayout()
        title = QLabel("对话")
        title.setObjectName("pageTitle")
        self.chat_persona_label = QLabel()
        self.chat_persona_label.setObjectName("mutedLabel")
        title_box.addWidget(title)
        title_box.addWidget(self.chat_persona_label)
        header.addLayout(title_box)
        header.addStretch()

        self.chat_mode = QComboBox()
        self.chat_mode.addItem("普通对话", "normal")
        self.chat_mode.addItem("知识库对话（RAG）", "rag")
        self.chat_mode.setMinimumWidth(190)
        header.addWidget(self.chat_mode)
        layout.addLayout(header)

        self.identity_panel = QFrame()
        self.identity_panel.setObjectName("identityPanel")
        identity_layout = QHBoxLayout(self.identity_panel)
        identity_layout.setContentsMargins(18, 12, 18, 12)
        identity_layout.setSpacing(10)
        self.identity_avatar = PersonaAvatarWidget(
            self.active_persona["id"],
            self.current_theme,
            display_size=62,
            animation_enabled=False,
        )
        self.identity_avatar.set_state(PersonaState.COMPLETE)
        identity_layout.addWidget(self.identity_avatar)
        identity_text = QVBoxLayout()
        identity_text.setSpacing(2)
        self.identity_name = QLabel()
        self.identity_name.setObjectName("identityName")
        self.identity_status = QLabel()
        self.identity_status.setObjectName("identityStatus")
        identity_text.addWidget(self.identity_name)
        identity_text.addWidget(self.identity_status)
        identity_layout.addLayout(identity_text)
        identity_layout.addStretch()
        identity_privacy = QLabel("PRIVATE  /  ON-DEVICE")
        identity_privacy.setObjectName("identityStatus")
        identity_layout.addWidget(identity_privacy)
        self.voice_master_button = QPushButton()
        self.voice_master_button.setObjectName("voiceStatusButton")
        self.voice_master_button.setCheckable(True)
        self.voice_master_button.clicked.connect(self._voice_master_clicked)
        identity_layout.addWidget(self.voice_master_button)
        layout.addWidget(self.identity_panel)

        self.conversation_widget = QWidget()
        self.conversation_widget.setObjectName("conversationCanvas")
        self.conversation_layout = QVBoxLayout(self.conversation_widget)
        self.conversation_layout.setContentsMargins(14, 14, 14, 14)
        self.conversation_layout.setSpacing(12)

        self.message_container = QWidget()
        self.message_container.setObjectName("conversationCanvas")
        self.messages_layout = QVBoxLayout(self.message_container)
        self.messages_layout.setContentsMargins(0, 0, 0, 0)
        self.messages_layout.setSpacing(12)
        self.conversation_layout.addWidget(self.message_container)
        self.conversation_layout.addStretch(1)
        self.idle_state = PersonaIdleWidget(self.current_theme)
        self.conversation_layout.addWidget(self.idle_state)
        self.conversation_layout.addStretch(1)

        self.conversation_scroll = QScrollArea()
        self.conversation_scroll.setWidgetResizable(True)
        self.conversation_scroll.setWidget(self.conversation_widget)
        self.conversation_scroll.setObjectName("conversationArea")
        self.conversation_scroll.viewport().setObjectName("conversationViewport")
        layout.addWidget(self.conversation_scroll, 1)

        input_row = QHBoxLayout()
        self.message_input = QPlainTextEdit()
        self.message_input.setPlaceholderText("输入消息。Ctrl+Enter 发送。")
        self.message_input.setFixedHeight(82)
        self.message_input.installEventFilter(self)
        self.send_button = QPushButton("发送")
        self.send_button.setObjectName("primaryButton")
        self.send_button.setFixedSize(104, 46)
        self.send_button.clicked.connect(self.send_message)
        input_row.addWidget(self.message_input, 1)
        voice_buttons = QVBoxLayout()
        voice_buttons.setSpacing(6)
        self.recording_indicator = QLabel("● 正在聆听")
        self.recording_indicator.setObjectName("recordingIndicator")
        self.recording_indicator.hide()
        self.microphone_button = QPushButton("麦克风")
        self.microphone_button.setObjectName("voiceActionButton")
        self.microphone_button.setFixedSize(104, 34)
        self.microphone_button.clicked.connect(self.toggle_microphone_recording)
        self.stop_playback_button = QPushButton("■ 停止语音")
        self.stop_playback_button.setObjectName("voiceActionButton")
        self.stop_playback_button.setFixedSize(104, 34)
        self.stop_playback_button.clicked.connect(self.stop_voice_playback)
        self.stop_playback_button.hide()
        voice_buttons.addWidget(self.recording_indicator)
        voice_buttons.addWidget(self.microphone_button)
        voice_buttons.addWidget(self.stop_playback_button)
        input_row.addLayout(voice_buttons)
        input_row.addWidget(self.send_button, alignment=Qt.AlignmentFlag.AlignBottom)
        layout.addLayout(input_row)
        self.voice_notice = QLabel("")
        self.voice_notice.setObjectName("voiceNotice")
        self.voice_notice.setWordWrap(True)
        self.voice_notice.hide()
        layout.addWidget(self.voice_notice)
        return page

    def _build_knowledge_page(self):
        page = QWidget()
        page.setObjectName("pageRoot")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(34, 28, 34, 28)
        layout.setSpacing(18)

        title = QLabel("知识库")
        title.setObjectName("pageTitle")
        subtitle = QLabel("所有文档、Embedding 和 ChromaDB 数据均保留在本机。")
        subtitle.setObjectName("mutedLabel")
        layout.addWidget(title)
        layout.addWidget(subtitle)

        buttons = QHBoxLayout()
        self.import_button = QPushButton("导入文档")
        self.delete_button = QPushButton("删除文档")
        self.reindex_button = QPushButton("重新索引")
        self.refresh_button = QPushButton("刷新")
        self.import_button.setObjectName("primaryButton")

        self.import_button.clicked.connect(self.choose_import_file)
        self.delete_button.clicked.connect(self.delete_selected_document)
        self.reindex_button.clicked.connect(self.reindex_selected_document)
        self.refresh_button.clicked.connect(self.refresh_library)

        for button in (
            self.import_button,
            self.delete_button,
            self.reindex_button,
            self.refresh_button,
        ):
            buttons.addWidget(button)

        buttons.addStretch()
        layout.addLayout(buttons)

        self.knowledge_table = QTableWidget(0, 5)
        self.knowledge_table.setHorizontalHeaderLabels(
            ["文件名", "类型", "Chunk 数", "PDF 页数", "源路径"]
        )
        self.knowledge_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        self.knowledge_table.setSelectionMode(
            QTableWidget.SelectionMode.SingleSelection
        )
        self.knowledge_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.knowledge_table.verticalHeader().setVisible(False)
        header = self.knowledge_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.knowledge_table, 1)

        self.knowledge_status = QLabel("")
        self.knowledge_status.setObjectName("mutedLabel")
        layout.addWidget(self.knowledge_status)
        return page

    def _build_persona_page(self):
        page = QWidget()
        page.setObjectName("pageRoot")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(34, 28, 34, 28)
        layout.setSpacing(18)

        title = QLabel("Persona")
        title.setObjectName("pageTitle")
        subtitle = QLabel("切换表达风格不会更改或重置知识库。")
        subtitle.setObjectName("mutedLabel")
        layout.addWidget(title)
        layout.addWidget(subtitle)

        self.persona_group = QButtonGroup(self)
        self.persona_group.setExclusive(True)
        self.persona_buttons = {}
        self.persona_avatar_widgets = {}

        for persona in list_personas():
            card = QFrame()
            card.setObjectName("personaCard")
            card_layout = QHBoxLayout(card)
            card_layout.setContentsMargins(20, 18, 20, 18)
            card_layout.setSpacing(16)
            avatar = PersonaAvatarWidget(
                persona["id"],
                get_theme(persona["id"]),
                display_size=78,
                animation_enabled=False,
            )
            avatar.set_state(PersonaState.COMPLETE)
            card_layout.addWidget(avatar, 0, Qt.AlignmentFlag.AlignTop)
            content_layout = QVBoxLayout()
            content_layout.setSpacing(7)
            radio = QRadioButton(persona["display_name"])
            radio.setProperty("persona_id", persona["id"])
            description = QLabel(persona["style_description"])
            description.setWordWrap(True)
            description.setObjectName("mutedLabel")
            greeting = QLabel(f"问候：{persona['greeting']}")
            greeting.setWordWrap(True)
            content_layout.addWidget(radio)
            content_layout.addWidget(description)
            content_layout.addWidget(greeting)
            card_layout.addLayout(content_layout, 1)
            self.persona_group.addButton(radio)
            self.persona_buttons[persona["id"]] = radio
            self.persona_avatar_widgets[persona["id"]] = avatar
            radio.toggled.connect(
                lambda checked, persona_id=persona["id"]: (
                    self.change_persona(persona_id) if checked else None
                )
            )
            layout.addWidget(card)

        layout.addStretch()
        return page

    def _build_settings_page(self):
        page = QWidget()
        page.setObjectName("pageRoot")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(34, 28, 34, 28)
        layout.setSpacing(16)

        title = QLabel("设置")
        title.setObjectName("pageTitle")
        layout.addWidget(title)

        self.settings_persona = QLabel()
        self.settings_language = QLabel(
            f"默认语言：{self.active_language['display_name']}"
        )
        privacy = QLabel(
            "隐私：Ollama、Embedding、ChromaDB、文档解析和 Persona 配置均在本机运行。"
        )
        gpu = QLabel(
            "GPU：已按现有项目环境配置。桌面 UI 本身不动态检测 GPU 状态。"
        )

        for label in (self.settings_persona, self.settings_language, privacy, gpu):
            label.setWordWrap(True)
            label.setObjectName("settingsLine")
            layout.addWidget(label)

        voice_section = QFrame()
        voice_section.setObjectName("settingsSection")
        voice_layout = QVBoxLayout(voice_section)
        voice_layout.setContentsMargins(18, 16, 18, 18)
        voice_layout.setSpacing(12)
        voice_title = QLabel("本地语音")
        voice_title.setObjectName("sectionTitle")
        voice_description = QLabel(
            "默认关闭。仅在点击麦克风后录音；STT、Piper TTS 与音频均在本机处理。"
        )
        voice_description.setObjectName("mutedLabel")
        voice_description.setWordWrap(True)
        voice_layout.addWidget(voice_title)
        voice_layout.addWidget(voice_description)

        toggle_grid = QGridLayout()
        toggle_grid.setHorizontalSpacing(24)
        toggle_grid.setVerticalSpacing(9)
        self.voice_enabled_checkbox = QCheckBox("语音模式")
        self.voice_input_checkbox = QCheckBox("语音输入")
        self.voice_output_checkbox = QCheckBox("AI 语音回答")
        self.voice_autoplay_checkbox = QCheckBox("自动播放 AI 回答")
        toggle_grid.addWidget(self.voice_enabled_checkbox, 0, 0)
        toggle_grid.addWidget(self.voice_input_checkbox, 0, 1)
        toggle_grid.addWidget(self.voice_output_checkbox, 1, 0)
        toggle_grid.addWidget(self.voice_autoplay_checkbox, 1, 1)
        voice_layout.addLayout(toggle_grid)

        recording_mode = QLabel("录音方式：点击开始 / 再次点击结束")
        recording_mode.setObjectName("mutedLabel")
        voice_layout.addWidget(recording_mode)

        device_grid = QGridLayout()
        device_grid.setHorizontalSpacing(14)
        device_grid.setVerticalSpacing(8)
        self.microphone_device_combo = QComboBox()
        self.speaker_device_combo = QComboBox()
        self.stt_model_combo = QComboBox()
        self.stt_model_combo.setEditable(True)
        self.stt_model_combo.addItems(("tiny", "base", "small"))
        device_grid.addWidget(QLabel("麦克风"), 0, 0)
        device_grid.addWidget(self.microphone_device_combo, 0, 1)
        device_grid.addWidget(QLabel("扬声器"), 1, 0)
        device_grid.addWidget(self.speaker_device_combo, 1, 1)
        device_grid.addWidget(QLabel("STT 模型"), 2, 0)
        device_grid.addWidget(self.stt_model_combo, 2, 1)
        voice_layout.addLayout(device_grid)

        model_note = QLabel(
            "Persona Piper Voice（可输入 data/voice/models 中的本地模型 ID）"
        )
        model_note.setObjectName("mutedLabel")
        voice_layout.addWidget(model_note)
        self.persona_voice_combos = {}
        voice_grid = QGridLayout()
        voice_grid.setHorizontalSpacing(12)
        voice_grid.setVerticalSpacing(7)
        voice_grid.addWidget(QLabel("Persona"), 0, 0)
        voice_grid.addWidget(QLabel("中文 Voice"), 0, 1)
        voice_grid.addWidget(QLabel("English Voice"), 0, 2)

        installed_voice_ids = list_installed_voice_ids()

        for row, persona_id in enumerate(("delamain", "fairy", "neutral"), start=1):
            profile = self.voice_settings["persona_voices"][persona_id]
            chinese_combo = self._build_voice_model_combo(
                installed_voice_ids,
                profile["zh-CN"],
            )
            english_combo = self._build_voice_model_combo(
                installed_voice_ids,
                profile["en"],
            )
            self.persona_voice_combos[persona_id] = {
                "zh-CN": chinese_combo,
                "en": english_combo,
            }
            voice_grid.addWidget(QLabel(persona_id.title()), row, 0)
            voice_grid.addWidget(chinese_combo, row, 1)
            voice_grid.addWidget(english_combo, row, 2)

        voice_layout.addLayout(voice_grid)
        layout.addWidget(voice_section)

        for checkbox in (
            self.voice_enabled_checkbox,
            self.voice_input_checkbox,
            self.voice_output_checkbox,
            self.voice_autoplay_checkbox,
        ):
            checkbox.toggled.connect(self._voice_settings_changed)

        self.microphone_device_combo.currentIndexChanged.connect(
            self._voice_settings_changed
        )
        self.speaker_device_combo.currentIndexChanged.connect(
            self._voice_settings_changed
        )
        self.stt_model_combo.currentTextChanged.connect(self._voice_settings_changed)

        for language_combos in self.persona_voice_combos.values():
            for combo in language_combos.values():
                combo.currentTextChanged.connect(self._voice_settings_changed)

        layout.addStretch()
        return page

    def _build_voice_model_combo(self, installed_voice_ids, selected_voice_id):
        """Create one editable local Piper model selector."""
        combo = QComboBox()
        combo.setEditable(True)
        values = list(installed_voice_ids)

        if selected_voice_id not in values:
            values.insert(0, selected_voice_id)

        combo.addItems(values)
        combo.setCurrentText(selected_voice_id)
        return combo

    def _voice_master_clicked(self, checked):
        """Persist the compact global switch without implying active recording."""
        self.voice_enabled_checkbox.setChecked(bool(checked))

    def _voice_settings_changed(self, *unused_arguments):
        """Persist all voice controls and enforce disabled-resource boundaries."""
        del unused_arguments

        if self._updating_voice_controls:
            return

        previous_settings = self.voice_settings
        updated = dict(previous_settings)
        updated["enabled"] = self.voice_enabled_checkbox.isChecked()
        updated["input_enabled"] = self.voice_input_checkbox.isChecked()
        updated["output_enabled"] = self.voice_output_checkbox.isChecked()
        updated["auto_playback"] = self.voice_autoplay_checkbox.isChecked()
        updated["microphone_device"] = self.microphone_device_combo.currentData()
        updated["speaker_device"] = self.speaker_device_combo.currentData()
        updated["stt_model"] = self.stt_model_combo.currentText().strip() or "tiny"
        updated["persona_voices"] = {
            persona_id: {
                **previous_settings["persona_voices"][persona_id],
                "zh-CN": combos["zh-CN"].currentText().strip(),
                "en": combos["en"].currentText().strip(),
            }
            for persona_id, combos in self.persona_voice_combos.items()
        }

        try:
            self.voice_settings = save_voice_settings(updated)
        except OSError as error:
            self._show_error(str(error))
            self.voice_settings = previous_settings
            self._sync_voice_controls()
            return

        if previous_settings["stt_model"] != self.voice_settings["stt_model"]:
            if self.stt_engine:
                self.stt_engine.unload()
            self.stt_engine = None

        if not self.voice_settings["enabled"]:
            self._release_voice_resources()
        else:
            if not self.voice_settings["input_enabled"]:
                self._cancel_microphone_recording()
            if not (
                self.voice_settings["output_enabled"]
                and self.voice_settings["auto_playback"]
            ):
                self.stop_voice_playback()

        self._sync_voice_controls()

        if (
            self.voice_settings["enabled"]
            and not previous_settings["enabled"]
        ):
            self._refresh_audio_devices()

    def _sync_voice_controls(self):
        """Make the compact switch, Settings controls, and actions agree."""
        self._updating_voice_controls = True

        try:
            enabled = self.voice_settings["enabled"]
            self.voice_master_button.setChecked(enabled)
            self.voice_master_button.setText("VOICE · ON" if enabled else "VOICE · OFF")
            self.voice_master_button.setProperty("voiceEnabled", enabled)
            self.voice_master_button.style().unpolish(self.voice_master_button)
            self.voice_master_button.style().polish(self.voice_master_button)
            self.voice_enabled_checkbox.setChecked(enabled)
            self.voice_input_checkbox.setChecked(
                self.voice_settings["input_enabled"]
            )
            self.voice_output_checkbox.setChecked(
                self.voice_settings["output_enabled"]
            )
            self.voice_autoplay_checkbox.setChecked(
                self.voice_settings["auto_playback"]
            )
            self._ensure_device_selection(
                self.microphone_device_combo,
                self.voice_settings["microphone_device"],
            )
            self._ensure_device_selection(
                self.speaker_device_combo,
                self.voice_settings["speaker_device"],
            )
            self.stt_model_combo.setCurrentText(self.voice_settings["stt_model"])
            self.voice_input_checkbox.setEnabled(enabled)
            self.voice_output_checkbox.setEnabled(enabled)
            self.voice_autoplay_checkbox.setEnabled(
                enabled and self.voice_settings["output_enabled"]
            )
            self.microphone_device_combo.setEnabled(
                enabled and self.voice_settings["input_enabled"]
            )
            self.speaker_device_combo.setEnabled(
                enabled and self.voice_settings["output_enabled"]
            )
            self.stt_model_combo.setEnabled(
                enabled and self.voice_settings["input_enabled"]
            )

            for combos in self.persona_voice_combos.values():
                for combo in combos.values():
                    combo.setEnabled(enabled and self.voice_settings["output_enabled"])
        finally:
            self._updating_voice_controls = False

        self._update_voice_action_availability()

    def _ensure_device_selection(self, combo, selected_device):
        """Keep system default and a saved device visible before enumeration."""
        if combo.findData(None) < 0:
            combo.insertItem(0, "系统默认", None)

        index = combo.findData(selected_device)

        if index < 0 and selected_device is not None:
            combo.addItem(f"已保存设备 #{selected_device}", selected_device)
            index = combo.count() - 1

        combo.setCurrentIndex(max(0, index))

    def _refresh_audio_devices(self):
        """Enumerate local devices in a worker only after voice is enabled."""
        if not self.voice_settings["enabled"]:
            return

        self._set_voice_notice("正在读取本机音频设备…")
        self._run_worker(
            list_audio_devices,
            on_success=self._audio_devices_loaded,
            on_error=lambda message: self._set_voice_notice(
                f"音频设备不可用：{message}",
                is_error=True,
            ),
        )

    def _audio_devices_loaded(self, device_lists):
        """Populate microphone and speaker selectors without opening either."""
        input_devices, output_devices = device_lists
        self._updating_voice_controls = True

        try:
            selections = (
                (
                    self.microphone_device_combo,
                    input_devices,
                    self.voice_settings["microphone_device"],
                ),
                (
                    self.speaker_device_combo,
                    output_devices,
                    self.voice_settings["speaker_device"],
                ),
            )

            for combo, devices, selected_device in selections:
                combo.clear()
                combo.addItem("系统默认", None)

                for device_index, device_name in devices:
                    combo.addItem(device_name, device_index)

                self._ensure_device_selection(combo, selected_device)
        finally:
            self._updating_voice_controls = False

        self._set_voice_notice(
            f"已检测到 {len(input_devices)} 个输入设备、"
            f"{len(output_devices)} 个输出设备。"
        )

    def _set_voice_notice(self, message="", is_error=False):
        """Show an optional voice-layer message without failing text chat."""
        self.voice_notice.setText(str(message))
        self.voice_notice.setProperty("error", bool(is_error))
        self.voice_notice.style().unpolish(self.voice_notice)
        self.voice_notice.style().polish(self.voice_notice)
        self.voice_notice.setVisible(bool(message))

    def _update_voice_action_availability(self):
        """Enable recording only when the explicit voice/input gates permit it."""
        recording = bool(self.voice_recorder and self.voice_recorder.is_recording)
        speech_active = self._speech_is_active()
        can_record = (
            self.voice_settings["enabled"]
            and self.voice_settings["input_enabled"]
            and (not self.chat_busy or speech_active)
            and not self.voice_transcribing
        )
        self.microphone_button.setEnabled(recording or can_record)
        self.microphone_button.setText("结束录音" if recording else "麦克风")
        self.send_button.setDisabled(
            self.chat_busy or recording or self.voice_transcribing
        )

    def _release_voice_resources(self):
        """Stop active audio and release all optional models/backends."""
        self.voice_operation_id += 1
        self._cancel_microphone_recording()
        self.stop_voice_playback(invalidate_operation=False)

        if self.stt_engine:
            self.stt_engine.unload()
        if self.speech_queue:
            self.speech_queue.shutdown(wait=False)

        self.stt_engine = None
        self.tts_engine = None
        self.audio_player = None
        self.speech_queue = None
        self.recording_indicator.hide()

    def toggle_microphone_recording(self):
        """Start on first click and stop/transcribe on the second click."""
        if not (
            self.voice_settings["enabled"]
            and self.voice_settings["input_enabled"]
        ):
            self._set_voice_notice("请先开启语音模式和语音输入。", is_error=True)
            return

        if self.voice_recorder and self.voice_recorder.is_recording:
            self._stop_microphone_recording()
        else:
            self._start_microphone_recording()

    def _start_microphone_recording(self):
        """Open the microphone only after this explicit UI action."""
        self.stop_voice_playback()
        recorder = MicrophoneRecorder()

        try:
            recorder.start(self.voice_settings["microphone_device"])
        except Exception as error:
            self._set_voice_notice(str(error), is_error=True)
            return

        self.voice_recorder = recorder
        self.voice_capture_panel = self._add_persona_panel(
            persona=self.active_persona,
            state=PersonaState.LISTENING,
        )
        self.voice_capture_row = self.voice_capture_panel.message_row
        self.recording_indicator.show()
        self._set_voice_notice("")
        self._update_voice_action_availability()

    def _stop_microphone_recording(self):
        """Release the microphone, then transcribe the private WAV in a worker."""
        recorder = self.voice_recorder

        if not recorder:
            return

        try:
            audio_path = recorder.stop()
        except Exception as error:
            self.recording_indicator.hide()
            self._voice_capture_failed(str(error))
            self._update_voice_action_availability()
            return

        self.recording_indicator.hide()
        self.voice_transcribing = True
        self._update_voice_action_availability()

        if self.voice_capture_panel:
            self.voice_capture_panel.set_state(PersonaState.THINKING)

        self._set_voice_notice("正在使用本地 faster-whisper 转写…")

        if self.stt_engine is None:
            self.stt_engine = LocalSpeechToText(self.voice_settings["stt_model"])

        self._run_worker(
            self.stt_engine.transcribe,
            audio_path,
            on_success=self._voice_transcription_succeeded,
            on_error=self._voice_capture_failed,
            on_finished=self._voice_transcription_finished,
        )

    def _voice_transcription_succeeded(self, result):
        transcript = str(result.get("text", "")).strip()

        if transcript:
            self.message_input.setPlainText(transcript)
            self.message_input.setFocus()
            self._set_voice_notice("本地转写完成。请确认文字后再发送。")

        self._remove_voice_capture_panel()

    def _voice_capture_failed(self, message):
        self._set_voice_notice(str(message), is_error=True)

        if self.voice_capture_panel:
            self.voice_capture_panel.set_error(str(message))

    def _voice_transcription_finished(self):
        self.voice_transcribing = False
        self._update_voice_action_availability()

    def _cancel_microphone_recording(self):
        if self.voice_recorder:
            try:
                self.voice_recorder.cancel()
            except Exception:
                pass

        self.voice_recorder = None
        self.recording_indicator.hide()
        self._remove_voice_capture_panel()
        self._update_voice_action_availability()

    def _remove_voice_capture_panel(self):
        row = self.voice_capture_row
        self.voice_capture_panel = None
        self.voice_capture_row = None

        if row:
            self.messages_layout.removeWidget(row)
            row.deleteLater()

        if self.current_ai_panel and self.chat_busy:
            self.current_ai_panel.set_avatar_animation_enabled(True)

    def _voice_output_allowed(self):
        return bool(
            self.voice_settings["enabled"]
            and self.voice_settings["output_enabled"]
            and self.voice_settings["auto_playback"]
        )

    def _ensure_speech_queue(self):
        if self.speech_queue is not None:
            return self.speech_queue

        self.tts_engine = LocalPiperTextToSpeech()
        self.audio_player = LocalAudioPlayer()
        self.speech_queue = ThreadedSpeechQueue(
            self.tts_engine,
            self.audio_player,
            on_activity=self.speech_queue_bridge.activity_changed.emit,
            on_started=self.speech_queue_bridge.speech_started.emit,
            on_warning=self.speech_queue_bridge.warning.emit,
            on_drained=self.speech_queue_bridge.drained.emit,
        )
        return self.speech_queue

    def _begin_streaming_speech(self, panel, user_message=""):
        """Create response-local segmentation and warm its likely local voice."""
        self.stop_voice_playback()

        if not self._voice_output_allowed():
            return

        profile = get_persona_voice_profile(
            self.voice_settings,
            panel.persona_id,
        )
        queue_manager = self._ensure_speech_queue()
        session_id = queue_manager.start_session()
        expected_language = self.active_language.get("id")

        if expected_language not in {"zh-CN", "en"}:
            expected_language = infer_response_language(user_message)

        self.streaming_speech_context = {
            "panel": panel,
            "segmenter": StreamingSentenceSegmenter(),
            "profile": profile,
            "session_id": session_id,
            "generation_done": False,
        }
        queue_manager.prepare_voice(session_id, profile, expected_language)

    def _queue_streamed_sentences(self, sentences):
        context = self.streaming_speech_context

        if not context or not self._voice_output_allowed():
            return

        queue_manager = None

        for sentence in sentences:
            if context["session_id"] is None:
                queue_manager = self._ensure_speech_queue()
                context["session_id"] = queue_manager.start_session()
                self.voice_playback_panel = context["panel"]
            elif queue_manager is None:
                queue_manager = self.speech_queue

            queue_manager.enqueue(
                context["session_id"],
                sentence,
                context["profile"],
                infer_response_language(sentence),
                self.voice_settings["speaker_device"],
            )

    def _finish_streaming_speech(self, panel):
        context = self.streaming_speech_context

        if not context or context["panel"] is not panel:
            self._complete_persona_panel(panel)
            return

        self._queue_streamed_sentences(context["segmenter"].finish())
        context["generation_done"] = True
        session_id = context["session_id"]

        if session_id is None or self.speech_queue is None:
            self._complete_persona_panel(panel)
            self.streaming_speech_context = None
            return

        self.speech_queue.finish_session(session_id)

        if self.speech_queue.is_active(session_id):
            panel.set_state(PersonaState.SPEAKING)

    def _speech_is_active(self):
        context = self.streaming_speech_context

        if not context or context["session_id"] is None or not self.speech_queue:
            return False

        return self.speech_queue.is_active(context["session_id"])

    def _speech_activity_changed(self, session_id, active):
        context = self.streaming_speech_context

        if not context or context["session_id"] != session_id:
            return

        panel = context["panel"]

        if active:
            panel.set_state(PersonaState.SPEAKING)
            self.stop_playback_button.show()
            self._set_voice_notice(self._speaking_status_text(panel))
        else:
            self.stop_playback_button.hide()

            if not context["generation_done"] and panel.state is not PersonaState.ERROR:
                panel.set_state(PersonaState.RESPONDING)

            if self._voice_warning_session != session_id:
                self._set_voice_notice("")

        self._update_voice_action_availability()

    def _speech_started(
        self,
        session_id,
        sequence,
        sentence,
        enqueued_at,
        voice_id,
    ):
        del sentence, voice_id
        context = self.streaming_speech_context

        if not context or context["session_id"] != session_id:
            return

        if sequence == 0 and session_id not in self._measured_speech_sessions:
            latency_ms = max(0.0, (time.perf_counter() - enqueued_at) * 1000.0)
            self.first_speech_latencies_ms.append(latency_ms)
            self._measured_speech_sessions.add(session_id)

        self._voice_warning_session = None
        self._set_voice_notice(self._speaking_status_text(context["panel"]))

    def _speech_warning(self, session_id, message):
        context = self.streaming_speech_context

        if not context or context["session_id"] != session_id:
            return

        self._voice_warning_session = session_id
        self._set_voice_notice(f"语音提示：{message}", is_error=True)

    def _speech_queue_drained(self, session_id):
        context = self.streaming_speech_context

        if not context or context["session_id"] != session_id:
            return

        panel = context["panel"]

        if panel.state is not PersonaState.ERROR:
            self._complete_persona_panel(panel)

        self.streaming_speech_context = None
        self.voice_playback_panel = None
        self.stop_playback_button.hide()

        if self._voice_warning_session != session_id:
            self._set_voice_notice("")

        self._update_voice_action_availability()

    @staticmethod
    def _speaking_status_text(panel):
        if panel.persona_id == "delamain":
            return "Delamain：正在输出语音"
        if panel.persona_id == "fairy":
            return "Fairy：正在说话"
        return "正在播放本地语音"

    def stop_voice_playback(self, invalidate_operation=True):
        """Stop current speech, clear both queues, and preserve generated text."""
        if invalidate_operation:
            self.voice_operation_id += 1

        context = self.streaming_speech_context

        if context:
            context["segmenter"].clear()

        if self.speech_queue:
            self.speech_queue.cancel()
        elif self.audio_player:
            self.audio_player.stop()

        panel = context["panel"] if context else self.voice_playback_panel

        if panel and panel.state is not PersonaState.ERROR:
            if self.chat_busy and panel is self.current_ai_panel:
                panel.set_state(PersonaState.RESPONDING)
            else:
                self._complete_persona_panel(panel)

        self.streaming_speech_context = None
        self.voice_playback_panel = None
        self._voice_warning_session = None
        self.stop_playback_button.hide()
        self._set_voice_notice("")
        self._update_voice_action_availability()

    def _apply_theme(self):
        """Apply the active Persona theme to every desktop UI component."""
        self.current_theme = get_theme(self.active_persona["id"])
        self.setProperty("personaTheme", self.current_theme["id"])
        self.setStyleSheet(build_stylesheet(self.current_theme))
        self.identity_name.setText(self.active_persona["display_name"].upper())
        self.identity_status.setText(self.current_theme["identity_status"])
        self.identity_avatar.set_persona(
            self.active_persona["id"],
            self.current_theme,
        )
        self.identity_avatar.set_state(PersonaState.COMPLETE)
        self.idle_state.set_theme(self.current_theme)

    def eventFilter(self, watched, event):
        if watched is self.message_input and event.type() == event.Type.KeyPress:
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                    self.send_message()
                    return True

        return super().eventFilter(watched, event)

    def _show_page(self, index):
        self.pages.setCurrentIndex(index)

        if index == 1:
            self.refresh_library()

    def _run_worker(
        self,
        operation,
        *args,
        on_token=None,
        on_progress=None,
        on_state=None,
        on_success=None,
        on_error=None,
        on_finished=None,
        **kwargs,
    ):
        thread = QThread(self)
        worker = BackgroundWorker(
            operation,
            *args,
            use_token_callback=on_token is not None,
            use_progress_callback=on_progress is not None,
            use_state_callback=on_state is not None,
            **kwargs,
        )
        worker.moveToThread(thread)
        thread.worker = worker
        thread.started.connect(worker.run)

        if on_token:
            worker.token.connect(on_token)
        if on_progress:
            worker.progress.connect(on_progress)
        if on_state:
            worker.state_changed.connect(on_state)
        if on_success:
            worker.succeeded.connect(on_success)
        if on_error:
            worker.failed.connect(on_error)
        if on_finished:
            worker.finished.connect(on_finished)

        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(lambda: self.worker_threads.discard(thread))
        self.worker_threads.add(thread)
        thread.start()

    def _run_health_check(self):
        self._run_worker(
            check_ollama,
            on_success=lambda result: self.ollama_status.setText("Ollama：在线"),
            on_error=lambda message: self.ollama_status.setText(
                f"Ollama：不可用 — {message}"
            ),
        )

    def _add_message(self, role, title, text=""):
        message = AIMessageWidget(role, title, text)
        row = MessageRow(message, role)
        self.messages_layout.addWidget(row)
        self._scroll_conversation_to_bottom()
        return message

    def _add_persona_panel(
        self,
        persona=None,
        state=PersonaState.IDLE,
        text="",
    ):
        panel_persona = dict(persona or self.active_persona)
        normalized_state = (
            state if isinstance(state, PersonaState) else PersonaState(state)
        )
        continuous_animation = (
            PersonaAvatarWidget.state_uses_continuous_animation(normalized_state)
        )

        if continuous_animation:
            for existing_panel in self.message_container.findChildren(
                PersonaDialoguePanel
            ):
                existing_panel.set_avatar_animation_enabled(False)

        panel = PersonaDialoguePanel(
            panel_persona,
            get_theme(panel_persona["id"]),
            state=state,
            text=text,
        )
        panel.set_avatar_animation_enabled(continuous_animation)
        row = MessageRow(panel, "ai")
        panel.message_row = row
        self.messages_layout.addWidget(row)
        self._scroll_conversation_to_bottom()
        return panel

    def _retire_latest_fairy_idle_panel(self):
        """Turn the previous standby Fairy response into static history."""
        panel = self.latest_completed_fairy_panel
        self.latest_completed_fairy_panel = None

        if panel is None:
            return

        panel.set_avatar_animation_mode(AvatarAnimationMode.HISTORY_STATIC)
        panel.set_avatar_animation_enabled(False)

    def _complete_persona_panel(self, panel):
        """Complete a response and give only the latest Fairy standby motion."""
        if panel.persona_id != "fairy":
            panel.complete()
            return

        if self.latest_completed_fairy_panel is not panel:
            self._retire_latest_fairy_idle_panel()

        panel.complete(keep_idle_animation=True)
        panel.set_avatar_animation_enabled(True)
        self.latest_completed_fairy_panel = panel

    def _scroll_conversation_to_bottom(self):
        bar = self.conversation_scroll.verticalScrollBar()
        bar.setValue(bar.maximum())

    def send_message(self):
        if self.voice_recorder and self.voice_recorder.is_recording:
            self._set_voice_notice("请先结束当前录音。", is_error=True)
            return

        message = self.message_input.toPlainText().strip()

        if not message:
            return

        self.stop_voice_playback()
        self._retire_latest_fairy_idle_panel()
        self.message_input.clear()
        self.user_message_count += 1
        self.idle_state.setVisible(self.user_message_count < 2)
        self._add_message("user", "YOU", message)
        self.current_ai_panel = self._add_persona_panel(
            persona=self.active_persona,
            state=PersonaState.LISTENING,
        )
        self._begin_streaming_speech(self.current_ai_panel, message)
        self._set_chat_busy(True)
        mode = self.chat_mode.currentData()
        self.current_chat_mode = mode
        operation = stream_knowledge_chat if mode == "rag" else stream_normal_chat

        self._run_worker(
            operation,
            message,
            persona=dict(self.active_persona),
            language=dict(self.active_language),
            on_token=self._append_stream_token,
            on_state=self._set_current_panel_state,
            on_success=self._chat_succeeded,
            on_error=self._chat_failed,
            on_finished=lambda: self._set_chat_busy(False),
        )

    def _append_stream_token(self, token):
        if self.current_ai_panel:
            self.current_ai_panel.append_text(token)

            context = self.streaming_speech_context

            if context and context["panel"] is self.current_ai_panel:
                sentences = context["segmenter"].feed(token)
                self._queue_streamed_sentences(sentences)

            self._scroll_conversation_to_bottom()

    def _set_current_panel_state(self, state):
        if self.current_ai_panel:
            if self._speech_is_active():
                return
            self.current_ai_panel.set_state(state)

    def _chat_succeeded(self, result):
        if not self.current_ai_panel:
            return

        if result and self.current_chat_mode == "rag":
            self.current_ai_panel.set_sources(result)

        completed_panel = self.current_ai_panel
        self._scroll_conversation_to_bottom()
        self._finish_streaming_speech(completed_panel)

    def _chat_failed(self, message):
        if self.current_ai_panel:
            self.stop_voice_playback()
            self.current_ai_panel.set_error(message)
        else:
            self._show_error(message)

    def _set_chat_busy(self, busy):
        self.chat_busy = bool(busy)
        self.send_button.setDisabled(self.chat_busy)
        self.message_input.setDisabled(busy)
        self.chat_mode.setDisabled(busy)
        self._update_voice_action_availability()

    def refresh_library(self):
        try:
            self.documents = list_documents()
        except Exception as error:
            self._show_error(str(error))
            return

        self.knowledge_table.setRowCount(len(self.documents))

        for row, document in enumerate(self.documents):
            values = (
                document["filename"],
                document["file_type"].lstrip(".").upper(),
                str(document["chunk_count"]),
                str(document["page_count"] or "—"),
                document["source_path"],
            )

            for column, value in enumerate(values):
                self.knowledge_table.setItem(row, column, QTableWidgetItem(value))

        self.knowledge_status.setText(
            f"共 {len(self.documents)} 个文档，"
            f"{sum(document['chunk_count'] for document in self.documents)} 个 Chunk。"
        )

    def choose_import_file(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "导入本地文档",
            str(Path.home()),
            DOCUMENT_FILTER,
        )

        if not file_path:
            return

        self._set_knowledge_busy(True)
        self.knowledge_status.setText(f"正在导入 {Path(file_path).name}…")
        self._run_worker(
            import_document,
            file_path,
            on_progress=self.knowledge_status.setText,
            on_success=lambda count: self._knowledge_operation_succeeded(
                f"导入完成：已存储 {count} 个 Chunk。"
            ),
            on_error=self._knowledge_operation_failed,
            on_finished=lambda: self._set_knowledge_busy(False),
        )

    def _selected_document(self):
        row = self.knowledge_table.currentRow()

        if row < 0 or row >= len(self.documents):
            self._show_error("请先在知识库列表中选择一个文档。")
            return None

        return self.documents[row]

    def delete_selected_document(self):
        document = self._selected_document()

        if not document:
            return

        answer = QMessageBox.question(
            self,
            "确认删除",
            f"删除“{document['filename']}”及其全部本地知识？\n\n"
            f"将移除 {document['chunk_count']} 个 Chunk，其他文档不受影响。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )

        if answer != QMessageBox.StandardButton.Yes:
            return

        try:
            count = delete_document(document["source_id"])
        except Exception as error:
            self._show_error(str(error))
            return

        self.refresh_library()
        self.knowledge_status.setText(f"已删除 {document['filename']} 的 {count} 个 Chunk。")

    def reindex_selected_document(self):
        document = self._selected_document()

        if not document:
            return

        self._set_knowledge_busy(True)
        self.knowledge_status.setText(f"正在重新索引 {document['filename']}…")
        self._run_worker(
            reindex_library_document,
            document,
            on_progress=self.knowledge_status.setText,
            on_success=lambda result: self._knowledge_operation_succeeded(
                f"重新索引完成：{result['stored_count']} 个 Chunk。"
            ),
            on_error=self._knowledge_operation_failed,
            on_finished=lambda: self._set_knowledge_busy(False),
        )

    def _knowledge_operation_succeeded(self, message):
        self.refresh_library()
        self.knowledge_status.setText(message)

    def _knowledge_operation_failed(self, message):
        self.knowledge_status.setText(f"操作失败：{message}")
        self._show_error(message)

    def _set_knowledge_busy(self, busy):
        for button in (
            self.import_button,
            self.delete_button,
            self.reindex_button,
            self.refresh_button,
        ):
            button.setDisabled(busy)

    def change_persona(self, persona_id):
        if persona_id == self.active_persona["id"]:
            return

        try:
            self.active_persona = switch_persona(persona_id)
        except Exception as error:
            self._show_error(str(error))
            return

        self._update_persona_display(add_greeting=True)

    def _update_persona_display(self, add_greeting=False):
        self._apply_theme()
        self.chat_persona_label.setText(
            f"当前 Persona：{self.active_persona['display_name']} · "
            f"{self.active_persona['style_description']}"
        )
        self.settings_persona.setText(
            f"当前 Persona：{self.active_persona['display_name']}\n"
            f"{self.active_persona['style_description']}"
        )
        selected_button = self.persona_buttons.get(self.active_persona["id"])

        if selected_button and not selected_button.isChecked():
            selected_button.blockSignals(True)
            selected_button.setChecked(True)
            selected_button.blockSignals(False)

        if add_greeting:
            self._add_persona_panel(
                persona=self.active_persona,
                state=PersonaState.COMPLETE,
                text=self.active_persona["greeting"],
            )

        self.idle_state.setVisible(self.user_message_count < 2)

    def _show_error(self, message):
        QMessageBox.critical(self, "本地 AI 错误", message)

    def closeEvent(self, event):
        """Avoid destroying active workers while a local operation is running."""
        if any(thread.isRunning() for thread in self.worker_threads):
            QMessageBox.information(
                self,
                "本地任务仍在运行",
                "请等待当前本地 AI 或文档任务完成后再关闭窗口。",
            )
            event.ignore()
            return

        self._cancel_microphone_recording()
        self.stop_voice_playback()

        if self.speech_queue:
            self.speech_queue.shutdown(wait=False)

        for avatar in self.findChildren(PersonaAvatarWidget):
            avatar.stop_animation()

        super().closeEvent(event)


def create_application(argv=None):
    """Create the Qt application and main window for tests or launch."""
    app = QApplication.instance() or QApplication(argv or sys.argv)
    app.setApplicationName(APP_TITLE)
    app.setStyle("Fusion")
    window = MainWindow()
    return app, window


def main():
    """Launch the local desktop application."""
    app, window = create_application()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
