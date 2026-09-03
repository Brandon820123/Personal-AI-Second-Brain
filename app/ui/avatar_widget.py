"""Cached Persona image avatars with lightweight state effects and fallbacks."""

import logging
import math
from enum import Enum
from pathlib import Path

from PySide6.QtCore import QElapsedTimer, QLineF, QPointF, QRectF, QSize, QTimer, Qt
from PySide6.QtGui import (
    QColor,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPolygonF,
    QRadialGradient,
)
from PySide6.QtWidgets import QWidget

from .avatar_animation_profiles import (
    DelamainAnimationProfile,
    get_persona_animation_profile,
)

try:
    from ..cloud_storage import cached_avatar_path
except ImportError:
    from cloud_storage import cached_avatar_path


LOGGER = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
AVATAR_DIRECTORY = PROJECT_ROOT / "assets" / "avatars"
AVATAR_ASSET_PATHS = {
    "delamain": AVATAR_DIRECTORY / "delamain.png",
    "fairy": AVATAR_DIRECTORY / "fairy.png",
}


def resolve_avatar_asset_path(persona_id):
    """Prefer a synchronized cloud avatar while retaining bundled fallbacks."""

    bundled_path = AVATAR_ASSET_PATHS.get(str(persona_id).strip().casefold())

    if bundled_path is None:
        return None

    return cached_avatar_path(bundled_path.name) or bundled_path

CONTINUOUS_ANIMATION_STATES = {
    "idle",
    "listening",
    "searching",
    "thinking",
    "responding",
}
# Backward-compatible public name used by lifecycle tests.
ACTIVE_STATES = CONTINUOUS_ANIMATION_STATES
FAIRY_ROTATION_DEGREES_PER_SECOND = 42.0
FAIRY_BREATHING_PERIOD_MS = 2100.0
FAIRY_BREATHING_MIN_SCALE = 0.98
FAIRY_BREATHING_MAX_SCALE = 1.05
FAIRY_STATIC_SETTLE_MS = 200.0
_FAIRY_BASE_SCALE_WAVE = (
    1.0 - FAIRY_BREATHING_MIN_SCALE
) / (FAIRY_BREATHING_MAX_SCALE - FAIRY_BREATHING_MIN_SCALE)
FAIRY_BREATHING_START_PHASE = math.acos(
    1.0 - 2.0 * _FAIRY_BASE_SCALE_WAVE
) / math.tau
FAIRY_WORKING_SETTLE_MS = 200.0
ENTRY_REVEAL_DURATION_MS = 550.0
ENTRY_REVEAL_START_SCALE = 0.94
ENTRY_REVEAL_OFFSET_PX = 6.0
DELAMAIN_ENTRY_REVEAL_DURATION_MS = DelamainAnimationProfile.entry_duration_ms
DELAMAIN_IDLE_PULSE_PERIOD_MS = DelamainAnimationProfile.idle_period_ms
AVATAR_LAYER_ORDER = ("background", "core", "foreground")


