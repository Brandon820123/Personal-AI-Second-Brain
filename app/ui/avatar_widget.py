"""Cached Persona image avatars with lightweight state effects and fallbacks."""

import logging
import math
from pathlib import Path

from PySide6.QtCore import QElapsedTimer, QLineF, QPointF, QRectF, QSize, QTimer, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QWidget


LOGGER = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
AVATAR_DIRECTORY = PROJECT_ROOT / "assets" / "avatars"
AVATAR_ASSET_PATHS = {
    "delamain": AVATAR_DIRECTORY / "delamain.png",
    "fairy": AVATAR_DIRECTORY / "fairy.png",
}

CONTINUOUS_ANIMATION_STATES = {
    "idle",
    "listening",
    "searching",
    "thinking",
    "responding",
}
# Backward-compatible public name used by lifecycle tests.
ACTIVE_STATES = CONTINUOUS_ANIMATION_STATES
FAIRY_ROTATION_DEGREES_PER_SECOND = 72.0

AVATAR_VISUAL_PROFILES = {
    "delamain": {
        "idle": {"motion": "ambient_breathe", "glow": 0.22, "border": 1.4},
        "listening": {"motion": "listening_hud", "glow": 0.48, "border": 2.0},
        "searching": {"motion": "vertical_scan", "glow": 0.50, "border": 1.8},
        "thinking": {"motion": "hud_cycle", "glow": 0.46, "border": 1.7},
        "responding": {"motion": "response_pulse", "glow": 0.66, "border": 2.3},
        "speaking": {"motion": "active_static", "glow": 0.62, "border": 2.4},
        "complete": {"motion": "stable_complete", "glow": 0.24, "border": 1.6},
        "error": {"motion": "warning_frame", "glow": 0.34, "border": 2.2},
    },
    "fairy": {
        "idle": {"motion": "core_rotation", "glow": 0.30, "border": 1.5},
        "listening": {"motion": "core_rotation", "glow": 0.34, "border": 1.6},
        "searching": {"motion": "core_rotation", "glow": 0.38, "border": 1.7},
        "thinking": {"motion": "core_rotation", "glow": 0.36, "border": 1.7},
        "responding": {"motion": "core_rotation", "glow": 0.40, "border": 1.8},
        "speaking": {"motion": "active_static", "glow": 0.68, "border": 2.4},
        "complete": {"motion": "stable_complete", "glow": 0.28, "border": 1.6},
        "error": {"motion": "warning_ring", "glow": 0.36, "border": 2.2},
    },
    "neutral": {
        "idle": {"motion": "ambient_breathe", "glow": 0.16, "border": 1.4},
        "listening": {"motion": "bright_frame", "glow": 0.30, "border": 1.8},
        "searching": {"motion": "search_orbit", "glow": 0.36, "border": 1.8},
        "thinking": {"motion": "glow_pulse", "glow": 0.32, "border": 1.7},
        "responding": {"motion": "response_pulse", "glow": 0.42, "border": 2.0},
        "speaking": {"motion": "active_static", "glow": 0.44, "border": 2.0},
        "complete": {"motion": "stable_complete", "glow": 0.18, "border": 1.4},
        "error": {"motion": "warning_frame", "glow": 0.28, "border": 2.0},
    },
}

_SOURCE_PIXMAP_CACHE = {}
_PREPARED_PIXMAP_CACHE = {}
_WARNED_ASSET_PATHS = set()


def clear_avatar_pixmap_cache():
    """Clear shared pixmaps for deterministic tests or asset development."""
    _SOURCE_PIXMAP_CACHE.clear()
    _PREPARED_PIXMAP_CACHE.clear()


def avatar_cache_sizes():
    """Return source/prepared cache counts for lightweight diagnostics."""
    return {
        "source": len(_SOURCE_PIXMAP_CACHE),
        "prepared": len(_PREPARED_PIXMAP_CACHE),
    }


