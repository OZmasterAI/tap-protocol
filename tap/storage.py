"""TAP storage — self-contained SQLite task queue and messaging.

No external dependencies. Two tables in one WAL-mode DB:
  messages — broadcast/directed messaging
  tasks    — priority-based task queue with atomic claiming
"""

from __future__ import annotations

import os
import sqlite3
import time
import uuid
from typing import Any

DEFAULT_DB_PATH = os.path.expanduser("~/.tap/tap.db")
_MAX_TEXT = 2000
VALID_STATUSES = frozenset(
    ("pending", "assigned", "running", "done", "failed", "cancelled")
)


def _get_conn(db_path: str) -> sqlite3.Connection:
    """Return a WAL-mode connection, creating schema if needed."""
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ts          REAL NOT NULL,
            from_agent  TEXT NOT NULL,
            to_agent    TEXT NOT NULL DEFAULT 'all',
            msg_type    TEXT NOT NULL,
            content     TEXT NOT NULL,
            consumed    INTEGER DEFAULT 0,
            reply_to    INTEGER DEFAULT NULL,
            FOREIGN KEY (reply_to) REFERENCES messages(id)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_msg_ts ON messages(ts)")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_msg_to ON messages(to_agent, consumed)"
    )

    conn.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id             TEXT PRIMARY KEY,
            created_at     REAL NOT NULL,
            updated_at     REAL NOT NULL,
            title          TEXT NOT NULL,
            description    TEXT DEFAULT '',
            created_by     TEXT NOT NULL,
            assigned_to    TEXT DEFAULT NULL,
            status         TEXT NOT NULL DEFAULT 'pending',
            priority       INTEGER DEFAULT 5,
            tags           TEXT DEFAULT '',
            result         TEXT DEFAULT '',
            depends_on     TEXT DEFAULT NULL,
            required_role  TEXT DEFAULT NULL,
            goal           TEXT DEFAULT NULL,
            parent_task_id TEXT DEFAULT NULL,
            FOREIGN KEY (parent_task_id) REFERENCES tasks(id)
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status, priority)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_agent ON tasks(assigned_to)")
    return conn


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return dict(row)


def _rows_to_dicts(rows: list) -> list[dict[str, Any]]:
    return [dict(r) for r in rows]


