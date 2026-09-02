"""Persona-specific timing and QPainter overlays for image avatars."""

import math

from PySide6.QtCore import QLineF, QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QLinearGradient,
    QPainterPath,
    QPen,
    QRadialGradient,
)


def _clamp(value, minimum=0.0, maximum=1.0):
    return max(minimum, min(maximum, float(value)))


def _smoothstep(value):
    progress = _clamp(value)
    return progress * progress * (3.0 - 2.0 * progress)


class PersonaAnimationProfile:
    """Base timing contract used by the generic avatar compositor."""

    persona_id = "neutral"
    entry_duration_ms = 550.0
    entry_start_scale = 0.94
    entry_offset_px = 6.0
    idle_period_ms = 2100.0
    idle_start_phase = 0.0

    def entry_layer_opacity(self, layer, progress):
        del layer
        return 1.0 - (1.0 - _clamp(progress)) ** 3

    def entry_glow_factor(self, progress):
        return 0.60 + 0.40 * _smoothstep(progress)

    def phase_for_elapsed(self, state, animation_mode, elapsed_ms, origin_phase):
        del state
        if getattr(animation_mode, "value", animation_mode) != "idle_breathing":
            return None
        return (origin_phase + max(0.0, elapsed_ms) / self.idle_period_ms) % 1.0

    def adjust_glow(self, state, animation_mode, phase, strength):
        del state, animation_mode, phase
        return strength

    def paint_core(
        self,
        painter,
        image_rect,
        avatar_pixmap,
        state,
        animation_mode,
        phase,
    ):
        del state, animation_mode, phase
        painter.drawPixmap(image_rect.toRect(), avatar_pixmap)

    def paint_background(
        self,
        painter,
        image_rect,
        color,
        theme,
        state,
        animation_mode,
        phase,
        entry_progress,
    ):
        del painter, image_rect, color, theme, state, animation_mode, phase
        del entry_progress

    def paint_state_overlay(
        self,
        painter,
        image_rect,
        color,
        theme,
        state,
        animation_mode,
        phase,
        entry_progress,
    ):
        del painter, image_rect, color, theme, state, animation_mode, phase
        del entry_progress
        return False

    def paint_foreground(
        self,
        painter,
        image_rect,
        color,
        theme,
        state,
        animation_mode,
        phase,
        entry_progress,
    ):
        del painter, image_rect, color, theme, state, animation_mode, phase
        del entry_progress


class FairyAnimationProfile(PersonaAnimationProfile):
    """Fairy timing remains owned by its existing circular renderer."""

    persona_id = "fairy"
    idle_start_phase = math.acos(
        1.0 - 2.0 * ((1.0 - 0.98) / (1.05 - 0.98))
    ) / math.tau


