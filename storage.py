from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class Storage:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.init_db()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    def init_db(self) -> None:
        with self.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS meetings (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    data_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    meeting_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS protocols (
                    meeting_id TEXT PRIMARY KEY,
                    data_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS audit_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    meeting_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    data_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )

    @staticmethod
    def now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def save_meeting(self, meeting_id: str, data: dict[str, Any]) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO meetings(id, created_at, data_json) VALUES (?, COALESCE((SELECT created_at FROM meetings WHERE id = ?), ?), ?)",
                (meeting_id, meeting_id, self.now(), json.dumps(data, ensure_ascii=False)),
            )

    def get_meeting(self, meeting_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute("SELECT data_json FROM meetings WHERE id = ?", (meeting_id,)).fetchone()
        return json.loads(row["data_json"]) if row else None

    def save_protocol(self, meeting_id: str, protocol: dict[str, Any]) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO protocols(meeting_id, data_json, updated_at) VALUES (?, ?, ?)",
                (meeting_id, json.dumps(protocol, ensure_ascii=False), self.now()),
            )

    def get_protocol(self, meeting_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute("SELECT data_json FROM protocols WHERE meeting_id = ?", (meeting_id,)).fetchone()
        return json.loads(row["data_json"]) if row else None

    def add_audit(self, meeting_id: str, event_type: str, data: dict[str, Any]) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT INTO audit_events(meeting_id, event_type, data_json, created_at) VALUES (?, ?, ?, ?)",
                (meeting_id, event_type, json.dumps(data, ensure_ascii=False), self.now()),
            )

    def create_job(self, job_id: str, meeting_id: str) -> None:
        now = self.now()
        with self.connect() as db:
            db.execute(
                "INSERT INTO jobs(id, meeting_id, status, stage, error, created_at, updated_at) VALUES (?, ?, 'queued', 'queued', NULL, ?, ?)",
                (job_id, meeting_id, now, now),
            )

    def update_job(self, job_id: str, *, status: str, stage: str, error: str | None = None) -> None:
        with self.connect() as db:
            db.execute("UPDATE jobs SET status = ?, stage = ?, error = ?, updated_at = ? WHERE id = ?", (status, stage, error, self.now(), job_id))

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return dict(row) if row else None
