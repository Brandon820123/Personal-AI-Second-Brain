"""Persist simple local Agent todos in a dedicated SQLite database."""

import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TODO_DB_PATH = PROJECT_ROOT / "data" / "todos.db"
TODO_STATUSES = frozenset({"pending", "completed"})
MAX_TODO_TITLE_CHARS = 300
MAX_TODO_DESCRIPTION_CHARS = 4000
MAX_TODO_LIST_LIMIT = 100


class TodoStoreError(RuntimeError):
    """Raised when the local todo database cannot complete an operation."""


def _utc_now():
    return datetime.now(timezone.utc).isoformat()


def _validate_text(value, field, maximum, *, allow_empty=False):
    if not isinstance(value, str):
        raise ValueError(f"Todo {field} must be text.")
    normalized = value.strip()
    if not normalized and not allow_empty:
        raise ValueError(f"Todo {field} must be non-empty text.")
    if len(normalized) > maximum:
        raise ValueError(
            f"Todo {field} must contain at most {maximum} characters."
        )
    return normalized


def _validate_todo_id(todo_id):
    if not isinstance(todo_id, str) or not todo_id.strip():
        raise ValueError("Todo ID must be non-empty text.")
    return todo_id.strip()


class TodoStore:
    """Provide create, list, and complete operations without delete support."""

    def __init__(self, db_path=None):
        selected_path = DEFAULT_TODO_DB_PATH if db_path is None else db_path
        try:
            self.db_path = Path(selected_path).expanduser()
        except (TypeError, ValueError) as error:
            raise ValueError("Todo database path is invalid.") from error
        self._initialization_lock = threading.Lock()
        self._initialized = False

    @contextmanager
    def _connection(self, *, write=False):
        connection = None
        try:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(self.db_path, timeout=5)
            connection.row_factory = sqlite3.Row
            with self._initialization_lock:
                if not self._initialized:
                    self._initialize_schema(connection)
                    self._initialized = True
            with connection:
                if write:
                    connection.execute("BEGIN IMMEDIATE")
                yield connection
        except (sqlite3.Error, OSError) as error:
            raise TodoStoreError(f"Could not access local todos: {error}") from error
        finally:
            if connection is not None:
                connection.close()

    @staticmethod
    def _initialize_schema(connection):
        with connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS todos (
                    id TEXT PRIMARY KEY NOT NULL,
                    title TEXT NOT NULL
                        CHECK(length(trim(title)) BETWEEN 1 AND 300),
                    description TEXT NOT NULL
                        CHECK(length(description) <= 4000),
                    status TEXT NOT NULL
                        CHECK(status IN ('pending', 'completed')),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_todos_status_created "
                "ON todos(status, created_at DESC)"
            )

    def create_todo(self, title, description=""):
        """Create one pending todo and return its stored record."""
        normalized_title = _validate_text(
            title,
            "title",
            MAX_TODO_TITLE_CHARS,
        )
        normalized_description = _validate_text(
            description,
            "description",
            MAX_TODO_DESCRIPTION_CHARS,
            allow_empty=True,
        )
        todo_id = str(uuid.uuid4())
        timestamp = _utc_now()
        with self._connection(write=True) as connection:
            connection.execute(
                """
                INSERT INTO todos (
                    id, title, description, status, created_at, updated_at
                ) VALUES (?, ?, ?, 'pending', ?, ?)
                """,
                (
                    todo_id,
                    normalized_title,
                    normalized_description,
                    timestamp,
                    timestamp,
                ),
            )
            return self._get_todo(connection, todo_id)

    def list_todos(self, status="pending", limit=50):
        """List bounded todos with pending records first for an all-status view."""
        if status not in TODO_STATUSES | {"all"}:
            raise ValueError("Todo status must be all, pending, or completed.")
        if type(limit) is not int or not 1 <= limit <= MAX_TODO_LIST_LIMIT:
            raise ValueError("Todo limit must be an integer between 1 and 100.")

        parameters = []
        where_clause = ""
        if status != "all":
            where_clause = "WHERE status = ?"
            parameters.append(status)
        parameters.append(limit)
        with self._connection() as connection:
            rows = connection.execute(
                f"""
                SELECT id, title, description, status, created_at,
                       updated_at, completed_at
                FROM todos
                {where_clause}
                ORDER BY CASE status WHEN 'pending' THEN 0 ELSE 1 END,
                         created_at DESC
                LIMIT ?
                """,
                parameters,
            ).fetchall()
            return [dict(row) for row in rows]

    def complete_todo(self, todo_id):
        """Mark one todo completed and return it, or None if it does not exist."""
        normalized_id = _validate_todo_id(todo_id)
        timestamp = _utc_now()
        with self._connection(write=True) as connection:
            connection.execute(
                """
                UPDATE todos
                SET status = 'completed', updated_at = ?,
                    completed_at = COALESCE(completed_at, ?)
                WHERE id = ?
                """,
                (timestamp, timestamp, normalized_id),
            )
            return self._get_todo(connection, normalized_id)

    @staticmethod
    def _get_todo(connection, todo_id):
        row = connection.execute(
            """
            SELECT id, title, description, status, created_at,
                   updated_at, completed_at
            FROM todos
            WHERE id = ?
            """,
            (todo_id,),
        ).fetchone()
        return dict(row) if row is not None else None