class DelamainAnimationProfile(PersonaAnimationProfile):
    """Calm, precise system-boot and HUD animation for Delamain."""

    persona_id = "delamain"
    entry_duration_ms = 720.0
    entry_start_scale = 0.95
    entry_offset_px = 4.0
    idle_period_ms = 4000.0
    idle_indicator_style = "internal_face_scan"
    monitoring_layer_style = "face_wave_grid"
    idle_scan_duration_ms = 1500.0
    face_scan_band_ratio = 0.14
    grid_columns = 8
    grid_rows = 10
    distortion_strip_count = 18
    state_period_ms = {
        "idle": 4000.0,
        "listening": 2800.0,
        "searching": 1800.0,
        "thinking": 1600.0,
        "responding": 3000.0,
        "complete": 4000.0,
    }

    def entry_layer_opacity(self, layer, progress):
        progress = _clamp(progress)
        if layer == "background":
            return _smoothstep(progress / 0.32)
        if layer == "core":
            return _smoothstep((progress - 0.16) / 0.58)
        return _smoothstep((progress - 0.06) / 0.66)

    def entry_glow_factor(self, progress):
        return 0.38 + 0.62 * _smoothstep(progress)

    def phase_for_elapsed(self, state, animation_mode, elapsed_ms, origin_phase):
        mode = getattr(animation_mode, "value", animation_mode)
        if mode == "history_static":
            return None
        period_ms = self.idle_period_ms if mode == "idle_breathing" else (
            self.state_period_ms.get(state)
        )
        if not period_ms:
            return None
        return (origin_phase + max(0.0, elapsed_ms) / period_ms) % 1.0

    def adjust_glow(self, state, animation_mode, phase, strength):
        mode = getattr(animation_mode, "value", animation_mode)
        wave = (1.0 + math.cos(phase * math.tau)) / 2.0
        if mode == "idle_breathing" or state in {"idle", "complete"}:
            return strength * (0.85 + 0.15 * wave)
        if state == "listening":
            return strength * (0.92 + 0.08 * wave)
        if state == "thinking":
            return strength * (0.90 + 0.10 * wave)
        if state == "responding":
            return strength * (0.94 + 0.06 * wave)
        return strength

    def paint_core(
        self,
        painter,
        image_rect,
        avatar_pixmap,
        state,
        animation_mode,
        phase,
    ):
        """Render the full portrait through restrained horizontal signal strips."""
        mode = getattr(animation_mode, "value", animation_mode)
        strength = {
            "idle": 0.48,
            "listening": 0.62,
            "searching": 0.92,
            "thinking": 0.82,
            "responding": 0.68,
            "complete": 0.48,
        }.get(state, 0.0)

        if mode == "history_static" or state == "error" or strength <= 0.0:
            painter.drawPixmap(image_rect.toRect(), avatar_pixmap)
            return

        painter.drawPixmap(image_rect.toRect(), avatar_pixmap)
        strip_height = image_rect.height() / self.distortion_strip_count
        maximum_offset = 1.08
        painter.save()
        portrait_clip = QPainterPath()
        portrait_clip.addRoundedRect(image_rect, 7, 7)
        painter.setClipPath(portrait_clip)

        for strip_index in range(self.distortion_strip_count):
            strip_top = image_rect.top() + strip_index * strip_height
            strip_rect = QRectF(
                image_rect.left(),
                strip_top,
                image_rect.width(),
                strip_height + 0.65,
            )
            strip_wave = math.sin(
                strip_index * 0.72 + phase * math.tau
            )
            slow_drift = math.sin(
                strip_index * 0.19 - phase * math.tau * 0.55
            )
            offset_x = maximum_offset * strength * (
                strip_wave * 0.72 + slow_drift * 0.28
            )
            painter.save()
            painter.setClipRect(strip_rect, Qt.ClipOperation.IntersectClip)
            painter.translate(offset_x, 0.0)
            painter.drawPixmap(image_rect.toRect(), avatar_pixmap)
            painter.restore()

        painter.restore()

    def paint_background(
        self,
        painter,
        image_rect,
        color,
        theme,
        state,
        animation_mode,
        phase,
        entry_progress,
    ):
        del state, animation_mode, entry_progress
        center = image_rect.center()
        atmosphere = QRadialGradient(center, image_rect.width() * 0.66)
        inner = QColor(color)
        inner.setAlpha(32)
        middle = QColor(color)
        middle.setAlpha(13)
        transparent = QColor(color)
        transparent.setAlpha(0)
        atmosphere.setColorAt(0.12, inner)
        atmosphere.setColorAt(0.62, middle)
        atmosphere.setColorAt(1.0, transparent)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(atmosphere)
        painter.drawEllipse(center, image_rect.width() * 0.64, image_rect.height() * 0.64)

        surround = QColor(theme.get("page", "#07111c"))
        painter.setBrush(surround)
        painter.drawRoundedRect(image_rect.adjusted(-2, -2, 2, 2), 8, 8)

        frame = QColor(color)
        frame.setAlpha(38 + round(10 * (1.0 + math.cos(phase * math.tau)) / 2.0))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(frame, 1.0))
        painter.drawRoundedRect(image_rect.adjusted(-5, -4, 5, 4), 10, 10)
        painter.drawLine(
            QLineF(image_rect.left() - 8, center.y(), image_rect.left() - 3, center.y())
        )
        painter.drawLine(
            QLineF(image_rect.right() + 3, center.y(), image_rect.right() + 8, center.y())
        )

    def paint_state_overlay(
        self,
        painter,
        image_rect,
        color,
        theme,
        state,
        animation_mode,
        phase,
        entry_progress,
    ):
        del theme, animation_mode, entry_progress
        monitoring_strength = {
            "idle": 0.38,
            "listening": 0.52,
            "searching": 0.78,
            "thinking": 0.72,
            "responding": 0.58,
            "complete": 0.38,
        }.get(state, 0.0)
        scan_progress = None

        if state in {"searching", "thinking"}:
            scan_progress = phase
        elif state in {"idle", "complete"}:
            scan_progress = self._idle_scan_progress(phase)

        if monitoring_strength:
            self._paint_face_monitoring_layer(
                painter,
                image_rect,
                color,
                phase,
                monitoring_strength,
                scan_progress,
            )

        if state == "listening":
            self._paint_listening(painter, image_rect, color, phase)
        elif state == "searching":
            self._paint_searching(painter, image_rect, color, phase)
        elif state == "thinking":
            self._paint_thinking(painter, image_rect, color, phase)
        elif state == "responding":
            self._paint_responding(painter, image_rect, color, phase)
        elif state == "error":
            self._paint_error_indicator(painter, image_rect, color)
        elif state in {"idle", "complete"}:
            if scan_progress is not None:
                self._paint_face_scan(
                    painter,
                    image_rect,
                    color,
                    scan_progress,
                    intensity=0.58,
                )
        return True

    def paint_foreground(
        self,
        painter,
        image_rect,
        color,
        theme,
        state,
        animation_mode,
        phase,
        entry_progress,
    ):
        del theme
        mode = getattr(animation_mode, "value", animation_mode)
        wave = (1.0 + math.cos(phase * math.tau)) / 2.0
        accent = QColor(color)
        alpha = 148 + round(32 * wave) if mode == "idle_breathing" else 190
        if state == "error":
            alpha = 125
        accent.setAlpha(alpha)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(accent, 1.35))
        self._paint_corners(painter, image_rect, image_rect.width() * 0.12, 4.0)

        if mode == "entry_reveal":
            scan_progress = _smoothstep((entry_progress - 0.22) / 0.58)
            scan_alpha = round(215 * math.sin(scan_progress * math.pi))
            if scan_alpha > 0:
                scan = QColor(color)
                scan.setAlpha(scan_alpha)
                scan_y = image_rect.top() + image_rect.height() * scan_progress
                painter.setPen(QPen(scan, 1.35))
                painter.drawLine(
                    QLineF(image_rect.left() - 3, scan_y, image_rect.right() + 3, scan_y)
                )

    @staticmethod
    def _paint_corners(painter, image_rect, length, offset):
        left = image_rect.left() - offset
        right = image_rect.right() + offset
        top = image_rect.top() - offset
        bottom = image_rect.bottom() + offset
        for x, direction_x in ((left, 1), (right, -1)):
            for y, direction_y in ((top, 1), (bottom, -1)):
                painter.drawLine(QLineF(x, y, x + length * direction_x, y))
                painter.drawLine(QLineF(x, y, x, y + length * direction_y))

    def _paint_listening(self, painter, image_rect, color, phase):
        wave = (1.0 + math.cos(phase * math.tau)) / 2.0
        hud = QColor(color)
        hud.setAlpha(round(185 + 45 * wave))
        painter.setPen(QPen(hud, 1.9))
        self._paint_corners(painter, image_rect, image_rect.width() * 0.19, 3.0)
        center_y = image_rect.center().y()
        side_length = image_rect.height() * (0.12 + 0.025 * wave)
        for x in (image_rect.left() - 5, image_rect.right() + 5):
            painter.drawLine(QLineF(x, center_y - side_length, x, center_y + side_length))

    def _paint_searching(self, painter, image_rect, color, phase):
        self._paint_face_scan(
            painter,
            image_rect,
            color,
            phase,
            intensity=1.0,
        )
        band_height = image_rect.height() * self.face_scan_band_ratio
        scan_y = (
            image_rect.top()
            - band_height / 2.0
            + (image_rect.height() + band_height) * phase
        )

        marker = QColor(color)
        marker.setAlpha(220)
        painter.setPen(QPen(marker, 1.55))
        marker_half = image_rect.height() * 0.05
        for marker_x in (image_rect.left() - 3, image_rect.right() + 3):
            painter.drawLine(
                QLineF(marker_x, scan_y - marker_half, marker_x, scan_y + marker_half)
            )

        data = QColor(color)
        data.setAlpha(150)
        painter.setPen(QPen(data, 1.0))
        tick_count = 3
        active_tick = int(phase * tick_count) % tick_count
        for index in range(tick_count):
            y = image_rect.top() + image_rect.height() * (0.23 + index * 0.17)
            width = image_rect.width() * (0.055 + (0.035 if index == active_tick else 0.0))
            painter.drawLine(QLineF(image_rect.right() + 5, y, image_rect.right() + 5 + width, y))

    def _paint_thinking(self, painter, image_rect, color, phase):
        self._paint_face_scan(
            painter,
            image_rect,
            color,
            phase,
            intensity=0.72,
        )
        length = image_rect.width() * 0.24
        inset = 2.5
        sides = (
            QLineF(image_rect.left() + inset, image_rect.top() - inset,
                   image_rect.left() + inset + length, image_rect.top() - inset),
            QLineF(image_rect.right() + inset, image_rect.top() + inset,
                   image_rect.right() + inset, image_rect.top() + inset + length),
            QLineF(image_rect.right() - inset, image_rect.bottom() + inset,
                   image_rect.right() - inset - length, image_rect.bottom() + inset),
            QLineF(image_rect.left() - inset, image_rect.bottom() - inset,
                   image_rect.left() - inset, image_rect.bottom() - inset - length),
        )
        active_position = phase * len(sides)
        for index, line in enumerate(sides):
            distance = min(
                (index - active_position) % len(sides),
                (active_position - index) % len(sides),
            )
            hud = QColor(color)
            hud.setAlpha(max(70, round(230 - distance * 58)))
            painter.setPen(QPen(hud, 2.0 if distance < 0.65 else 1.25))
            painter.drawLine(line)

        indicator = QColor(color)
        indicator.setAlpha(190)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(indicator)
        marker_y = image_rect.top() + image_rect.height() * (0.30 + 0.40 * phase)
        painter.drawEllipse(QPointF(image_rect.left() - 5, marker_y), 1.5, 1.5)

    def _paint_responding(self, painter, image_rect, color, phase):
        wave = (1.0 + math.cos(phase * math.tau)) / 2.0
        active = QColor(color)
        active.setAlpha(round(178 + 42 * wave))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(active, 1.65 + 0.35 * wave))
        painter.drawRoundedRect(image_rect.adjusted(-3, -3, 3, 3), 9, 9)
        bar_y = image_rect.bottom() + 5
        bar_width = image_rect.width() * (0.29 + 0.12 * wave)
        painter.drawLine(
            QLineF(
                image_rect.center().x() - bar_width / 2,
                bar_y,
                image_rect.center().x() + bar_width / 2,
                bar_y,
            )
        )
        tick_x = image_rect.left() + image_rect.width() * (0.18 + 0.64 * phase)
        painter.drawLine(QLineF(tick_x, image_rect.top() - 5, tick_x + 5, image_rect.top() - 5))

    def _paint_face_monitoring_layer(
        self,
        painter,
        image_rect,
        color,
        phase,
        strength,
        scan_progress,
    ):
        """Overlay a clipped sampling grid and restrained full-face refresh waves."""
        painter.save()
        clip = QPainterPath()
        clip.addRoundedRect(image_rect, 7, 7)
        painter.setClipPath(clip)

        scan_response = (
            math.sin(_clamp(scan_progress) * math.pi)
            if scan_progress is not None
            else 0.0
        )
        if scan_progress is not None:
            screen_response = QColor(color)
            screen_response.setAlpha(round((7 + 17 * scan_response) * strength))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(screen_response)
            painter.drawRect(image_rect)

            curtain_height = image_rect.height() * 0.56
            curtain_center = (
                image_rect.top()
                - curtain_height / 2.0
                + (image_rect.height() + curtain_height) * scan_progress
            )
            curtain_top = curtain_center - curtain_height / 2.0
            curtain_bottom = curtain_center + curtain_height / 2.0
            curtain = QLinearGradient(
                0.0,
                curtain_top,
                0.0,
                curtain_bottom,
            )
            transparent = QColor(color)
            transparent.setAlpha(0)
            curtain_soft = QColor(color)
            curtain_soft.setAlpha(round(20 * strength))
            curtain_focus = QColor(color)
            curtain_focus.setAlpha(
                round((34 + 24 * scan_response) * strength)
            )
            curtain.setColorAt(0.0, transparent)
            curtain.setColorAt(0.24, curtain_soft)
            curtain.setColorAt(0.52, curtain_focus)
            curtain.setColorAt(0.80, curtain_soft)
            curtain.setColorAt(1.0, transparent)
            painter.setBrush(curtain)
            painter.drawRect(
                QRectF(
                    image_rect.left(),
                    curtain_top,
                    image_rect.width(),
                    curtain_height,
                )
            )

        breath = 0.86 + 0.14 * (1.0 + math.cos(phase * math.tau)) / 2.0
        grid = QColor(color)
        grid.setAlpha(
            round(
                (12 + 13 * strength)
                * breath
                * (1.0 + 0.48 * scan_response)
            )
        )
        painter.setPen(QPen(grid, 0.55))
        column_width = image_rect.width() / self.grid_columns
        row_height = image_rect.height() / self.grid_rows

        for column in range(1, self.grid_columns):
            x = image_rect.left() + column * column_width
            painter.drawLine(QLineF(x, image_rect.top(), x, image_rect.bottom()))

        for row in range(1, self.grid_rows):
            y = image_rect.top() + row * row_height
            painter.drawLine(QLineF(image_rect.left(), y, image_rect.right(), y))

        refresh_spacing = max(4.0, image_rect.height() / 17.0)
        refresh_offset = phase * refresh_spacing
        refresh_y = image_rect.top() - refresh_spacing + refresh_offset
        refresh_index = 0
        while refresh_y < image_rect.bottom():
            variation = (
                1.0
                + math.sin(refresh_index * 0.83 + phase * math.tau)
            ) / 2.0
            refresh = QColor(color)
            refresh.setAlpha(round((4 + 10 * variation) * strength))
            painter.setPen(QPen(refresh, 0.65))
            painter.drawLine(
                QLineF(
                    image_rect.left(),
                    refresh_y,
                    image_rect.right(),
                    refresh_y,
                )
            )
            refresh_y += refresh_spacing
            refresh_index += 1

        ripple_amplitude = max(0.38, image_rect.height() * 0.0045)
        sample_count = 24
        for wave_index, wave_origin in enumerate((0.16, 0.48, 0.80)):
            center_y = image_rect.top() + image_rect.height() * (
                (wave_origin + phase * 0.34) % 1.0
            )
            ripple = QPainterPath()
            for index in range(sample_count + 1):
                position = index / sample_count
                x = image_rect.left() + image_rect.width() * position
                y = center_y + math.sin(
                    position * math.tau * 1.10
                    + phase * math.tau
                    + wave_index * 0.72
                ) * ripple_amplitude
                if index == 0:
                    ripple.moveTo(x, y)
                else:
                    ripple.lineTo(x, y)
            ripple_color = QColor(color)
            ripple_color.setAlpha(round((18 + wave_index * 3) * strength))
            painter.setPen(
                QPen(
                    ripple_color,
                    0.75,
                    Qt.PenStyle.SolidLine,
                    Qt.PenCapStyle.RoundCap,
                )
            )
            painter.drawPath(ripple)

        painter.restore()

    def _idle_scan_progress(self, phase):
        active_ratio = self.idle_scan_duration_ms / self.idle_period_ms
        if phase >= active_ratio:
            return None
        return _smoothstep(phase / active_ratio)

    def _paint_face_scan(
        self,
        painter,
        image_rect,
        color,
        progress,
        intensity,
    ):
        """Sweep a clipped, softly rippled identification band across the face."""
        progress = _clamp(progress)
        intensity = _clamp(intensity)
        band_height = image_rect.height() * self.face_scan_band_ratio
        scan_y = (
            image_rect.top()
            - band_height / 2.0
            + (image_rect.height() + band_height) * progress
        )
        band_top = scan_y - band_height / 2.0
        band_bottom = scan_y + band_height / 2.0

        painter.save()
        clip = QPainterPath()
        clip.addRoundedRect(image_rect, 7, 7)
        painter.setClipPath(clip)

        gradient = QLinearGradient(0.0, band_top, 0.0, band_bottom)
        transparent = QColor(color)
        transparent.setAlpha(0)
        soft = QColor(color)
        soft.setAlpha(round(24 * intensity))
        focus = QColor(color)
        focus.setAlpha(round(50 * intensity))
        gradient.setColorAt(0.0, transparent)
        gradient.setColorAt(0.28, soft)
        gradient.setColorAt(0.52, focus)
        gradient.setColorAt(0.78, soft)
        gradient.setColorAt(1.0, transparent)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(gradient)
        painter.drawRect(
            QRectF(
                image_rect.left(),
                band_top,
                image_rect.width(),
                band_height,
            )
        )

        ripple_amplitude = max(0.45, image_rect.height() * 0.006)
        sample_count = 28
        for relative_y, alpha, width in (
            (0.22, 52, 0.70),
            (0.50, 108, 1.00),
            (0.78, 44, 0.65),
        ):
            ripple = QPainterPath()
            for index in range(sample_count + 1):
                position = index / sample_count
                x = image_rect.left() + image_rect.width() * position
                wave_offset = math.sin(
                    position * math.tau * 1.15 + progress * math.tau
                ) * ripple_amplitude
                y = band_top + band_height * relative_y + wave_offset
                if index == 0:
                    ripple.moveTo(x, y)
                else:
                    ripple.lineTo(x, y)
            line = QColor(color)
            line.setAlpha(round(alpha * intensity))
            painter.setPen(
                QPen(
                    line,
                    width,
                    Qt.PenStyle.SolidLine,
                    Qt.PenCapStyle.RoundCap,
                )
            )
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(ripple)

        fine_line = QColor(color)
        fine_line.setAlpha(round(25 * intensity))
        painter.setPen(QPen(fine_line, 0.6))
        line_spacing = max(3.0, image_rect.height() * 0.035)
        line_y = band_top + line_spacing
        while line_y < band_bottom:
            painter.drawLine(
                QLineF(image_rect.left(), line_y, image_rect.right(), line_y)
            )
            line_y += line_spacing

        enhanced_grid = QColor(color)
        enhanced_grid.setAlpha(round(32 * intensity))
        painter.setPen(QPen(enhanced_grid, 0.65))
        column_width = image_rect.width() / self.grid_columns
        for column in range(1, self.grid_columns):
            x = image_rect.left() + column * column_width
            painter.drawLine(QLineF(x, band_top, x, band_bottom))
        painter.restore()

        frame_response = QColor(color)
        frame_response.setAlpha(round((52 + 72 * intensity) * math.sin(progress * math.pi)))
        painter.setPen(QPen(frame_response, 1.0 + 0.35 * intensity))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(image_rect.adjusted(-2, -2, 2, 2), 8, 8)

    @staticmethod
    def _paint_error_indicator(painter, image_rect, color):
        warning = QColor(color)
        warning.setAlpha(185)
        painter.setPen(QPen(warning, 1.4))
        y = image_rect.top() - 5
        painter.drawLine(QLineF(image_rect.right() - 14, y, image_rect.right() - 4, y))


_PROFILES = {
    "fairy": FairyAnimationProfile(),
    "delamain": DelamainAnimationProfile(),
    "neutral": PersonaAnimationProfile(),
}


def get_persona_animation_profile(persona_id):
    """Return the stateless visual profile for one Persona."""
    return _PROFILES.get(str(persona_id).strip().casefold(), _PROFILES["neutral"])
