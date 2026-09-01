"""Centralized visual themes for the PySide6 desktop interface."""


THEMES = {
    "delamain": {
        "id": "delamain",
        "background": "#070c13",
        "page": "#0a111b",
        "sidebar": "#0b1420",
        "surface": "#101b28",
        "surface_alt": "#132233",
        "surface_hover": "#182d42",
        "border": "#223b52",
        "border_strong": "#2c6680",
        "text": "#e8f2fa",
        "muted": "#8096aa",
        "accent": "#52d6ec",
        "accent_bright": "#8be9f7",
        "accent_deep": "#12647a",
        "accent_soft": "#102f3d",
        "user_surface": "#14354a",
        "user_border": "#28708c",
        "status_surface": "#0e3038",
        "status_text": "#6ce9df",
        "error": "#ff8f8f",
        "disabled": "#5e7080",
        "radius": 6,
        "card_radius": 8,
        "identity_status": "SYSTEM READY  ·  LOCAL",
        "idle_label": "LOCAL SYSTEM READY",
        "idle_kind": "geometry",
    },
    "fairy": {
        "id": "fairy",
        "background": "#0b0815",
        "page": "#100c1d",
        "sidebar": "#120d22",
        "surface": "#191329",
        "surface_alt": "#21183a",
        "surface_hover": "#2a2050",
        "border": "#392d5a",
        "border_strong": "#7354be",
        "text": "#f1ecff",
        "muted": "#a096ba",
        "accent": "#a983ff",
        "accent_bright": "#cbb6ff",
        "accent_deep": "#6645b2",
        "accent_soft": "#2a1d4d",
        "user_surface": "#302052",
        "user_border": "#7d5bd0",
        "status_surface": "#251b43",
        "status_text": "#cab8ff",
        "error": "#ff9ebc",
        "disabled": "#716982",
        "radius": 11,
        "card_radius": 15,
        "identity_status": "ONLINE  ·  LOCAL",
        "idle_label": "READY WHEN YOU ARE",
        "idle_kind": "waves",
    },
    "neutral": {
        "id": "neutral",
        "background": "#0d0e10",
        "page": "#111315",
        "sidebar": "#121416",
        "surface": "#191c1f",
        "surface_alt": "#202428",
        "surface_hover": "#292e33",
        "border": "#343a40",
        "border_strong": "#626b73",
        "text": "#e6e8ea",
        "muted": "#92989e",
        "accent": "#aeb7bf",
        "accent_bright": "#d6dce1",
        "accent_deep": "#555f67",
        "accent_soft": "#252b2f",
        "user_surface": "#292f34",
        "user_border": "#59636b",
        "status_surface": "#242a2d",
        "status_text": "#c2c9ce",
        "error": "#e09a9a",
        "disabled": "#686e73",
        "radius": 5,
        "card_radius": 7,
        "identity_status": "READY  ·  LOCAL",
        "idle_label": "LOCAL MODE READY",
        "idle_kind": "minimal",
    },
}


def get_theme(persona_id):
    """Return the theme for a Persona, using Neutral as a safe fallback."""
    return dict(THEMES.get(str(persona_id).casefold(), THEMES["neutral"]))


