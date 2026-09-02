"""Tests for reusable Persona dialogue panels and cached image avatars."""

import gc
import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRectF
from PySide6.QtGui import QImage, QPainter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from app.personas import get_persona
from app.ui import AvatarAnimationMode, PersonaDialoguePanel, PersonaState
from app.ui.avatar_widget import (
    ACTIVE_STATES,
    AVATAR_LAYER_ORDER,
    AVATAR_ASSET_PATHS,
    AVATAR_VISUAL_PROFILES,
    DELAMAIN_ENTRY_REVEAL_DURATION_MS,
    DELAMAIN_IDLE_PULSE_PERIOD_MS,
    ENTRY_REVEAL_DURATION_MS,
    ENTRY_REVEAL_OFFSET_PX,
    ENTRY_REVEAL_START_SCALE,
    FAIRY_BREATHING_MAX_SCALE,
    FAIRY_BREATHING_MIN_SCALE,
    FAIRY_BREATHING_PERIOD_MS,
    FAIRY_BREATHING_START_PHASE,
    FAIRY_ROTATION_DEGREES_PER_SECOND,
    FAIRY_STATIC_SETTLE_MS,
    FAIRY_WORKING_SETTLE_MS,
    PersonaAvatarWidget,
    avatar_cache_sizes,
    clear_avatar_pixmap_cache,
)
from app.ui.avatar_animation_profiles import (
    DelamainAnimationProfile,
    FairyAnimationProfile,
)
from app.ui.persona_dialogue_panel import STATUS_TEXT, parse_source_groups
from app.ui_themes import get_theme


