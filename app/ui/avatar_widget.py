"""Original, programmatically drawn Persona avatars for the desktop UI."""

import math

from PySide6.QtCore import QLineF, QPointF, QRectF, QTimer, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget


ACTIVE_STATES = {"listening", "searching", "thinking", "responding", "speaking"}

AVATAR_VISUAL_PROFILES = {
    "delamain": {
        "idle": {"motion": "stable", "brightness": 0.72},
        "listening": {"motion": "scan", "brightness": 0.82},
        "searching": {"motion": "search_ring", "brightness": 0.88},
        "thinking": {"motion": "layer_analysis", "brightness": 0.84},
        "responding": {"motion": "core_pulse", "brightness": 1.0},
        "speaking": {"motion": "voice_pulse", "brightness": 1.0},
        "complete": {"motion": "stable_complete", "brightness": 0.88},
        "error": {"motion": "warning", "brightness": 0.78},
    },
    "fairy": {
        "idle": {"motion": "calm", "brightness": 0.72},
        "listening": {"motion": "listening_waves", "brightness": 0.9},
        "searching": {"motion": "search_orbit", "brightness": 0.88},
        "thinking": {"motion": "thoughtful_halo", "brightness": 0.82},
        "responding": {"motion": "speaking_pulse", "brightness": 1.0},
        "speaking": {"motion": "voice_glow", "brightness": 1.0},
        "complete": {"motion": "relaxed", "brightness": 0.82},
        "error": {"motion": "concerned", "brightness": 0.76},
    },
    "neutral": {
        "idle": {"motion": "stable", "brightness": 0.72},
        "listening": {"motion": "active", "brightness": 0.82},
        "searching": {"motion": "active", "brightness": 0.88},
        "thinking": {"motion": "active", "brightness": 0.84},
        "responding": {"motion": "active", "brightness": 1.0},
        "speaking": {"motion": "voice_pulse", "brightness": 1.0},
        "complete": {"motion": "stable_complete", "brightness": 0.82},
        "error": {"motion": "warning", "brightness": 0.76},
    },
}