class AvatarAnimationMode(str, Enum):
    """Separate visual motion ownership from the dialogue's semantic state."""

    ENTRY_REVEAL = "entry_reveal"
    WORKING = "working"
    IDLE_BREATHING = "idle_breathing"
    HISTORY_STATIC = "history_static"

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
        self.animation_profile = get_persona_animation_profile(self.persona_id)
        self.theme = dict(theme)
        self.state = "idle"
        self.phase = 0.0
        self.animation_mode = AvatarAnimationMode.WORKING
        self.asset_path = None
        self.asset_warning = ""
        self.source_pixmap = QPixmap()
        self._animation_enabled = bool(animation_enabled)
        self._uses_default_asset_paths = asset_paths is None
        self._asset_paths = dict(AVATAR_ASSET_PATHS)
        self._frame_clock = QElapsedTimer()
        self._mode_clock = QElapsedTimer()
        self._mode_origin_phase = 0.0
        self._layer_origin_phase = 0.0
        self._settle_clock = QElapsedTimer()
        self._settling_to_static = False
        self._settle_from_scale = 1.0
        self._working_settle_clock = QElapsedTimer()
        self._working_to_idle = False
        self._working_exit_phase = 0.0
        self._entry_clock = QElapsedTimer()
        self._entry_target_mode = AvatarAnimationMode.HISTORY_STATIC

        if asset_paths is not None:
            self._asset_paths.update(asset_paths)

        avatar_size = max(40, int(display_size))
        self.setFixedSize(avatar_size, avatar_size)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        self.animation_timer = QTimer(self)
        self.animation_timer.setInterval(16)
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
        self.animation_profile = get_persona_animation_profile(self.persona_id)

        if theme is not None:
            self.theme = dict(theme)

        if self._uses_default_asset_paths:
            self.asset_path = resolve_avatar_asset_path(self.persona_id)
        else:
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
        self._mode_origin_phase = 0.0
        self._layer_origin_phase = 0.0
        self._settling_to_static = False
        self._settle_from_scale = 1.0
        self._working_to_idle = False
        self._working_exit_phase = 0.0
        self._entry_target_mode = AvatarAnimationMode.HISTORY_STATIC
        self._mode_clock.restart()
        self._sync_animation_timer()
        self.update()

    def reload_cached_asset(self, force=False):
        """Reload only the image source, preserving state and animation clocks."""

        if not self._uses_default_asset_paths:
            return False

        refreshed_path = resolve_avatar_asset_path(self.persona_id)

        if refreshed_path == self.asset_path and not force:
            return False

        self.asset_path = refreshed_path
        self.asset_warning = ""
        self.source_pixmap = QPixmap()

        if self.asset_path is not None:
            self.source_pixmap = self._load_source_pixmap(self.asset_path)

        self.update()
        return True

    def set_state(self, state, preserve_animation_mode=False):
        """Update effects while preserving animation continuity between states."""
        normalized_state = getattr(state, "value", state)

        if normalized_state == self.state:
            self._sync_animation_timer()
            return

        self.state = normalized_state

        target_mode = (
            AvatarAnimationMode.WORKING
            if self.state_uses_continuous_animation(normalized_state)
            else AvatarAnimationMode.HISTORY_STATIC
        )

        if self.animation_mode is AvatarAnimationMode.ENTRY_REVEAL:
            self._entry_target_mode = target_mode
            self.update()
            return

        if preserve_animation_mode:
            self._sync_animation_timer()
            self.update()
            return

        self.set_animation_mode(target_mode)
        self.update()

    @staticmethod
    def state_uses_continuous_animation(state):
        """Return whether a presentation state owns a continuous timer."""
        return getattr(state, "value", state) in CONTINUOUS_ANIMATION_STATES

    def set_continuous_animation_enabled(self, enabled):
        """Allow only the owning active panel to run a continuous effect."""
        self._animation_enabled = bool(enabled)

        if not self._animation_enabled and self._settling_to_static:
            self._finish_static_settle()

        self._sync_animation_timer()

    def set_animation_mode(self, mode):
        """Select static, working, or latest-response standby animation."""
        normalized_mode = (
            mode if isinstance(mode, AvatarAnimationMode) else AvatarAnimationMode(mode)
        )

        if normalized_mode is self.animation_mode:
            self._sync_animation_timer()
            return

        previous_mode = self.animation_mode

        if (
            previous_mode is AvatarAnimationMode.WORKING
            and normalized_mode is AvatarAnimationMode.IDLE_BREATHING
            and self.persona_id == "fairy"
        ):
            self._update_fairy_phase_from_clock()
            self._working_exit_phase = self.phase
            self._working_to_idle = True
            self._working_settle_clock.restart()
        else:
            self._working_to_idle = False

        if (
            previous_mode is AvatarAnimationMode.IDLE_BREATHING
            and normalized_mode is AvatarAnimationMode.HISTORY_STATIC
            and self.persona_id == "fairy"
            and self.state == "complete"
        ):
            self._update_fairy_phase_from_clock()
            self._settle_from_scale = self._fairy_breathing_scale(self.phase)
            self._settling_to_static = True
            self._settle_clock.restart()
        else:
            self._settling_to_static = False

        previous_layer_phase = self._layer_rotation_phase(self._entry_progress())
        self.animation_mode = normalized_mode

        if normalized_mode is AvatarAnimationMode.IDLE_BREATHING:
            self.phase = self.animation_profile.idle_start_phase
        else:
            self.phase = 0.0

        self._layer_origin_phase = previous_layer_phase
        self._mode_origin_phase = self.phase
        self._mode_clock.restart()
        self._sync_animation_timer()
        self.update()

    def start_entry_reveal(self, target_mode=None):
        """Play one internal reveal, then continue in the requested mode."""
        if target_mode is None:
            target_mode = (
                AvatarAnimationMode.WORKING
                if self.state_uses_continuous_animation(self.state)
                else AvatarAnimationMode.HISTORY_STATIC
            )

        self._entry_target_mode = (
            target_mode
            if isinstance(target_mode, AvatarAnimationMode)
            else AvatarAnimationMode(target_mode)
        )
        self._working_to_idle = False
        self._settling_to_static = False
        self.animation_mode = AvatarAnimationMode.ENTRY_REVEAL
        self.phase = 0.0
        self._entry_clock.restart()
        self._sync_animation_timer()
        self.update()

    def set_entry_target_mode(self, mode):
        """Update where an in-progress reveal should settle."""
        normalized_mode = (
            mode if isinstance(mode, AvatarAnimationMode) else AvatarAnimationMode(mode)
        )

        if self.animation_mode is AvatarAnimationMode.ENTRY_REVEAL:
            self._entry_target_mode = normalized_mode
        else:
            self.set_animation_mode(normalized_mode)

    @property
    def is_settling_to_static(self):
        return self._settling_to_static

    @property
    def is_transitioning_to_idle(self):
        return self._working_to_idle

    @property
    def entry_target_mode(self):
        return self._entry_target_mode

    def stop_animation(self):
        """Stop this widget's timer immediately."""
        if self._settling_to_static:
            self._finish_static_settle()

        self._working_to_idle = False
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
        if self._settling_to_static:
            self._finish_static_settle()

        self._working_to_idle = False
        self.animation_timer.stop()
        super().hideEvent(event)

    def closeEvent(self, event):
        if self._settling_to_static:
            self._finish_static_settle()

        self._working_to_idle = False
        self.animation_timer.stop()
        super().closeEvent(event)

    def _sync_animation_timer(self):
        should_animate = (
            self._animation_enabled
            and (
                self.animation_mode is not AvatarAnimationMode.HISTORY_STATIC
                or self._settling_to_static
            )
            and self.isVisible()
        )

        if should_animate and not self.animation_timer.isActive():
            self._frame_clock.restart()
            self._mode_origin_phase = self.phase
            self._mode_clock.restart()

            if self.animation_mode is AvatarAnimationMode.ENTRY_REVEAL:
                self._entry_clock.restart()

            self.animation_timer.start()
        elif not should_animate:
            self.animation_timer.stop()

    def _advance_animation(self):
        if self.animation_mode is AvatarAnimationMode.ENTRY_REVEAL:
            elapsed_ms = max(0, self._entry_clock.elapsed())
            self.phase = min(
                1.0,
                elapsed_ms / self.animation_profile.entry_duration_ms,
            )

            if self.phase >= 1.0:
                self._finish_entry_reveal()
            else:
                self.update()
            return

        if self.persona_id == "fairy":
            if self._settling_to_static:
                if self._settle_clock.elapsed() >= FAIRY_STATIC_SETTLE_MS:
                    self._finish_static_settle()
                    self._sync_animation_timer()
                else:
                    self.update()
                return

            self._update_fairy_phase_from_clock()

            if (
                self._working_to_idle
                and self._working_settle_clock.elapsed() >= FAIRY_WORKING_SETTLE_MS
            ):
                self._working_to_idle = False

            self.update()
            return

        profile_phase = self.animation_profile.phase_for_elapsed(
            self.state,
            self.animation_mode,
            self._mode_clock.elapsed(),
            self._mode_origin_phase,
        )

        if profile_phase is not None:
            self.phase = profile_phase
            self.update()
            return

        elapsed_ms = max(1, self._frame_clock.restart())
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

    def _finish_entry_reveal(self):
        target_mode = self._entry_target_mode
        entry_exit_phase = self._layer_rotation_phase(1.0)
        self.animation_mode = target_mode

        if target_mode is AvatarAnimationMode.IDLE_BREATHING:
            self.phase = self.animation_profile.idle_start_phase
        elif target_mode is AvatarAnimationMode.WORKING:
            self.phase = entry_exit_phase
        else:
            self.phase = 0.0

        self._layer_origin_phase = entry_exit_phase
        self._mode_origin_phase = self.phase
        self._mode_clock.restart()
        self._sync_animation_timer()
        self.update()

    def _entry_progress(self):
        if self.animation_mode is not AvatarAnimationMode.ENTRY_REVEAL:
            return 1.0

        return min(
            1.0,
            max(
                0.0,
                self._entry_clock.elapsed()
                / self.animation_profile.entry_duration_ms,
            ),
        )

    def _update_fairy_phase_from_clock(self):
        elapsed_ms = max(0, self._mode_clock.elapsed())

        if self.animation_mode is AvatarAnimationMode.IDLE_BREATHING:
            self.phase = (
                self._mode_origin_phase
                + elapsed_ms / FAIRY_BREATHING_PERIOD_MS
            ) % 1.0
        elif self.animation_mode is AvatarAnimationMode.WORKING:
            self.phase = (
                self._mode_origin_phase
                + FAIRY_ROTATION_DEGREES_PER_SECOND
                * elapsed_ms
                / 360_000.0
            ) % 1.0

    @staticmethod
    def _fairy_breathing_scale(phase):
        wave = (1.0 - math.cos(float(phase) * math.tau)) / 2.0
        return FAIRY_BREATHING_MIN_SCALE + wave * (
            FAIRY_BREATHING_MAX_SCALE - FAIRY_BREATHING_MIN_SCALE
        )

    def _static_settle_scale(self):
        if not self._settling_to_static:
            return 1.0

        progress = min(1.0, self._settle_clock.elapsed() / FAIRY_STATIC_SETTLE_MS)
        eased_progress = (1.0 - math.cos(progress * math.pi)) / 2.0
        return self._settle_from_scale + (
            1.0 - self._settle_from_scale
        ) * eased_progress

    def _finish_static_settle(self):
        self._settling_to_static = False
        self._settle_from_scale = 1.0
        self.phase = 0.0
        self.update()

    def _working_settle_phase(self):
        progress = min(
            1.0,
            self._working_settle_clock.elapsed() / FAIRY_WORKING_SETTLE_MS,
        )
        eased_progress = (1.0 - math.cos(progress * math.pi)) / 2.0
        shortest_delta = (-self._working_exit_phase + 0.5) % 1.0 - 0.5
        return (
            self._working_exit_phase + shortest_delta * eased_progress
        ) % 1.0

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
        base_image_rect = QRectF(
            padding,
            padding,
            self.width() - padding * 2,
            self.height() - padding * 2,
        )
        image_rect = QRectF(base_image_rect)
        border_color = self._state_color()
        glow_strength = float(profile["glow"])
        breathing_wave = (1.0 - math.cos(self.phase * math.tau)) / 2.0
        entry_progress = self._entry_progress()
        entry_ease = 1.0 - (1.0 - entry_progress) ** 3

        painter.save()

        if self.animation_mode is AvatarAnimationMode.ENTRY_REVEAL:
            entry_scale = self.animation_profile.entry_start_scale + (
                1.0 - self.animation_profile.entry_start_scale
            ) * entry_ease
            center = QRectF(self.rect()).center()
            painter.translate(
                0.0,
                self.animation_profile.entry_offset_px * (1.0 - entry_ease),
            )
            painter.translate(center)
            painter.scale(entry_scale, entry_scale)
            painter.translate(-center)
            glow_strength *= self.animation_profile.entry_glow_factor(
                entry_progress
            )

        if (
            self.persona_id == "fairy"
            and self.animation_mode is AvatarAnimationMode.IDLE_BREATHING
        ):
            breathing_scale = self._fairy_breathing_scale(self.phase)
            expansion = image_rect.width() * (breathing_scale - 1.0) / 2.0
            image_rect = image_rect.adjusted(
                -expansion,
                -expansion,
                expansion,
                expansion,
            )
            glow_strength *= 0.80 + breathing_wave * 0.20
        elif (
            self.animation_mode is AvatarAnimationMode.IDLE_BREATHING
            and self.persona_id != "delamain"
        ):
            glow_strength *= 0.84 + breathing_wave * 0.16
        elif self.persona_id == "fairy" and self._settling_to_static:
            settle_scale = self._static_settle_scale()
            expansion = image_rect.width() * (settle_scale - 1.0) / 2.0
            image_rect = image_rect.adjusted(
                -expansion,
                -expansion,
                expansion,
                expansion,
            )

        if self.persona_id != "delamain":
            if motion == "ambient_breathe":
                glow_strength *= 0.78 + wave * 0.22
            elif motion == "response_pulse":
                glow_strength *= 0.72 + wave * 0.42
            elif motion == "listening_hud":
                glow_strength *= 0.88 + wave * 0.18

        glow_strength = self.animation_profile.adjust_glow(
            self.state,
            self.animation_mode,
            self.phase,
            glow_strength,
        )

        painter.save()
        painter.setOpacity(self._entry_layer_opacity("background", entry_progress))
        self._paint_avatar_background(
            painter,
            image_rect,
            border_color,
            glow_strength,
            entry_progress,
        )
        self._paint_glow(painter, image_rect, border_color, glow_strength)
        painter.restore()

        prepared_width = base_image_rect.width()

        if self.persona_id == "fairy":
            prepared_width *= FAIRY_BREATHING_MAX_SCALE

        avatar_pixmap = self._prepared_avatar_pixmap(round(prepared_width))
        painter.save()
        painter.setOpacity(self._entry_layer_opacity("core", entry_progress))
        self.animation_profile.paint_core(
            painter,
            image_rect,
            avatar_pixmap,
            self.state,
            self.animation_mode,
            self.phase,
        )
        painter.restore()

        painter.save()
        painter.setOpacity(self._entry_layer_opacity("foreground", entry_progress))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(border_color, float(profile["border"])))

        if self.persona_id == "fairy":
            painter.drawEllipse(image_rect)
        else:
            painter.drawRoundedRect(image_rect, 7, 7)

        state_overlay_painted = self.animation_profile.paint_state_overlay(
            painter,
            image_rect,
            border_color,
            self.theme,
            self.state,
            self.animation_mode,
            self.phase,
            entry_progress,
        )

        if not state_overlay_painted and motion == "core_rotation" and self.animation_mode in {
            AvatarAnimationMode.ENTRY_REVEAL,
            AvatarAnimationMode.WORKING,
        }:
            rotation_phase = (
                self._layer_rotation_phase(entry_progress)
                if self.animation_mode is AvatarAnimationMode.ENTRY_REVEAL
                else self.phase
            )
            self._paint_fairy_core_rotation(
                painter,
                image_rect,
                avatar_pixmap,
                phase=rotation_phase,
            )
        elif self.persona_id == "fairy" and self._working_to_idle:
            self._paint_fairy_core_rotation(
                painter,
                image_rect,
                avatar_pixmap,
                phase=self._working_settle_phase(),
            )

        self._paint_avatar_foreground(
            painter,
            image_rect,
            border_color,
            entry_progress,
        )

        if motion in {"warning_frame", "warning_ring"}:
            self._paint_warning_overlay(painter, image_rect, border_color)

        painter.restore()
        painter.restore()

    def _entry_layer_opacity(self, layer, entry_progress):
        if self.animation_mode is not AvatarAnimationMode.ENTRY_REVEAL:
            return 1.0
        return self.animation_profile.entry_layer_opacity(layer, entry_progress)

    def _paint_avatar_background(
        self,
        painter,
        image_rect,
        color,
        glow_strength,
        entry_progress,
    ):
        """Paint the soft aura and persona geometry behind the sharp PNG core."""
        center = image_rect.center()
        aura_radius = image_rect.width() * 0.58
        aura = QRadialGradient(center, aura_radius)
        inner = QColor(color)
        inner.setAlpha(max(10, min(72, round(72 * glow_strength))))
        edge = QColor(color)
        edge.setAlpha(0)
        aura.setColorAt(0.28, inner)
        aura.setColorAt(1.0, edge)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(aura)
        painter.drawEllipse(center, aura_radius, aura_radius)

        if self.persona_id == "fairy":
            geometry_color = QColor(self.theme.get("accent_bright", color.name()))
            geometry_color.setAlpha(36)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(geometry_color, 1.15))
            radius = image_rect.width() * 0.51
            angle_offset = self._layer_rotation_phase(entry_progress) * math.tau
            polygon = QPolygonF(
                [
                    QPointF(
                        center.x() + math.cos(angle_offset + index * math.tau / 6) * radius,
                        center.y() + math.sin(angle_offset + index * math.tau / 6) * radius,
                    )
                    for index in range(6)
                ]
            )
            painter.drawPolygon(polygon)

        self.animation_profile.paint_background(
            painter,
            image_rect,
            color,
            self.theme,
            self.state,
            self.animation_mode,
            self.phase,
            entry_progress,
        )

    def _paint_avatar_foreground(self, painter, image_rect, color, entry_progress):
        """Paint crisp ring/frame accents above the persona image."""
        accent = QColor(self.theme.get("accent_bright", color.name()))
        if self.animation_mode is AvatarAnimationMode.IDLE_BREATHING:
            idle_wave = (1.0 - math.cos(self.phase * math.tau)) / 2.0
            accent.setAlpha(round(150 + idle_wave * 35))
        else:
            accent.setAlpha(185)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        if self.persona_id == "fairy":
            ring_rect = image_rect.adjusted(-3.5, -3.5, 3.5, 3.5)
            start_angle = self._layer_rotation_phase(entry_progress) * 360.0
            painter.setPen(QPen(accent, 1.35, Qt.PenStyle.SolidLine))
            painter.drawArc(ring_rect, round(start_angle * 16), round(72 * 16))
            painter.drawArc(
                ring_rect,
                round((start_angle + 180) * 16),
                round(38 * 16),
            )
            marker_angle = math.radians(start_angle + 72)
            marker_radius = ring_rect.width() / 2.0
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(accent)
            painter.drawEllipse(
                QPointF(
                    ring_rect.center().x() + math.cos(marker_angle) * marker_radius,
                    ring_rect.center().y() - math.sin(marker_angle) * marker_radius,
                ),
                1.7,
                1.7,
            )

        self.animation_profile.paint_foreground(
            painter,
            image_rect,
            color,
            self.theme,
            self.state,
            self.animation_mode,
            self.phase,
            entry_progress,
        )

        if self.animation_mode is AvatarAnimationMode.ENTRY_REVEAL:
            sweep = QColor(accent)
            sweep.setAlpha(round(210 * math.sin(entry_progress * math.pi)))
            painter.setPen(QPen(sweep, 1.4))

            if self.persona_id == "fairy":
                sweep_rect = image_rect.adjusted(-5, -5, 5, 5)
                painter.drawArc(
                    sweep_rect,
                    round((90 - entry_progress * 300) * 16),
                    round(48 * 16),
                )

    def _layer_rotation_phase(self, entry_progress):
        """Return a stable phase for decorative layers without extra timers."""
        if self.animation_mode is AvatarAnimationMode.ENTRY_REVEAL:
            return entry_progress * 0.42
        if self.animation_mode is AvatarAnimationMode.WORKING:
            return self.phase
        if self.animation_mode is AvatarAnimationMode.IDLE_BREATHING:
            return (
                self._layer_origin_phase + self._mode_clock.elapsed() / 12_000.0
            ) % 1.0
        return self._layer_origin_phase

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

    def _paint_fairy_core_rotation(
        self,
        painter,
        image_rect,
        avatar_pixmap,
        phase=None,
    ):
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
        rotation_phase = self.phase if phase is None else phase
        painter.rotate(rotation_phase * 360.0)
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
