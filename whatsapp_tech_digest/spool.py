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
        self._initialize()

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "DurableSpool":
        return self

    def __exit__(self, _exc_type: object, _exc: object, _traceback: object) -> None:
        self.close()

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
              output TEXT, omission_note TEXT, candidate_count INTEGER NOT NULL DEFAULT 0,
              source_count INTEGER NOT NULL DEFAULT 0, model_id TEXT, config_hash TEXT, content_hash TEXT,
              coverage_snapshot TEXT, checkpoint_decision TEXT NOT NULL DEFAULT 'retain',
              UNIQUE(checkpoint_seq, cutoff_seq)
            );
            CREATE TABLE IF NOT EXISTS digest_provenance (
              digest_id TEXT PRIMARY KEY REFERENCES digest_runs(digest_id) ON DELETE CASCADE,
              schema_version TEXT NOT NULL, provenance_json TEXT NOT NULL, provenance_hash TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS reviewed_artifacts (
              review_id TEXT PRIMARY KEY, checkpoint_seq INTEGER NOT NULL, cutoff_seq INTEGER NOT NULL,
              run_type TEXT NOT NULL, created_at TEXT NOT NULL, content_hash TEXT NOT NULL,
              candidate_count INTEGER NOT NULL, source_count INTEGER NOT NULL, model_id TEXT,
              config_hash TEXT NOT NULL, coverage_snapshot TEXT, schema_version TEXT NOT NULL,
              provenance_json TEXT NOT NULL, provenance_hash TEXT NOT NULL
            );
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

    def snapshot(self) -> dict[str, int]:
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            checkpoint = self.connection.execute("SELECT checkpoint_seq, delivery_state FROM checkpoints WHERE id=1").fetchone()
            if checkpoint["delivery_state"] == "unknown":
                raise DeliveryBlockedError("unresolved SMTP acceptance blocks a new digest snapshot")
            cutoff = int(self.connection.execute("SELECT value FROM metadata WHERE key='next_change_seq'").fetchone()["value"]) - 1
            self.connection.execute("COMMIT")
            return {"checkpoint_seq": int(checkpoint["checkpoint_seq"]), "cutoff_seq": cutoff}
        except Exception:
            self.connection.execute("ROLLBACK")
            raise

    def pending_events(self, snapshot: Mapping[str, int], *, now: datetime | None = None) -> tuple[list[dict[str, Any]], str, str | None]:
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
        horizon_hours = 24 if checkpoint == 0 else 72
        horizon = now - timedelta(hours=horizon_hours)
        within = [row for row in rows if datetime.fromisoformat(row["committed_at"]) >= horizon]
        omitted = len(rows) - len(within)
        run_type = "first-run" if checkpoint == 0 else ("catch-up" if omitted else "normal")
        note = f"{omitted} durable changes older than {horizon_hours}h omitted" if omitted else None
        return within, run_type, note

    def require_current(self, events: Sequence[Mapping[str, Any]]) -> None:
        """Fail closed if a source changed after it was selected for processing."""
        if not events:
            return
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            for event in events:
                row = self.connection.execute(
                    "SELECT current_change_seq,tombstone FROM messages WHERE message_id=? AND chat_jid=?",
                    (str(event["message_id"]), self.target_group_jid),
                ).fetchone()
                if row is None or int(row["current_change_seq"]) != int(event["change_seq"]) or bool(row["tombstone"]):
                    raise DeliveryBlockedError("a selected source was revoked or superseded; retry from a new snapshot")
            self.connection.execute("COMMIT")
        except Exception:
            self.connection.execute("ROLLBACK")
            raise

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

    def _canonical_provenance(self, provenance: Mapping[str, Any], cutoff: int) -> tuple[str, str, str]:
        if not isinstance(provenance, Mapping) or set(provenance) != {"schema_version", "topics", "unanswered"}:
            raise ValueError("provenance schema drift")
        schema_version = provenance.get("schema_version")
        topics, unanswered = provenance.get("topics"), provenance.get("unanswered")
        if schema_version != "actionable-provenance-v1" or not isinstance(topics, list) or not topics or not isinstance(unanswered, list):
            raise ValueError("invalid actionable provenance envelope")
        topic_fields = ("source_revisions", "raw_keep_revisions", "reference_revisions", "question_revisions", "contributor_revisions")
        revisions: list[tuple[str, int]] = []
        for topic in topics:
            if not isinstance(topic, Mapping) or set(topic) != set(topic_fields):
                raise ValueError("provenance topic schema drift")
            source_revisions: set[tuple[str, int]] = set()
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
                revisions.extend(parsed)
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
            revisions.extend([question, *parsed_context])
        for message_id, change_seq in set(revisions):
            row = self.connection.execute(
                "SELECT m.current_change_seq,m.tombstone FROM messages m JOIN message_versions v USING(message_id) WHERE m.message_id=? AND m.chat_jid=? AND v.change_seq=? AND v.change_seq<=?",
                (message_id, self.target_group_jid, change_seq, cutoff),
            ).fetchone()
            if row is None or int(row["current_change_seq"]) != change_seq or bool(row["tombstone"]):
                raise DeliveryBlockedError("provenance source was revoked, superseded, or outside the digest snapshot")
        canonical = json.dumps(provenance, sort_keys=True, separators=(",", ":"))
        return schema_version, canonical, hashlib.sha256(canonical.encode()).hexdigest()

    def record_reviewed_artifact(self, snapshot: Mapping[str, int], run_type: str, output: str, *, candidate_count: int, source_count: int, model_id: str, config_hash: str, coverage_snapshot: str, provenance: Mapping[str, Any] | None) -> str:
        """Persist revision-only evidence for a human-reviewed render without delivery state."""
        if not output or provenance is None or not config_hash:
            raise ValueError("reviewed artifacts require nonempty output, provenance, and config hash")
        checkpoint, cutoff = int(snapshot["checkpoint_seq"]), int(snapshot["cutoff_seq"])
        schema_version, provenance_json, provenance_hash = self._canonical_provenance(provenance, cutoff)
        content_hash = hashlib.sha256(output.encode()).hexdigest()
        identity = json.dumps({
            "schema": "reviewed-artifact-v1", "checkpoint_seq": checkpoint, "cutoff_seq": cutoff,
            "content_hash": content_hash, "provenance_hash": provenance_hash, "config_hash": config_hash,
        }, sort_keys=True, separators=(",", ":"))
        review_id = hashlib.sha256(identity.encode()).hexdigest()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            current = self.connection.execute("SELECT checkpoint_seq, delivery_state FROM checkpoints WHERE id=1").fetchone()
            if int(current["checkpoint_seq"]) != checkpoint or current["delivery_state"] != "clear":
                raise DeliveryBlockedError("checkpoint changed while preparing reviewed artifact")
            existing = self.connection.execute("SELECT content_hash, provenance_hash, config_hash FROM reviewed_artifacts WHERE review_id=?", (review_id,)).fetchone()
            if existing is not None:
                if tuple(existing) != (content_hash, provenance_hash, config_hash):
                    raise DeliveryBlockedError("reviewed artifact identity collision")
            else:
                self.connection.execute(
                    "INSERT INTO reviewed_artifacts(review_id,checkpoint_seq,cutoff_seq,run_type,created_at,content_hash,candidate_count,source_count,model_id,config_hash,coverage_snapshot,schema_version,provenance_json,provenance_hash) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (review_id, checkpoint, cutoff, run_type, self._now(), content_hash, candidate_count, source_count, model_id, config_hash, coverage_snapshot, schema_version, provenance_json, provenance_hash),
                )
            self.connection.execute("COMMIT")
            return review_id
        except Exception:
            self.connection.execute("ROLLBACK")
            raise

    def reviewed_artifact(self, review_id: str, output: str, *, config_hash: str) -> dict[str, Any]:
        """Validate an exact reviewed body and return its revision-only delivery envelope."""
        if not isinstance(review_id, str) or not review_id or not output or not config_hash:
            raise DeliveryBlockedError("reviewed artifact identity, body, and config hash are required")
        row = self.connection.execute("SELECT * FROM reviewed_artifacts WHERE review_id=?", (review_id,)).fetchone()
        if row is None:
            raise DeliveryBlockedError("reviewed artifact is unavailable")
        if row["config_hash"] != config_hash:
            raise DeliveryBlockedError("reviewed artifact was created under a different policy")
        if row["content_hash"] != hashlib.sha256(output.encode()).hexdigest():
            raise DeliveryBlockedError("reviewed artifact body hash does not match")
        provenance = json.loads(row["provenance_json"])
        schema_version, canonical, provenance_hash = self._canonical_provenance(provenance, int(row["cutoff_seq"]))
        if schema_version != row["schema_version"] or canonical != row["provenance_json"] or provenance_hash != row["provenance_hash"]:
            raise DeliveryBlockedError("reviewed artifact provenance is invalid")
        current = self.connection.execute("SELECT checkpoint_seq, delivery_state FROM checkpoints WHERE id=1").fetchone()
        if int(current["checkpoint_seq"]) != int(row["checkpoint_seq"]) or current["delivery_state"] != "clear":
            raise DeliveryBlockedError("reviewed artifact checkpoint is no longer deliverable")
        return {
            "snapshot": {"checkpoint_seq": int(row["checkpoint_seq"]), "cutoff_seq": int(row["cutoff_seq"])},
            "run_type": row["run_type"], "candidate_count": int(row["candidate_count"]),
            "source_count": int(row["source_count"]), "model_id": row["model_id"],
            "config_hash": row["config_hash"], "coverage_snapshot": row["coverage_snapshot"],
            "provenance": provenance,
        }

    def record_run(self, snapshot: Mapping[str, int], run_type: str, smtp_state: str, output: str | None = None, message_id: str | None = None, omission_note: str | None = None, *, candidate_count: int = 0, source_count: int = 0, model_id: str | None = None, config_hash: str | None = None, coverage_snapshot: str | None = None, provenance: Mapping[str, Any] | None = None) -> str:
        if smtp_state not in self._STATES:
            raise ValueError("invalid SMTP state")
        checkpoint, cutoff = int(snapshot["checkpoint_seq"]), int(snapshot["cutoff_seq"])
        digest_id = self._digest_id(snapshot)
        content_hash = hashlib.sha256((output or "").encode()).hexdigest()
        if provenance is not None and (smtp_state not in {"accepted", "unknown"} or not output):
            raise ValueError("trusted provenance requires a nonempty validated delivery candidate")
        if smtp_state == "empty" and self._snapshot_has_active_sources(snapshot):
            raise DeliveryBlockedError(
                "an active source snapshot cannot advance as an empty digest"
            )
        if smtp_state in self._TERMINAL and omission_note:
            raise DeliveryBlockedError(
                "omitted durable changes require explicit reconciliation before checkpoint advance"
            )
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            provenance_row = self._canonical_provenance(provenance, cutoff) if provenance is not None else None
            current = self.connection.execute("SELECT checkpoint_seq, delivery_state FROM checkpoints WHERE id=1").fetchone()
            existing = self.connection.execute("SELECT smtp_state FROM digest_runs WHERE digest_id=?", (digest_id,)).fetchone()
            if existing and existing["smtp_state"] in self._TERMINAL and existing["smtp_state"] != smtp_state:
                raise DeliveryBlockedError("completed digest is immutable")
            if existing and existing["smtp_state"] == "unknown" and smtp_state != "unknown":
                raise DeliveryBlockedError("unknown delivery must use reconcile_delivery")
            if not existing:
                self.connection.execute("INSERT INTO digest_runs(digest_id,checkpoint_seq,cutoff_seq,run_type,created_at,smtp_message_id,smtp_state,output,omission_note,candidate_count,source_count,model_id,config_hash,content_hash,coverage_snapshot,checkpoint_decision) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (digest_id, checkpoint, cutoff, run_type, self._now(), message_id, smtp_state, output, omission_note, candidate_count, source_count, model_id, config_hash, content_hash, coverage_snapshot, "advance" if smtp_state in self._TERMINAL else "retain"))
            else:
                self.connection.execute("UPDATE digest_runs SET smtp_state=?,smtp_message_id=?,output=?,omission_note=?,candidate_count=?,source_count=?,model_id=?,config_hash=?,content_hash=?,coverage_snapshot=?,checkpoint_decision=? WHERE digest_id=?", (smtp_state, message_id, output, omission_note, candidate_count, source_count, model_id, config_hash, content_hash, coverage_snapshot, "advance" if smtp_state in self._TERMINAL else "retain", digest_id))
            if provenance_row is not None:
                schema_version, provenance_json, provenance_hash = provenance_row
                self.connection.execute(
                    "INSERT INTO digest_provenance(digest_id,schema_version,provenance_json,provenance_hash) VALUES (?,?,?,?) ON CONFLICT(digest_id) DO UPDATE SET schema_version=excluded.schema_version,provenance_json=excluded.provenance_json,provenance_hash=excluded.provenance_hash",
                    (digest_id, schema_version, provenance_json, provenance_hash),
                )
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
            self.connection.execute(
                "UPDATE checkpoints SET checkpoint_seq=?,delivery_state='clear',updated_at=? WHERE id=1",
                (run["cutoff_seq"], self._now()),
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
        with self.connection:
            checkpoint_seq = int(
                self.connection.execute(
                    "SELECT checkpoint_seq FROM checkpoints WHERE id=1"
                ).fetchone()["checkpoint_seq"]
            )
            stale_versions = [row["change_seq"] for row in self.connection.execute(
                "SELECT change_seq FROM message_versions "
                "WHERE committed_at < ? AND change_seq <= ?",
                (raw_cutoff, checkpoint_seq),
            )]
            if stale_versions:
                self.connection.executemany("DELETE FROM classifications WHERE change_seq=?", [(seq,) for seq in stale_versions])
                self.connection.executemany("DELETE FROM message_versions WHERE change_seq=?", [(seq,) for seq in stale_versions])
            self.connection.execute("DELETE FROM messages WHERE message_id NOT IN (SELECT DISTINCT message_id FROM message_versions)")
            digests = self.connection.execute("DELETE FROM digest_runs WHERE created_at < ? AND smtp_state != 'unknown'", (digest_cutoff,)).rowcount
        return len(stale_versions), digests