class PersonaAvatarWidget(QWidget):
    """Draw and lightly animate the avatar for one immutable Persona identity."""

    def __init__(self, persona_id, theme, parent=None):
        super().__init__(parent)
        self.persona_id = persona_id
        self.theme = dict(theme)
        self.state = "idle"
        self.phase = 0.0
        self.setFixedSize(92, 92)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        self.animation_timer = QTimer(self)
        self.animation_timer.setInterval(50)
        self.animation_timer.timeout.connect(self._advance_animation)

    def set_state(self, state):
        """Update the avatar state without changing any AI behavior."""
        self.state = getattr(state, "value", state)
        self.phase = 0.0

        if self.state in ACTIVE_STATES:
            if not self.animation_timer.isActive():
                self.animation_timer.start()
        else:
            self.animation_timer.stop()

        self.update()

    def _advance_animation(self):
        phase_step = 0.025 if self.state == "thinking" else 0.04
        self.phase = (self.phase + phase_step) % 1.0
        self.update()

    def visual_profile(self):
        """Return the state profile used by painting and lifecycle tests."""
        persona_profiles = AVATAR_VISUAL_PROFILES.get(
            self.persona_id,
            AVATAR_VISUAL_PROFILES["neutral"],
        )
        return dict(persona_profiles.get(self.state, persona_profiles["idle"]))

    def _state_color(self):
        color_key = "error" if self.state == "error" else "accent"
        return QColor(self.theme[color_key])

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        if self.persona_id == "delamain":
            self._paint_delamain(painter)
        elif self.persona_id == "fairy":
            self._paint_fairy(painter)
        else:
            self._paint_neutral(painter)

    def _paint_delamain(self, painter):
        color = self._state_color()
        profile = self.visual_profile()
        motion = profile["motion"]
        wave = (math.sin(self.phase * math.tau) + 1) / 2
        pulse_amount = 0.24 if motion in {"core_pulse", "voice_pulse"} else 0.08
        pulse = profile["brightness"] * (1 - pulse_amount + pulse_amount * wave)
        center = QPointF(46, 46)

        for layer_index, (size, alpha) in enumerate(((72, 70), (54, 110), (34, 175))):
            layer = QColor(color)
            layer.setAlpha(int(alpha * pulse))
            painter.setPen(QPen(layer, 1.4))
            if motion == "layer_analysis" and layer_index > 0:
                painter.save()
                painter.translate(center)
                direction = -1 if layer_index == 1 else 1
                painter.rotate(direction * self.phase * 22)
                painter.drawRect(QRectF(-size / 2, -size / 2, size, size))
                painter.restore()
            else:
                painter.drawRect(QRectF(46 - size / 2, 46 - size / 2, size, size))

        core = QColor(color)
        core.setAlpha(min(255, int(235 * pulse)))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(core)
        core_size = 12 + (
            3 * wave if motion in {"core_pulse", "voice_pulse"} else 0
        )
        painter.drawRect(
            QRectF(46 - core_size / 2, 46 - core_size / 2, core_size, core_size)
        )

        guide = QColor(color)
        guide.setAlpha(80)
        painter.setPen(QPen(guide, 1))
        painter.drawLine(QLineF(6, center.y(), 24, center.y()))
        painter.drawLine(QLineF(68, center.y(), 86, center.y()))
        painter.drawLine(QLineF(center.x(), 6, center.x(), 24))
        painter.drawLine(QLineF(center.x(), 68, center.x(), 86))

        if motion in {"scan", "search_ring"}:
            scan = QColor(color)
            scan.setAlpha(135 if motion == "scan" else 175)
            scan_y = 18 + self.phase * 56
            painter.setPen(QPen(scan, 1.5))
            painter.drawLine(QLineF(20, scan_y, 72, scan_y))

        if motion == "scan":
            scan_guide = QColor(color)
            scan_guide.setAlpha(105)
            painter.setPen(QPen(scan_guide, 1))
            offset = 5 + self.phase * 8
            painter.drawLine(QLineF(10, 30 + offset, 18, 30 + offset))
            painter.drawLine(QLineF(74, 54 - offset, 82, 54 - offset))
        elif motion == "search_ring":
            ring = QColor(color)
            ring.setAlpha(180)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(ring, 1.5))
            start_angle = int(-self.phase * 360 * 16)
            painter.drawArc(QRectF(10, 10, 72, 72), start_angle, 105 * 16)
            painter.drawArc(QRectF(14, 14, 64, 64), start_angle + 180 * 16, 70 * 16)
        elif motion == "warning":
            warning = QColor(color)
            warning.setAlpha(175)
            painter.setPen(QPen(warning, 1.6))
            painter.drawLine(QLineF(17, 17, 27, 17))
            painter.drawLine(QLineF(17, 17, 17, 27))
            painter.drawLine(QLineF(65, 75, 75, 75))
            painter.drawLine(QLineF(75, 65, 75, 75))

        if motion == "voice_pulse":
            voice_ring = QColor(color)
            voice_ring.setAlpha(int(70 + 75 * wave))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(voice_ring, 1.4))
            ring_size = 62 + 5 * wave
            painter.drawRect(
                QRectF(
                    46 - ring_size / 2,
                    46 - ring_size / 2,
                    ring_size,
                    ring_size,
                )
            )

    def _paint_fairy(self, painter):
        color = self._state_color()
        profile = self.visual_profile()
        motion = profile["motion"]
        wave = (math.sin(self.phase * math.tau) + 1) / 2
        pulse_amount = 0.2 if motion in {"speaking_pulse", "voice_glow"} else 0.08
        pulse = profile["brightness"] * (1 - pulse_amount + pulse_amount * wave)

        halo = QColor(color)
        halo_alpha = 185 if motion == "listening_waves" else 145
        halo.setAlpha(int(halo_alpha * pulse))
        painter.setPen(QPen(halo, 2))
        halo_offset = int(self.phase * 50 * 16) if motion == "thoughtful_halo" else 0
        painter.drawArc(QRectF(17, 10, 58, 28), 15 * 16 + halo_offset, 150 * 16)

        glow = QColor(color)
        glow.setAlpha(int(34 * pulse))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(glow)
        painter.drawEllipse(QRectF(13, 17, 66, 66))

        shell = QColor(self.theme["surface_alt"])
        painter.setBrush(shell)
        outline = QColor(color)
        outline.setAlpha(190)
        painter.setPen(QPen(outline, 1.7))
        painter.drawRoundedRect(QRectF(22, 25, 48, 46), 17, 17)

        eye_color = QColor(self.theme["accent_bright"])
        eye_alpha = 255 if motion in {
            "listening_waves",
            "speaking_pulse",
            "voice_glow",
        } else 225
        eye_color.setAlpha(eye_alpha)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(eye_color)
        eye_height = 2.6 if motion in {"relaxed", "calm"} else 4.5
        left_eye_height = 2.8 if motion == "thoughtful_halo" else eye_height
        painter.drawRoundedRect(QRectF(34, 43, 6, left_eye_height), 3, 3)
        painter.drawRoundedRect(QRectF(52, 43, 6, eye_height), 3, 3)

        if motion == "listening_waves":
            waves = QColor(color)
            waves.setAlpha(120)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(waves, 1.2))
            painter.drawArc(QRectF(8, 35, 18, 28), 70 * 16, 220 * 16)
            painter.drawArc(QRectF(66, 35, 18, 28), -110 * 16, 220 * 16)
        elif motion == "search_orbit":
            orbit = QColor(color)
            orbit.setAlpha(150)
            painter.setPen(QPen(orbit, 1.3))
            start_angle = int(self.phase * 360 * 16)
            painter.drawArc(QRectF(12, 15, 68, 68), start_angle, 95 * 16)
            orbit_angle = self.phase * math.tau
            orbit_point = QPointF(
                46 + math.cos(orbit_angle) * 34,
                49 + math.sin(orbit_angle) * 34,
            )
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(orbit)
            painter.drawEllipse(orbit_point, 2.3, 2.3)
        elif motion == "thoughtful_halo":
            thought = QColor(color)
            thought.setAlpha(145)
            painter.setPen(QPen(thought, 1.2))
            painter.drawLine(QLineF(33, 39, 40, 40))
            painter.drawLine(QLineF(51, 40, 59, 38))
        elif motion in {"speaking_pulse", "voice_glow"}:
            speaking = QColor(color)
            speaking.setAlpha(int(150 + 80 * wave))
            painter.setPen(QPen(speaking, 1.4))
            mouth_width = 8 + 4 * wave
            painter.drawArc(
                QRectF(46 - mouth_width / 2, 55, mouth_width, 7),
                200 * 16,
                140 * 16,
            )
        elif motion == "concerned":
            concern = QColor(color)
            concern.setAlpha(180)
            painter.setPen(QPen(concern, 1.4))
            painter.drawLine(QLineF(33, 39, 40, 41))
            painter.drawLine(QLineF(52, 41, 59, 39))
            painter.drawArc(QRectF(40, 56, 12, 7), 20 * 16, 140 * 16)

    def _paint_neutral(self, painter):
        color = self._state_color()
        profile = self.visual_profile()
        wave = (math.sin(self.phase * math.tau) + 1) / 2
        pulse = profile["brightness"] * (0.9 + 0.1 * wave)
        outline = QColor(color)
        outline.setAlpha(int(165 * pulse))
        painter.setPen(QPen(outline, 1.5))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(QRectF(22, 22, 48, 48))
        painter.drawLine(QLineF(32, 46, 60, 46))

        if self.state in ACTIVE_STATES:
            marker = QColor(color)
            marker.setAlpha(int(220 * pulse))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(marker)
            painter.drawEllipse(QPointF(46, 46), 4, 4)
