"""Small, local SQLite memory storage with indexed lexical retrieval."""

import re
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_MEMORY_DB_PATH = Path(__file__).resolve().parents[1] / "data" / "memory.db"
MEMORY_TYPES = frozenset({"personal", "project", "conversation"})
MAX_CONTENT_LENGTH = 4000
MAX_SOURCE_LENGTH = 200
MAX_SEARCH_LIMIT = 50
MAX_QUERY_TERMS = 64
_CJK_PATTERN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")
_WORD_PATTERN = re.compile(r"[^\W_]+(?:['’][^\W_]+)?", re.UNICODE)
_ENGLISH_STOP_WORDS = frozenset(
    "a an and are as at be been but by can could do does for from had has have "
    "he her hers him his how i if in into is it its me my of on or our ours "
    "please remember she should so some tell than that the their them then "
    "there these they this those to us was we were what when where which who "
    "why will with would you your yours".split()
)
_CHINESE_FILLER_PATTERN = re.compile(
    "请告诉我|我想知道|请帮我|请记住|请问|为什么|什么|如何|怎么|是否|"
    "可以|记住|我的|我们|你的|你们|请|我|你|的|吗|呢|是|了"
)


class MemoryStoreError(RuntimeError):
    """Raised when local memory cannot be read or persisted."""


def _validate_text(value, field, maximum):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Memory {field} must be nonempty text.")

    value = value.strip()

    if len(value) > maximum:
        raise ValueError(f"Memory {field} must contain at most {maximum} characters.")

    return value


def _validate_memory_type(memory_type):
    if not isinstance(memory_type, str) or memory_type not in MEMORY_TYPES:
        raise ValueError("Memory type must be personal, project, or conversation.")

    return memory_type


def _validate_importance(importance):
    if type(importance) is not int or not 1 <= importance <= 5:
        raise ValueError("Memory importance must be an integer between 1 and 5.")

    return importance


def _validate_limit(limit):
    if type(limit) is not int or limit < 1:
        raise ValueError("Memory limit must be a positive integer.")

    return min(limit, MAX_SEARCH_LIMIT)


def _memory_terms(text, limit=None):
    """Return unique casefolded words and CJK bigrams in occurrence order."""

    normalized = _CHINESE_FILLER_PATTERN.sub(" ", text.casefold())
    terms = {}

    for match in re.finditer(
        r"[\u3400-\u4dbf\u4e00-\u9fff]+|[^\u3400-\u4dbf\u4e00-\u9fff]+",
        normalized,
    ):
        fragment = match.group()

        if _CJK_PATTERN.fullmatch(fragment):
            candidates = (
                fragment[position:position + 2]
                for position in range(len(fragment) - 1)
            )
        else:
            candidates = (
                word for word in _WORD_PATTERN.findall(fragment)
                if word not in _ENGLISH_STOP_WORDS
            )

        for candidate in candidates:
            terms[candidate] = None

            if limit is not None and len(terms) >= limit:
                return list(terms)

    return list(terms)