def build_stylesheet(theme):
    """Build the application stylesheet from one centralized theme mapping."""
    radius = theme["radius"]
    card_radius = theme["card_radius"]
    return f"""
        QMainWindow, #applicationRoot, #pageRoot {{
            background: {theme['background']};
            color: {theme['text']};
            font-size: 14px;
        }}
        QWidget {{ color: {theme['text']}; font-size: 14px; }}
        #sidebar {{
            background: {theme['sidebar']};
            border-right: 1px solid {theme['border']};
        }}
        #brand {{
            color: {theme['accent_bright']};
            font-size: 21px;
            font-weight: 700;
            letter-spacing: 2px;
        }}
        #brandSubtitle, #sectionEyebrow {{
            color: {theme['muted']};
            font-size: 10px;
            font-weight: 700;
            letter-spacing: 1px;
        }}
        #navButton {{
            text-align: left;
            padding: 12px 14px;
            border: 0;
            border-radius: {radius}px;
            color: {theme['muted']};
            background: transparent;
        }}
        #navButton:hover {{
            background: {theme['surface_hover']};
            color: {theme['text']};
        }}
        #navButton:checked {{
            background: {theme['accent_soft']};
            color: {theme['accent_bright']};
            border-left: 3px solid {theme['accent']};
        }}
        #statusBadge {{
            background: {theme['status_surface']};
            color: {theme['status_text']};
            border: 1px solid {theme['border_strong']};
            border-radius: {radius}px;
            padding: 4px 7px;
            font-size: 9px;
            font-weight: 700;
        }}
        #pageTitle {{
            font-size: 27px;
            font-weight: 650;
            color: {theme['text']};
        }}
        #mutedLabel {{ color: {theme['muted']}; }}
        #identityPanel {{
            background: {theme['surface']};
            border: 1px solid {theme['border']};
            border-left: 3px solid {theme['accent']};
            border-radius: {card_radius}px;
        }}
        #identityName {{
            color: {theme['accent_bright']};
            font-size: 18px;
            font-weight: 750;
            letter-spacing: 2px;
        }}
        #identityStatus {{
            color: {theme['status_text']};
            font-size: 10px;
            font-weight: 700;
            letter-spacing: 1px;
        }}
        #conversationArea {{
            background: {theme['page']};
            border: 1px solid {theme['border']};
            border-radius: {card_radius}px;
        }}
        #conversationViewport, #conversationCanvas, #messageRow {{
            background: transparent;
        }}
        #userBubble {{
            background: {theme['user_surface']};
            border: 1px solid {theme['user_border']};
            border-radius: {card_radius}px;
        }}
        #aiBubble {{
            background: {theme['surface']};
            border: 1px solid {theme['border']};
            border-radius: {card_radius}px;
        }}
        #bubbleTitle {{
            color: {theme['accent_bright']};
            font-size: 11px;
            font-weight: 750;
            letter-spacing: 1px;
        }}
        #bubbleBody {{ color: {theme['text']}; font-size: 14px; }}
        #sourcesPanel {{
            background: {theme['surface']};
            border: 1px solid {theme['border']};
            border-radius: {card_radius}px;
        }}
        #sourcesTitle {{
            color: {theme['accent_bright']};
            font-size: 11px;
            font-weight: 750;
            letter-spacing: 1px;
        }}
        #sourceCard {{
            background: {theme['surface_alt']};
            border-left: 2px solid {theme['accent']};
            border-radius: {radius}px;
        }}
        #sourceName {{ color: {theme['text']}; font-weight: 650; }}
        #sourceDetails {{ color: {theme['muted']}; font-size: 12px; }}
        #personaCard, #settingsLine {{
            background: {theme['surface']};
            border: 1px solid {theme['border']};
            border-radius: {card_radius}px;
            padding: 16px;
        }}
        QPushButton {{
            background: {theme['surface_alt']};
            color: {theme['text']};
            border: 1px solid {theme['border']};
            border-radius: {radius}px;
            padding: 9px 14px;
        }}
        QPushButton:hover {{
            background: {theme['surface_hover']};
            border-color: {theme['border_strong']};
        }}
        QPushButton:disabled {{
            color: {theme['disabled']};
            background: {theme['background']};
        }}
        #primaryButton {{
            background: {theme['accent_deep']};
            border-color: {theme['accent']};
            color: {theme['text']};
            font-weight: 650;
        }}
        #primaryButton:hover {{ background: {theme['border_strong']}; }}
        #voiceStatusButton {{
            color: {theme['disabled']};
            background: {theme['background']};
            border-color: {theme['border']};
            font-size: 10px;
            font-weight: 750;
            letter-spacing: 1px;
            padding: 6px 10px;
        }}
        #voiceStatusButton[voiceEnabled="true"] {{
            color: {theme['accent_bright']};
            background: {theme['accent_deep']};
            border-color: {theme['accent']};
        }}
        #voiceActionButton {{
            font-size: 11px;
            padding: 6px 10px;
        }}
        #recordingIndicator {{
            color: {theme['error']};
            font-size: 11px;
            font-weight: 700;
        }}
        #voiceNotice {{
            color: {theme['muted']};
            font-size: 11px;
            padding: 2px 4px;
        }}
        #voiceNotice[error="true"] {{ color: {theme['error']}; }}
        #settingsSection {{
            background: {theme['surface']};
            border: 1px solid {theme['border']};
            border-radius: {card_radius}px;
        }}
        #sectionTitle {{
            color: {theme['accent_bright']};
            font-size: 15px;
            font-weight: 700;
        }}
        QPlainTextEdit, QComboBox {{
            background: {theme['surface']};
            border: 1px solid {theme['border']};
            border-radius: {radius}px;
            padding: 9px;
            color: {theme['text']};
            selection-background-color: {theme['accent_deep']};
        }}
        QComboBox QAbstractItemView {{
            background: {theme['surface']};
            color: {theme['text']};
            selection-background-color: {theme['accent_deep']};
        }}
        QTableWidget {{
            background: {theme['page']};
            alternate-background-color: {theme['surface']};
            border: 1px solid {theme['border']};
            border-radius: {radius}px;
            gridline-color: {theme['border']};
        }}
        QHeaderView::section {{
            background: {theme['surface_alt']};
            color: {theme['muted']};
            border: 0;
            border-bottom: 1px solid {theme['border']};
            padding: 9px;
        }}
        QTableWidget::item {{ padding: 8px; }}
        QTableWidget::item:selected {{ background: {theme['accent_deep']}; }}
        QRadioButton {{
            font-size: 17px;
            font-weight: 650;
            color: {theme['text']};
            spacing: 10px;
        }}
        QCheckBox {{
            color: {theme['text']};
            spacing: 8px;
        }}
        QCheckBox::indicator {{
            width: 16px;
            height: 16px;
            border: 1px solid {theme['border_strong']};
            border-radius: 4px;
            background: {theme['background']};
        }}
        QCheckBox::indicator:checked {{
            background: {theme['accent']};
            border-color: {theme['accent_bright']};
        }}
        QRadioButton::indicator:checked {{
            background: {theme['accent']};
            border: 3px solid {theme['accent_soft']};
            border-radius: 7px;
        }}
        QScrollBar:vertical {{ background: {theme['page']}; width: 9px; }}
        QScrollBar::handle:vertical {{
            background: {theme['border_strong']};
            border-radius: 4px;
            min-height: 24px;
        }}
    """
