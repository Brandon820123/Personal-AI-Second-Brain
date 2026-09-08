"""Tests for persistent, bounded local memory storage and lexical retrieval."""

import sqlite3
import tempfile
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from app.memory_store import (
    DEFAULT_MEMORY_DB_PATH,
    MAX_CONTENT_LENGTH,
    MEMORY_TYPES,
    MemoryStore,
    MemoryStoreError,
)


class MemoryStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temporary_directory.name) / "data" / "memory.db"
        self.store = MemoryStore(self.db_path)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_constructor_is_lazy_and_default_path_is_project_relative(self):
        self.assertFalse(self.db_path.parent.exists())
        self.assertEqual(self.store.search_memories("please remember what"), [])
        self.assertFalse(self.db_path.exists())
        self.assertEqual(
            DEFAULT_MEMORY_DB_PATH,
            Path(__file__).resolve().parents[1] / "data" / "memory.db",
        )
        self.assertEqual(MemoryStore().db_path, DEFAULT_MEMORY_DB_PATH)

    def test_add_and_get_preserve_required_fields_and_utc_timestamps(self):
        memory = self.store.add_memory(
            "  I prefer Python for backend development.  ",
            importance=4,
            source="chat:user",
        )

        self.assertEqual(
            set(memory),
            {"id", "content", "memory_type", "importance", "created_at", "updated_at", "source"},
        )
        self.assertEqual(str(uuid.UUID(memory["id"])), memory["id"])
        self.assertEqual(memory["content"], "I prefer Python for backend development.")
        self.assertEqual(memory["memory_type"], "personal")
        self.assertEqual(memory["importance"], 4)
        self.assertEqual(memory["source"], "chat:user")
        self.assertEqual(memory["created_at"], memory["updated_at"])
        self.assertEqual(datetime.fromisoformat(memory["created_at"]).utcoffset(), timedelta(0))
        self.assertEqual(self.store.get_memory(memory["id"]), memory)
        self.assertTrue(self.db_path.is_file())

    def test_memories_and_search_persist_across_store_instances(self):
        memory = self.store.add_memory("Atlas uses SQLite", "project")
        reopened = MemoryStore(self.db_path)

        self.assertEqual(reopened.get_memory(memory["id"]), memory)
        self.assertEqual(reopened.search_memories("sqlite")[0]["id"], memory["id"])

    def test_all_memory_types_have_separate_records_and_can_be_filtered(self):
        records = {
            memory_type: self.store.add_memory("Atlas planning", memory_type)
            for memory_type in MEMORY_TYPES
        }

        self.assertEqual(len({memory["id"] for memory in records.values()}), 3)

        for memory_type, memory in records.items():
            with self.subTest(memory_type=memory_type):
                self.assertEqual(self.store.list_memories(memory_type), [memory])
                results = self.store.search_memories("Atlas", memory_type)
                self.assertEqual([result["id"] for result in results], [memory["id"]])

    def test_duplicate_add_is_idempotent_without_overwriting_metadata(self):
        first = self.store.add_memory("Atlas planning", "project", 2, "manual")
        second = self.store.add_memory("  Atlas planning  ", "project", 5, "chat:user")

        self.assertEqual(first, second)
        self.assertEqual(self.store.list_memories(), [first])

    def test_update_preserves_identity_and_replaces_search_index(self):
        memory = self.store.add_memory("Atlas uses Python", "personal")
        updated = self.store.update_memory(
            memory["id"],
            content="Borealis uses Rust",
            memory_type="project",
            importance=5,
            source="project:borealis",
        )

        self.assertEqual(updated["id"], memory["id"])
        self.assertEqual(updated["created_at"], memory["created_at"])
        self.assertGreater(updated["updated_at"], memory["updated_at"])
        self.assertEqual(updated["content"], "Borealis uses Rust")
        self.assertEqual(updated["memory_type"], "project")
        self.assertEqual(updated["importance"], 5)
        self.assertEqual(updated["source"], "project:borealis")
        self.assertEqual(self.store.get_memory(memory["id"]), updated)
        self.assertEqual(self.store.search_memories("Atlas Python"), [])
        self.assertEqual(self.store.search_memories("Rust")[0]["id"], memory["id"])
        self.assertEqual(self.store.search_memories("Rust", "personal"), [])
        self.assertEqual(self.store.update_memory(memory["id"]), updated)

    def test_duplicate_update_raises_and_keeps_original_record_and_terms(self):
        first = self.store.add_memory("Atlas planning", "project")
        second = self.store.add_memory("Borealis planning", "project")

        with self.assertRaises(ValueError):
            self.store.update_memory(second["id"], content=first["content"])

        self.assertEqual(self.store.get_memory(second["id"]), second)
        self.assertEqual(self.store.search_memories("Borealis")[0]["id"], second["id"])

    def test_delete_cascades_search_terms_and_missing_operations_are_explicit(self):
        memory = self.store.add_memory("Atlas planning", "project")

        self.assertTrue(self.store.delete_memory(memory["id"]))
        self.assertFalse(self.store.delete_memory(memory["id"]))
        self.assertIsNone(self.store.get_memory(memory["id"]))
        self.assertIsNone(self.store.update_memory(memory["id"], importance=4))
        self.assertEqual(self.store.search_memories("Atlas"), [])

        connection = sqlite3.connect(self.db_path)
        try:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM memory_terms").fetchone()[0], 0)
        finally:
            connection.close()

    def test_relevance_ranks_before_importance_and_importance_breaks_ties(self):
        detailed = self.store.add_memory("Atlas Python SQLite", "project", 1)
        important = self.store.add_memory("Atlas SQLite migration", "project", 5)
        ordinary = self.store.add_memory("Atlas SQLite testing", "project", 2)
        self.store.add_memory("Unrelated gardening preference", importance=5)
        results = self.store.search_memories("Please tell me about ATLAS python SQLite")

        self.assertEqual(
            [result["id"] for result in results],
            [detailed["id"], important["id"], ordinary["id"]],
        )
        self.assertEqual([result["relevance"] for result in results], [3, 2, 2])

    def test_english_uses_casefolded_whole_words(self):
        target = self.store.add_memory("I prefer Python and a CAFÉ workspace.")
        self.store.add_memory("A pythonic workflow works well.")

        self.assertEqual(self.store.search_memories("PYTHON")[0]["id"], target["id"])
        self.assertEqual(len(self.store.search_memories("python")), 1)
        self.assertEqual(self.store.search_memories("café")[0]["id"], target["id"])
        self.assertEqual(self.store.search_memories("pyth"), [])

    def test_chinese_bigrams_find_relevant_preferences_and_project_notes(self):
        preference = self.store.add_memory("我长期喜欢不加糖的咖啡。")
        project = self.store.add_memory("知识库项目已经完成本地扫描，下一步增加自动同步。", "project")
        self.store.add_memory("周末计划练习钢琴", importance=5)

        self.assertEqual(
            [record["id"] for record in self.store.search_memories("请问我的咖啡偏好是什么？")],
            [preference["id"]],
        )
        self.assertEqual(
            [record["id"] for record in self.store.search_memories("本地扫描和自动同步进度")],
            [project["id"]],
        )

    def test_empty_filler_and_unrelated_queries_return_no_memories(self):
        self.store.add_memory("I prefer Python", importance=5)
        self.store.add_memory("我喜欢咖啡", importance=5)

        for query in ("", " \n ", "please remember what is my", "请问我什么", "？！？", "astronomy"):
            with self.subTest(query=query):
                self.assertEqual(self.store.search_memories(query), [])

    def test_search_limits_and_browsing_pagination_are_bounded(self):
        for index in range(55):
            self.store.add_memory(f"Atlas milestone {index}", "project")

        self.assertEqual(len(self.store.search_memories("Atlas")), 5)
        self.assertEqual(len(self.store.search_memories("Atlas", limit=2)), 2)
        self.assertEqual(len(self.store.search_memories("Atlas", limit=1000)), 50)
        self.assertEqual(len(self.store.list_memories(limit=1000)), 50)
        first = self.store.list_memories(limit=20)
        second = self.store.list_memories(limit=20, offset=20)
        self.assertFalse({row["id"] for row in first} & {row["id"] for row in second})
        self.assertEqual(len(self.store.list_memories(offset=50)), 5)

    def test_query_uses_only_first_64_distinct_terms(self):
        early = self.store.add_memory("term0")
        self.store.add_memory("term64")
        query = " ".join(f"term{index}" for index in range(100))

        self.assertEqual([row["id"] for row in self.store.search_memories(query)], [early["id"]])

    def test_sql_syntax_in_user_text_is_literal_data(self):
        payload = "Atlas'); DROP TABLE memories; -- 100%_"
        memory = self.store.add_memory(payload, source="manual'; --")

        self.assertEqual(self.store.get_memory(memory["id"])["content"], payload)
        self.assertEqual(self.store.search_memories(payload)[0]["id"], memory["id"])
        self.assertIsNone(self.store.get_memory("' OR 1=1 --"))
        self.assertFalse(self.store.delete_memory("' OR 1=1 --"))
        self.assertEqual(self.store.get_memory(memory["id"]), memory)

    def test_invalid_memory_values_raise_before_creating_database(self):
        invalid_values = (
            {"content": ""}, {"content": " \n"}, {"content": None},
            {"content": "x" * (MAX_CONTENT_LENGTH + 1)},
            {"memory_type": "unknown"}, {"memory_type": []},
            {"importance": True}, {"importance": 0}, {"importance": 6},
            {"importance": 3.0}, {"importance": "3"},
            {"source": ""}, {"source": None}, {"source": "x" * 201},
        )

        for values in invalid_values:
            with self.subTest(values=values):
                arguments = {"content": "Atlas preference", **values}

                with self.assertRaises(ValueError):
                    self.store.add_memory(**arguments)

        self.assertFalse(self.db_path.exists())

    def test_invalid_update_search_list_and_ids_raise_value_error(self):
        memory = self.store.add_memory("Atlas preference")
        operations = (
            lambda: self.store.update_memory(memory["id"], content=""),
            lambda: self.store.update_memory(memory["id"], memory_type="invalid"),
            lambda: self.store.update_memory(memory["id"], importance=True),
            lambda: self.store.update_memory(memory["id"], source=""),
            lambda: self.store.get_memory(None),
            lambda: self.store.delete_memory(""),
            lambda: self.store.search_memories(None),
            lambda: self.store.search_memories("Atlas", memory_type=[]),
            lambda: self.store.search_memories("Atlas", limit=True),
            lambda: self.store.search_memories("Atlas", limit=0),
            lambda: self.store.list_memories(memory_type="invalid"),
            lambda: self.store.list_memories(limit=-1),
            lambda: self.store.list_memories(offset=-1),
            lambda: self.store.list_memories(offset=True),
        )

        for operation in operations:
            with self.assertRaises(ValueError):
                operation()

        self.assertEqual(self.store.get_memory(memory["id"]), memory)

    def test_filesystem_and_corrupt_database_errors_are_wrapped(self):
        root = Path(self.temporary_directory.name)
        blocker = root / "not-a-directory"
        blocker.write_text("blocked", encoding="utf-8")

        with self.assertRaises(MemoryStoreError):
            MemoryStore(blocker / "memory.db").add_memory("Atlas")

        corrupt = root / "corrupt.db"
        corrupt.write_bytes(b"this is not a SQLite database")

        with self.assertRaises(MemoryStoreError):
            MemoryStore(corrupt).get_memory("missing")

    def test_failed_index_write_rolls_back_content_update(self):
        memory = self.store.add_memory("Atlas Python")

        with patch.object(self.store, "_replace_terms", side_effect=sqlite3.OperationalError("disk full")):
            with self.assertRaises(MemoryStoreError):
                self.store.update_memory(memory["id"], content="Borealis Rust")

        self.assertEqual(self.store.get_memory(memory["id"]), memory)
        self.assertEqual(self.store.search_memories("Atlas")[0]["id"], memory["id"])
        self.assertEqual(self.store.search_memories("Borealis"), [])

    def test_concurrent_workers_share_store_without_shared_connections(self):
        def add_and_read(index):
            memory = self.store.add_memory(f"Atlas milestone {index}", "project")
            return self.store.get_memory(memory["id"])

        with ThreadPoolExecutor(max_workers=4) as executor:
            memories = list(executor.map(add_and_read, range(12)))

        self.assertEqual(len({memory["id"] for memory in memories}), 12)
        self.assertEqual(len(self.store.search_memories("Atlas", limit=50)), 12)

    def test_concurrent_duplicate_adds_from_separate_stores_are_idempotent(self):
        def add_duplicate(_):
            return MemoryStore(self.db_path).add_memory("Atlas uses SQLite", "project")

        with ThreadPoolExecutor(max_workers=4) as executor:
            memories = list(executor.map(add_duplicate, range(8)))

        self.assertEqual(len({memory["id"] for memory in memories}), 1)
        self.assertEqual(len(self.store.list_memories()), 1)


if __name__ == "__main__":
    unittest.main()
