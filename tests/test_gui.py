"""Offscreen smoke tests for the PySide6 desktop interface."""

import gc
import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from app import gui
from app.personas import get_persona
from app.ui import (
    AvatarAnimationMode,
    PersonaAvatarWidget,
    PersonaDialoguePanel,
    PersonaState,
)
from app.ui_themes import THEMES, build_stylesheet, get_theme


class GuiSmokeTests(unittest.TestCase):
    """Check that the window, pages, library, and persona UI initialize."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.documents = [
            {
                "source_id": "textbook",
                "filename": "Textbook.pdf",
                "file_type": ".pdf",
                "chunk_count": 1620,
                "page_count": 554,
                "source_path": "C:/Private/Textbook.pdf",
            }
        ]
        self.health_patch = patch.object(gui.MainWindow, "_run_health_check")
        self.list_patch = patch("app.gui.list_documents", return_value=self.documents)
        self.health_patch.start()
        self.list_patch.start()
        self.window = gui.MainWindow()

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        self.list_patch.stop()
        self.health_patch.stop()
        gc.collect()

    def test_window_has_all_pages_and_textbook_row(self):
        self.assertEqual(self.window.pages.count(), 4)
        self.assertEqual(self.window.knowledge_table.rowCount(), 1)
        self.assertEqual(self.window.knowledge_table.item(0, 0).text(), "Textbook.pdf")
        self.assertEqual(self.window.knowledge_table.item(0, 2).text(), "1620")
        self.assertEqual(self.window.knowledge_table.item(0, 3).text(), "554")
        self.assertEqual(self.window.chat_mode.count(), 2)
        self.assertIsInstance(self.window.identity_avatar, PersonaAvatarWidget)
        self.assertEqual(
            self.window.identity_avatar.persona_id,
            self.window.active_persona["id"],
        )
        self.assertEqual(
            set(self.window.persona_avatar_widgets),
            {"delamain", "fairy", "neutral"},
        )
        self.assertTrue(
            self.window.persona_avatar_widgets["delamain"].uses_image_asset
        )
        self.assertTrue(self.window.persona_avatar_widgets["fairy"].uses_image_asset)
        self.assertFalse(
            self.window.persona_avatar_widgets["neutral"].uses_image_asset
        )

    @patch("app.gui.switch_persona")
    def test_persona_switch_updates_name_and_greeting(self, mock_switch):
        selected_persona = next(
            persona
            for persona in gui.list_personas()
            if persona["id"] != self.window.active_persona["id"]
        )
        mock_switch.return_value = selected_persona

        previous_panels = self.window.conversation_widget.findChildren(
            PersonaDialoguePanel
        )
        self.window.change_persona(selected_persona["id"])
        panels = self.window.conversation_widget.findChildren(PersonaDialoguePanel)

        self.assertEqual(self.window.active_persona["id"], selected_persona["id"])
        self.assertEqual(self.window.current_theme["id"], selected_persona["id"])
        self.assertIn(
            selected_persona["display_name"],
            self.window.chat_persona_label.text(),
        )
        self.assertEqual(
            self.window.identity_name.text(),
            selected_persona["display_name"].upper(),
        )
        self.assertEqual(
            self.window.identity_status.text(),
            self.window.current_theme["identity_status"],
        )
        self.assertEqual(
            self.window.identity_avatar.persona_id,
            selected_persona["id"],
        )
        self.assertIn(
            self.window.current_theme["accent"],
            self.window.styleSheet(),
        )
        self.assertEqual(len(panels), len(previous_panels) + 1)
        self.assertEqual(panels[-1]._text, selected_persona["greeting"])
        self.assertEqual(panels[-1].persona_id, selected_persona["id"])

    def test_chat_cards_align_user_right_and_assistant_left(self):
        user_message = self.window._add_message("user", "YOU", "User question")
        ai_message = self.window._add_persona_panel(
            get_persona("fairy"),
            PersonaState.COMPLETE,
            "AI answer",
        )
        rows = self.window.message_container.findChildren(gui.MessageRow)
        user_row = next(row for row in rows if row.message is user_message)
        ai_row = next(row for row in rows if row.message is ai_message)

        self.assertIsNotNone(user_row.layout().itemAt(0).spacerItem())
        self.assertIs(user_row.layout().itemAt(1).widget(), user_message)
        self.assertIs(ai_row.layout().itemAt(0).widget(), ai_message)
        self.assertIsNotNone(ai_row.layout().itemAt(1).spacerItem())

    def test_rag_sources_are_grouped_into_clean_cards(self):
        source_text = (
            "Sources:\n"
            "[1] Textbook.pdf - page 191 - chunk 526\n"
            "[2] Textbook.pdf - page 194 - chunk 532\n"
            "[3] notes.md - chunk 4"
        )

        panel = self.window._add_persona_panel(
            get_persona("fairy"),
            PersonaState.RESPONDING,
            "Grounded answer",
        )
        panel.set_sources(source_text)

        self.assertEqual(
            panel.source_groups,
            [
                ("Textbook.pdf", ["Page 191", "Page 194"]),
                ("notes.md", ["Chunk 4"]),
            ],
        )
        self.assertEqual(
            len(panel.findChildren(gui.QFrame, "dialogueSourceCard")),
            2,
        )

    def test_normal_chat_evolves_one_panel_through_streaming_states(self):
        def fake_worker(operation, message, **callbacks):
            callbacks["on_state"]("thinking")
            callbacks["on_token"]("RAG 会先检索资料。")
            callbacks["on_success"](None)
            callbacks["on_finished"]()

        self.window.message_input.setPlainText("给我简单解释一下 RAG。")

        with patch.object(self.window, "_run_worker", side_effect=fake_worker):
            self.window.send_message()

        panel = self.window.current_ai_panel
        self.assertEqual(
            panel.state_history,
            [
                PersonaState.LISTENING,
                PersonaState.THINKING,
                PersonaState.RESPONDING,
                PersonaState.COMPLETE,
            ],
        )
        self.assertIn("RAG", panel._text)
        self.assertIn(panel, self.window.findChildren(PersonaDialoguePanel))

    def test_rag_chat_keeps_sources_inside_the_streamed_panel(self):
        source_text = "Sources:\n[1] Textbook.pdf - page 191 - chunk 526"

        def fake_worker(operation, message, **callbacks):
            callbacks["on_state"]("searching")
            callbacks["on_state"]("thinking")
            callbacks["on_token"]("温室气体会吸收地表辐射。")
            callbacks["on_success"](source_text)
            callbacks["on_finished"]()

        self.window.chat_mode.setCurrentIndex(1)
        self.window.message_input.setPlainText("为什么温室气体会导致升温？")

        with patch.object(self.window, "_run_worker", side_effect=fake_worker):
            self.window.send_message()

        panel = self.window.current_ai_panel
        self.assertEqual(panel.state, PersonaState.COMPLETE)
        self.assertIn(PersonaState.SEARCHING, panel.state_history)
        self.assertEqual(panel.source_groups, [("Textbook.pdf", ["Page 191"])])

    def test_chat_error_remains_in_the_same_persona_panel(self):
        def fake_worker(operation, message, **callbacks):
            callbacks["on_state"]("thinking")
            callbacks["on_error"]("无法连接本地 Ollama，请启动 Ollama 后重试。")
            callbacks["on_finished"]()

        self.window.message_input.setPlainText("你好")

        with patch.object(self.window, "_run_worker", side_effect=fake_worker):
            self.window.send_message()

        panel = self.window.current_ai_panel
        self.assertEqual(panel.state, PersonaState.ERROR)
        self.assertIn("无法连接本地 Ollama", panel._text)

    @patch("app.gui.switch_persona")
    def test_historical_panel_keeps_its_original_persona_theme(self, mock_switch):
        self.window.active_persona = get_persona("delamain")
        self.window._update_persona_display()
        old_panel = self.window._add_persona_panel(
            get_persona("delamain"),
            PersonaState.COMPLETE,
            "Delamain answer",
        )
        old_stylesheet = old_panel.styleSheet()
        mock_switch.return_value = get_persona("fairy")

        self.window.change_persona("fairy")
        panels = self.window.findChildren(PersonaDialoguePanel)

        self.assertEqual(old_panel.persona_id, "delamain")
        self.assertEqual(old_panel.theme["id"], "delamain")
        self.assertEqual(old_panel.styleSheet(), old_stylesheet)
        self.assertEqual(old_panel.avatar.asset_path.name, "delamain.png")
        self.assertEqual(panels[-1].persona_id, "fairy")
        self.assertEqual(panels[-1].avatar.asset_path.name, "fairy.png")

    @patch("app.gui.switch_persona")
    def test_delamain_fairy_delamain_keeps_historical_avatar_assets(
        self,
        mock_switch,
    ):
        self.window.active_persona = get_persona("delamain")
        self.window._update_persona_display()
        original_panel = self.window._add_persona_panel(
            get_persona("delamain"),
            PersonaState.COMPLETE,
            "Original Delamain answer",
        )
        mock_switch.side_effect = [get_persona("fairy"), get_persona("delamain")]

        self.window.change_persona("fairy")
        fairy_panel = self.window.findChildren(PersonaDialoguePanel)[-1]
        self.window.change_persona("delamain")
        final_panel = self.window.findChildren(PersonaDialoguePanel)[-1]

        self.assertEqual(original_panel.avatar.asset_path.name, "delamain.png")
        self.assertEqual(fairy_panel.avatar.asset_path.name, "fairy.png")
        self.assertEqual(final_panel.avatar.asset_path.name, "delamain.png")
        self.assertEqual(self.window.identity_avatar.persona_id, "delamain")

    def test_only_newest_active_panel_runs_continuous_avatar_animation(self):
        self.window.show()
        first = self.window._add_persona_panel(
            get_persona("delamain"),
            PersonaState.SEARCHING,
        )
        self.app.processEvents()

        self.assertTrue(first.avatar.animation_timer.isActive())

        second = self.window._add_persona_panel(
            get_persona("fairy"),
            PersonaState.THINKING,
        )
        self.app.processEvents()

        self.assertFalse(first.avatar.animation_timer.isActive())
        self.assertTrue(second.avatar.animation_timer.isActive())

        second.hide()
        self.app.processEvents()
        self.assertFalse(second.avatar.animation_timer.isActive())

    def test_latest_completed_fairy_breathes_until_next_user_message(self):
        self.window.active_persona = get_persona("fairy")
        self.window._update_persona_display()
        self.window.show()
        panel = self.window._add_persona_panel(
            get_persona("fairy"),
            PersonaState.RESPONDING,
            "First answer",
        )
        self.window.current_ai_panel = panel
        self.window._complete_persona_panel(panel)
        self.app.processEvents()

        self.assertEqual(panel.state, PersonaState.COMPLETE)
        self.assertEqual(
            panel.avatar.animation_mode,
            AvatarAnimationMode.IDLE_BREATHING,
        )
        self.assertTrue(panel.avatar.animation_timer.isActive())
        idle_start_phase = panel.avatar.phase
        QTest.qWait(100)
        self.assertGreater(panel.avatar.phase, idle_start_phase)

        self.window.message_input.setPlainText("Next question")

        with patch.object(self.window, "_run_worker"):
            self.window.send_message()

        next_panel = self.window.current_ai_panel
        self.app.processEvents()
        self.assertEqual(
            panel.avatar.animation_mode,
            AvatarAnimationMode.HISTORY_STATIC,
        )
        self.assertTrue(panel.avatar.is_settling_to_static)
        QTest.qWait(250)
        self.assertFalse(panel.avatar.animation_timer.isActive())
        self.assertFalse(panel.avatar.is_settling_to_static)
        self.assertEqual(panel.avatar.phase, 0.0)
        self.assertIsNot(next_panel, panel)
        self.assertEqual(
            next_panel.avatar.animation_mode,
            AvatarAnimationMode.ENTRY_REVEAL,
        )
        self.assertEqual(
            next_panel.avatar.entry_target_mode,
            AvatarAnimationMode.WORKING,
        )
        self.assertTrue(next_panel.avatar.animation_timer.isActive())

        next_panel.set_state(PersonaState.THINKING)
        next_panel.append_text("Second answer")
        self.window._complete_persona_panel(next_panel)
        self.app.processEvents()

        fairy_panels = [
            candidate
            for candidate in self.window.findChildren(PersonaDialoguePanel)
            if candidate.persona_id == "fairy"
        ]
        animated_panels = [
            candidate
            for candidate in fairy_panels
            if candidate.avatar.animation_timer.isActive()
        ]
        self.assertEqual(animated_panels, [next_panel])
        self.assertEqual(
            next_panel.avatar.entry_target_mode,
            AvatarAnimationMode.IDLE_BREATHING,
        )
        QTest.qWait(600)
        self.assertEqual(
            next_panel.avatar.animation_mode,
            AvatarAnimationMode.IDLE_BREATHING,
        )

    def test_five_fairy_answers_keep_exactly_one_animation_timer(self):
        self.window.active_persona = get_persona("fairy")
        self.window._update_persona_display()
        self.window.show()
        completed_panels = []

        def finish_immediately(operation, message, **callbacks):
            del operation, message
            callbacks["on_state"]("thinking")
            callbacks["on_token"]("Answer")
            callbacks["on_success"](None)
            callbacks["on_finished"]()

        with patch.object(
            self.window,
            "_run_worker",
            side_effect=finish_immediately,
        ):
            for question_number in range(5):
                self.window.message_input.setPlainText(
                    f"Question {question_number + 1}"
                )
                self.window.send_message()
                self.app.processEvents()
                completed_panels.append(self.window.current_ai_panel)
                QTest.qWait(250)
                animated_panels = [
                    panel
                    for panel in completed_panels
                    if panel.avatar.animation_timer.isActive()
                ]
                self.assertEqual(animated_panels, [self.window.current_ai_panel])

        for historical_panel in completed_panels[:-1]:
            self.assertEqual(
                historical_panel.avatar.animation_mode,
                AvatarAnimationMode.HISTORY_STATIC,
            )
            self.assertFalse(historical_panel.avatar.animation_timer.isActive())

        self.assertEqual(
            completed_panels[-1].avatar.entry_target_mode,
            AvatarAnimationMode.IDLE_BREATHING,
        )
        QTest.qWait(600)
        self.assertEqual(
            completed_panels[-1].avatar.animation_mode,
            AvatarAnimationMode.IDLE_BREATHING,
        )

    def test_latest_chat_avatar_reveals_on_return_then_resumes_idle(self):
        self.window._retire_latest_fairy_idle_panel()
        self.window.active_persona = get_persona("fairy")
        self.window._update_persona_display(add_greeting=True)
        panel = self.window.latest_completed_fairy_panel
        self.window.show()
        self.app.processEvents()

        self.assertEqual(panel.avatar.animation_mode, AvatarAnimationMode.ENTRY_REVEAL)
        self.assertEqual(
            panel.avatar.entry_target_mode,
            AvatarAnimationMode.IDLE_BREATHING,
        )
        QTest.qWait(600)
        self.assertEqual(
            panel.avatar.animation_mode,
            AvatarAnimationMode.IDLE_BREATHING,
        )

        self.window._show_page(1)
        self.window._show_page(0)
        self.app.processEvents()

        self.assertEqual(panel.avatar.animation_mode, AvatarAnimationMode.ENTRY_REVEAL)
        self.assertEqual(
            panel.avatar.entry_target_mode,
            AvatarAnimationMode.IDLE_BREATHING,
        )
        QTest.qWait(600)
        self.assertEqual(
            panel.avatar.animation_mode,
            AvatarAnimationMode.IDLE_BREATHING,
        )

    def test_delamain_greeting_reveals_then_uses_latest_only_hud_idle(self):
        self.window._retire_latest_idle_avatar_panel()
        self.window.active_persona = get_persona("delamain")
        self.window._update_persona_display(add_greeting=True)
        panel = self.window.latest_idle_avatar_panel
        self.window.show()
        self.app.processEvents()

        self.assertEqual(panel.avatar.animation_mode, AvatarAnimationMode.ENTRY_REVEAL)
        self.assertEqual(
            panel.avatar.entry_target_mode,
            AvatarAnimationMode.IDLE_BREATHING,
        )
        QTest.qWait(600)
        self.assertEqual(
            panel.avatar.animation_mode,
            AvatarAnimationMode.IDLE_BREATHING,
        )
        self.assertTrue(panel.avatar.animation_timer.isActive())

        self.window.message_input.setPlainText("Next system request")

        with patch.object(self.window, "_run_worker"):
            self.window.send_message()

        self.app.processEvents()
        self.assertEqual(
            panel.avatar.animation_mode,
            AvatarAnimationMode.HISTORY_STATIC,
        )
        self.assertFalse(panel.avatar.animation_timer.isActive())

    def test_avatar_remains_square_when_window_is_resized(self):
        self.window.show()
        original_size = self.window.identity_avatar.size()

        self.window.resize(1500, 940)
        self.app.processEvents()

        self.assertEqual(original_size.width(), original_size.height())
        self.assertEqual(self.window.identity_avatar.size(), original_size)

    def test_idle_state_uses_active_persona_theme(self):
        self.assertTrue(self.window.idle_state.isVisibleTo(self.window))
        self.assertEqual(
            self.window.idle_state.theme["id"],
            self.window.active_persona["id"],
        )

        self.window.user_message_count = 2
        self.window._update_persona_display()

        self.assertTrue(self.window.idle_state.isHidden())

    @patch("app.gui.QFileDialog.getOpenFileName")
    def test_import_uses_windows_file_picker(self, mock_picker):
        mock_picker.return_value = ("", "")

        self.window.choose_import_file()

        mock_picker.assert_called_once()
        self.assertIn("*.pdf", mock_picker.call_args.args[3])


class ThemeConfigurationTests(unittest.TestCase):
    """Verify theme definitions stay complete, distinct, and centralized."""

    def test_all_personas_have_distinct_theme_accents(self):
        self.assertEqual(set(THEMES), {"delamain", "fairy", "neutral"})
        self.assertEqual(len({theme["accent"] for theme in THEMES.values()}), 3)
        self.assertEqual(len({theme["idle_kind"] for theme in THEMES.values()}), 3)

    def test_unknown_persona_uses_neutral_theme(self):
        self.assertEqual(get_theme("unknown")["id"], "neutral")

    def test_stylesheet_uses_theme_configuration(self):
        fairy = get_theme("fairy")
        stylesheet = build_stylesheet(fairy)

        self.assertIn(fairy["accent"], stylesheet)
        self.assertIn(fairy["background"], stylesheet)
        self.assertIn(f"border-radius: {fairy['card_radius']}px", stylesheet)


if __name__ == "__main__":
    unittest.main()
