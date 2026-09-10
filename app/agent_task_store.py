"""Independent SQLite history for Agent tasks; no executable approvals are stored."""

from contextlib import closing, contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from threading import Lock


DEFAULT_TASK_DB = Path(__file__).resolve().parents[1] / "data" / "agent_tasks.db"
ACTIVE_STATES = {"PLANNING", "EXECUTING", "WAITING_CONFIRMATION"}
TERMINAL_STATES = {"COMPLETE", "FAILED", "CANCELLED", "INTERRUPTED"}
INTERRUPTED_MESSAGE = "该任务因上次程序退出而中断。"


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def summary(value, limit=1200):
    if value is None:
        return None
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    if "Traceback (most recent call last)" in text:
        text = text.strip().splitlines()[-1]
    return text[:limit]


class AgentTaskStore:
    """Open one short-lived connection per worker operation."""

    def __init__(self, db_path=None):
        self.db_path = Path(db_path or DEFAULT_TASK_DB)
        self._schema_ready = False
        self._schema_lock = Lock()

    @contextmanager
    def _connect(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.db_path, timeout=5)) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            if not self._schema_ready:
                with self._schema_lock:
                    if not self._schema_ready:
                        connection.executescript("""
                            CREATE TABLE IF NOT EXISTS agent_tasks (
                                task_id TEXT PRIMARY KEY, goal TEXT NOT NULL, status TEXT NOT NULL,
                                persona TEXT NOT NULL, created_at TEXT NOT NULL, started_at TEXT,
                                completed_at TEXT, current_step INTEGER NOT NULL,
                                total_steps INTEGER NOT NULL, error_message TEXT
                            );
                            CREATE TABLE IF NOT EXISTS agent_task_steps (
                                task_id TEXT NOT NULL REFERENCES agent_tasks(task_id) ON DELETE CASCADE,
                                step_index INTEGER NOT NULL, tool TEXT NOT NULL, status TEXT NOT NULL,
                                result_summary TEXT, started_at TEXT, completed_at TEXT,
                                PRIMARY KEY (task_id, step_index)
                            );
                            CREATE INDEX IF NOT EXISTS agent_tasks_created ON agent_tasks(created_at DESC);
                        """)
                        self._schema_ready = True
            with connection:
                yield connection

    def save_snapshot(self, snapshot, persona):
        state = snapshot["state"]
        if state not in ACTIVE_STATES | TERMINAL_STATES:
            raise ValueError("Invalid Agent task state.")
        now = utc_now()
        steps = snapshot["plan"]["steps"]
        task_id = snapshot["task_id"]
        with self._connect() as db:
            # Never let a delayed progress event resurrect a terminal task.
            previous = db.execute("SELECT status FROM agent_tasks WHERE task_id=?", (task_id,)).fetchone()
            if previous and previous["status"] in TERMINAL_STATES:
                return
            db.execute("""
                INSERT INTO agent_tasks VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET status=excluded.status,
                    completed_at=excluded.completed_at, current_step=excluded.current_step,
                    total_steps=excluded.total_steps, error_message=excluded.error_message
            """, (task_id, snapshot["plan"]["goal"], state, persona, now, now,
                  now if state in TERMINAL_STATES else None,
                  snapshot.get("current_step", 0), len(steps), summary(snapshot.get("error"), 240)))
            for index, step in enumerate(steps):
                status = step["status"]
                started = status in {"running", "completed", "failed"} or step.get("attempts", 0) > 0
                finished = status in {"completed", "failed", "cancelled", "interrupted"}
                db.execute("""
                    INSERT INTO agent_task_steps VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(task_id, step_index) DO UPDATE SET status=excluded.status,
                        result_summary=COALESCE(excluded.result_summary, agent_task_steps.result_summary),
                        started_at=COALESCE(agent_task_steps.started_at, excluded.started_at),
                        completed_at=CASE WHEN excluded.status IN ('running', 'pending', 'waiting_confirmation')
                            THEN NULL ELSE COALESCE(agent_task_steps.completed_at, excluded.completed_at) END
                """, (task_id, index, step["tool"], status,
                      summary(step.get("error") or step.get("result")),
                      now if started else None, now if finished else None))

    def recover_interrupted(self):
        """Run once at application startup, before allowing a new task."""
        with self._connect() as db:
            now = utc_now()
            db.execute("""UPDATE agent_task_steps SET status='interrupted', completed_at=?
                WHERE status IN ('pending', 'running', 'waiting_confirmation')
                AND task_id IN (SELECT task_id FROM agent_tasks
                    WHERE status IN ('PLANNING', 'EXECUTING', 'WAITING_CONFIRMATION'))""", (now,))
            result = db.execute("""UPDATE agent_tasks SET status='INTERRUPTED',
                error_message=?, completed_at=?
                WHERE status IN ('PLANNING', 'EXECUTING', 'WAITING_CONFIRMATION')""",
                (INTERRUPTED_MESSAGE, now))
            return result.rowcount

    def list_tasks(self, limit=50):
        with self._connect() as db:
            return [dict(row) for row in db.execute(
                "SELECT * FROM agent_tasks ORDER BY created_at DESC LIMIT ?", (min(max(limit, 1), 100),),
            )]

    def get_task(self, task_id):
        with self._connect() as db:
            row = db.execute("SELECT * FROM agent_tasks WHERE task_id=?", (task_id,)).fetchone()
            if row is None:
                return None
            result = dict(row)
            result["steps"] = [dict(step) for step in db.execute(
                "SELECT * FROM agent_task_steps WHERE task_id=? ORDER BY step_index", (task_id,),
            )]
            return result
