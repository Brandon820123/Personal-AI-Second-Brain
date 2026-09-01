"""Tests for reusable Persona dialogue panels and cached image avatars."""

import gc
import hashlib
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from app.personas import get_persona
from app.ui import PersonaDialoguePanel, PersonaState
from app.ui.avatar_widget import (
    ACTIVE_STATES,
    AVATAR_ASSET_PATHS,
    AVATAR_VISUAL_PROFILES,
    PersonaAvatarWidget,
    avatar_cache_sizes,
    clear_avatar_pixmap_cache,
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
            "search_orbit",
        )
        self.assertEqual(
            AVATAR_VISUAL_PROFILES["fairy"]["error"]["motion"],
            "warning_ring",
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
        panel.show()
        self.app.processEvents()

        panel.set_state(PersonaState.THINKING)
        self.app.processEvents()
        self.assertTrue(panel.avatar.animation_timer.isActive())
        panel.append_text("找到")
        panel.append_text("资料了。")

        self.assertEqual(id(panel), original_identity)
        self.assertEqual(panel._text, "找到资料了。")
        self.assertEqual(panel.state, PersonaState.RESPONDING)
        self.assertEqual(panel.avatar.state, "responding")
        self.assertTrue(panel.avatar.animation_timer.isActive())

        panel.complete()

        self.assertEqual(panel.state, PersonaState.COMPLETE)
        self.assertFalse(panel.avatar.animation_timer.isActive())

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