class MemoryStore:
    """CRUD storage with no shared connections and no constructor disk writes.

    A database and its indexes are created on the first storage operation. Every
    operation opens and closes its own connection, allowing one store to be used
    from successive Qt workers or other threads. Search is lexical, not semantic.
    """

    def __init__(self, db_path=None):
        selected_path = DEFAULT_MEMORY_DB_PATH if db_path is None else db_path

        if isinstance(selected_path, str) and (
            not selected_path.strip() or "\x00" in selected_path
            or selected_path == ":memory:"
        ):
            raise ValueError("Memory database must have a persistent file path.")

        try:
            self.db_path = Path(selected_path).expanduser()
        except (TypeError, ValueError) as error:
            raise ValueError("Memory database path is invalid.") from error

        self._initialization_lock = threading.Lock()
        self._initialized = False

    @contextmanager
    def _connection(self, *, write=False):
        connection = None

        try:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(self.db_path, timeout=5)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")

            with self._initialization_lock:
                if not self._initialized:
                    self._initialize_schema(connection)
                    self._initialized = True

            with connection:
                if write:
                    connection.execute("BEGIN IMMEDIATE")

                yield connection
        except (sqlite3.Error, OSError) as error:
            raise MemoryStoreError(f"Could not access local memory: {error}") from error
        finally:
            if connection is not None:
                connection.close()

    @staticmethod
    def _initialize_schema(connection):
        with connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY NOT NULL,
                    content TEXT NOT NULL CHECK(length(trim(content)) BETWEEN 1 AND 4000),
                    memory_type TEXT NOT NULL
                        CHECK(memory_type IN ('personal', 'project', 'conversation')),
                    importance INTEGER NOT NULL
                        CHECK(typeof(importance) = 'integer' AND importance BETWEEN 1 AND 5),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    source TEXT NOT NULL CHECK(length(trim(source)) BETWEEN 1 AND 200),
                    UNIQUE(content, memory_type)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_terms (
                    memory_id TEXT NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
                    term TEXT NOT NULL,
                    PRIMARY KEY(memory_id, term)
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_memory_terms_term "
                "ON memory_terms(term, memory_id)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_memories_type_updated "
                "ON memories(memory_type, updated_at DESC)"
            )

    @staticmethod
    def _replace_terms(connection, memory_id, content):
        connection.execute("DELETE FROM memory_terms WHERE memory_id = ?", (memory_id,))
        connection.executemany(
            "INSERT INTO memory_terms(memory_id, term) VALUES (?, ?)",
            ((memory_id, term) for term in _memory_terms(content)),
        )

    def add_memory(self, content, memory_type="personal", importance=3, source="manual"):
        """Persist a selected fact; an identical fact/type returns its existing row."""

        content = _validate_text(content, "content", MAX_CONTENT_LENGTH)
        memory_type = _validate_memory_type(memory_type)
        importance = _validate_importance(importance)
        source = _validate_text(source, "source", MAX_SOURCE_LENGTH)

        with self._connection(write=True) as connection:
            existing = connection.execute(
                "SELECT * FROM memories WHERE content = ? AND memory_type = ?",
                (content, memory_type),
            ).fetchone()

            if existing is not None:
                return dict(existing)

            timestamp = datetime.now(timezone.utc).isoformat()
            memory = {
                "id": str(uuid.uuid4()),
                "content": content,
                "memory_type": memory_type,
                "importance": importance,
                "created_at": timestamp,
                "updated_at": timestamp,
                "source": source,
            }
            connection.execute(
                "INSERT INTO memories "
                "(id, content, memory_type, importance, created_at, updated_at, source) "
                "VALUES (:id, :content, :memory_type, :importance, "
                ":created_at, :updated_at, :source)",
                memory,
            )
            self._replace_terms(connection, memory["id"], content)
            return memory

    def get_memory(self, memory_id):
        """Return one memory or None when its identifier does not exist."""

        memory_id = _validate_text(memory_id, "id", 200)

        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM memories WHERE id = ?", (memory_id,),
            ).fetchone()
            return dict(row) if row is not None else None

    def update_memory(
        self, memory_id, *, content=None, memory_type=None, importance=None, source=None,
    ):
        """Update supplied fields and their index atomically, preserving created_at."""

        memory_id = _validate_text(memory_id, "id", 200)
        changes = {}

        if content is not None:
            changes["content"] = _validate_text(content, "content", MAX_CONTENT_LENGTH)

        if memory_type is not None:
            changes["memory_type"] = _validate_memory_type(memory_type)

        if importance is not None:
            changes["importance"] = _validate_importance(importance)

        if source is not None:
            changes["source"] = _validate_text(source, "source", MAX_SOURCE_LENGTH)

        with self._connection(write=True) as connection:
            row = connection.execute(
                "SELECT * FROM memories WHERE id = ?", (memory_id,),
            ).fetchone()

            if row is None:
                return None

            memory = dict(row)

            if not changes or all(memory[field] == value for field, value in changes.items()):
                return memory

            memory.update(changes)
            duplicate = connection.execute(
                "SELECT id FROM memories WHERE content = ? AND memory_type = ? AND id != ?",
                (memory["content"], memory["memory_type"], memory_id),
            ).fetchone()

            if duplicate is not None:
                raise ValueError("Another memory already has this content and type.")

            memory["updated_at"] = datetime.now(timezone.utc).isoformat()
            connection.execute(
                "UPDATE memories SET content = :content, memory_type = :memory_type, "
                "importance = :importance, source = :source, updated_at = :updated_at "
                "WHERE id = :id",
                memory,
            )

            if "content" in changes:
                self._replace_terms(connection, memory_id, memory["content"])

            return memory

    def delete_memory(self, memory_id):
        """Delete one memory and its search terms, returning whether it existed."""

        memory_id = _validate_text(memory_id, "id", 200)

        with self._connection(write=True) as connection:
            cursor = connection.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
            return cursor.rowcount > 0

    def search_memories(self, query, memory_type=None, limit=5):
        """Retrieve only matching memories, ordered by overlap before importance.

        Queries use at most 64 distinct useful terms and return at most 50 rows.
        Empty or filler-only queries return no memories; use list_memories for
        browsing. Chinese matching requires a shared adjacent character pair.
        """

        if not isinstance(query, str):
            raise ValueError("Memory search query must be text.")

        if memory_type is not None:
            memory_type = _validate_memory_type(memory_type)

        limit = _validate_limit(limit)
        terms = _memory_terms(query, limit=MAX_QUERY_TERMS)

        if not terms:
            return []

        placeholders = ", ".join("?" for _ in terms)
        type_filter = " AND m.memory_type = ?" if memory_type is not None else ""
        parameters = [*terms]

        if memory_type is not None:
            parameters.append(memory_type)

        parameters.append(limit)

        with self._connection() as connection:
            rows = connection.execute(
                "SELECT m.*, COUNT(*) AS relevance FROM memory_terms AS t "
                "JOIN memories AS m ON m.id = t.memory_id "
                f"WHERE t.term IN ({placeholders}){type_filter} "
                "GROUP BY m.id ORDER BY relevance DESC, m.importance DESC, "
                "m.updated_at DESC, m.id ASC LIMIT ?",
                parameters,
            ).fetchall()
            return [dict(row) for row in rows]

    def list_memories(self, memory_type=None, limit=50, offset=0):
        """Return a bounded page for future memory administration interfaces."""

        if memory_type is not None:
            memory_type = _validate_memory_type(memory_type)

        limit = _validate_limit(limit)

        if type(offset) is not int or offset < 0:
            raise ValueError("Memory offset must be a nonnegative integer.")

        type_filter = " WHERE memory_type = ?" if memory_type is not None else ""
        parameters = [memory_type] if memory_type is not None else []
        parameters.extend((limit, offset))

        with self._connection() as connection:
            rows = connection.execute(
                f"SELECT * FROM memories{type_filter} "
                "ORDER BY updated_at DESC, id ASC LIMIT ? OFFSET ?",
                parameters,
            ).fetchall()
            return [dict(row) for row in rows]