class TAPStorage:
    """Task queue and messaging backed by SQLite."""

    def __init__(self, db_path: str = ""):
        self.db_path = db_path or os.environ.get("TAP_DB_PATH", DEFAULT_DB_PATH)

    def _conn(self) -> sqlite3.Connection:
        return _get_conn(self.db_path)

    # -- Tasks -----------------------------------------------------------

    def dispatch(
        self,
        agent_id: str,
        title: str,
        description: str = "",
        priority: int = 5,
        depends_on: str | None = None,
    ) -> str | None:
        """Create and assign a task to a specific agent. Returns task_id."""
        try:
            conn = self._conn()
            try:
                task_id = str(uuid.uuid4())
                now = time.time()
                conn.execute(
                    "INSERT INTO tasks "
                    "(id, created_at, updated_at, title, description, created_by, "
                    "assigned_to, status, priority, depends_on, required_role) "
                    "VALUES (?, ?, ?, ?, ?, 'tap', ?, 'pending', ?, ?, ?)",
                    (
                        task_id,
                        now,
                        now,
                        title[:_MAX_TEXT],
                        (description or "")[:_MAX_TEXT],
                        agent_id,
                        priority,
                        depends_on,
                        agent_id,
                    ),
                )
                conn.commit()
                return task_id
            finally:
                conn.close()
        except Exception:
            return None

    def dispatch_to_role(
        self,
        role: str,
        title: str,
        description: str = "",
        priority: int = 5,
    ) -> str | None:
        """Create a task for any agent with a given role. Returns task_id."""
        try:
            conn = self._conn()
            try:
                task_id = str(uuid.uuid4())
                now = time.time()
                conn.execute(
                    "INSERT INTO tasks "
                    "(id, created_at, updated_at, title, description, created_by, "
                    "status, priority, required_role) "
                    "VALUES (?, ?, ?, ?, ?, 'tap', 'pending', ?, ?)",
                    (
                        task_id,
                        now,
                        now,
                        title[:_MAX_TEXT],
                        (description or "")[:_MAX_TEXT],
                        priority,
                        role,
                    ),
                )
                conn.commit()
                return task_id
            finally:
                conn.close()
        except Exception:
            return None

    def complete(self, task_id: str, result: str = "") -> bool:
        """Mark a task as done and broadcast completion."""
        ok = self._update_task(task_id, "done", result)
        if ok:
            self.broadcast("system", f"task_complete: {task_id}")
        return ok

    def fail(self, task_id: str, error: str = "") -> bool:
        """Mark a task as failed."""
        return self._update_task(task_id, "failed", error)

    def get(self, task_id: str) -> dict[str, Any] | None:
        """Get a task by ID."""
        try:
            conn = self._conn()
            try:
                row = conn.execute(
                    "SELECT * FROM tasks WHERE id = ?", (task_id,)
                ).fetchone()
                return _row_to_dict(row)
            finally:
                conn.close()
        except Exception:
            return None

    def pending(self, role: str | None = None) -> list[dict[str, Any]]:
        """List pending tasks, optionally filtered by role."""
        try:
            conn = self._conn()
            try:
                if role:
                    rows = conn.execute(
                        "SELECT * FROM tasks WHERE status = 'pending' "
                        "AND (required_role IS NULL OR required_role = ?) "
                        "ORDER BY priority ASC, created_at ASC",
                        (role,),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT * FROM tasks WHERE status = 'pending' "
                        "ORDER BY priority ASC, created_at ASC"
                    ).fetchall()
                return _rows_to_dicts(rows)
            finally:
                conn.close()
        except Exception:
            return []

    def claim(self, agent_id: str, role: str | None = None) -> dict[str, Any] | None:
        """Atomically claim the highest-priority pending task."""
        try:
            conn = self._conn()
            try:
                conn.execute("BEGIN EXCLUSIVE")
                conditions = [
                    "status = 'pending'",
                    "(depends_on IS NULL OR depends_on IN "
                    "(SELECT id FROM tasks WHERE status = 'done'))",
                ]
                params: list = []
                if role:
                    conditions.append("(required_role IS NULL OR required_role = ?)")
                    params.append(role)
                else:
                    conditions.append("required_role IS NULL")

                where = " AND ".join(conditions)
                row = conn.execute(
                    f"SELECT * FROM tasks WHERE {where} "
                    "ORDER BY priority ASC, created_at ASC LIMIT 1",
                    params,
                ).fetchone()

                if not row:
                    conn.execute("COMMIT")
                    return None

                task = _row_to_dict(row)
                conn.execute(
                    "UPDATE tasks SET status = 'assigned', assigned_to = ?, "
                    "updated_at = ? WHERE id = ?",
                    (agent_id, time.time(), task["id"]),
                )
                conn.execute("COMMIT")
                task["status"] = "assigned"
                task["assigned_to"] = agent_id
                return task
            except Exception:
                try:
                    conn.execute("ROLLBACK")
                except Exception:
                    pass
                raise
            finally:
                conn.close()
        except Exception:
            return None

    def cancel_stale_tasks(self, timeout_s: int = 300) -> int:
        """Cancel tasks stuck in assigned/running state."""
        try:
            cutoff = time.time() - timeout_s
            conn = self._conn()
            try:
                cursor = conn.execute(
                    "UPDATE tasks SET status = 'cancelled', updated_at = ? "
                    "WHERE status IN ('assigned', 'running') AND updated_at < ?",
                    (time.time(), cutoff),
                )
                conn.commit()
                return cursor.rowcount
            finally:
                conn.close()
        except Exception:
            return 0

    def archive(self, older_than_hours: int = 48) -> int:
        """Delete completed/failed/cancelled tasks older than threshold."""
        try:
            cutoff = time.time() - (older_than_hours * 3600)
            conn = self._conn()
            try:
                cursor = conn.execute(
                    "DELETE FROM tasks WHERE status IN ('done', 'failed', 'cancelled') "
                    "AND updated_at < ?",
                    (cutoff,),
                )
                conn.commit()
                return cursor.rowcount
            finally:
                conn.close()
        except Exception:
            return 0

    def _update_task(self, task_id: str, status: str, result: str = "") -> bool:
        if status not in VALID_STATUSES:
            return False
        try:
            conn = self._conn()
            try:
                cursor = conn.execute(
                    "UPDATE tasks SET status = ?, result = ?, updated_at = ? "
                    "WHERE id = ?",
                    (status, (result or "")[:_MAX_TEXT], time.time(), task_id),
                )
                conn.commit()
                return cursor.rowcount > 0
            finally:
                conn.close()
        except Exception:
            return False

    # -- Messaging -------------------------------------------------------

    def broadcast(self, from_agent: str, message: str) -> bool:
        """Broadcast a message to all agents."""
        try:
            conn = self._conn()
            try:
                conn.execute(
                    "INSERT INTO messages (ts, from_agent, to_agent, msg_type, content) "
                    "VALUES (?, ?, 'all', 'broadcast', ?)",
                    (time.time(), from_agent, message[:_MAX_TEXT]),
                )
                conn.commit()
                return True
            finally:
                conn.close()
        except Exception:
            return False

    def read(self, agent_id: str, since: float = 0.0) -> list[dict[str, Any]]:
        """Read messages for an agent since timestamp."""
        try:
            conn = self._conn()
            try:
                rows = conn.execute(
                    "SELECT id, ts, from_agent, to_agent, msg_type, content, consumed "
                    "FROM messages WHERE ts > ? AND (to_agent = 'all' OR to_agent = ?) "
                    "ORDER BY ts DESC LIMIT 50",
                    (since, agent_id),
                ).fetchall()
                return _rows_to_dicts(rows)
            finally:
                conn.close()
        except Exception:
            return []
