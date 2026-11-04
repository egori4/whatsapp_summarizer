"""SQLite storage for WhatsApp Technical Digest v2."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
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

            CREATE TABLE IF NOT EXISTS daily_digest_artifacts (
                run_id TEXT PRIMARY KEY REFERENCES runs(run_id),
                workflow_id TEXT NOT NULL,
                completed_at TEXT NOT NULL,
                checkpoint_before INTEGER NOT NULL,
                checkpoint_after INTEGER NOT NULL,
                outcome TEXT NOT NULL,
                window_start TEXT,
                window_end TEXT,
                digest_json TEXT NOT NULL,
                schema_version INTEGER NOT NULL DEFAULT 1
            );
            CREATE INDEX IF NOT EXISTS idx_daily_artifact_period
                ON daily_digest_artifacts(workflow_id, completed_at);
            CREATE TABLE IF NOT EXISTS weekly_reports (
                workflow_id TEXT NOT NULL,
                week_start_local_date TEXT NOT NULL,
                week_end_local_date TEXT NOT NULL,
                run_id TEXT NOT NULL REFERENCES runs(run_id),
                delivered_at TEXT NOT NULL,
                input_run_ids_json TEXT NOT NULL,
                digest_json TEXT,
                PRIMARY KEY (workflow_id, week_start_local_date)
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

    def select_context(self, group_jid: str, through_seq: int, since_utc: str, limit: int) -> list[sqlite3.Row]:
        rows = list(self._conn.execute(
            """
            SELECT * FROM messages
            WHERE group_jid=? AND change_seq<=? AND occurred_at>=?
              AND deleted=0 AND TRIM(COALESCE(NULLIF(text, ''), caption, ''))<>''
            ORDER BY occurred_at DESC, change_seq DESC, message_id DESC
            LIMIT ?
            """,
            (group_jid, through_seq, since_utc, limit),
        ))
        rows.reverse()
        return rows

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
        digest_artifact: dict[str, Any] | None = None,
        window_start: str | None = None,
        window_end: str | None = None,
        checkpoint_before: int | None = None,
    ) -> None:
        stamp = _utc_now()
        with self._tx() as conn:
            existing = conn.execute("SELECT status FROM runs WHERE run_id=?", (run_id,)).fetchone()
            if existing is None:
                raise ValueError("unknown run_id")
            # Avoid stale duplicate finalization moving a later checkpoint backwards.
            if existing["status"] in {"delivered", "success-no-material", "success-no-pending", "success-no-usable"}:
                return
            if digest_artifact is not None:
                state = conn.execute("SELECT checkpoint_seq FROM workflow_state WHERE workflow_id=?", (workflow_id,)).fetchone()
                actual_before = int(state["checkpoint_seq"] or 0) if state else 0
                if checkpoint_before is None or actual_before != checkpoint_before:
                    raise ValueError("daily checkpoint changed before artifact finalization")
                conn.execute(
                    """INSERT INTO daily_digest_artifacts
                      (run_id, workflow_id, completed_at, checkpoint_before, checkpoint_after,
                       outcome, window_start, window_end, digest_json)
                      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (run_id, workflow_id, stamp, checkpoint_before, checkpoint_seq,
                     status, window_start, window_end,
                     json.dumps(digest_artifact, ensure_ascii=False, separators=(",", ":"))),
                )
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

    def daily_anchor(self, workflow_id: str, boundary_utc: str) -> sqlite3.Row | None:
        return self._conn.execute(
            """SELECT * FROM daily_digest_artifacts WHERE workflow_id=? AND completed_at<=?
            ORDER BY completed_at DESC, run_id DESC LIMIT 1""",
            (workflow_id, boundary_utc),
        ).fetchone()

    def daily_artifacts(self, workflow_id: str, after_utc: str, through_utc: str) -> list[sqlite3.Row]:
        return list(self._conn.execute(
            """SELECT * FROM daily_digest_artifacts
            WHERE workflow_id=? AND completed_at>? AND completed_at<=?
            ORDER BY completed_at, run_id""",
            (workflow_id, after_utc, through_utc),
        ))

    def weekly_report(self, workflow_id: str, week_start: str) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM weekly_reports WHERE workflow_id=? AND week_start_local_date=?",
            (workflow_id, week_start),
        ).fetchone()

    def last_weekly_report(self, workflow_id: str) -> sqlite3.Row | None:
        return self._conn.execute(
            """SELECT * FROM weekly_reports WHERE workflow_id=?
            ORDER BY week_start_local_date DESC LIMIT 1""", (workflow_id,),
        ).fetchone()

    def last_weekly_attempt(self, workflow_id: str) -> sqlite3.Row | None:
        return self._conn.execute(
            """SELECT run_id, started_at, status, failure_stage FROM runs
            WHERE workflow_id=? AND mode IN ('weekly', 'weekly-scheduled', 'weekly-dry-run')
            ORDER BY started_at DESC, run_id DESC LIMIT 1""", (workflow_id,),
        ).fetchone()

    def complete_weekly_delivery(
        self, *, run_id: str, workflow_id: str, week_start: str, week_end: str,
        source_run_ids: list[str], digest: dict[str, Any],
        provider: str, model: str, reasoning: str,
    ) -> None:
        with self._tx() as conn:
            conn.execute(
                """INSERT INTO weekly_reports (workflow_id, week_start_local_date,
                week_end_local_date, run_id, delivered_at, input_run_ids_json, digest_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (workflow_id, week_start, week_end, run_id, _utc_now(),
                 json.dumps(source_run_ids), json.dumps(digest, ensure_ascii=False)),
            )
            conn.execute(
                """UPDATE runs SET completed_at=?, status='weekly-delivered', provider=?,
                model=?, reasoning=? WHERE run_id=?""",
                (_utc_now(), provider, model, reasoning, run_id),
            )

    def purge_digest_artifacts(self, retention_days: int, *, now: datetime | None = None) -> int:
        if retention_days < 0:
            raise ValueError("retention_days must be non-negative")
        current = now or datetime.now(timezone.utc)
        cutoff = (current - timedelta(days=retention_days)).isoformat()
        with self._tx() as conn:
            removed = conn.execute(
                "DELETE FROM daily_digest_artifacts WHERE completed_at<?", (cutoff,)
            ).rowcount
            conn.execute(
                """UPDATE weekly_reports SET digest_json=NULL, input_run_ids_json='[]'
                WHERE delivered_at<? AND digest_json IS NOT NULL""", (cutoff,)
            )
        return removed

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