class PersonaAvatarWidget(QWidget):
    """Display one Persona asset with cached masking and state-only effects."""

    def __init__(
        self,
        persona_id,
        theme,
        parent=None,
        display_size=104,
        animation_enabled=True,
        asset_paths=None,
    ):
        super().__init__(parent)
        self.persona_id = "neutral"
        self.theme = dict(theme)
        self.state = "idle"
        self.phase = 0.0
        self.asset_path = None
        self.asset_warning = ""
        self.source_pixmap = QPixmap()
        self._animation_enabled = bool(animation_enabled)
        self._asset_paths = dict(AVATAR_ASSET_PATHS)
        self._frame_clock = QElapsedTimer()

        if asset_paths is not None:
            self._asset_paths.update(asset_paths)

        avatar_size = max(40, int(display_size))
        self.setFixedSize(avatar_size, avatar_size)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        self.animation_timer = QTimer(self)
        self.animation_timer.setInterval(33)
        self.animation_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.animation_timer.timeout.connect(self._advance_animation)
        self.set_persona(persona_id, theme)

    @property
    def uses_image_asset(self):
        return not self.source_pixmap.isNull()

    @property
    def animation_enabled(self):
        return self._animation_enabled

    def set_persona(self, persona_id, theme=None):
        """Change a reusable current-persona widget without affecting history."""
        self.persona_id = str(persona_id).strip().casefold() or "neutral"

        if theme is not None:
            self.theme = dict(theme)

        self.asset_path = self._asset_paths.get(self.persona_id)
        self.asset_warning = ""
        self.source_pixmap = QPixmap()

        if self.asset_path is not None:
            self.source_pixmap = self._load_source_pixmap(self.asset_path)

            if self.source_pixmap.isNull():
                self.asset_warning = (
                    f"Avatar asset unavailable for {self.persona_id}: "
                    f"{self.asset_path}. Using programmatic fallback."
                )
                warning_key = str(Path(self.asset_path).resolve())

                if warning_key not in _WARNED_ASSET_PATHS:
                    _WARNED_ASSET_PATHS.add(warning_key)
                    LOGGER.warning(self.asset_warning)

        self.phase = 0.0
        self._sync_animation_timer()
        self.update()

    def set_state(self, state):
        """Update frame effects without modifying the underlying source image."""
        self.state = getattr(state, "value", state)
        self.phase = 0.0
        self._sync_animation_timer()
        self.update()

    def set_continuous_animation_enabled(self, enabled):
        """Allow only the owning active panel to run a continuous effect."""
        self._animation_enabled = bool(enabled)
        self._sync_animation_timer()

    def stop_animation(self):
        """Stop this widget's timer immediately."""
        self.animation_timer.stop()

    def visual_profile(self):
        """Return the state profile used by painting and lifecycle tests."""
        persona_profiles = AVATAR_VISUAL_PROFILES.get(
            self.persona_id,
            AVATAR_VISUAL_PROFILES["neutral"],
        )
        return dict(persona_profiles.get(self.state, persona_profiles["idle"]))

    def showEvent(self, event):
        super().showEvent(event)
        self._sync_animation_timer()

    def hideEvent(self, event):
        self.animation_timer.stop()
        super().hideEvent(event)

    def closeEvent(self, event):
        self.animation_timer.stop()
        super().closeEvent(event)

    def _sync_animation_timer(self):
        should_animate = (
            self._animation_enabled
            and self.state in CONTINUOUS_ANIMATION_STATES
            and self.isVisible()
        )

        if should_animate and not self.animation_timer.isActive():
            self._frame_clock.restart()
            self.animation_timer.start()
        elif not should_animate:
            self.animation_timer.stop()

    def _advance_animation(self):
        elapsed_ms = max(1, self._frame_clock.restart())

        if self.persona_id == "fairy":
            phase_step = (
                FAIRY_ROTATION_DEGREES_PER_SECOND
                * elapsed_ms
                / 360_000.0
            )
            self.phase = (self.phase + phase_step) % 1.0
            self.update()
            return

        phase_steps = {
            "idle": 0.010,
            "listening": 0.024,
            "searching": 0.035,
            "thinking": 0.016,
            "responding": 0.022,
        }
        phase_step = phase_steps.get(self.state, 0.0) * elapsed_ms / 60.0
        self.phase = (self.phase + phase_step) % 1.0
        self.update()

    @staticmethod
    def _load_source_pixmap(asset_path):
        resolved_path = str(Path(asset_path).expanduser().resolve())

        if resolved_path in _SOURCE_PIXMAP_CACHE:
            return _SOURCE_PIXMAP_CACHE[resolved_path]

        pixmap = QPixmap(resolved_path)

        if not pixmap.isNull():
            _SOURCE_PIXMAP_CACHE[resolved_path] = pixmap

        return pixmap

    def _prepared_avatar_pixmap(self, logical_size):
        if self.source_pixmap.isNull():
            return QPixmap()

        device_ratio = max(1.0, float(self.devicePixelRatioF()))
        pixel_size = max(1, round(logical_size * device_ratio))
        shape = "circle" if self.persona_id == "fairy" else "rounded"
        path_key = str(Path(self.asset_path).expanduser().resolve())
        cache_key = (path_key, pixel_size, shape, round(device_ratio, 2))

        if cache_key in _PREPARED_PIXMAP_CACHE:
            return _PREPARED_PIXMAP_CACHE[cache_key]

        scaled = self.source_pixmap.scaled(
            QSize(pixel_size, pixel_size),
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
        prepared = QPixmap(pixel_size, pixel_size)
        prepared.fill(Qt.GlobalColor.transparent)
        painter = QPainter(prepared)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        clip_path = QPainterPath()
        pixel_rect = QRectF(0, 0, pixel_size, pixel_size)

        if shape == "circle":
            clip_path.addEllipse(pixel_rect)
        else:
            corner_radius = pixel_size * 0.075
            clip_path.addRoundedRect(pixel_rect, corner_radius, corner_radius)

        painter.setClipPath(clip_path)
        painter.drawPixmap(0, 0, scaled)
        painter.end()
        prepared.setDevicePixelRatio(device_ratio)
        _PREPARED_PIXMAP_CACHE[cache_key] = prepared
        return prepared

    def _state_color(self):
        color_key = "error" if self.state == "error" else "accent"
        return QColor(self.theme[color_key])

    def paintEvent(self, event):
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        if self.uses_image_asset:
            self._paint_image_avatar(painter)
        else:
            painter.save()
            painter.scale(self.width() / 92.0, self.height() / 92.0)

            if self.persona_id == "delamain":
                self._paint_delamain_fallback(painter)
            elif self.persona_id == "fairy":
                self._paint_fairy_fallback(painter)
            else:
                self._paint_neutral_fallback(painter)

            painter.restore()

    def _paint_image_avatar(self, painter):
        profile = self.visual_profile()
        motion = profile["motion"]
        wave = (math.sin(self.phase * math.tau) + 1.0) / 2.0
        padding = max(6.0, self.width() * 0.07)
        image_rect = QRectF(
            padding,
            padding,
            self.width() - padding * 2,
            self.height() - padding * 2,
        )
        border_color = self._state_color()
        glow_strength = float(profile["glow"])

        if motion == "ambient_breathe":
            glow_strength *= 0.78 + wave * 0.22
        elif motion == "response_pulse":
            glow_strength *= 0.72 + wave * 0.42
        elif motion == "listening_hud":
            glow_strength *= 0.88 + wave * 0.18

        self._paint_glow(painter, image_rect, border_color, glow_strength)

        if self.persona_id == "delamain":
            surround = QColor(self.theme.get("page", "#07111c"))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(surround)
            painter.drawRoundedRect(image_rect.adjusted(-2, -2, 2, 2), 8, 8)

        avatar_pixmap = self._prepared_avatar_pixmap(round(image_rect.width()))
        painter.drawPixmap(image_rect.toRect(), avatar_pixmap)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(border_color, float(profile["border"])))

        if self.persona_id == "fairy":
            painter.drawEllipse(image_rect)
        else:
            painter.drawRoundedRect(image_rect, 7, 7)

        if motion == "listening_hud":
            self._paint_delamain_listening_hud(
                painter,
                image_rect,
                border_color,
                wave,
            )
        elif motion == "vertical_scan":
            self._paint_delamain_scan(painter, image_rect, border_color)
        elif motion == "hud_cycle":
            self._paint_delamain_thinking_hud(painter, image_rect, border_color)
        elif motion == "response_pulse" and self.persona_id == "delamain":
            self._paint_delamain_response(painter, image_rect, border_color, wave)
        elif motion == "core_rotation":
            self._paint_fairy_core_rotation(painter, image_rect, avatar_pixmap)

        if motion in {"warning_frame", "warning_ring"}:
            self._paint_warning_overlay(painter, image_rect, border_color)

    def _paint_glow(self, painter, image_rect, color, strength):
        for expansion, alpha_scale in ((3.0, 0.64), (6.0, 0.30)):
            glow = QColor(color)
            glow.setAlpha(max(0, min(150, round(150 * strength * alpha_scale))))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(glow, 2.0 + expansion * 0.25))
            glow_rect = image_rect.adjusted(
                -expansion,
                -expansion,
                expansion,
                expansion,
            )

            if self.persona_id == "fairy":
                painter.drawEllipse(glow_rect)
            else:
                painter.drawRoundedRect(glow_rect, 8 + expansion, 8 + expansion)

    def _paint_delamain_scan(self, painter, image_rect, color):
        painter.save()
        clip_path = QPainterPath()
        clip_path.addRoundedRect(image_rect, 7, 7)
        painter.setClipPath(clip_path)
        scan_y = image_rect.top() + image_rect.height() * self.phase
        band_color = QColor(color)
        band_color.setAlpha(34)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(band_color)
        painter.drawRect(
            QRectF(
                image_rect.left(),
                scan_y - max(3.0, image_rect.height() * 0.045),
                image_rect.width(),
                max(6.0, image_rect.height() * 0.09),
            )
        )
        scan_color = QColor(color)
        scan_color.setAlpha(205)
        painter.setPen(QPen(scan_color, 1.4, Qt.PenStyle.SolidLine))
        painter.drawLine(QLineF(image_rect.left(), scan_y, image_rect.right(), scan_y))
        painter.restore()

        marker_color = QColor(color)
        marker_color.setAlpha(220)
        painter.setPen(QPen(marker_color, 1.6))
        marker_half = max(3.0, image_rect.height() * 0.045)
        for marker_x in (image_rect.left() - 3, image_rect.right() + 3):
            painter.drawLine(
                QLineF(marker_x, scan_y - marker_half, marker_x, scan_y + marker_half)
            )

    def _paint_delamain_listening_hud(self, painter, image_rect, color, wave):
        hud_color = QColor(color)
        hud_color.setAlpha(round(170 + 70 * wave))
        painter.setPen(QPen(hud_color, 2.0))
        length = image_rect.width() * 0.18
        offset = 3.0
        left = image_rect.left() - offset
        right = image_rect.right() + offset
        top = image_rect.top() - offset
        bottom = image_rect.bottom() + offset
        for x, x_direction in ((left, 1), (right, -1)):
            for y, y_direction in ((top, 1), (bottom, -1)):
                painter.drawLine(QLineF(x, y, x + length * x_direction, y))
                painter.drawLine(QLineF(x, y, x, y + length * y_direction))

        painter.setPen(QPen(hud_color, 1.2))
        side_length = image_rect.height() * (0.12 + 0.04 * wave)
        center_y = image_rect.center().y()
        painter.drawLine(
            QLineF(left - 2, center_y - side_length, left - 2, center_y + side_length)
        )
        painter.drawLine(
            QLineF(right + 2, center_y - side_length, right + 2, center_y + side_length)
        )

    def _paint_delamain_thinking_hud(self, painter, image_rect, color):
        segment_length = image_rect.width() * 0.25
        inset = 2.5
        sides = (
            QLineF(image_rect.left() + inset, image_rect.top() - inset,
                   image_rect.left() + inset + segment_length, image_rect.top() - inset),
            QLineF(image_rect.right() + inset, image_rect.top() + inset,
                   image_rect.right() + inset, image_rect.top() + inset + segment_length),
            QLineF(image_rect.right() - inset, image_rect.bottom() + inset,
                   image_rect.right() - inset - segment_length, image_rect.bottom() + inset),
            QLineF(image_rect.left() - inset, image_rect.bottom() - inset,
                   image_rect.left() - inset, image_rect.bottom() - inset - segment_length),
        )
        active_side = int(self.phase * len(sides)) % len(sides)

        for index, line in enumerate(sides):
            distance = (index - active_side) % len(sides)
            hud_color = QColor(color)
            hud_color.setAlpha(max(60, 235 - distance * 52))
            painter.setPen(QPen(hud_color, 2.2 if distance == 0 else 1.4))
            painter.drawLine(line)

        center = image_rect.center()
        indicator = QColor(color)
        indicator.setAlpha(210)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(indicator)
        angle = self.phase * math.tau
        marker_radius = image_rect.width() * 0.42
        painter.drawEllipse(
            QPointF(
                center.x() + math.cos(angle) * marker_radius,
                center.y() + math.sin(angle) * marker_radius,
            ),
            1.8,
            1.8,
        )

    def _paint_delamain_response(self, painter, image_rect, color, wave):
        response_color = QColor(color)
        response_color.setAlpha(round(145 + 90 * wave))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(response_color, 1.2 + wave * 1.2))
        painter.drawRoundedRect(image_rect.adjusted(-3, -3, 3, 3), 9, 9)
        bar_width = image_rect.width() * (0.28 + 0.18 * wave)
        bar_y = image_rect.bottom() + 5
        painter.drawLine(
            QLineF(
                image_rect.center().x() - bar_width / 2,
                bar_y,
                image_rect.center().x() + bar_width / 2,
                bar_y,
            )
        )

    def _paint_fairy_core_rotation(self, painter, image_rect, avatar_pixmap):
        """Rotate only the Fairy's inner ring pixels at a constant speed."""
        center = image_rect.center()
        outer_radius = image_rect.width() * 0.365
        inner_radius = image_rect.width() * 0.135
        ring_clip = QPainterPath()
        ring_clip.setFillRule(Qt.FillRule.OddEvenFill)
        ring_clip.addEllipse(center, outer_radius, outer_radius)
        ring_clip.addEllipse(center, inner_radius, inner_radius)

        painter.save()
        painter.setClipPath(ring_clip, Qt.ClipOperation.IntersectClip)
        painter.translate(center)
        painter.rotate(self.phase * 360.0)
        painter.translate(-center)
        painter.drawPixmap(image_rect.toRect(), avatar_pixmap)
        painter.restore()

    def _paint_warning_overlay(self, painter, image_rect, color):
        warning_color = QColor(color)
        warning_color.setAlpha(210)
        warning_pen = QPen(warning_color, 1.8, Qt.PenStyle.DashLine)
        painter.setPen(warning_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        if self.persona_id == "fairy":
            painter.drawEllipse(image_rect.adjusted(-3, -3, 3, 3))
        else:
            painter.drawRoundedRect(image_rect.adjusted(-3, -3, 3, 3), 9, 9)

    def _paint_delamain_fallback(self, painter):
        color = self._state_color()
        wave = (math.sin(self.phase * math.tau) + 1) / 2
        center = QPointF(46, 46)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        for size, alpha in ((72, 70), (54, 110), (34, 175)):
            layer = QColor(color)
            layer.setAlpha(alpha)
            painter.setPen(QPen(layer, 1.4))
            painter.drawRect(QRectF(46 - size / 2, 46 - size / 2, size, size))

        core = QColor(color)
        core.setAlpha(230)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(core)
        core_size = 12 + (2 * wave if self.state == "thinking" else 0)
        painter.drawRect(
            QRectF(46 - core_size / 2, 46 - core_size / 2, core_size, core_size)
        )
        guide = QColor(color)
        guide.setAlpha(80)
        painter.setPen(QPen(guide, 1))
        painter.drawLine(QLineF(6, center.y(), 24, center.y()))
        painter.drawLine(QLineF(68, center.y(), 86, center.y()))

    def _paint_fairy_fallback(self, painter):
        color = self._state_color()
        wave = (math.sin(self.phase * math.tau) + 1) / 2
        glow = QColor(color)
        glow.setAlpha(int(48 + 28 * wave))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(glow)
        painter.drawEllipse(QRectF(13, 13, 66, 66))
        shell = QColor(self.theme["surface_alt"])
        painter.setBrush(shell)
        outline = QColor(color)
        outline.setAlpha(210)
        painter.setPen(QPen(outline, 1.8))
        painter.drawRoundedRect(QRectF(22, 25, 48, 46), 17, 17)
        eye_color = QColor(self.theme["accent_bright"])
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(eye_color)
        painter.drawRoundedRect(QRectF(34, 43, 6, 4), 3, 3)
        painter.drawRoundedRect(QRectF(52, 43, 6, 4), 3, 3)

    def _paint_neutral_fallback(self, painter):
        color = self._state_color()
        profile = self.visual_profile()
        wave = (math.sin(self.phase * math.tau) + 1) / 2
        pulse = 0.9 + 0.1 * wave
        outline = QColor(color)
        outline.setAlpha(int(165 * pulse))
        painter.setPen(QPen(outline, float(profile["border"])))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(QRectF(22, 22, 48, 48))
        painter.drawLine(QLineF(32, 46, 60, 46))

        if self.state not in {"idle", "complete", "error"}:
            marker = QColor(color)
            marker.setAlpha(int(220 * pulse))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(marker)
            painter.drawEllipse(QPointF(46, 46), 4, 4)
