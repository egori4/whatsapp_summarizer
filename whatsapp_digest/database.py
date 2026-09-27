"""SQLite storage for WhatsApp Technical Digest v2."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
from typing import Iterator, Any


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class DigestDatabase:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._initialize()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "DigestDatabase":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            yield self._conn
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def _initialize(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value INTEGER NOT NULL
            );
            INSERT OR IGNORE INTO metadata(key, value) VALUES ('change_seq', 0);

            CREATE TABLE IF NOT EXISTS messages (
                message_id TEXT PRIMARY KEY,
                group_jid TEXT NOT NULL,
                sender_id TEXT,
                display_name TEXT,
                occurred_at TEXT NOT NULL,
                text TEXT,
                caption TEXT,
                deleted INTEGER NOT NULL DEFAULT 0 CHECK (deleted IN (0,1)),
                change_seq INTEGER NOT NULL UNIQUE,
                last_changed_at TEXT NOT NULL,
                collected_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_messages_group_seq
                ON messages(group_jid, change_seq);

            CREATE TABLE IF NOT EXISTS discovered_groups (
                group_jid TEXT PRIMARY KEY,
                display_name TEXT,
                first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS workflow_state (
                workflow_id TEXT PRIMARY KEY,
                checkpoint_seq INTEGER,
                initialized INTEGER NOT NULL DEFAULT 0 CHECK (initialized IN (0,1)),
                last_success_at TEXT,
                last_run_id TEXT,
                last_run_status TEXT
            );

            CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY,
                workflow_id TEXT NOT NULL,
                mode TEXT NOT NULL,
                started_at TEXT NOT NULL,
                completed_at TEXT,
                window_start TEXT,
                window_end TEXT,
                checkpoint_before INTEGER,
                cutoff_seq INTEGER,
                message_count INTEGER NOT NULL DEFAULT 0,
                provider TEXT,
                model TEXT,
                reasoning TEXT,
                status TEXT NOT NULL,
                failure_stage TEXT,
                failure_reason TEXT
            );
            """
        )
        self._conn.commit()

    @staticmethod
    def _next_seq(conn: sqlite3.Connection) -> int:
        row = conn.execute("SELECT value FROM metadata WHERE key='change_seq'").fetchone()
        current = int(row[0])
        nxt = current + 1
        conn.execute("UPDATE metadata SET value=? WHERE key='change_seq'", (nxt,))
        return nxt

    def latest_seq(self) -> int:
        row = self._conn.execute("SELECT value FROM metadata WHERE key='change_seq'").fetchone()
        return int(row[0])

    def discover_group(self, group_jid: str, display_name: str | None = None, *, seen_at: str | None = None) -> None:
        stamp = seen_at or _utc_now()
        with self._tx() as conn:
            conn.execute(
                """
                INSERT INTO discovered_groups(group_jid, display_name, first_seen, last_seen)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(group_jid) DO UPDATE SET
                    display_name = COALESCE(excluded.display_name, discovered_groups.display_name),
                    last_seen = excluded.last_seen
                """,
                (group_jid, display_name, stamp, stamp),
            )

    def list_discovered_groups(self) -> list[sqlite3.Row]:
        return list(self._conn.execute(
            "SELECT group_jid, display_name, first_seen, last_seen FROM discovered_groups ORDER BY COALESCE(display_name, group_jid), group_jid"
        ))

    def upsert_message(
        self,
        *,
        message_id: str,
        group_jid: str,
        sender_id: str | None,
        display_name: str | None,
        occurred_at: str,
        text: str | None,
        caption: str | None = None,
        deleted: bool = False,
        changed_at: str | None = None,
        collected_at: str | None = None,
    ) -> tuple[str, int]:
        changed = changed_at or _utc_now()
        collected = collected_at or changed
        normalized = (
            group_jid,
            sender_id,
            display_name,
            occurred_at,
            text,
            caption,
            1 if deleted else 0,
        )
        with self._tx() as conn:
            existing = conn.execute(
                """
                SELECT group_jid, sender_id, display_name, occurred_at, text, caption, deleted, change_seq
                FROM messages WHERE message_id=?
                """,
                (message_id,),
            ).fetchone()
            if existing is not None:
                current = tuple(existing[key] for key in (
                    "group_jid", "sender_id", "display_name", "occurred_at", "text", "caption", "deleted"
                ))
                if current == normalized:
                    return "unchanged", int(existing["change_seq"])
                seq = self._next_seq(conn)
                conn.execute(
                    """
                    UPDATE messages SET
                        group_jid=?, sender_id=?, display_name=?, occurred_at=?,
                        text=?, caption=?, deleted=?, change_seq=?,
                        last_changed_at=?, collected_at=?
                    WHERE message_id=?
                    """,
                    (*normalized, seq, changed, collected, message_id),
                )
                return "updated", seq
            seq = self._next_seq(conn)
            conn.execute(
                """
                INSERT INTO messages(
                    message_id, group_jid, sender_id, display_name, occurred_at,
                    text, caption, deleted, change_seq, last_changed_at, collected_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (message_id, *normalized, seq, changed, collected),
            )
            return "inserted", seq

    def mark_deleted(self, message_id: str, *, changed_at: str | None = None) -> tuple[str, int | None]:
        row = self._conn.execute("SELECT * FROM messages WHERE message_id=?", (message_id,)).fetchone()
        if row is None:
            return "missing", None
        return self.upsert_message(
            message_id=message_id,
            group_jid=row["group_jid"],
            sender_id=row["sender_id"],
            display_name=row["display_name"],
            occurred_at=row["occurred_at"],
            text=row["text"],
            caption=row["caption"],
            deleted=True,
            changed_at=changed_at,
            collected_at=row["collected_at"],
        )

    def ensure_workflow(self, workflow_id: str) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO workflow_state(workflow_id) VALUES (?)",
            (workflow_id,),
        )
        self._conn.commit()

    def workflow_state(self, workflow_id: str) -> sqlite3.Row:
        self.ensure_workflow(workflow_id)
        row = self._conn.execute("SELECT * FROM workflow_state WHERE workflow_id=?", (workflow_id,)).fetchone()
        assert row is not None
        return row

    def set_checkpoint(self, workflow_id: str, checkpoint_seq: int, *, run_id: str | None = None, status: str = "success") -> None:
        self.ensure_workflow(workflow_id)
        self._conn.execute(
            """
            UPDATE workflow_state SET
                checkpoint_seq=?, initialized=1, last_success_at=?,
                last_run_id=?, last_run_status=?
            WHERE workflow_id=?
            """,
            (checkpoint_seq, _utc_now(), run_id, status, workflow_id),
        )
        self._conn.commit()

    def pending_count(self, workflow_id: str, group_jid: str) -> int:
        state = self.workflow_state(workflow_id)
        checkpoint = state["checkpoint_seq"]
        checkpoint = int(checkpoint) if checkpoint is not None else 0
        row = self._conn.execute(
            "SELECT COUNT(*) FROM messages WHERE group_jid=? AND change_seq>?",
            (group_jid, checkpoint),
        ).fetchone()
        return int(row[0])

    def select_changes(self, group_jid: str, after_seq: int, cutoff_seq: int) -> list[sqlite3.Row]:
        return list(self._conn.execute(
            """
            SELECT * FROM messages
            WHERE group_jid=? AND change_seq>? AND change_seq<=?
            ORDER BY change_seq
            """,
            (group_jid, after_seq, cutoff_seq),
        ))

    def select_since(self, group_jid: str, since_utc: str, cutoff_seq: int) -> list[sqlite3.Row]:
        return list(self._conn.execute(
            """
            SELECT * FROM messages
            WHERE group_jid=? AND occurred_at>=? AND change_seq<=?
            ORDER BY change_seq
            """,
            (group_jid, since_utc, cutoff_seq),
        ))

    def get_messages(self, message_ids: list[str]) -> list[sqlite3.Row]:
        if not message_ids:
            return []
        placeholders = ",".join("?" for _ in message_ids)
        return list(self._conn.execute(
            f"SELECT * FROM messages WHERE message_id IN ({placeholders})",
            message_ids,
        ))

    def sources_are_fresh(self, snapshot_rows: list[sqlite3.Row]) -> bool:
        for snapshot in snapshot_rows:
            current = self._conn.execute(
                "SELECT change_seq, deleted FROM messages WHERE message_id=?",
                (snapshot["message_id"],),
            ).fetchone()
            if current is None:
                return False
            if int(current["change_seq"]) != int(snapshot["change_seq"]):
                return False
            if int(current["deleted"]) != int(snapshot["deleted"]):
                return False
        return True

    def complete_success_and_checkpoint(
        self,
        run_id: str,
        workflow_id: str,
        checkpoint_seq: int,
        *,
        status: str,
        provider: str | None = None,
        model: str | None = None,
        reasoning: str | None = None,
    ) -> None:
        stamp = _utc_now()
        with self._tx() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO workflow_state(workflow_id) VALUES (?)",
                (workflow_id,),
            )
            conn.execute(
                """
                UPDATE workflow_state SET
                    checkpoint_seq=?, initialized=1, last_success_at=?,
                    last_run_id=?, last_run_status=?
                WHERE workflow_id=?
                """,
                (checkpoint_seq, stamp, run_id, status, workflow_id),
            )
            conn.execute(
                """
                UPDATE runs SET completed_at=?, status=?, provider=?, model=?, reasoning=?,
                    failure_stage=NULL, failure_reason=NULL
                WHERE run_id=?
                """,
                (stamp, status, provider, model, reasoning, run_id),
            )

    def create_run(self, *, run_id: str, workflow_id: str, mode: str, checkpoint_before: int | None, cutoff_seq: int | None, message_count: int = 0) -> None:
        self._conn.execute(
            """
            INSERT INTO runs(run_id, workflow_id, mode, started_at, checkpoint_before, cutoff_seq, message_count, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'running')
            """,
            (run_id, workflow_id, mode, _utc_now(), checkpoint_before, cutoff_seq, message_count),
        )
        self._conn.commit()

    def finish_run(
        self,
        run_id: str,
        *,
        status: str,
        provider: str | None = None,
        model: str | None = None,
        reasoning: str | None = None,
        failure_stage: str | None = None,
        failure_reason: str | None = None,
    ) -> None:
        self._conn.execute(
            """
            UPDATE runs SET completed_at=?, status=?, provider=?, model=?, reasoning=?,
                failure_stage=?, failure_reason=? WHERE run_id=?
            """,
            (_utc_now(), status, provider, model, reasoning, failure_stage, failure_reason, run_id),
        )
        self._conn.commit()

    def purge_processed_messages(self, workflow_groups: dict[str, str], retention_days: int, *, now: datetime | None = None) -> int:
        if retention_days < 0:
            raise ValueError("retention_days must be non-negative")
        current = now or datetime.now(timezone.utc)
        cutoff_time = (current - timedelta(days=retention_days)).isoformat()
        removable_ids: list[str] = []
        for workflow_id, group_jid in workflow_groups.items():
            state = self.workflow_state(workflow_id)
            checkpoint = state["checkpoint_seq"]
            if not state["initialized"] or checkpoint is None:
                continue
            rows = self._conn.execute(
                """
                SELECT message_id FROM messages
                WHERE group_jid=? AND change_seq<=? AND last_changed_at<?
                """,
                (group_jid, int(checkpoint), cutoff_time),
            )
            removable_ids.extend(row["message_id"] for row in rows)
        if not removable_ids:
            return 0
        with self._tx() as conn:
            conn.executemany("DELETE FROM messages WHERE message_id=?", [(mid,) for mid in removable_ids])
        return len(removable_ids)