class PersonaDialoguePanelTests(unittest.TestCase):
    """Check shared state, streaming, citations, errors, and animations."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.panels = []

    def tearDown(self):
        for panel in self.panels:
            panel.close()
            panel.deleteLater()
        self.app.processEvents()
        gc.collect()

    def make_panel(self, persona_id="fairy", state=PersonaState.IDLE):
        panel = PersonaDialoguePanel(
            get_persona(persona_id),
            get_theme(persona_id),
            state=state,
        )
        self.panels.append(panel)
        return panel

    def test_every_persona_has_text_for_every_state(self):
        for persona_id in ("delamain", "fairy", "neutral"):
            self.assertEqual(set(STATUS_TEXT[persona_id]), set(PersonaState))

    def test_avatar_and_status_remain_synchronized_for_every_state(self):
        panel = self.make_panel("delamain")
        panel.show()
        self.app.processEvents()

        for state in PersonaState:
            panel.set_state(state)
            self.app.processEvents()

            self.assertEqual(panel.state, state)
            self.assertEqual(panel.avatar.state, state.value)
            self.assertEqual(
                panel.status_label.text(),
                STATUS_TEXT["delamain"][state],
            )
            self.assertEqual(
                panel.avatar.animation_timer.isActive(),
                state.value in ACTIVE_STATES,
            )

    def test_delamain_and_fairy_define_visuals_for_every_state(self):
        for persona_id in ("delamain", "fairy"):
            self.assertEqual(
                set(AVATAR_VISUAL_PROFILES[persona_id]),
                {state.value for state in PersonaState},
            )

        self.assertEqual(
            AVATAR_VISUAL_PROFILES["delamain"]["searching"]["motion"],
            "vertical_scan",
        )
        self.assertEqual(
            AVATAR_VISUAL_PROFILES["delamain"]["thinking"]["motion"],
            "hud_cycle",
        )
        self.assertEqual(
            AVATAR_VISUAL_PROFILES["fairy"]["searching"]["motion"],
            "core_rotation",
        )
        self.assertEqual(
            AVATAR_VISUAL_PROFILES["fairy"]["error"]["motion"],
            "warning_ring",
        )

    def test_personas_select_separate_animation_profile_classes(self):
        fairy = PersonaAvatarWidget("fairy", get_theme("fairy"))
        delamain = PersonaAvatarWidget("delamain", get_theme("delamain"))

        self.assertIsInstance(fairy.animation_profile, FairyAnimationProfile)
        self.assertIsInstance(delamain.animation_profile, DelamainAnimationProfile)
        self.assertEqual(DELAMAIN_ENTRY_REVEAL_DURATION_MS, 720.0)
        self.assertEqual(DELAMAIN_IDLE_PULSE_PERIOD_MS, 4000.0)
        self.assertEqual(
            delamain.animation_profile.idle_indicator_style,
            "internal_face_scan",
        )
        self.assertEqual(
            delamain.animation_profile.monitoring_layer_style,
            "face_wave_grid",
        )
        self.assertEqual(delamain.animation_profile.grid_columns, 8)
        self.assertEqual(delamain.animation_profile.grid_rows, 10)
        self.assertEqual(delamain.animation_profile.distortion_strip_count, 18)
        self.assertEqual(delamain.animation_profile.idle_scan_duration_ms, 1500.0)
        self.assertEqual(delamain.animation_profile.face_scan_band_ratio, 0.14)
        self.assertAlmostEqual(
            delamain.animation_profile._idle_scan_progress(0.1875),
            0.5,
        )
        self.assertIsNone(delamain.animation_profile._idle_scan_progress(0.5))
        self.assertFalse(
            hasattr(delamain.animation_profile, "_paint_online_indicator")
        )
        self.assertFalse(
            hasattr(delamain.animation_profile, "_paint_scan_wave_indicator")
        )
        self.assertGreater(
            delamain.animation_profile.entry_layer_opacity("background", 0.12),
            delamain.animation_profile.entry_layer_opacity("core", 0.12),
        )

        fairy.close()
        fairy.deleteLater()
        delamain.close()
        delamain.deleteLater()

    def test_delamain_search_uses_absolute_time_and_keeps_portrait_stable(self):
        avatar = PersonaAvatarWidget("delamain", get_theme("delamain"))
        original_size = avatar.size()
        original_pixmap_key = avatar.source_pixmap.cacheKey()
        avatar.set_state(PersonaState.SEARCHING)
        avatar._mode_origin_phase = 0.10

        with patch.object(avatar._mode_clock, "elapsed", return_value=450):
            avatar._advance_animation()
        self.assertAlmostEqual(avatar.phase, 0.35, places=6)

        with patch.object(avatar._mode_clock, "elapsed", return_value=900):
            avatar._advance_animation()
        self.assertAlmostEqual(avatar.phase, 0.60, places=6)
        self.assertEqual(avatar.size(), original_size)
        self.assertEqual(avatar.source_pixmap.cacheKey(), original_pixmap_key)

        avatar.close()
        avatar.deleteLater()

    def test_delamain_active_states_animate_only_hud_layers(self):
        avatar = PersonaAvatarWidget("delamain", get_theme("delamain"))
        original_size = avatar.size()
        original_pixmap_key = avatar.source_pixmap.cacheKey()
        avatar.show()
        self.app.processEvents()

        for state in (
            PersonaState.IDLE,
            PersonaState.LISTENING,
            PersonaState.SEARCHING,
            PersonaState.THINKING,
            PersonaState.RESPONDING,
        ):
            avatar.set_state(state)
            avatar.phase = 0.12
            avatar.update()
            self.app.processEvents()
            before = avatar.grab().toImage().bits().tobytes()
            avatar.phase = 0.58
            avatar.update()
            self.app.processEvents()
            after = avatar.grab().toImage().bits().tobytes()
            self.assertNotEqual(before, after, state)

        self.assertEqual(avatar.size(), original_size)
        self.assertEqual(avatar.source_pixmap.cacheKey(), original_pixmap_key)
        avatar.close()
        avatar.deleteLater()

    def test_delamain_idle_face_grid_moves_between_low_frequency_scans(self):
        avatar = PersonaAvatarWidget("delamain", get_theme("delamain"))
        original_pixmap_key = avatar.source_pixmap.cacheKey()
        avatar.set_state(PersonaState.COMPLETE)
        avatar.set_animation_mode(AvatarAnimationMode.IDLE_BREATHING)
        avatar.show()
        self.app.processEvents()

        self.assertIsNone(avatar.animation_profile._idle_scan_progress(0.55))
        self.assertIsNone(avatar.animation_profile._idle_scan_progress(0.75))
        avatar.phase = 0.55
        avatar.update()
        self.app.processEvents()
        before = avatar.grab().toImage().bits().tobytes()
        avatar.phase = 0.75
        avatar.update()
        self.app.processEvents()
        after = avatar.grab().toImage().bits().tobytes()

        self.assertNotEqual(before, after)
        self.assertEqual(avatar.source_pixmap.cacheKey(), original_pixmap_key)
        avatar.close()
        avatar.deleteLater()

    def test_delamain_core_pixels_participate_in_signal_wave(self):
        avatar = PersonaAvatarWidget("delamain", get_theme("delamain"))
        avatar_pixmap = avatar._prepared_avatar_pixmap(90)
        image_rect = QRectF(7, 7, 90, 90)
        signatures = []

        for phase in (0.14, 0.61):
            image = QImage(104, 104, QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(0)
            painter = QPainter(image)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            avatar.animation_profile.paint_core(
                painter,
                image_rect,
                avatar_pixmap,
                "thinking",
                AvatarAnimationMode.WORKING,
                phase,
            )
            painter.end()
            signatures.append(hashlib.sha256(image.bits().tobytes()).hexdigest())

        self.assertNotEqual(signatures[0], signatures[1])
        avatar.close()
        avatar.deleteLater()

    def test_fairy_active_states_use_one_constant_rotation_language(self):
        active_motions = {
            AVATAR_VISUAL_PROFILES["fairy"][state]["motion"]
            for state in ACTIVE_STATES
        }

        self.assertEqual(active_motions, {"core_rotation"})
        self.assertEqual(FAIRY_ROTATION_DEGREES_PER_SECOND, 42.0)

    def test_fairy_rotation_keeps_widget_and_source_pixmap_stable(self):
        avatar = PersonaAvatarWidget("fairy", get_theme("fairy"))
        original_size = avatar.size()
        original_pixmap_key = avatar.source_pixmap.cacheKey()

        for state in ACTIVE_STATES:
            avatar.set_state(state)
            avatar.phase = 0.73
            self.assertEqual(avatar.size(), original_size)
            self.assertEqual(avatar.source_pixmap.cacheKey(), original_pixmap_key)

        avatar.close()
        avatar.deleteLater()

    def test_fairy_idle_breathing_uses_a_subtle_slow_mode(self):
        panel = self.make_panel("fairy", PersonaState.RESPONDING)
        panel.show()
        self.app.processEvents()

        panel.complete(keep_idle_animation=True)
        self.app.processEvents()

        self.assertEqual(
            panel.avatar.animation_mode,
            AvatarAnimationMode.IDLE_BREATHING,
        )
        self.assertTrue(panel.avatar.animation_timer.isActive())
        self.assertEqual(FAIRY_BREATHING_PERIOD_MS, 2100.0)
        self.assertEqual(FAIRY_BREATHING_MIN_SCALE, 0.98)
        self.assertEqual(FAIRY_BREATHING_MAX_SCALE, 1.05)
        self.assertEqual(panel.avatar.animation_timer.interval(), 16)
        self.assertAlmostEqual(
            panel.avatar._fairy_breathing_scale(FAIRY_BREATHING_START_PHASE),
            1.0,
            places=6,
        )
        self.assertEqual(panel.avatar._fairy_breathing_scale(0.0), 0.98)
        self.assertEqual(panel.avatar._fairy_breathing_scale(0.5), 1.05)

    def test_delamain_latest_idle_pulses_without_scaling_portrait(self):
        panel = self.make_panel("delamain", PersonaState.RESPONDING)
        panel.show()
        original_size = panel.avatar.size()
        panel.complete(keep_idle_animation=True)
        self.app.processEvents()

        self.assertEqual(
            panel.avatar.animation_mode,
            AvatarAnimationMode.IDLE_BREATHING,
        )
        self.assertTrue(panel.avatar.animation_timer.isActive())
        start_phase = panel.avatar.phase
        QTest.qWait(90)
        self.assertGreater(panel.avatar.phase, start_phase)
        self.assertEqual(panel.avatar.size(), original_size)

    def test_fairy_phase_uses_absolute_elapsed_time_not_callback_count(self):
        avatar = PersonaAvatarWidget("fairy", get_theme("fairy"))
        avatar.set_state(PersonaState.THINKING)
        avatar._mode_origin_phase = 0.25

        with patch.object(avatar._mode_clock, "elapsed", return_value=1000):
            avatar._advance_animation()

        expected_phase = 0.25 + FAIRY_ROTATION_DEGREES_PER_SECOND / 360.0
        self.assertAlmostEqual(avatar.phase, expected_phase, places=6)

        with patch.object(avatar._mode_clock, "elapsed", return_value=2000):
            avatar._advance_animation()

        expected_phase = 0.25 + 2 * FAIRY_ROTATION_DEGREES_PER_SECOND / 360.0
        self.assertAlmostEqual(avatar.phase, expected_phase, places=6)
        avatar.close()
        avatar.deleteLater()

    def test_entry_reveal_is_time_based_and_preserves_widget_geometry(self):
        avatar = PersonaAvatarWidget("fairy", get_theme("fairy"))
        original_size = avatar.size()
        avatar.start_entry_reveal(AvatarAnimationMode.WORKING)

        with patch.object(
            avatar._entry_clock,
            "elapsed",
            return_value=round(ENTRY_REVEAL_DURATION_MS / 2),
        ):
            avatar._advance_animation()
            self.assertAlmostEqual(avatar.phase, 0.5, places=2)

        self.assertEqual(avatar.size(), original_size)
        self.assertEqual(ENTRY_REVEAL_DURATION_MS, 550.0)
        self.assertEqual(ENTRY_REVEAL_START_SCALE, 0.94)
        self.assertEqual(ENTRY_REVEAL_OFFSET_PX, 6.0)
        self.assertEqual(AVATAR_LAYER_ORDER, ("background", "core", "foreground"))

        with patch.object(
            avatar._entry_clock,
            "elapsed",
            return_value=round(ENTRY_REVEAL_DURATION_MS),
        ):
            avatar._advance_animation()

        self.assertEqual(avatar.animation_mode, AvatarAnimationMode.WORKING)
        avatar.close()
        avatar.deleteLater()

    def test_entry_reveal_retargets_without_recreating_avatar(self):
        panel = self.make_panel("fairy", PersonaState.THINKING)
        panel.show()
        panel.start_avatar_entry_reveal(AvatarAnimationMode.WORKING)
        avatar_identity = id(panel.avatar)

        panel.append_text("Streaming")
        panel.complete(keep_idle_animation=True)

        self.assertEqual(id(panel.avatar), avatar_identity)
        self.assertEqual(panel.avatar.animation_mode, AvatarAnimationMode.ENTRY_REVEAL)
        self.assertEqual(
            panel.avatar.entry_target_mode,
            AvatarAnimationMode.IDLE_BREATHING,
        )

        with patch.object(
            panel.avatar._entry_clock,
            "elapsed",
            return_value=round(ENTRY_REVEAL_DURATION_MS),
        ):
            panel.avatar._advance_animation()

        self.assertEqual(
            panel.avatar.animation_mode,
            AvatarAnimationMode.IDLE_BREATHING,
        )

    def test_fairy_breathing_reuses_one_prepared_pixmap_size(self):
        clear_avatar_pixmap_cache()
        avatar = PersonaAvatarWidget("fairy", get_theme("fairy"))
        avatar.set_state(PersonaState.COMPLETE)
        avatar.set_animation_mode(AvatarAnimationMode.IDLE_BREATHING)
        avatar.show()

        for phase in (0.0, 0.125, 0.25, 0.5, 0.75, 0.875):
            avatar.phase = phase
            avatar.update()
            self.app.processEvents()
            avatar.grab()

        self.assertEqual(avatar_cache_sizes(), {"source": 1, "prepared": 1})
        avatar.close()
        avatar.deleteLater()

    def test_fairy_history_transition_settles_once_then_stops(self):
        panel = self.make_panel("fairy", PersonaState.RESPONDING)
        panel.show()
        panel.complete(keep_idle_animation=True)
        panel.avatar.phase = 0.5
        panel.set_avatar_animation_mode(AvatarAnimationMode.HISTORY_STATIC)
        self.app.processEvents()

        self.assertTrue(panel.avatar.is_settling_to_static)
        self.assertTrue(panel.avatar.animation_timer.isActive())
        self.assertEqual(FAIRY_STATIC_SETTLE_MS, 200.0)
        QTest.qWait(250)
        self.assertFalse(panel.avatar.is_settling_to_static)
        self.assertFalse(panel.avatar.animation_timer.isActive())
        self.assertEqual(panel.avatar.phase, 0.0)

    def test_fairy_working_to_idle_transition_starts_at_base_scale(self):
        panel = self.make_panel("fairy", PersonaState.RESPONDING)
        panel.show()
        QTest.qWait(80)
        avatar_identity = id(panel.avatar)
        timer_identity = id(panel.avatar.animation_timer)

        panel.complete(keep_idle_animation=True)
        self.app.processEvents()

        self.assertEqual(id(panel.avatar), avatar_identity)
        self.assertEqual(id(panel.avatar.animation_timer), timer_identity)
        self.assertTrue(panel.avatar.is_transitioning_to_idle)
        self.assertAlmostEqual(
            panel.avatar._fairy_breathing_scale(panel.avatar.phase),
            1.0,
            delta=0.001,
        )
        self.assertEqual(FAIRY_WORKING_SETTLE_MS, 200.0)
        QTest.qWait(250)
        self.assertFalse(panel.avatar.is_transitioning_to_idle)
        self.assertTrue(panel.avatar.animation_timer.isActive())
        self.assertEqual(
            panel.avatar.animation_mode,
            AvatarAnimationMode.IDLE_BREATHING,
        )

    def test_primary_states_have_distinct_rendered_overlays(self):
        for persona_id in ("delamain", "fairy"):
            avatar = PersonaAvatarWidget(persona_id, get_theme(persona_id))
            avatar.show()
            self.app.processEvents()
            signatures = {}

            for state in (
                PersonaState.IDLE,
                PersonaState.LISTENING,
                PersonaState.SEARCHING,
                PersonaState.THINKING,
                PersonaState.RESPONDING,
            ):
                avatar.set_state(state)
                avatar.phase = 0.31
                avatar.update()
                self.app.processEvents()
                image = avatar.grab().toImage()
                signatures[state] = hashlib.sha256(
                    image.bits().tobytes()
                ).hexdigest()

            self.assertEqual(len(set(signatures.values())), len(signatures))
            avatar.close()
            avatar.deleteLater()

    def test_every_continuous_state_changes_between_animation_frames(self):
        avatar = PersonaAvatarWidget("fairy", get_theme("fairy"))
        avatar.show()
        self.app.processEvents()

        for state in ACTIVE_STATES:
            avatar.set_state(state)
            avatar.phase = 0.12
            avatar.update()
            self.app.processEvents()
            before = avatar.grab().toImage().bits().tobytes()
            avatar.phase = 0.48
            avatar.update()
            self.app.processEvents()
            after = avatar.grab().toImage().bits().tobytes()
            self.assertNotEqual(before, after, state)

        avatar.close()
        avatar.deleteLater()

    def test_streaming_updates_the_same_panel_and_avatar(self):
        panel = self.make_panel("fairy", PersonaState.LISTENING)
        original_identity = id(panel)
        original_avatar_identity = id(panel.avatar)
        original_timer_identity = id(panel.avatar.animation_timer)
        panel.show()
        self.app.processEvents()

        panel.set_state(PersonaState.THINKING)
        self.app.processEvents()
        self.assertTrue(panel.avatar.animation_timer.isActive())
        QTest.qWait(90)
        thinking_phase = panel.avatar.phase
        panel.append_text("找到")
        responding_start_phase = panel.avatar.phase
        panel.append_text("资料了。")

        self.assertEqual(id(panel), original_identity)
        self.assertEqual(id(panel.avatar), original_avatar_identity)
        self.assertEqual(id(panel.avatar.animation_timer), original_timer_identity)
        self.assertEqual(panel._text, "找到资料了。")
        self.assertEqual(panel.state, PersonaState.RESPONDING)
        self.assertEqual(panel.avatar.state, "responding")
        self.assertTrue(panel.avatar.animation_timer.isActive())
        self.assertGreater(thinking_phase, 0.0)
        self.assertAlmostEqual(responding_start_phase, thinking_phase, places=5)

        QTest.qWait(100)
        responding_later_phase = panel.avatar.phase
        self.assertGreater(
            (responding_later_phase - responding_start_phase) % 1.0,
            0.0,
        )

        panel.append_text("继续生成。")
        self.assertTrue(panel.avatar.animation_timer.isActive())
        self.assertAlmostEqual(panel.avatar.phase, responding_later_phase, places=5)

        panel.complete()
        completed_phase = panel.avatar.phase
        QTest.qWait(100)

        self.assertEqual(panel.state, PersonaState.COMPLETE)
        self.assertFalse(panel.avatar.animation_timer.isActive())
        self.assertEqual(panel.avatar.phase, completed_phase)
        self.assertEqual(
            panel.avatar.animation_mode,
            AvatarAnimationMode.HISTORY_STATIC,
        )

    def test_completed_historical_panel_remains_static(self):
        panel = self.make_panel("fairy", PersonaState.RESPONDING)
        panel.avatar._advance_animation()
        active_phase = panel.avatar.phase

        panel.complete()
        completed_phase = panel.avatar.phase
        QTest.qWait(120)

        self.assertFalse(panel.avatar.animation_timer.isActive())
        self.assertGreater(active_phase, 0)
        self.assertEqual(panel.avatar.phase, completed_phase)

    def test_persona_status_language_changes(self):
        delamain = self.make_panel("delamain", PersonaState.SEARCHING)
        fairy = self.make_panel("fairy", PersonaState.SEARCHING)
        neutral = self.make_panel("neutral", PersonaState.SEARCHING)

        self.assertEqual(delamain.status_label.text(), "正在检索知识库")
        self.assertEqual(fairy.status_label.text(), "正在查找相关资料")
        self.assertEqual(neutral.status_label.text(), "检索中")

    def test_pdf_sources_are_grouped_without_internal_ids(self):
        sources = (
            "Sources:\n"
            "[1] Textbook.pdf - page 191 - chunk 526\n"
            "[2] Textbook.pdf - page 194 - chunk 532\n"
            "[3] notes.md - chunk 4"
        )

        self.assertEqual(
            parse_source_groups(sources),
            [
                ("Textbook.pdf", ["Page 191", "Page 194"]),
                ("notes.md", ["Chunk 4"]),
            ],
        )
        panel = self.make_panel("delamain", PersonaState.RESPONDING)
        panel.set_sources(sources)

        self.assertTrue(panel.sources_section.isVisibleTo(panel))
        displayed_sources = "\n".join(
            label.text()
            for label in panel.sources_section.findChildren(type(panel.status_label))
        )
        self.assertIn("Page 191", displayed_sources)
        self.assertNotIn("526", displayed_sources)

    def test_error_state_keeps_readable_message_in_panel(self):
        panel = self.make_panel("neutral", PersonaState.THINKING)

        panel.set_error("无法连接本地 Ollama，请启动 Ollama 后重试。")

        self.assertEqual(panel.state, PersonaState.ERROR)
        self.assertIn("无法连接本地 Ollama", panel.response_label.text())
        self.assertNotIn("Traceback", panel.response_label.text())
        self.assertFalse(panel.avatar.animation_timer.isActive())

    def test_avatar_uses_the_image_asset_and_panel_identity(self):
        panel = self.make_panel("delamain")

        self.assertIsInstance(panel.avatar, PersonaAvatarWidget)
        self.assertEqual(panel.avatar.persona_id, "delamain")
        self.assertEqual(panel.avatar.theme["accent"], get_theme("delamain")["accent"])
        self.assertTrue(panel.avatar.uses_image_asset)
        self.assertEqual(panel.avatar.asset_path, AVATAR_ASSET_PATHS["delamain"])

    def test_avatar_assets_are_square_opaque_and_have_no_white_edge(self):
        for persona_id in ("delamain", "fairy"):
            image = QImage(str(AVATAR_ASSET_PATHS[persona_id]))

            self.assertFalse(image.isNull())
            self.assertEqual(image.width(), image.height())

            edge_pixels = []

            for position in range(image.width()):
                edge_pixels.extend(
                    (
                        image.pixelColor(position, 0),
                        image.pixelColor(position, image.height() - 1),
                        image.pixelColor(0, position),
                        image.pixelColor(image.width() - 1, position),
                    )
                )

            self.assertTrue(all(pixel.alpha() == 255 for pixel in edge_pixels))
            self.assertFalse(
                any(
                    pixel.red() > 248
                    and pixel.green() > 248
                    and pixel.blue() > 248
                    for pixel in edge_pixels
                )
            )

    def test_missing_avatar_logs_warning_and_uses_programmatic_fallback(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            missing_path = Path(temporary_directory) / "missing.png"

            with self.assertLogs("app.ui.avatar_widget", level="WARNING"):
                avatar = PersonaAvatarWidget(
                    "delamain",
                    get_theme("delamain"),
                    asset_paths={"delamain": missing_path},
                )

            avatar.show()
            self.app.processEvents()
            fallback_image = avatar.grab().toImage()

            self.assertFalse(avatar.uses_image_asset)
            self.assertIn("programmatic fallback", avatar.asset_warning)
            self.assertFalse(fallback_image.isNull())
            avatar.close()
            avatar.deleteLater()

    def test_source_and_scaled_pixmaps_are_shared_by_widget_size(self):
        clear_avatar_pixmap_cache()
        first = PersonaAvatarWidget("fairy", get_theme("fairy"), display_size=78)
        second = PersonaAvatarWidget("fairy", get_theme("fairy"), display_size=78)
        first.show()
        second.show()
        self.app.processEvents()
        first.grab()
        second.grab()

        self.assertEqual(avatar_cache_sizes(), {"source": 1, "prepared": 1})
        first.close()
        second.close()
        first.deleteLater()
        second.deleteLater()

    def test_hidden_panel_stops_continuous_avatar_animation(self):
        panel = self.make_panel("fairy", PersonaState.SEARCHING)
        panel.show()
        self.app.processEvents()

        self.assertTrue(panel.avatar.animation_timer.isActive())

        panel.hide()
        self.app.processEvents()

        self.assertFalse(panel.avatar.animation_timer.isActive())

    def test_panel_uses_qt_fade_animation_when_shown(self):
        panel = self.make_panel("fairy")
        panel.show()
        self.app.processEvents()

        self.assertTrue(panel._appearance_started)
        self.assertEqual(panel.appearance_animation.duration(), 200)
        self.assertEqual(panel.appearance_animation.animationCount(), 2)
        self.assertEqual(
            panel.position_animation.startValue().y()
            - panel.position_animation.endValue().y(),
            6,
        )
        self.assertIsNotNone(panel.graphicsEffect())


if __name__ == "__main__":
    unittest.main()
