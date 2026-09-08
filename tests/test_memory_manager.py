"""Tests for conservative automatic memory extraction and consolidation."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.memory_manager import (
    MEMORY_CONFIDENCE_THRESHOLD,
    MemoryManager,
    extract_memory_candidate,
    finalize_chat_memory,
)


class MemoryManagerAutomationTests(unittest.TestCase):
    """Exercise candidate quality rules against an isolated SQLite database."""

    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        database_path = Path(self.temporary_directory.name) / "memory.db"
        self.manager = MemoryManager(db_path=database_path)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_meaningless_or_short_lived_messages_are_not_saved(self):
        messages = (
            "今天天气不错",
            "我今天有点难过",
            "怎么实现 RAG？",
            "谢谢你的帮助",
        )

        for message in messages:
            result = self.manager.process_chat_message(message)
            self.assertEqual(result["action"], "rejected")

        self.assertEqual(self.manager.list_memories(limit=50), [])

    def test_long_term_preference_is_saved_with_confidence(self):
        result = self.manager.process_chat_message("我偏好深色主题")

        self.assertEqual(result["action"], "added")
        self.assertGreaterEqual(
            result["candidate"]["confidence"],
            MEMORY_CONFIDENCE_THRESHOLD,
        )
        self.assertEqual(result["memory"]["memory_type"], "personal")
        self.assertEqual(result["memory"]["importance"], 4)
        self.assertEqual(result["memory"]["source"], "chat:auto")

    def test_candidate_below_confidence_threshold_is_rejected(self):
        low_confidence_candidate = {
            "content": "可能长期使用某个主题",
            "memory_type": "personal",
            "importance": 3,
            "confidence": MEMORY_CONFIDENCE_THRESHOLD - 0.01,
            "reason": "uncertain_preference",
            "explicit": False,
        }

        with patch(
            "app.memory_manager.extract_memory_candidate",
            return_value=low_confidence_candidate,
        ):
            result = self.manager.process_chat_message("ambiguous input")

        self.assertEqual(result["action"], "rejected")
        self.assertEqual(result["reason"], "below_confidence_threshold")
        self.assertEqual(self.manager.list_memories(limit=50), [])

    def test_english_goal_is_not_mistaken_for_a_question(self):
        result = self.manager.process_chat_message(
            "My long-term goal is to build a local-first assistant"
        )

        self.assertEqual(result["action"], "added")
        self.assertEqual(result["candidate"]["reason"], "long_term_goal")
        self.assertEqual(result["memory"]["importance"], 5)

    def test_stable_habit_is_saved(self):
        result = self.manager.process_chat_message("我每天早上阅读半小时")

        self.assertEqual(result["action"], "added")
        self.assertEqual(result["candidate"]["reason"], "stable_habit")
        self.assertEqual(result["memory"]["memory_type"], "personal")

    def test_equivalent_preference_does_not_create_a_duplicate(self):
        first = self.manager.process_chat_message("我偏好深色主题")
        second = self.manager.process_chat_message("我更喜欢深色主题")

        self.assertEqual(first["action"], "added")
        self.assertEqual(second["action"], "duplicate")
        self.assertEqual(second["memory"]["id"], first["memory"]["id"])
        self.assertEqual(len(self.manager.list_memories(limit=50)), 1)

    def test_new_project_status_updates_the_existing_memory(self):
        first = self.manager.process_chat_message(
            "项目正在开发 Local File Scanner"
        )
        second = self.manager.process_chat_message("Local File Scanner 已完成")

        self.assertEqual(first["action"], "added")
        self.assertEqual(second["action"], "updated")
        self.assertEqual(second["memory"]["id"], first["memory"]["id"])
        self.assertEqual(second["memory"]["content"], "Local File Scanner 已完成")
        self.assertEqual(len(self.manager.list_memories(limit=50)), 1)

    def test_completed_project_is_not_replaced_by_stale_progress(self):
        completed = self.manager.process_chat_message("Memory GUI 已完成")
        stale = self.manager.process_chat_message("Memory GUI 正在开发")

        self.assertEqual(completed["action"], "added")
        self.assertEqual(stale["action"], "duplicate")
        self.assertEqual(stale["reason"], "stale_project_status")
        self.assertEqual(
            self.manager.get_memory(completed["memory"]["id"])["content"],
            "Memory GUI 已完成",
        )

    def test_explicit_remember_request_is_saved_directly(self):
        result = self.manager.process_chat_message("请记住：我的牙医叫林医生")

        self.assertEqual(result["action"], "added")
        self.assertEqual(result["candidate"]["confidence"], 1.0)
        self.assertEqual(result["candidate"]["reason"], "explicit_remember_request")
        self.assertEqual(result["memory"]["content"], "我的牙医叫林医生")
        self.assertEqual(result["memory"]["source"], "chat:user")

    def test_post_chat_finalizer_does_not_process_explicit_request_twice(self):
        saved = self.manager.remember_from_message("请记住：我的牙医叫林医生")
        result = finalize_chat_memory(
            "请记住：我的牙医叫林医生",
            self.manager,
        )

        self.assertIsNotNone(saved)
        self.assertEqual(result["action"], "rejected")
        self.assertEqual(len(self.manager.list_memories(limit=50)), 1)

    def test_importance_reflects_long_term_value(self):
        goal = extract_memory_candidate("我的长期目标是建立本地优先的个人 AI")
        project = extract_memory_candidate("Phase 9C 已完成")
        conversation = extract_memory_candidate(
            "本次对话的重要结论是每周复盘项目里程碑"
        )

        self.assertEqual(goal["importance"], 5)
        self.assertEqual(project["importance"], 4)
        self.assertEqual(conversation["importance"], 3)

    def test_only_important_conversation_conclusion_is_summarized(self):
        ordinary = self.manager.process_chat_message("我们聊了很多开发想法")
        important = self.manager.process_chat_message(
            "本次对话的重要结论是每周复盘项目里程碑"
        )

        self.assertEqual(ordinary["action"], "rejected")
        self.assertEqual(important["action"], "added")
        self.assertEqual(important["memory"]["memory_type"], "conversation")
        self.assertEqual(important["memory"]["content"], "每周复盘项目里程碑")

    def test_development_log_reports_candidate_decisions(self):
        with self.assertLogs("app.memory_manager", level="DEBUG") as logs:
            self.manager.process_chat_message("我偏好深色主题")
            self.manager.process_chat_message("我更喜欢深色主题")
            self.manager.process_chat_message("普通闲聊")

        output = "\n".join(logs.output)
        self.assertIn("Memory candidate -> added", output)
        self.assertIn("Memory candidate -> duplicate", output)
        self.assertIn("Memory candidate -> rejected", output)


if __name__ == "__main__":
    unittest.main()
