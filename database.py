from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List

from models import StudySession, WorkflowEvent


class StudyDatabase:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 30000")
        return conn

    def _table_columns(self, conn: sqlite3.Connection, table: str) -> List[str]:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        return [str(row["name"]) for row in rows]

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    participant_id TEXT NOT NULL,
                    participant_name TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    workflow_type TEXT NOT NULL,
                    manual_step_role TEXT NOT NULL DEFAULT 'Task Planner',
                    task_json TEXT NOT NULL,
                    repo_path TEXT NOT NULL,
                    current_node_idx INTEGER NOT NULL DEFAULT 0,
                    iteration_count INTEGER NOT NULL DEFAULT 0,
                    waiting_for_human INTEGER NOT NULL DEFAULT 0,
                    waiting_role TEXT,
                    wait_started_at TEXT,
                    support_requests_count INTEGER NOT NULL DEFAULT 0,
                    messages_json TEXT NOT NULL DEFAULT '[]',
                    submitted_patch TEXT,
                    evaluation_json TEXT,
                    last_handoff_json TEXT,
                    last_patch_application_json TEXT,
                    last_test_run_json TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    event_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    role TEXT,
                    content TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES sessions(session_id)
                )
                """
            )
            self._migrate_schema(conn)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_sessions_participant_id_created_at "
                "ON sessions (participant_id, created_at DESC)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_events_session_id_created_at "
                "ON events (session_id, created_at ASC)"
            )
            conn.commit()

    def _migrate_schema(self, conn: sqlite3.Connection) -> None:
        self._ensure_columns(
            conn,
            table="sessions",
            required_columns={
                "session_id": "TEXT PRIMARY KEY",
                "participant_id": "TEXT NOT NULL DEFAULT ''",
                "participant_name": "TEXT NOT NULL",
                "created_at": "TEXT NOT NULL",
                "status": "TEXT NOT NULL",
                "workflow_type": "TEXT NOT NULL DEFAULT 'planner'",
                "manual_step_role": "TEXT NOT NULL DEFAULT 'Task Planner'",
                "task_json": "TEXT NOT NULL DEFAULT '{}'",
                "repo_path": "TEXT NOT NULL DEFAULT ''",
                "current_node_idx": "INTEGER NOT NULL DEFAULT 0",
                "iteration_count": "INTEGER NOT NULL DEFAULT 0",
                "waiting_for_human": "INTEGER NOT NULL DEFAULT 0",
                "waiting_role": "TEXT",
                "wait_started_at": "TEXT",
                "support_requests_count": "INTEGER NOT NULL DEFAULT 0",
                "messages_json": "TEXT NOT NULL DEFAULT '[]'",
                "submitted_patch": "TEXT",
                "evaluation_json": "TEXT",
                "last_handoff_json": "TEXT",
                "last_patch_application_json": "TEXT",
                "last_test_run_json": "TEXT",
            },
        )
        self._ensure_columns(
            conn,
            table="events",
            required_columns={
                "event_id": "TEXT PRIMARY KEY",
                "session_id": "TEXT NOT NULL",
                "created_at": "TEXT NOT NULL",
                "event_type": "TEXT NOT NULL",
                "role": "TEXT",
                "content": "TEXT NOT NULL",
                "metadata_json": "TEXT NOT NULL DEFAULT '{}'",
            },
        )

    def _ensure_columns(
        self,
        conn: sqlite3.Connection,
        *,
        table: str,
        required_columns: Dict[str, str],
    ) -> None:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        existing = {str(row["name"]) for row in rows}
        for column, ddl in required_columns.items():
            if column in existing:
                continue
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")

    def save_session(self, session: StudySession) -> None:
        with self._connect() as conn:
            session_values: Dict[str, Any] = {
                "session_id": session.session_id,
                "participant_id": session.participant_id,
                "participant_name": session.participant_name,
                "created_at": session.created_at,
                "status": session.status,
                "workflow_type": session.workflow_type,
                "manual_step_role": session.manual_step_role,
                "task_json": json.dumps(session.task, ensure_ascii=False),
                "repo_path": session.repo_path,
                "current_node_idx": session.current_node_idx,
                "iteration_count": session.iteration_count,
                "waiting_for_human": int(session.waiting_for_human),
                "waiting_role": session.waiting_role,
                "wait_started_at": session.wait_started_at,
                "support_requests_count": session.support_requests_count,
                "messages_json": json.dumps(session.messages, ensure_ascii=False),
                "submitted_patch": session.submitted_patch,
                "evaluation_json": (
                    json.dumps(session.evaluation, ensure_ascii=False)
                    if session.evaluation is not None
                    else None
                ),
                "last_handoff_json": (
                    json.dumps(session.last_handoff, ensure_ascii=False)
                    if session.last_handoff is not None
                    else None
                ),
                "last_patch_application_json": (
                    json.dumps(session.last_patch_application, ensure_ascii=False)
                    if session.last_patch_application is not None
                    else None
                ),
                "last_test_run_json": (
                    json.dumps(session.last_test_run, ensure_ascii=False)
                    if session.last_test_run is not None
                    else None
                ),
            }
            # Backward compatibility for older DBs with round-based columns.
            session_values["tasks_json"] = json.dumps([session.task], ensure_ascii=False)
            session_values["repo_paths_json"] = json.dumps([session.repo_path], ensure_ascii=False)
            session_values["current_round"] = 0
            session_values["current_turn"] = len(session.messages)
            session_values["summary_json"] = json.dumps({}, ensure_ascii=False)

            existing_columns = self._table_columns(conn, "sessions")
            insert_columns = [c for c in session_values if c in existing_columns]
            placeholders = ", ".join("?" for _ in insert_columns)
            column_sql = ", ".join(insert_columns)
            update_sql = ", ".join(
                f"{c} = excluded.{c}" for c in insert_columns if c != "session_id"
            )
            insert_values = [session_values[c] for c in insert_columns]

            conn.execute(
                f"""
                INSERT INTO sessions ({column_sql})
                VALUES ({placeholders})
                ON CONFLICT(session_id) DO UPDATE SET
                    {update_sql}
                """,
                insert_values,
            )
            conn.commit()

    def append_event(self, event: WorkflowEvent) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO events (
                    event_id,
                    session_id,
                    created_at,
                    event_type,
                    role,
                    content,
                    metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.session_id,
                    event.created_at,
                    event.event_type,
                    event.role,
                    event.content,
                    json.dumps(event.metadata, ensure_ascii=False),
                ),
            )
            conn.commit()

    def list_sessions(self, participant_id: str | None = None) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            if participant_id:
                rows = conn.execute(
                    """
                    SELECT
                        session_id,
                        participant_id,
                        participant_name,
                        created_at,
                        status,
                        workflow_type,
                        manual_step_role,
                        current_node_idx,
                        task_json
                    FROM sessions
                    WHERE participant_id = ?
                    ORDER BY created_at DESC
                    """,
                    (participant_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT
                        session_id,
                        participant_id,
                        participant_name,
                        created_at,
                        status,
                        workflow_type,
                        manual_step_role,
                        current_node_idx,
                        task_json
                    FROM sessions
                    ORDER BY created_at DESC
                    """
                ).fetchall()

        sessions: List[Dict[str, Any]] = []
        for row in rows:
            payload = dict(row)
            task = json.loads(payload.pop("task_json") or "{}")
            payload["task_instance_id"] = task.get("instance_id", "")
            payload["task_repo"] = task.get("repo", "")
            sessions.append(payload)
        return sessions

    def get_events(self, session_id: str, participant_id: str | None = None) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            if participant_id:
                rows = conn.execute(
                    """
                    SELECT e.event_id, e.session_id, e.created_at, e.event_type, e.role, e.content, e.metadata_json
                    FROM events e
                    INNER JOIN sessions s ON s.session_id = e.session_id
                    WHERE e.session_id = ? AND s.participant_id = ?
                    ORDER BY e.created_at ASC
                    """,
                    (session_id, participant_id),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT event_id, session_id, created_at, event_type, role, content, metadata_json
                    FROM events
                    WHERE session_id = ?
                    ORDER BY created_at ASC
                    """,
                    (session_id,),
                ).fetchall()

        output: List[Dict[str, Any]] = []
        for row in rows:
            output.append(
                {
                    "event_id": row["event_id"],
                    "session_id": row["session_id"],
                    "created_at": row["created_at"],
                    "event_type": row["event_type"],
                    "role": row["role"],
                    "content": row["content"],
                    "metadata": json.loads(row["metadata_json"] or "{}"),
                }
            )
        return output

    def get_session(self, session_id: str, participant_id: str | None = None) -> StudySession | None:
        with self._connect() as conn:
            if participant_id:
                row = conn.execute(
                    """
                    SELECT *
                    FROM sessions
                    WHERE session_id = ? AND participant_id = ?
                    """,
                    (session_id, participant_id),
                ).fetchone()
            else:
                row = conn.execute(
                    """
                    SELECT *
                    FROM sessions
                    WHERE session_id = ?
                    """,
                    (session_id,),
                ).fetchone()

        if row is None:
            return None
        return self._session_from_row(row)

    def load_sessions(self) -> List[StudySession]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM sessions
                ORDER BY created_at DESC
                """
            ).fetchall()
        return [self._session_from_row(row) for row in rows]

    def clear_all(self) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM events")
            conn.execute("DELETE FROM sessions")
            conn.commit()

    def _session_from_row(self, row: sqlite3.Row) -> StudySession:
        session = StudySession(
            session_id=row["session_id"],
            participant_id=row["participant_id"] or row["participant_name"],
            participant_name=row["participant_name"],
            created_at=row["created_at"],
            task=json.loads(row["task_json"] or "{}"),
            repo_path=row["repo_path"],
            workflow_type=row["workflow_type"],
            manual_step_role=row["manual_step_role"] or "Task Planner",
            status=row["status"],
            current_node_idx=int(row["current_node_idx"] or 0),
            iteration_count=int(row["iteration_count"] or 0),
            waiting_for_human=bool(row["waiting_for_human"]),
            waiting_role=row["waiting_role"],
            wait_started_at=row["wait_started_at"],
            support_requests_count=int(row["support_requests_count"] or 0),
            messages=json.loads(row["messages_json"] or "[]"),
            submitted_patch=row["submitted_patch"],
            evaluation=(
                json.loads(row["evaluation_json"])
                if row["evaluation_json"]
                else None
            ),
            last_handoff=(
                json.loads(row["last_handoff_json"])
                if row["last_handoff_json"]
                else None
            ),
            last_patch_application=(
                json.loads(row["last_patch_application_json"])
                if row["last_patch_application_json"]
                else None
            ),
            last_test_run=(
                json.loads(row["last_test_run_json"])
                if row["last_test_run_json"]
                else None
            ),
        )
        session.events = [
            WorkflowEvent(
                event_id=event["event_id"],
                session_id=event["session_id"],
                created_at=event["created_at"],
                event_type=event["event_type"],
                role=event["role"],
                content=event["content"],
                metadata=event["metadata"],
            )
            for event in self.get_events(session.session_id, participant_id=session.participant_id)
        ]
        return session
