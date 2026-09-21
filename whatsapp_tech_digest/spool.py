from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence


class BoundaryError(ValueError):
    """An input or reopened database crosses the approved target boundary."""


class DeliveryBlockedError(RuntimeError):
    """An unresolved SMTP outcome must be reconciled before another snapshot."""


_GROUP_JID = re.compile(r"^[0-9]{8,}(?:-[0-9]{8,})?@g\.us$")
_LOCK_BUSY_TIMEOUT_MS = 250
_LOCK_ATTEMPTS = 2


def _transient_lock(error: sqlite3.OperationalError) -> bool:
    message = str(error).casefold()
    return "locked" in message or "busy" in message


class DurableSpool:
    """Single-target, append-only SQLite state for the offline digest pipeline."""

    _TERMINAL = {"accepted", "empty"}
    _STATES = {"failed", "accepted", "empty", "unknown"}

    def __init__(self, path: str | Path, target_group_jid: str) -> None:
        if not isinstance(target_group_jid, str) or not _GROUP_JID.fullmatch(target_group_jid):
            raise BoundaryError("an exact immutable @g.us target group JID is required before opening the spool")
        self.target_group_jid = target_group_jid
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self.path.parent, 0o700)
        first_create = not self.path.exists()
        self.connection = sqlite3.connect(self.path, isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute(f"PRAGMA busy_timeout={_LOCK_BUSY_TIMEOUT_MS}")
        if first_create:
            os.chmod(self.path, 0o600)
        self._preflight_existing_boundary()
        self._initialize()

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "DurableSpool":
        return self

    def __exit__(self, _exc_type: object, _exc: object, _traceback: object) -> None:
        self.close()

    def _preflight_existing_boundary(self) -> None:
        """Reject an existing foreign or unpinned populated spool before schema writes."""
        tables = {
            str(row["name"])
            for row in self.connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if "metadata" not in tables:
            if "messages" in tables and int(
                self.connection.execute("SELECT COUNT(*) AS n FROM messages").fetchone()["n"]
            ):
                raise BoundaryError("legacy spool without immutable target metadata is unsafe to migrate")
            return
        pin = self.connection.execute(
            "SELECT value FROM metadata WHERE key='target_group_jid'"
        ).fetchone()
        if pin is not None and pin["value"] != self.target_group_jid:
            raise BoundaryError("spool is permanently bound to a different target JID")
        if "messages" in tables:
            row_count = int(self.connection.execute("SELECT COUNT(*) AS n FROM messages").fetchone()["n"])
            if pin is None and row_count:
                raise BoundaryError("legacy spool without immutable target metadata is unsafe to migrate")
            mixed = self.connection.execute(
                "SELECT 1 FROM messages WHERE chat_jid != ? LIMIT 1", (self.target_group_jid,)
            ).fetchone()
            if mixed:
                raise BoundaryError("mixed-JID state is unsafe and cannot be reopened")

    def _initialize(self) -> None:
        self.connection.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA foreign_keys=ON;
            CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS messages (
              message_id TEXT PRIMARY KEY, chat_jid TEXT NOT NULL, ingest_seq INTEGER NOT NULL UNIQUE,
              current_change_seq INTEGER NOT NULL UNIQUE, occurred_at TEXT NOT NULL, committed_at TEXT NOT NULL,
              participant TEXT NOT NULL, display_name TEXT, current_text TEXT, tombstone INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS message_versions (
              change_seq INTEGER PRIMARY KEY, message_id TEXT NOT NULL REFERENCES messages(message_id),
              change_type TEXT NOT NULL CHECK(change_type IN ('insert','edit','revocation')),
              committed_at TEXT NOT NULL, occurred_at TEXT NOT NULL, participant TEXT NOT NULL,
              text TEXT, content_hash TEXT, tombstone INTEGER NOT NULL CHECK(tombstone IN (0,1))
            );
            CREATE TABLE IF NOT EXISTS classifications (
              change_seq INTEGER PRIMARY KEY REFERENCES message_versions(change_seq),
              disposition TEXT NOT NULL, confidence REAL, category TEXT, topic_hint TEXT, rationale TEXT,
              model_id TEXT NOT NULL, prompt_version TEXT NOT NULL DEFAULT 'v1', schema_version TEXT NOT NULL DEFAULT 'v1',
              failure_state TEXT NOT NULL DEFAULT 'ok'
            );
            CREATE TABLE IF NOT EXISTS collector_health_intervals (
              id INTEGER PRIMARY KEY, state TEXT NOT NULL, started_at TEXT NOT NULL, ended_at TEXT,
              reason TEXT NOT NULL, bridge_identity TEXT, guard_active INTEGER NOT NULL,
              last_heartbeat_at TEXT, observed_events INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS checkpoints (
              id INTEGER PRIMARY KEY CHECK(id=1), checkpoint_seq INTEGER NOT NULL,
              delivery_state TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS digest_runs (
              digest_id TEXT PRIMARY KEY, checkpoint_seq INTEGER NOT NULL, cutoff_seq INTEGER NOT NULL,
              run_type TEXT NOT NULL, created_at TEXT NOT NULL, smtp_message_id TEXT, smtp_state TEXT NOT NULL,
              output TEXT, omission_note TEXT, omission_checkpoint_seq INTEGER, candidate_count INTEGER NOT NULL DEFAULT 0,
              source_count INTEGER NOT NULL DEFAULT 0, model_id TEXT, config_hash TEXT, content_hash TEXT,
              coverage_snapshot TEXT, checkpoint_decision TEXT NOT NULL DEFAULT 'retain',
              UNIQUE(checkpoint_seq, cutoff_seq)
            );
            CREATE TABLE IF NOT EXISTS digest_provenance (
              digest_id TEXT PRIMARY KEY REFERENCES digest_runs(digest_id) ON DELETE CASCADE,
              schema_version TEXT NOT NULL, provenance_json TEXT NOT NULL, provenance_hash TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS open_questions (
              message_id TEXT PRIMARY KEY, question_change_seq INTEGER NOT NULL UNIQUE,
              status TEXT NOT NULL CHECK(status IN ('open','partial','resolved')),
              opened_digest_id TEXT NOT NULL, updated_digest_id TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS reviewed_artifacts (
              review_id TEXT PRIMARY KEY, checkpoint_seq INTEGER NOT NULL, cutoff_seq INTEGER NOT NULL,
              run_type TEXT NOT NULL, created_at TEXT NOT NULL, content_hash TEXT NOT NULL,
              candidate_count INTEGER NOT NULL, source_count INTEGER NOT NULL, model_id TEXT,
              config_hash TEXT NOT NULL, coverage_snapshot TEXT, schema_version TEXT NOT NULL,
              provenance_json TEXT NOT NULL, provenance_hash TEXT NOT NULL,
              window_kind TEXT NOT NULL DEFAULT 'complete', consumed_at TEXT
            );
        """)
        digest_run_columns = {row["name"] for row in self.connection.execute("PRAGMA table_info(digest_runs)")}
        if "omission_checkpoint_seq" not in digest_run_columns:
            self.connection.execute("ALTER TABLE digest_runs ADD COLUMN omission_checkpoint_seq INTEGER")
        reviewed_columns = {
            row["name"] for row in self.connection.execute("PRAGMA table_info(reviewed_artifacts)")
        }
        if "window_kind" not in reviewed_columns:
            self.connection.execute(
                "ALTER TABLE reviewed_artifacts ADD COLUMN window_kind TEXT NOT NULL DEFAULT 'complete'"
            )
        if "consumed_at" not in reviewed_columns:
            self.connection.execute("ALTER TABLE reviewed_artifacts ADD COLUMN consumed_at TEXT")
        question_columns = list(self.connection.execute("PRAGMA table_info(open_questions)"))
        question_primary_key = next((row["name"] for row in question_columns if row["pk"]), None)
        if question_primary_key == "question_change_seq":
            self.connection.executescript("""
                BEGIN IMMEDIATE;
                ALTER TABLE open_questions RENAME TO open_questions_revision_keyed;
                CREATE TABLE open_questions (
                  message_id TEXT PRIMARY KEY, question_change_seq INTEGER NOT NULL UNIQUE,
                  status TEXT NOT NULL CHECK(status IN ('open','partial','resolved')),
                  opened_digest_id TEXT NOT NULL, updated_digest_id TEXT NOT NULL
                );
                INSERT INTO open_questions(message_id,question_change_seq,status,opened_digest_id,updated_digest_id)
                SELECT legacy.message_id,legacy.question_change_seq,legacy.status,
                       legacy.opened_digest_id,legacy.updated_digest_id
                  FROM open_questions_revision_keyed legacy
                  JOIN (
                    SELECT message_id,MAX(question_change_seq) AS question_change_seq
                      FROM open_questions_revision_keyed GROUP BY message_id
                  ) latest USING(message_id,question_change_seq);
                DROP TABLE open_questions_revision_keyed;
                COMMIT;
            """)
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            pin = self.connection.execute("SELECT value FROM metadata WHERE key='target_group_jid'").fetchone()
            row_count = self.connection.execute("SELECT COUNT(*) AS n FROM messages").fetchone()["n"]
            if pin is None:
                if row_count:
                    raise BoundaryError("legacy spool without immutable target metadata is unsafe to migrate")
                self.connection.execute("INSERT INTO metadata(key, value) VALUES ('target_group_jid', ?)", (self.target_group_jid,))
            elif pin["value"] != self.target_group_jid:
                raise BoundaryError("spool is permanently bound to a different target JID")
            mixed = self.connection.execute("SELECT 1 FROM messages WHERE chat_jid != ? LIMIT 1", (self.target_group_jid,)).fetchone()
            if mixed:
                raise BoundaryError("mixed-JID state is unsafe and cannot be reopened")
            for key in ("next_ingest_seq", "next_change_seq"):
                self.connection.execute("INSERT OR IGNORE INTO metadata VALUES (?, '1')", (key,))
            self.connection.execute("INSERT OR IGNORE INTO checkpoints VALUES (1, 0, 'clear', ?)", (self._now(),))
            self.connection.execute("COMMIT")
        except Exception:
            self.connection.execute("ROLLBACK")
            raise

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _next(self, key: str) -> int:
        value = int(self.connection.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()["value"])
        self.connection.execute("UPDATE metadata SET value=? WHERE key=?", (str(value + 1), key))
        return value

    @staticmethod
    def normalize_participant(value: object) -> str:
        if not isinstance(value, str) or not value.strip():
            raise BoundaryError("participant normalization requires a nonempty JID")
        bare = value.split("@", 1)[0].split(":", 1)[0]
        if not bare or any(char.isspace() for char in bare):
            raise BoundaryError("participant JID cannot be normalized")
        return bare

    def _validate_event(self, event: Mapping[str, Any]) -> tuple[str, str, str, str]:
        if event.get("chat_jid") != self.target_group_jid:
            raise BoundaryError("non-target chat must never enter durable state")
        message_id, timestamp = event.get("message_id"), event.get("timestamp")
        if not isinstance(message_id, str) or not message_id or not isinstance(timestamp, str) or not timestamp:
            raise BoundaryError("event lacks immutable ID or timestamp")
        text = event.get("text")
        if text is not None and not isinstance(text, str):
            raise BoundaryError("text must be a string or absent on revocation")
        return message_id, timestamp, self.normalize_participant(event.get("participant")), text or ""

    def append_message(self, event: Mapping[str, Any]) -> int:
        for attempt in range(_LOCK_ATTEMPTS):
            try:
                return self._append_once(event)
            except sqlite3.OperationalError as exc:
                if not _transient_lock(exc) or attempt + 1 == _LOCK_ATTEMPTS:
                    raise
        raise RuntimeError("unreachable lock retry exhaustion")

    def _append_once(self, event: Mapping[str, Any]) -> int:
        message_id, occurred_at, participant, text = self._validate_event(event)
        revoked = event.get("revoked") is True
        text_value = None if revoked else text
        content_hash = None if revoked else hashlib.sha256(text.encode("utf-8")).hexdigest()
        now = self._now()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            existing = self.connection.execute("SELECT * FROM messages WHERE message_id=?", (message_id,)).fetchone()
            if existing and existing["current_text"] == text_value and int(existing["tombstone"]) == int(revoked):
                self.connection.execute("COMMIT")
                return int(existing["ingest_seq"])
            change_seq = self._next("next_change_seq")
            if existing:
                ingest_seq, change_type = int(existing["ingest_seq"]), ("revocation" if revoked else "edit")
                self.connection.execute("UPDATE messages SET current_change_seq=?, current_text=?, tombstone=?, committed_at=?, participant=?, display_name=? WHERE message_id=?", (change_seq, text_value, int(revoked), now, participant, event.get("display_name"), message_id))
            else:
                ingest_seq, change_type = self._next("next_ingest_seq"), ("revocation" if revoked else "insert")
                self.connection.execute("INSERT INTO messages VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (message_id, self.target_group_jid, ingest_seq, change_seq, occurred_at, now, participant, event.get("display_name"), text_value, int(revoked)))
            self.connection.execute("INSERT INTO message_versions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (change_seq, message_id, change_type, now, occurred_at, participant, text_value, content_hash, int(revoked)))
            if existing and revoked:
                self.connection.execute("DELETE FROM open_questions WHERE message_id=?", (message_id,))
            elif existing:
                self.connection.execute(
                    "UPDATE open_questions SET question_change_seq=? "
                    "WHERE message_id=? AND status IN ('open','partial')",
                    (change_seq, message_id),
                )
            if not self.connection.execute("SELECT 1 FROM collector_health_intervals WHERE ended_at IS NULL").fetchone():
                self.connection.execute(
                    "INSERT INTO collector_health_intervals(state,started_at,reason,bridge_identity,guard_active,last_heartbeat_at) VALUES (?,?,?,?,?,?)",
                    ("unknown", now, "target event observed before collector lifecycle was established", None, 0, now),
                )
            self.connection.execute("UPDATE collector_health_intervals SET observed_events=observed_events+1,last_heartbeat_at=? WHERE ended_at IS NULL", (now,))
            self.connection.execute("COMMIT")
            return ingest_seq
        except Exception:
            self.connection.execute("ROLLBACK")
            raise

    def snapshot(self, *, cutoff_seq: int | None = None) -> dict[str, int]:
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            checkpoint = self.connection.execute("SELECT checkpoint_seq, delivery_state FROM checkpoints WHERE id=1").fetchone()
            if checkpoint["delivery_state"] == "unknown":
                raise DeliveryBlockedError("unresolved SMTP acceptance blocks a new digest snapshot")
            latest = int(self.connection.execute("SELECT value FROM metadata WHERE key='next_change_seq'").fetchone()["value"]) - 1
            checkpoint_seq = int(checkpoint["checkpoint_seq"])
            if cutoff_seq is None:
                cutoff = latest
            else:
                if not isinstance(cutoff_seq, int) or isinstance(cutoff_seq, bool):
                    raise ValueError("bounded cutoff must be an integer durable change sequence")
                cutoff = cutoff_seq
                if cutoff <= checkpoint_seq:
                    raise DeliveryBlockedError("bounded cutoff must be strictly after the checkpoint")
                if cutoff > latest:
                    raise DeliveryBlockedError("bounded cutoff is later than the latest durable change")
                if not self._is_target_change(cutoff):
                    raise DeliveryBlockedError("bounded cutoff must identify a durable change")
                if not self._snapshot_has_active_sources(
                    {"checkpoint_seq": checkpoint_seq, "cutoff_seq": cutoff}
                ):
                    raise DeliveryBlockedError("bounded cutoff selects an empty active prefix")
            self.connection.execute("COMMIT")
            return {"checkpoint_seq": checkpoint_seq, "cutoff_seq": cutoff}
        except Exception:
            self.connection.execute("ROLLBACK")
            raise

    def pending_events(
        self,
        snapshot: Mapping[str, int],
        *,
        now: datetime | None = None,
        include_historical_prefix: bool = False,
    ) -> tuple[list[dict[str, Any]], str, str | None]:
        now = now or datetime.now(timezone.utc)
        checkpoint, cutoff = int(snapshot["checkpoint_seq"]), int(snapshot["cutoff_seq"])
        rows = [dict(row) for row in self.connection.execute("""
            SELECT v.change_seq, v.message_id, v.change_type, v.committed_at, v.occurred_at AS timestamp,
                   v.participant, v.text, v.tombstone, m.ingest_seq, m.chat_jid, m.display_name
              FROM messages m
              JOIN message_versions v ON v.change_seq = (
                  SELECT MAX(snapshot_version.change_seq)
                    FROM message_versions snapshot_version
                   WHERE snapshot_version.message_id = m.message_id
                     AND snapshot_version.change_seq <= ?
              )
             WHERE v.change_seq > ? AND m.chat_jid=? AND v.tombstone=0
             ORDER BY v.change_seq
        """, (cutoff, checkpoint, self.target_group_jid))]
        if include_historical_prefix:
            return rows, "bounded-backlog", None
        horizon_hours = 24 if checkpoint == 0 else 72
        horizon = now - timedelta(hours=horizon_hours)
        within = [row for row in rows if datetime.fromisoformat(row["committed_at"]) >= horizon]
        omitted = len(rows) - len(within)
        run_type = "first-run" if checkpoint == 0 else ("catch-up" if omitted else "normal")
        note = f"{omitted} durable changes older than {horizon_hours}h omitted" if omitted else None
        return within, run_type, note

    def carry_forward_events(
        self, snapshot: Mapping[str, int], *, allow_later_changes: bool = False
    ) -> list[dict[str, Any]]:
        """Return only current source revisions for unresolved cross-day questions."""
        cutoff = int(snapshot["cutoff_seq"])
        if allow_later_changes:
            rows = self.connection.execute("""
                SELECT v.change_seq, v.message_id, v.change_type, v.committed_at,
                       v.occurred_at AS timestamp, v.participant, v.text, v.tombstone,
                       m.ingest_seq, m.chat_jid, m.display_name
                  FROM open_questions q
                  JOIN messages m ON m.message_id=q.message_id
                  JOIN message_versions v ON v.message_id=q.message_id
                 WHERE q.status IN ('open','partial')
                   AND v.change_seq <= ? AND m.chat_jid=? AND v.tombstone=0 AND m.tombstone=0
                   AND v.change_seq=(SELECT MAX(change_seq) FROM message_versions
                       WHERE message_id=q.message_id AND change_seq<=?)
                 ORDER BY v.change_seq
            """, (cutoff, self.target_group_jid, cutoff))
        else:
            rows = self.connection.execute("""
                SELECT v.change_seq, v.message_id, v.change_type, v.committed_at,
                       v.occurred_at AS timestamp, v.participant, v.text, v.tombstone,
                       m.ingest_seq, m.chat_jid, m.display_name
                  FROM open_questions q
                  JOIN message_versions v ON v.change_seq=q.question_change_seq
                  JOIN messages m ON m.message_id=q.message_id
                 WHERE q.status IN ('open','partial')
                   AND v.change_seq <= ?
                   AND m.chat_jid=?
                   AND m.current_change_seq=v.change_seq
                   AND m.tombstone=0
                 ORDER BY v.change_seq
            """, (cutoff, self.target_group_jid))
        return [dict(row) for row in rows]

    def omission_prefix_checkpoint(self, snapshot: Mapping[str, int], *, now: datetime | None = None) -> int | None:
        """Return the safe omission prefix boundary, or None when old/fresh rows interleave."""
        now = now or datetime.now(timezone.utc)
        checkpoint, cutoff = int(snapshot["checkpoint_seq"]), int(snapshot["cutoff_seq"])
        rows = self.connection.execute("""
            SELECT v.change_seq, v.committed_at
              FROM messages m
              JOIN message_versions v ON v.change_seq = (
                  SELECT MAX(snapshot_version.change_seq)
                    FROM message_versions snapshot_version
                   WHERE snapshot_version.message_id = m.message_id
                     AND snapshot_version.change_seq <= ?
              )
             WHERE v.change_seq > ? AND m.chat_jid=? AND v.tombstone=0
             ORDER BY v.change_seq
        """, (cutoff, checkpoint, self.target_group_jid)).fetchall()
        horizon = now - timedelta(hours=24 if checkpoint == 0 else 72)
        omitted = [int(row["change_seq"]) for row in rows if datetime.fromisoformat(row["committed_at"]) < horizon]
        if not omitted:
            return checkpoint
        boundary = max(omitted)
        if any(int(row["change_seq"]) <= boundary and datetime.fromisoformat(row["committed_at"]) >= horizon for row in rows):
            return None
        return boundary

    def require_current(
        self, events: Sequence[Mapping[str, Any]], *, cutoff_seq: int | None = None
    ) -> None:
        """Fail closed if a source changed after it was selected for processing."""
        if not events:
            return
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            for event in events:
                if cutoff_seq is None:
                    row = self.connection.execute(
                        "SELECT current_change_seq AS change_seq,tombstone FROM messages "
                        "WHERE message_id=? AND chat_jid=?",
                        (str(event["message_id"]), self.target_group_jid),
                    ).fetchone()
                else:
                    row = self.connection.execute(
                        "SELECT v.change_seq,v.tombstone FROM message_versions v "
                        "JOIN messages m USING(message_id) WHERE v.message_id=? AND m.chat_jid=? "
                        "AND m.tombstone=0 "
                        "AND v.change_seq=(SELECT MAX(change_seq) FROM message_versions "
                        "WHERE message_id=v.message_id AND change_seq<=?)",
                        (str(event["message_id"]), self.target_group_jid, cutoff_seq),
                    ).fetchone()
                if row is None or int(row["change_seq"]) != int(event["change_seq"]) or bool(row["tombstone"]):
                    raise DeliveryBlockedError("a selected source was revoked or superseded; retry from a new snapshot")
            self.connection.execute("COMMIT")
        except Exception:
            self.connection.execute("ROLLBACK")
            raise

    def _is_target_change(self, change_seq: int) -> bool:
        return self.connection.execute(
            "SELECT 1 FROM message_versions v JOIN messages m USING(message_id) "
            "WHERE v.change_seq=? AND m.chat_jid=?",
            (change_seq, self.target_group_jid),
        ).fetchone() is not None

    def _snapshot_has_active_sources(self, snapshot: Mapping[str, int]) -> bool:
        checkpoint, cutoff = int(snapshot["checkpoint_seq"]), int(snapshot["cutoff_seq"])
        row = self.connection.execute("""
            SELECT 1
              FROM messages m
              JOIN message_versions v ON v.change_seq = (
                  SELECT MAX(snapshot_version.change_seq)
                    FROM message_versions snapshot_version
                   WHERE snapshot_version.message_id = m.message_id
                     AND snapshot_version.change_seq <= ?
              )
             WHERE v.change_seq > ? AND m.chat_jid=? AND v.tombstone=0
             LIMIT 1
        """, (cutoff, checkpoint, self.target_group_jid)).fetchone()
        return row is not None

    def _digest_id(self, snapshot: Mapping[str, int]) -> str:
        return hashlib.sha256(f"schema-v1:{snapshot['checkpoint_seq']}:{snapshot['cutoff_seq']}:{self.target_group_jid}".encode()).hexdigest()[:24]

    @staticmethod
    def _provenance_revision(value: object) -> tuple[str, int]:
        if (
            not isinstance(value, list)
            or len(value) != 2
            or not isinstance(value[0], str)
            or not value[0]
            or not isinstance(value[1], int)
            or isinstance(value[1], bool)
            or value[1] < 1
        ):
            raise ValueError("provenance revisions must be [immutable_message_id, change_seq]")
        return value[0], value[1]

    def _canonical_provenance(
        self,
        provenance: Mapping[str, Any],
        cutoff: int,
        *,
        allow_later_changes: bool = False,
    ) -> tuple[str, str, str]:
        if not isinstance(provenance, Mapping) or set(provenance) != {"schema_version", "topics", "unanswered"}:
            raise ValueError("provenance schema drift")
        schema_version = provenance.get("schema_version")
        topics, unanswered = provenance.get("topics"), provenance.get("unanswered")
        if (
            schema_version not in {"actionable-provenance-v1", "actionable-provenance-v2"}
            or not isinstance(topics, list)
            or not isinstance(unanswered, list)
            or (not topics and not unanswered)
        ):
            raise ValueError("invalid actionable provenance envelope")
        topic_fields = ("source_revisions", "raw_keep_revisions", "reference_revisions", "question_revisions", "contributor_revisions")
        expected_topic_fields = set(topic_fields)
        if schema_version == "actionable-provenance-v2":
            expected_topic_fields.add("question_resolution")
        revisions: list[tuple[str, int]] = []
        answered_questions: dict[tuple[str, int], str] = {}
        for topic in topics:
            if not isinstance(topic, Mapping) or set(topic) != expected_topic_fields:
                raise ValueError("provenance topic schema drift")
            source_revisions: set[tuple[str, int]] = set()
            topic_questions: list[tuple[str, int]] = []
            for field in topic_fields:
                values = topic[field]
                if not isinstance(values, list):
                    raise ValueError("provenance revision list required")
                parsed = [self._provenance_revision(value) for value in values]
                if len(parsed) != len(set(parsed)):
                    raise ValueError("duplicate provenance revision")
                if field == "source_revisions":
                    if not parsed:
                        raise ValueError("provenance topic requires source revisions")
                    source_revisions = set(parsed)
                elif not set(parsed) <= source_revisions:
                    raise ValueError("dependent provenance revision is outside its topic")
                if field == "question_revisions":
                    topic_questions = parsed
                revisions.extend(parsed)
            resolution = topic.get("question_resolution", "resolved" if topic_questions else None)
            if (
                (topic_questions and resolution not in {"partial", "resolved"})
                or (not topic_questions and resolution is not None)
                or any(question in answered_questions for question in topic_questions)
            ):
                raise ValueError("question resolution provenance is invalid")
            if schema_version == "actionable-provenance-v2" and topic_questions:
                question_set = set(topic_questions)
                contributor_set = {
                    self._provenance_revision(value)
                    for value in topic["contributor_revisions"]
                }
                if not contributor_set - question_set:
                    edit_row = None
                    if len(question_set) == 1 and source_revisions == question_set:
                        edit_row = self.connection.execute(
                            "SELECT change_type FROM message_versions WHERE message_id=? AND change_seq=?",
                            next(iter(question_set)),
                        ).fetchone()
                    self_resolving_edit = (
                        edit_row is not None and edit_row["change_type"] == "edit"
                    )
                    if not self_resolving_edit:
                        raise ValueError("question resolution requires a distinct answer revision")
            answered_questions.update({question: resolution for question in topic_questions})
        unanswered_questions: set[tuple[str, int]] = set()
        for unanswered_item in unanswered:
            if not isinstance(unanswered_item, Mapping) or set(unanswered_item) != {"question_revision", "context_revisions"}:
                raise ValueError("unanswered provenance schema drift")
            question = self._provenance_revision(unanswered_item["question_revision"])
            context = unanswered_item["context_revisions"]
            if not isinstance(context, list):
                raise ValueError("unanswered provenance context must be a list")
            parsed_context = [self._provenance_revision(value) for value in context]
            if len(parsed_context) != len(set(parsed_context)):
                raise ValueError("duplicate unanswered provenance revision")
            unanswered_questions.add(question)
            revisions.extend([question, *parsed_context])
        if allow_later_changes:
            active_question_rows = self.connection.execute("""
                SELECT q.message_id,q.question_change_seq
                  FROM open_questions q
                  JOIN messages m ON m.message_id=q.message_id
                  JOIN message_versions v ON v.message_id=q.message_id AND v.change_seq=q.question_change_seq
                 WHERE q.status IN ('open','partial')
                   AND q.question_change_seq <= ? AND m.chat_jid=? AND v.tombstone=0
                   AND m.tombstone=0
                   AND v.change_seq=(SELECT MAX(change_seq) FROM message_versions
                       WHERE message_id=q.message_id AND change_seq<=?)
            """, (cutoff, self.target_group_jid, cutoff))
        else:
            active_question_rows = self.connection.execute("""
                SELECT q.message_id,q.question_change_seq
                  FROM open_questions q
                  JOIN messages m ON m.message_id=q.message_id
                 WHERE q.status IN ('open','partial')
                   AND q.question_change_seq <= ?
                   AND m.chat_jid=?
                   AND m.current_change_seq=q.question_change_seq
                   AND m.tombstone=0
            """, (cutoff, self.target_group_jid))
        active_questions = {
            (str(row["message_id"]), int(row["question_change_seq"]))
            for row in active_question_rows
        }
        if not active_questions <= set(answered_questions) | unanswered_questions:
            raise DeliveryBlockedError("validated provenance omitted a carried question")
        for message_id, change_seq in set(revisions):
            if allow_later_changes:
                row = self.connection.execute(
                    "SELECT v.change_seq,v.tombstone FROM message_versions v JOIN messages m USING(message_id) "
                    "WHERE m.message_id=? AND m.chat_jid=? AND v.change_seq=? AND v.change_seq<=? "
                    "AND m.tombstone=0 "
                    "AND v.change_seq=(SELECT MAX(change_seq) FROM message_versions WHERE message_id=m.message_id AND change_seq<=?)",
                    (message_id, self.target_group_jid, change_seq, cutoff, cutoff),
                ).fetchone()
            else:
                row = self.connection.execute(
                    "SELECT m.current_change_seq AS change_seq,m.tombstone FROM messages m "
                    "JOIN message_versions v USING(message_id) WHERE m.message_id=? AND m.chat_jid=? "
                    "AND v.change_seq=? AND v.change_seq<=?",
                    (message_id, self.target_group_jid, change_seq, cutoff),
                ).fetchone()
            if row is None or int(row["change_seq"]) != change_seq or bool(row["tombstone"]):
                raise DeliveryBlockedError("provenance source was revoked, superseded, or outside the digest snapshot")
        canonical = json.dumps(provenance, sort_keys=True, separators=(",", ":"))
        return schema_version, canonical, hashlib.sha256(canonical.encode()).hexdigest()

    def validate_provenance(
        self,
        snapshot: Mapping[str, int],
        provenance: Mapping[str, Any],
        *,
        allow_later_changes: bool = False,
    ) -> None:
        """Validate revision ownership and carried-question coverage before an external side effect."""
        self._canonical_provenance(
            provenance,
            int(snapshot["cutoff_seq"]),
            allow_later_changes=allow_later_changes,
        )

    def _update_question_state(self, digest_id: str, provenance: Mapping[str, Any]) -> None:
        unanswered = {
            self._provenance_revision(item["question_revision"])
            for item in provenance["unanswered"]
        }
        answered: list[tuple[tuple[str, int], str, list[tuple[str, int]]]] = []
        for topic in provenance["topics"]:
            topic_sources = [
                self._provenance_revision(revision) for revision in topic["source_revisions"]
            ]
            status = topic.get("question_resolution", "resolved")
            answered.extend(
                (self._provenance_revision(revision), status, topic_sources)
                for revision in topic["question_revisions"]
            )
        for message_id, change_seq in unanswered:
            current_source = self.connection.execute(
                "SELECT current_change_seq,tombstone FROM messages WHERE message_id=? AND chat_jid=?",
                (message_id, self.target_group_jid),
            ).fetchone()
            if current_source is None or bool(current_source["tombstone"]):
                continue
            change_seq = int(current_source["current_change_seq"])
            existing = self.connection.execute(
                "SELECT status,opened_digest_id FROM open_questions WHERE message_id=?",
                (message_id,),
            ).fetchone()
            status = "partial" if existing is not None and existing["status"] == "partial" else "open"
            opened_digest_id = existing["opened_digest_id"] if existing is not None else digest_id
            self.connection.execute(
                "INSERT INTO open_questions(message_id,question_change_seq,status,opened_digest_id,updated_digest_id) "
                "VALUES (?,?,?,?,?) ON CONFLICT(message_id) DO UPDATE SET "
                "question_change_seq=excluded.question_change_seq,status=excluded.status,"
                "updated_digest_id=excluded.updated_digest_id",
                (message_id, change_seq, status, opened_digest_id, digest_id),
            )
        for (message_id, change_seq), status, topic_sources in answered:
            if any(
                (
                    current := self.connection.execute(
                        "SELECT current_change_seq,tombstone FROM messages WHERE message_id=? AND chat_jid=?",
                        (source_message_id, self.target_group_jid),
                    ).fetchone()
                ) is None
                or bool(current["tombstone"])
                or int(current["current_change_seq"]) != source_change_seq
                for source_message_id, source_change_seq in topic_sources
            ):
                continue
            self.connection.execute(
                "UPDATE open_questions SET status=?,updated_digest_id=? "
                "WHERE question_change_seq=? AND message_id=? AND status IN ('open','partial')",
                (status, digest_id, change_seq, message_id),
            )

    @staticmethod
    def _review_identity(
        checkpoint: int,
        cutoff: int,
        content_hash: str,
        provenance_hash: str,
        config_hash: str,
        window_kind: str,
        run_type: str,
        candidate_count: int,
        source_count: int,
        model_id: str | None,
        coverage_snapshot: str,
    ) -> str:
        identity = json.dumps({
            "schema": "reviewed-artifact-v2", "checkpoint_seq": checkpoint,
            "cutoff_seq": cutoff, "content_hash": content_hash,
            "provenance_hash": provenance_hash, "config_hash": config_hash,
            "window_kind": window_kind, "run_type": run_type,
            "candidate_count": candidate_count, "source_count": source_count,
            "model_id": model_id, "coverage_snapshot": coverage_snapshot,
        }, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(identity.encode()).hexdigest()

    def record_reviewed_artifact(
        self,
        snapshot: Mapping[str, int],
        run_type: str,
        output: str,
        *,
        candidate_count: int,
        source_count: int,
        model_id: str,
        config_hash: str,
        coverage_snapshot: str,
        provenance: Mapping[str, Any] | None,
        window_kind: str = "complete",
    ) -> str:
        """Persist revision-only evidence for a human-reviewed render without delivery state."""
        if not output or provenance is None or not config_hash:
            raise ValueError("reviewed artifacts require nonempty output, provenance, and config hash")
        if window_kind not in {"complete", "bounded-historical-backlog"}:
            raise ValueError("reviewed artifact window kind is invalid")
        try:
            coverage = json.loads(coverage_snapshot)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("reviewed artifact coverage metadata is invalid") from exc
        if not isinstance(coverage, dict):
            raise ValueError("reviewed artifact coverage metadata is invalid")
        coverage_snapshot = json.dumps(coverage, sort_keys=True, separators=(",", ":"))
        checkpoint, cutoff = int(snapshot["checkpoint_seq"]), int(snapshot["cutoff_seq"])
        schema_version, provenance_json, provenance_hash = self._canonical_provenance(
            provenance,
            cutoff,
            allow_later_changes=window_kind == "bounded-historical-backlog",
        )
        content_hash = hashlib.sha256(output.encode()).hexdigest()
        review_id = self._review_identity(
            checkpoint, cutoff, content_hash, provenance_hash, config_hash, window_kind,
            run_type, candidate_count, source_count, model_id, coverage_snapshot,
        )
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            current = self.connection.execute("SELECT checkpoint_seq, delivery_state FROM checkpoints WHERE id=1").fetchone()
            if int(current["checkpoint_seq"]) != checkpoint or current["delivery_state"] != "clear":
                raise DeliveryBlockedError("checkpoint changed while preparing reviewed artifact")
            existing = self.connection.execute(
                "SELECT content_hash,provenance_hash,config_hash,window_kind FROM reviewed_artifacts WHERE review_id=?",
                (review_id,),
            ).fetchone()
            if existing is not None:
                if tuple(existing) != (content_hash, provenance_hash, config_hash, window_kind):
                    raise DeliveryBlockedError("reviewed artifact identity collision")
            else:
                self.connection.execute(
                    "INSERT INTO reviewed_artifacts(review_id,checkpoint_seq,cutoff_seq,run_type,created_at,content_hash,candidate_count,source_count,model_id,config_hash,coverage_snapshot,schema_version,provenance_json,provenance_hash,window_kind,consumed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL)",
                    (review_id, checkpoint, cutoff, run_type, self._now(), content_hash, candidate_count, source_count, model_id, config_hash, coverage_snapshot, schema_version, provenance_json, provenance_hash, window_kind),
                )
            self.connection.execute("COMMIT")
            return review_id
        except Exception:
            self.connection.execute("ROLLBACK")
            raise

    def portable_review_facts(self, review_id: str) -> dict[str, Any]:
        """Return the private revision-only facts needed to build a portable envelope."""
        row = self.connection.execute(
            "SELECT * FROM reviewed_artifacts WHERE review_id=?", (review_id,)
        ).fetchone()
        if row is None:
            raise KeyError(review_id)
        try:
            coverage = json.loads(row["coverage_snapshot"])
        except (TypeError, json.JSONDecodeError) as exc:
            raise DeliveryBlockedError("reviewed artifact coverage metadata is invalid") from exc
        if not isinstance(coverage, dict):
            raise DeliveryBlockedError("reviewed artifact coverage metadata is invalid")
        return {
            "review_id": row["review_id"],
            "checkpoint_seq": int(row["checkpoint_seq"]),
            "cutoff_seq": int(row["cutoff_seq"]),
            "window_kind": row["window_kind"],
            "run_type": row["run_type"],
            "artifact_hash": row["content_hash"],
            "candidate_count": int(row["candidate_count"]),
            "source_count": int(row["source_count"]),
            "model_id": row["model_id"],
            "policy_hash": row["config_hash"],
            "coverage_metadata": coverage,
            "provenance_schema_version": row["schema_version"],
            "provenance": json.loads(row["provenance_json"]),
            "provenance_hash": row["provenance_hash"],
        }

    def consume_portable_review(
        self,
        envelope: Mapping[str, Any],
        output: str,
        *,
        config_hash: str,
        expected_window: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Independently validate and consume one portable review on this live spool."""
        checkpoint = int(envelope["checkpoint_seq"])
        cutoff = int(envelope["cutoff_seq"])
        window_kind = str(envelope["window_kind"])
        if window_kind not in {"complete", "bounded-historical-backlog"}:
            raise DeliveryBlockedError("portable review window kind is invalid")
        if envelope["policy_hash"] != config_hash:
            raise DeliveryBlockedError("portable review was created under a different policy")
        if envelope["artifact_hash"] != hashlib.sha256(output.encode()).hexdigest():
            raise DeliveryBlockedError("portable review artifact hash does not match")
        provenance = envelope["provenance"]
        allow_later_changes = window_kind == "bounded-historical-backlog"

        self.connection.execute("BEGIN IMMEDIATE")
        try:
            current = self.connection.execute(
                "SELECT checkpoint_seq,delivery_state FROM checkpoints WHERE id=1"
            ).fetchone()
            latest = int(self.connection.execute(
                "SELECT value FROM metadata WHERE key='next_change_seq'"
            ).fetchone()["value"]) - 1
            if int(current["checkpoint_seq"]) != checkpoint or current["delivery_state"] != "clear":
                raise DeliveryBlockedError("portable review checkpoint is no longer deliverable")
            if cutoff <= checkpoint or cutoff > latest:
                raise DeliveryBlockedError("portable review cutoff is outside the live pending window")
            if window_kind == "complete" and cutoff != latest:
                raise DeliveryBlockedError("complete portable review is stale")
            if not self._is_target_change(cutoff) or not self._snapshot_has_active_sources(
                {"checkpoint_seq": checkpoint, "cutoff_seq": cutoff}
            ):
                raise DeliveryBlockedError("portable review cutoff is not a nonempty durable prefix")
            if (
                int(envelope["source_count"]) != int(expected_window["source_count"])
                or int(envelope["candidate_count"]) != int(expected_window["candidate_count"])
                or envelope["run_type"] != expected_window["run_type"]
            ):
                raise DeliveryBlockedError(
                    "portable review counts or run type do not match the live window"
                )
            schema_version, provenance_json, provenance_hash = self._canonical_provenance(
                provenance, cutoff, allow_later_changes=allow_later_changes
            )
            if (
                envelope["provenance_schema_version"] != schema_version
                or envelope["provenance_hash"] != provenance_hash
            ):
                raise DeliveryBlockedError("portable review provenance hash does not match")
            expected_review_id = self._review_identity(
                checkpoint,
                cutoff,
                envelope["artifact_hash"],
                provenance_hash,
                config_hash,
                window_kind,
                envelope["run_type"],
                int(envelope["candidate_count"]),
                int(envelope["source_count"]),
                envelope["model_id"],
                json.dumps(
                    envelope["coverage_metadata"], sort_keys=True, separators=(",", ":")
                ),
            )
            if envelope["review_id"] != expected_review_id:
                raise DeliveryBlockedError("portable review identity does not match")
            existing_run = self.connection.execute(
                "SELECT smtp_state FROM digest_runs WHERE checkpoint_seq=? AND cutoff_seq=?",
                (checkpoint, cutoff),
            ).fetchone()
            # Only a transport failure leaves the snapshot retryable; anything else is terminal.
            if existing_run is not None and existing_run["smtp_state"] != "failed":
                raise DeliveryBlockedError(
                    "portable review snapshot was already delivered or is unresolved"
                )
            existing = self.connection.execute(
                "SELECT content_hash,provenance_hash,config_hash,window_kind,consumed_at "
                "FROM reviewed_artifacts WHERE review_id=?",
                (expected_review_id,),
            ).fetchone()
            expected = (
                envelope["artifact_hash"], provenance_hash, config_hash, window_kind
            )
            if existing is not None:
                if tuple(existing)[:4] != expected:
                    raise DeliveryBlockedError("portable review identity collision")
                if existing["consumed_at"] is not None and existing_run is None:
                    raise DeliveryBlockedError("portable review was already consumed")
                self.connection.execute(
                    "UPDATE reviewed_artifacts SET consumed_at=? WHERE review_id=?",
                    (self._now(), expected_review_id),
                )
            else:
                coverage_snapshot = json.dumps(
                    envelope["coverage_metadata"], sort_keys=True, separators=(",", ":")
                )
                self.connection.execute(
                    "INSERT INTO reviewed_artifacts(review_id,checkpoint_seq,cutoff_seq,run_type,created_at,content_hash,candidate_count,source_count,model_id,config_hash,coverage_snapshot,schema_version,provenance_json,provenance_hash,window_kind,consumed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        expected_review_id, checkpoint, cutoff, envelope["run_type"],
                        self._now(), envelope["artifact_hash"], envelope["candidate_count"],
                        envelope["source_count"], envelope["model_id"], config_hash,
                        coverage_snapshot, schema_version, provenance_json, provenance_hash,
                        window_kind, self._now(),
                    ),
                )
            self.connection.execute("COMMIT")
        except Exception:
            self.connection.execute("ROLLBACK")
            raise
        return {
            "snapshot": {"checkpoint_seq": checkpoint, "cutoff_seq": cutoff},
            "run_type": envelope["run_type"],
            "candidate_count": int(envelope["candidate_count"]),
            "source_count": int(envelope["source_count"]),
            "model_id": envelope["model_id"],
            "config_hash": config_hash,
            "coverage_snapshot": json.dumps(
                self.coverage(), sort_keys=True, separators=(",", ":")
            ),
            "provenance": provenance,
            "window_kind": window_kind,
        }

    def record_run(self, snapshot: Mapping[str, int], run_type: str, smtp_state: str, output: str | None = None, message_id: str | None = None, omission_note: str | None = None, *, omission_checkpoint_seq: int | None = None, candidate_count: int = 0, source_count: int = 0, model_id: str | None = None, config_hash: str | None = None, coverage_snapshot: str | None = None, provenance: Mapping[str, Any] | None = None, allow_later_changes: bool = False, allow_active_empty: bool = False) -> str:
        if smtp_state not in self._STATES:
            raise ValueError("invalid SMTP state")
        if allow_active_empty and smtp_state != "empty":
            raise ValueError("active empty permission applies only to an empty run")
        checkpoint, cutoff = int(snapshot["checkpoint_seq"]), int(snapshot["cutoff_seq"])
        if omission_checkpoint_seq is not None and not omission_note:
            raise ValueError("an omission checkpoint requires an omission note")
        if omission_checkpoint_seq is not None and omission_checkpoint_seq != -1 and not checkpoint <= omission_checkpoint_seq <= cutoff:
            raise ValueError("omission checkpoint must remain within its snapshot")
        digest_id = self._digest_id(snapshot)
        content_hash = hashlib.sha256((output or "").encode()).hexdigest()
        if provenance is not None and (smtp_state not in {"accepted", "unknown"} or not output):
            raise ValueError("trusted provenance requires a nonempty validated delivery candidate")
        if smtp_state == "empty" and not allow_active_empty and self._snapshot_has_active_sources(snapshot):
            raise DeliveryBlockedError(
                "an active source snapshot cannot advance as an empty digest"
            )
        if smtp_state in self._TERMINAL and omission_note:
            raise DeliveryBlockedError(
                "omitted durable changes require explicit reconciliation before checkpoint advance"
            )
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            provenance_row = self._canonical_provenance(
                provenance, cutoff, allow_later_changes=allow_later_changes
            ) if provenance is not None else None
            current = self.connection.execute("SELECT checkpoint_seq, delivery_state FROM checkpoints WHERE id=1").fetchone()
            existing = self.connection.execute("SELECT smtp_state FROM digest_runs WHERE digest_id=?", (digest_id,)).fetchone()
            if existing and existing["smtp_state"] in self._TERMINAL and existing["smtp_state"] != smtp_state:
                raise DeliveryBlockedError("completed digest is immutable")
            if existing and existing["smtp_state"] == "unknown" and smtp_state != "unknown":
                raise DeliveryBlockedError("unknown delivery must use reconcile_delivery")
            if not existing:
                self.connection.execute("INSERT INTO digest_runs(digest_id,checkpoint_seq,cutoff_seq,run_type,created_at,smtp_message_id,smtp_state,output,omission_note,omission_checkpoint_seq,candidate_count,source_count,model_id,config_hash,content_hash,coverage_snapshot,checkpoint_decision) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (digest_id, checkpoint, cutoff, run_type, self._now(), message_id, smtp_state, output, omission_note, omission_checkpoint_seq, candidate_count, source_count, model_id, config_hash, content_hash, coverage_snapshot, "advance" if smtp_state in self._TERMINAL else "retain"))
            else:
                self.connection.execute("UPDATE digest_runs SET smtp_state=?,smtp_message_id=?,output=?,omission_note=?,omission_checkpoint_seq=?,candidate_count=?,source_count=?,model_id=?,config_hash=?,content_hash=?,coverage_snapshot=?,checkpoint_decision=? WHERE digest_id=?", (smtp_state, message_id, output, omission_note, omission_checkpoint_seq, candidate_count, source_count, model_id, config_hash, content_hash, coverage_snapshot, "advance" if smtp_state in self._TERMINAL else "retain", digest_id))
            if provenance_row is not None:
                schema_version, provenance_json, provenance_hash = provenance_row
                self.connection.execute(
                    "INSERT INTO digest_provenance(digest_id,schema_version,provenance_json,provenance_hash) VALUES (?,?,?,?) ON CONFLICT(digest_id) DO UPDATE SET schema_version=excluded.schema_version,provenance_json=excluded.provenance_json,provenance_hash=excluded.provenance_hash",
                    (digest_id, schema_version, provenance_json, provenance_hash),
                )
                if smtp_state == "accepted":
                    self._update_question_state(digest_id, provenance)
            if smtp_state in self._TERMINAL:
                if int(current["checkpoint_seq"]) != checkpoint:
                    raise DeliveryBlockedError("checkpoint changed during digest finalization")
                self.connection.execute("UPDATE checkpoints SET checkpoint_seq=?,delivery_state='clear',updated_at=? WHERE id=1", (cutoff, self._now()))
            elif smtp_state == "unknown":
                self.connection.execute("UPDATE checkpoints SET delivery_state='unknown',updated_at=? WHERE id=1", (self._now(),))
            self.connection.execute("COMMIT")
            return digest_id
        except Exception:
            self.connection.execute("ROLLBACK")
            raise

    def reconcile_delivery(self, digest_id: str, outcome: str, *, message_id: str | None = None) -> None:
        if outcome not in {"accepted", "failed"}:
            raise ValueError("reconciliation outcome must be accepted or failed")
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            run = self.connection.execute("SELECT * FROM digest_runs WHERE digest_id=?", (digest_id,)).fetchone()
            current = self.connection.execute("SELECT * FROM checkpoints WHERE id=1").fetchone()
            if not run or run["smtp_state"] != "unknown" or current["delivery_state"] != "unknown":
                raise DeliveryBlockedError("only an unresolved digest can be reconciled")
            if outcome == "accepted":
                self.connection.execute("UPDATE checkpoints SET checkpoint_seq=?,delivery_state='clear',updated_at=? WHERE id=1", (run["cutoff_seq"], self._now()))
                provenance_row = self.connection.execute(
                    "SELECT provenance_json FROM digest_provenance WHERE digest_id=?",
                    (digest_id,),
                ).fetchone()
                if provenance_row is not None:
                    self._update_question_state(digest_id, json.loads(provenance_row["provenance_json"]))
            else:
                self.connection.execute("UPDATE checkpoints SET delivery_state='clear',updated_at=? WHERE id=1", (self._now(),))
            self.connection.execute("UPDATE digest_runs SET smtp_state=?,smtp_message_id=?,checkpoint_decision=? WHERE digest_id=?", (outcome, message_id, "advance" if outcome == "accepted" else "retain", digest_id))
            self.connection.execute("COMMIT")
        except Exception:
            self.connection.execute("ROLLBACK")
            raise

    def reconcile_omission(self, digest_id: str, *, confirmation: str) -> None:
        """Record an explicit operator decision to discard an omitted snapshot."""
        if confirmation != "ACKNOWLEDGE_OMITTED_DURABLE_CHANGES":
            raise DeliveryBlockedError("exact omission acknowledgement is required")
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            run = self.connection.execute("SELECT * FROM digest_runs WHERE digest_id=?", (digest_id,)).fetchone()
            current = self.connection.execute("SELECT * FROM checkpoints WHERE id=1").fetchone()
            if not run or run["smtp_state"] != "failed" or not run["omission_note"]:
                raise DeliveryBlockedError("only a failed omission run can be acknowledged")
            if int(current["checkpoint_seq"]) != int(run["checkpoint_seq"]) or current["delivery_state"] != "clear":
                raise DeliveryBlockedError("checkpoint changed; omission acknowledgement is unsafe")
            boundary = run["omission_checkpoint_seq"]
            if boundary is None:
                boundary = run["cutoff_seq"]
            if int(boundary) == -1:
                raise DeliveryBlockedError("omitted changes are not a contiguous prefix; acknowledgement would discard fresh sources")
            if not int(run["checkpoint_seq"]) <= int(boundary) <= int(run["cutoff_seq"]):
                raise DeliveryBlockedError("omission acknowledgement boundary is outside its snapshot")
            self.connection.execute(
                "UPDATE checkpoints SET checkpoint_seq=?,delivery_state='clear',updated_at=? WHERE id=1",
                (boundary, self._now()),
            )
            self.connection.execute(
                "UPDATE digest_runs SET checkpoint_decision='operator-acknowledged-omission' WHERE digest_id=?",
                (digest_id,),
            )
            self.connection.execute("COMMIT")
        except Exception:
            self.connection.execute("ROLLBACK")
            raise

    def run(self, digest_id: str) -> dict[str, Any]:
        row = self.connection.execute("SELECT * FROM digest_runs WHERE digest_id=?", (digest_id,)).fetchone()
        if row is None:
            raise KeyError(digest_id)
        return dict(row)

    def provenance(self, digest_id: str) -> dict[str, Any]:
        row = self.connection.execute(
            "SELECT schema_version,provenance_json,provenance_hash FROM digest_provenance WHERE digest_id=?",
            (digest_id,),
        ).fetchone()
        if row is None:
            raise KeyError(digest_id)
        return {
            "schema_version": row["schema_version"],
            "provenance": json.loads(row["provenance_json"]),
            "provenance_hash": row["provenance_hash"],
        }

    def question_states(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self.connection.execute(
            "SELECT message_id,question_change_seq AS change_seq,status,opened_digest_id,updated_digest_id "
            "FROM open_questions ORDER BY question_change_seq"
        )]

    def classify(self, change_seq: int, disposition: str, confidence: float | None, model_id: str, failure_state: str = "ok", *, category: str | None = None, topic_hint: str | None = None, rationale: str | None = None) -> None:
        if disposition not in {"MATERIAL", "SUPPORTING_CONTEXT", "NOISE", "UNCERTAIN"}:
            raise ValueError("invalid disposition")
        with self.connection:
            self.connection.execute("INSERT OR REPLACE INTO classifications(change_seq,disposition,confidence,category,topic_hint,rationale,model_id,failure_state) VALUES (?,?,?,?,?,?,?,?)", (change_seq, disposition, confidence, category, topic_hint, rationale, model_id, failure_state))

    def health_transition(self, state: str, reason: str, *, bridge_identity: str | None = None, guard_active: bool = True) -> int:
        if state not in {"healthy", "degraded", "down", "unknown"}:
            raise ValueError("invalid health state")
        with self.connection:
            self.connection.execute("UPDATE collector_health_intervals SET ended_at=? WHERE ended_at IS NULL", (self._now(),))
            cursor = self.connection.execute("INSERT INTO collector_health_intervals(state,started_at,reason,bridge_identity,guard_active,last_heartbeat_at) VALUES (?,?,?,?,?,?)", (state, self._now(), reason, bridge_identity, int(guard_active), self._now()))
        return int(cursor.lastrowid)

    def health(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self.connection.execute("SELECT * FROM collector_health_intervals ORDER BY id")]

    def coverage(self) -> dict[str, Any]:
        """Auditable collector coverage; observed rows are not claimed total activity."""
        intervals = self.health()
        gaps = [
            {
                "id": interval["id"],
                "state": interval["state"],
                "started_at": interval["started_at"],
                "ended_at": interval["ended_at"],
                "reason": interval["reason"],
                "guard_active": bool(interval["guard_active"]),
                "observed_events": interval["observed_events"],
            }
            for interval in intervals
            if interval["state"] != "healthy" or not bool(interval["guard_active"])
        ]
        return {
            "observed_events": sum(int(interval["observed_events"]) for interval in intervals),
            "collector_state": intervals[-1]["state"] if intervals else "unknown",
            "gaps": gaps,
        }

    def messages(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self.connection.execute("SELECT ingest_seq,message_id,chat_jid,participant,current_text AS text,current_change_seq AS change_seq,tombstone FROM messages WHERE chat_jid=? ORDER BY ingest_seq", (self.target_group_jid,))]

    def events_up_to(self, cutoff_seq: int) -> list[dict[str, Any]]:
        return [dict(row) for row in self.connection.execute("SELECT v.change_seq,m.ingest_seq,v.message_id,m.chat_jid,v.participant,v.occurred_at AS timestamp,v.text,v.tombstone FROM message_versions v JOIN messages m USING(message_id) WHERE v.change_seq<=? AND m.chat_jid=? ORDER BY v.change_seq", (cutoff_seq, self.target_group_jid))]

    def purge(self, now: datetime | None = None) -> tuple[int, int]:
        now = now or datetime.now(timezone.utc)
        raw_cutoff, digest_cutoff = (now - timedelta(days=7)).isoformat(), (now - timedelta(days=90)).isoformat()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            checkpoint_seq = int(
                self.connection.execute(
                    "SELECT checkpoint_seq FROM checkpoints WHERE id=1"
                ).fetchone()["checkpoint_seq"]
            )
            stale_versions = [row["change_seq"] for row in self.connection.execute(
                "SELECT change_seq FROM message_versions "
                "WHERE committed_at < ? AND change_seq <= ? "
                "AND change_seq NOT IN (SELECT question_change_seq FROM open_questions WHERE status IN ('open','partial'))",
                (raw_cutoff, checkpoint_seq),
            )]
            if stale_versions:
                self.connection.executemany("DELETE FROM classifications WHERE change_seq=?", [(seq,) for seq in stale_versions])
                self.connection.executemany("DELETE FROM message_versions WHERE change_seq=?", [(seq,) for seq in stale_versions])
            self.connection.execute(
                "DELETE FROM open_questions WHERE status='resolved' "
                "AND question_change_seq NOT IN (SELECT change_seq FROM message_versions)"
            )
            self.connection.execute("DELETE FROM messages WHERE message_id NOT IN (SELECT DISTINCT message_id FROM message_versions)")
            digests = self.connection.execute("DELETE FROM digest_runs WHERE created_at < ? AND smtp_state != 'unknown'", (digest_cutoff,)).rowcount
            self.connection.execute("COMMIT")
            return len(stale_versions), digests
        except Exception:
            self.connection.execute("ROLLBACK")
            raise
