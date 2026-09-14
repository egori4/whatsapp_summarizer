from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from whatsapp_tech_digest.spool import DeliveryBlockedError, DurableSpool

TARGET = "10000000-00000002@g.us"


def event(message_id: str = "a") -> dict[str, str]:
    return {
        "chat_jid": TARGET,
        "message_id": message_id,
        "participant": "15551234567:1@s.whatsapp.net",
        "timestamp": "2026-09-01T12:00:00+00:00",
        "text": "DefensePro technical guidance",
    }


def provenance(message_id: str = "a", change_seq: int = 1) -> dict[str, object]:
    revision = [message_id, change_seq]
    return {
        "schema_version": "actionable-provenance-v1",
        "topics": [{
            "source_revisions": [revision],
            "raw_keep_revisions": [revision],
            "reference_revisions": [],
            "question_revisions": [],
            "contributor_revisions": [],
        }],
        "unanswered": [],
    }


def unanswered_provenance(message_id: str = "question", change_seq: int = 1) -> dict[str, object]:
    return {
        "schema_version": "actionable-provenance-v1",
        "topics": [],
        "unanswered": [{
            "question_revision": [message_id, change_seq],
            "context_revisions": [],
        }],
    }


class Part2StateEngineTests(unittest.TestCase):
    def spool(self) -> DurableSpool:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        return DurableSpool(Path(directory.name) / "state" / "spool.sqlite3", TARGET)

    def test_append_retries_one_transient_lock_and_preserves_event(self) -> None:
        spool = self.spool()
        original = spool._append_once
        attempts = []

        def lock_once(payload):
            attempts.append(payload)
            if len(attempts) == 1:
                raise sqlite3.OperationalError("database is locked")
            return original(payload)

        with patch.object(spool, "_append_once", side_effect=lock_once):
            self.assertEqual(spool.append_message(event()), 1)

        self.assertEqual(len(attempts), 2)
        self.assertEqual([row["message_id"] for row in spool.messages()], ["a"])

    def test_append_exhausted_transient_lock_re_raises(self) -> None:
        spool = self.spool()

        with patch.object(spool, "_append_once", side_effect=sqlite3.OperationalError("database is locked")) as append_once:
            with self.assertRaises(sqlite3.OperationalError):
                spool.append_message(event())

        self.assertEqual(append_once.call_count, 2)

    def test_existing_spool_is_upgraded_non_destructively_with_provenance_table(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "state" / "spool.sqlite3"
        spool = DurableSpool(path, TARGET)
        spool.append_message(event())
        spool.connection.execute("DROP TABLE digest_provenance")
        spool.connection.close()

        reopened = DurableSpool(path, TARGET)
        self.addCleanup(reopened.connection.close)
        self.assertEqual(reopened.messages()[0]["message_id"], "a")
        self.assertIsNotNone(reopened.connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='digest_provenance'").fetchone())

    def test_existing_spool_is_upgraded_with_cross_day_question_state(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "state" / "spool.sqlite3"
        spool = DurableSpool(path, TARGET)
        spool.connection.execute("DROP TABLE open_questions")
        spool.connection.close()

        reopened = DurableSpool(path, TARGET)
        self.addCleanup(reopened.connection.close)

        self.assertIsNotNone(reopened.connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='open_questions'"
        ).fetchone())

    def test_revision_keyed_question_state_is_migrated_to_stable_message_identity(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "state" / "spool.sqlite3"
        spool = DurableSpool(path, TARGET)
        spool.append_message({**event("question"), "text": "Does release R2 support import?"})
        digest_id = spool.record_run(
            spool.snapshot(), "first-run", "accepted",
            output="Unanswered technical question.", provenance=unanswered_provenance(),
        )
        spool.connection.executescript("""
            ALTER TABLE open_questions RENAME TO stable_open_questions;
            CREATE TABLE open_questions (
              question_change_seq INTEGER PRIMARY KEY, message_id TEXT NOT NULL,
              status TEXT NOT NULL CHECK(status IN ('open','partial','resolved')),
              opened_digest_id TEXT NOT NULL, updated_digest_id TEXT NOT NULL
            );
            INSERT INTO open_questions(question_change_seq,message_id,status,opened_digest_id,updated_digest_id)
            SELECT question_change_seq,message_id,status,opened_digest_id,updated_digest_id
              FROM stable_open_questions;
            DROP TABLE stable_open_questions;
        """)
        spool.close()

        reopened = DurableSpool(path, TARGET)
        self.addCleanup(reopened.close)

        primary_key = next(
            row["name"] for row in reopened.connection.execute("PRAGMA table_info(open_questions)")
            if row["pk"]
        )
        self.assertEqual(primary_key, "message_id")
        self.assertEqual(reopened.question_states()[0]["opened_digest_id"], digest_id)
        self.assertEqual([item["message_id"] for item in reopened.carry_forward_events(reopened.snapshot())], ["question"])

    def test_target_boundary_is_checked_before_question_state_migration(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "state" / "spool.sqlite3"
        path.parent.mkdir(parents=True)
        connection = sqlite3.connect(path)
        connection.executescript("""
            CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO metadata VALUES ('target_group_jid', '20000000-00000003@g.us');
            CREATE TABLE messages (
              message_id TEXT PRIMARY KEY, chat_jid TEXT NOT NULL, ingest_seq INTEGER NOT NULL UNIQUE,
              current_change_seq INTEGER NOT NULL UNIQUE, occurred_at TEXT NOT NULL, committed_at TEXT NOT NULL,
              participant TEXT NOT NULL, display_name TEXT, current_text TEXT, tombstone INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE open_questions (
              question_change_seq INTEGER PRIMARY KEY, message_id TEXT NOT NULL,
              status TEXT NOT NULL, opened_digest_id TEXT NOT NULL, updated_digest_id TEXT NOT NULL
            );
        """)
        connection.close()

        with self.assertRaisesRegex(ValueError, "different target"):
            DurableSpool(path, TARGET)

        inspection = sqlite3.connect(path)
        self.addCleanup(inspection.close)
        primary_key = next(
            row[1] for row in inspection.execute("PRAGMA table_info(open_questions)") if row[5]
        )
        self.assertEqual(primary_key, "question_change_seq")

    def test_accepted_unanswered_question_is_carried_and_later_resolved(self) -> None:
        spool = self.spool()
        spool.append_message({**event("question"), "text": "Does the deployment tool support release R2?"})
        first_snapshot = spool.snapshot()
        first_digest = spool.record_run(
            first_snapshot,
            "first-run",
            "accepted",
            output="Unanswered technical question.",
            source_count=1,
            provenance=unanswered_provenance(),
        )

        self.assertEqual(spool.question_states(), [{
            "message_id": "question",
            "change_seq": 1,
            "status": "open",
            "opened_digest_id": first_digest,
            "updated_digest_id": first_digest,
        }])

        spool.append_message({
            **event("answer"),
            "timestamp": "2026-09-02T12:00:00+00:00",
            "text": "Release R2 is supported by the deployment tool.",
        })
        second_snapshot = spool.snapshot()
        pending, _, _ = spool.pending_events(second_snapshot)
        carried = spool.carry_forward_events(second_snapshot)

        self.assertEqual([item["message_id"] for item in pending], ["answer"])
        self.assertEqual([item["message_id"] for item in carried], ["question"])
        self.assertEqual(carried[0]["change_seq"], 1)

        resolved = provenance("answer", 2)
        resolved["topics"][0]["source_revisions"] = [["question", 1], ["answer", 2]]
        resolved["topics"][0]["raw_keep_revisions"] = [["answer", 2]]
        resolved["topics"][0]["question_revisions"] = [["question", 1]]
        second_digest = spool.record_run(
            second_snapshot,
            "normal",
            "accepted",
            output="Source-backed answer.",
            source_count=2,
            provenance=resolved,
        )

        self.assertEqual(spool.question_states(), [{
            "message_id": "question",
            "change_seq": 1,
            "status": "resolved",
            "opened_digest_id": first_digest,
            "updated_digest_id": second_digest,
        }])
        self.assertEqual(spool.carry_forward_events(spool.snapshot()), [])

    def test_accepted_candidate_must_account_for_each_carried_question(self) -> None:
        spool = self.spool()
        spool.append_message({**event("question"), "text": "Does the deployment tool support release R2?"})
        spool.record_run(
            spool.snapshot(),
            "first-run",
            "accepted",
            output="Unanswered technical question.",
            provenance=unanswered_provenance(),
        )
        spool.append_message(event("unrelated"))

        with self.assertRaisesRegex(DeliveryBlockedError, "carried question"):
            spool.record_run(
                spool.snapshot(),
                "normal",
                "accepted",
                output="Unrelated update.",
                provenance=provenance("unrelated", 2),
            )

    def test_limited_cross_day_answer_remains_partial_and_carries_forward(self) -> None:
        spool = self.spool()
        spool.append_message({**event("question"), "text": "Does the deployment tool support release R2?"})
        spool.record_run(
            spool.snapshot(), "first-run", "accepted",
            output="Unanswered technical question.", provenance=unanswered_provenance(),
        )
        spool.append_message({**event("workaround"), "text": "Release R2 works only with manual validation."})
        payload = provenance("workaround", 2)
        payload["schema_version"] = "actionable-provenance-v2"
        payload["topics"][0]["source_revisions"] = [["question", 1], ["workaround", 2]]
        payload["topics"][0]["question_revisions"] = [["question", 1]]
        payload["topics"][0]["contributor_revisions"] = [["workaround", 2]]
        payload["topics"][0]["question_resolution"] = "partial"
        spool.record_run(
            spool.snapshot(), "normal", "accepted",
            output="Limited source-backed answer.", provenance=payload,
        )

        self.assertEqual(spool.question_states()[0]["status"], "partial")
        self.assertEqual(
            [item["message_id"] for item in spool.carry_forward_events(spool.snapshot())],
            ["question"],
        )

    def test_edit_rebinds_open_state_and_cannot_be_silently_omitted(self) -> None:
        spool = self.spool()
        spool.append_message({**event("question"), "text": "Does release R2 support import?"})
        spool.record_run(
            spool.snapshot(), "first-run", "accepted",
            output="Unanswered technical question.", provenance=unanswered_provenance(),
        )
        spool.append_message({**event("question"), "text": "Does release R2 support compact import?"})
        spool.append_message(event("unrelated"))

        self.assertEqual(
            [(item["message_id"], item["change_seq"]) for item in spool.carry_forward_events(spool.snapshot())],
            [("question", 2)],
        )
        with self.assertRaisesRegex(DeliveryBlockedError, "carried question"):
            spool.record_run(
                spool.snapshot(), "normal", "accepted", output="Unrelated update.",
                provenance=provenance("unrelated", 3),
            )

    def test_revocation_closes_open_state_and_releases_raw_retention(self) -> None:
        spool = self.spool()
        spool.append_message({**event("question"), "text": "Does release R2 support import?"})
        spool.record_run(
            spool.snapshot(), "first-run", "accepted",
            output="Unanswered technical question.", provenance=unanswered_provenance(),
        )
        spool.append_message({**event("question"), "text": None, "revoked": True})
        spool.record_run(spool.snapshot(), "normal", "empty")
        old = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
        spool.connection.execute("UPDATE message_versions SET committed_at=?", (old,))

        raw, _ = spool.purge(datetime.now(timezone.utc))

        self.assertEqual(raw, 2)
        self.assertEqual(spool.question_states(), [])
        self.assertEqual(spool.events_up_to(2), [])

    def test_accepted_reconciliation_tracks_the_current_edited_revision(self) -> None:
        spool = self.spool()
        spool.append_message({**event("question"), "text": "Does release R2 support import?"})
        snapshot = spool.snapshot()
        digest_id = spool.record_run(
            snapshot, "first-run", "unknown", output="Unanswered technical question.",
            provenance=unanswered_provenance(),
        )
        spool.append_message({**event("question"), "text": "Does release R2 support compact import?"})

        spool.reconcile_delivery(digest_id, "accepted")

        self.assertEqual(spool.question_states()[0]["change_seq"], 2)
        self.assertEqual(
            [(item["message_id"], item["change_seq"]) for item in spool.carry_forward_events(spool.snapshot())],
            [("question", 2)],
        )

    def test_accepted_reconciliation_does_not_reopen_a_revoked_question(self) -> None:
        spool = self.spool()
        spool.append_message({**event("question"), "text": "Does release R2 support import?"})
        digest_id = spool.record_run(
            spool.snapshot(), "first-run", "unknown", output="Unanswered technical question.",
            provenance=unanswered_provenance(),
        )
        spool.append_message({**event("question"), "text": None, "revoked": True})

        spool.reconcile_delivery(digest_id, "accepted")

        self.assertEqual(spool.question_states(), [])

    def test_accepted_reconciliation_does_not_resolve_from_revoked_answer_evidence(self) -> None:
        spool = self.spool()
        spool.append_message({**event("question"), "text": "Does release R2 support import?"})
        spool.record_run(
            spool.snapshot(), "first-run", "accepted",
            output="Unanswered technical question.", provenance=unanswered_provenance(),
        )
        spool.append_message({**event("answer"), "text": "Release R2 supports import."})
        payload = provenance("answer", 2)
        payload["schema_version"] = "actionable-provenance-v2"
        payload["topics"][0]["source_revisions"] = [["question", 1], ["answer", 2]]
        payload["topics"][0]["question_revisions"] = [["question", 1]]
        payload["topics"][0]["contributor_revisions"] = [["answer", 2]]
        payload["topics"][0]["question_resolution"] = "resolved"
        digest_id = spool.record_run(
            spool.snapshot(), "normal", "unknown",
            output="Source-backed answer.", provenance=payload,
        )
        spool.append_message({**event("answer"), "text": None, "revoked": True})

        spool.reconcile_delivery(digest_id, "accepted")

        self.assertEqual(spool.question_states()[0]["status"], "open")

    def test_v2_provenance_rejects_self_resolution_except_for_an_edited_update(self) -> None:
        spool = self.spool()
        spool.append_message({**event("question"), "text": "Does release R2 support import?"})
        self_resolution = provenance("question", 1)
        self_resolution["schema_version"] = "actionable-provenance-v2"
        self_resolution["topics"][0]["question_revisions"] = [["question", 1]]
        self_resolution["topics"][0]["question_resolution"] = "resolved"

        with self.assertRaisesRegex(ValueError, "distinct answer revision"):
            spool.validate_provenance(spool.snapshot(), self_resolution)

        spool.record_run(
            spool.snapshot(), "first-run", "accepted",
            output="Unanswered technical question.", provenance=unanswered_provenance(),
        )
        spool.append_message({**event("question"), "text": "Release R2 supports import."})
        edited_update = provenance("question", 2)
        edited_update["schema_version"] = "actionable-provenance-v2"
        edited_update["topics"][0]["question_revisions"] = [["question", 2]]
        edited_update["topics"][0]["question_resolution"] = "resolved"

        spool.record_run(
            spool.snapshot(), "normal", "accepted",
            output="Edited source supplies the answer.", provenance=edited_update,
        )
        self.assertEqual(spool.question_states()[0]["status"], "resolved")

    def test_validated_digest_persists_hashed_revision_only_provenance(self) -> None:
        spool = self.spool()
        spool.append_message(event())
        snapshot = spool.snapshot()
        payload = provenance()

        digest_id = spool.record_run(
            snapshot, "first-run", "accepted", output="Reader-safe digest.",
            source_count=1, provenance=payload,
        )

        stored = spool.provenance(digest_id)
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        self.assertEqual(stored["provenance"], payload)
        self.assertEqual(stored["provenance_hash"], hashlib.sha256(canonical.encode()).hexdigest())
        self.assertNotIn(event()["text"], canonical)
        self.assertEqual(spool.snapshot()["checkpoint_seq"], 1)

    def test_provenance_refuses_superseded_source_revision(self) -> None:
        spool = self.spool()
        spool.append_message(event())
        snapshot = spool.snapshot()
        spool.append_message({**event(), "text": "Corrected technical guidance"})

        with self.assertRaises(DeliveryBlockedError):
            spool.record_run(snapshot, "first-run", "accepted", output="Reader-safe digest.", provenance=provenance())

        self.assertEqual(spool.connection.execute("SELECT COUNT(*) FROM digest_runs").fetchone()[0], 0)

    def test_failed_digest_cannot_persist_trusted_provenance(self) -> None:
        spool = self.spool()
        spool.append_message(event())

        with self.assertRaises(ValueError):
            spool.record_run(spool.snapshot(), "first-run", "failed", provenance=provenance())

        self.assertEqual(spool.connection.execute("SELECT COUNT(*) FROM digest_runs").fetchone()[0], 0)
        self.assertEqual(spool.connection.execute("SELECT COUNT(*) FROM digest_provenance").fetchone()[0], 0)

    def test_nonempty_snapshot_cannot_advance_as_empty(self) -> None:
        spool = self.spool()
        spool.append_message(event())
        snapshot = spool.snapshot()

        with self.assertRaises(DeliveryBlockedError):
            spool.record_run(snapshot, "first-run", "empty", source_count=1)

        self.assertEqual(spool.snapshot()["checkpoint_seq"], 0)
        self.assertEqual(spool.connection.execute("SELECT COUNT(*) FROM digest_runs").fetchone()[0], 0)

    def test_purge_retains_uncheckpointed_raw_versions(self) -> None:
        spool = self.spool()
        spool.append_message(event())
        old = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
        spool.connection.execute("UPDATE message_versions SET committed_at=? WHERE change_seq=1", (old,))

        raw, _ = spool.purge(datetime.now(timezone.utc))

        self.assertEqual(raw, 0)
        self.assertEqual([row["message_id"] for row in spool.events_up_to(1)], ["a"])
        self.assertEqual(spool.snapshot()["checkpoint_seq"], 0)

    def test_purge_rolls_back_all_retention_changes_on_failure(self) -> None:
        spool = self.spool()
        spool.append_message({**event("question"), "text": "Does release R2 support import?"})
        spool.record_run(
            spool.snapshot(), "first-run", "accepted",
            output="Unanswered technical question.", provenance=unanswered_provenance(),
        )
        spool.append_message({**event("answer"), "text": "Release R2 supports import."})
        payload = provenance("answer", 2)
        payload["schema_version"] = "actionable-provenance-v2"
        payload["topics"][0]["source_revisions"] = [["question", 1], ["answer", 2]]
        payload["topics"][0]["question_revisions"] = [["question", 1]]
        payload["topics"][0]["contributor_revisions"] = [["answer", 2]]
        payload["topics"][0]["question_resolution"] = "resolved"
        spool.record_run(
            spool.snapshot(), "normal", "accepted",
            output="Source-backed answer.", provenance=payload,
        )
        old = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
        spool.connection.execute("UPDATE message_versions SET committed_at=?", (old,))
        spool.connection.executescript("""
            CREATE TRIGGER fail_resolved_cleanup BEFORE DELETE ON open_questions
            BEGIN SELECT RAISE(ABORT, 'synthetic cleanup failure'); END;
        """)

        with self.assertRaisesRegex(sqlite3.IntegrityError, "synthetic cleanup failure"):
            spool.purge(datetime.now(timezone.utc))

        self.assertEqual(
            spool.connection.execute("SELECT COUNT(*) FROM message_versions").fetchone()[0],
            2,
        )

    def test_omitted_durable_changes_cannot_advance_checkpoint(self) -> None:
        spool = self.spool()
        spool.append_message(event())
        snapshot = spool.snapshot()
        old = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        spool.connection.execute("UPDATE message_versions SET committed_at=? WHERE change_seq=1", (old,))
        events, run_type, omission = spool.pending_events(snapshot, now=datetime.now(timezone.utc))

        self.assertEqual(events, [])
        self.assertEqual(run_type, "first-run")
        self.assertIsNotNone(omission)
        with self.assertRaises(DeliveryBlockedError):
            spool.record_run(snapshot, run_type, "accepted", source_count=0, omission_note=omission)

        self.assertEqual(spool.snapshot()["checkpoint_seq"], 0)

    def test_operator_acknowledgement_is_required_to_release_an_omission_block(self) -> None:
        spool = self.spool()
        spool.append_message(event())
        snapshot = spool.snapshot()
        old = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        spool.connection.execute("UPDATE message_versions SET committed_at=? WHERE change_seq=1", (old,))
        _events, run_type, omission = spool.pending_events(snapshot, now=datetime.now(timezone.utc))
        digest_id = spool.record_run(snapshot, run_type, "failed", omission_note=omission)

        with self.assertRaises(DeliveryBlockedError):
            spool.reconcile_omission(digest_id, confirmation="wrong")
        spool.reconcile_omission(digest_id, confirmation="ACKNOWLEDGE_OMITTED_DURABLE_CHANGES")

        self.assertEqual(spool.snapshot()["checkpoint_seq"], 1)
        self.assertEqual(spool.run(digest_id)["checkpoint_decision"], "operator-acknowledged-omission")

    def test_omission_acknowledgement_preserves_a_fresh_suffix_for_digesting(self) -> None:
        spool = self.spool()
        spool.append_message(event("old"))
        spool.append_message(event("fresh"))
        now = datetime.now(timezone.utc)
        old = (now - timedelta(hours=25)).isoformat()
        spool.connection.execute("UPDATE message_versions SET committed_at=? WHERE change_seq=1", (old,))
        snapshot = spool.snapshot()
        events, run_type, omission = spool.pending_events(snapshot, now=now)
        self.assertEqual([item["message_id"] for item in events], ["fresh"])
        self.assertEqual(run_type, "first-run")
        self.assertIsNotNone(omission)
        self.assertEqual(spool.omission_prefix_checkpoint(snapshot, now=now), 1)
        digest_id = spool.record_run(
            snapshot, run_type, "failed", omission_note=omission,
            omission_checkpoint_seq=1,
        )

        spool.reconcile_omission(digest_id, confirmation="ACKNOWLEDGE_OMITTED_DURABLE_CHANGES")
        remaining, remaining_run_type, remaining_omission = spool.pending_events(spool.snapshot(), now=now)

        self.assertEqual(spool.snapshot()["checkpoint_seq"], 1)
        self.assertEqual([item["message_id"] for item in remaining], ["fresh"])
        self.assertEqual(remaining_run_type, "normal")
        self.assertIsNone(remaining_omission)

    def test_revocation_only_snapshot_can_advance_without_exposing_revoked_content(self) -> None:
        spool = self.spool()
        spool.append_message(event("sensitive"))
        spool.append_message({**event("sensitive"), "text": None, "revoked": True})
        snapshot = spool.snapshot()

        spool.record_run(snapshot, "first-run", "empty", source_count=0)

        self.assertEqual(spool.snapshot()["checkpoint_seq"], 2)

    def test_revoked_before_checkpoint_never_enters_pending_model_input(self) -> None:
        spool = self.spool()
        spool.append_message(event("sensitive"))
        spool.append_message({**event("sensitive"), "text": None, "revoked": True})

        events, run_type, omission = spool.pending_events(spool.snapshot())

        self.assertEqual(events, [])
        self.assertEqual(run_type, "first-run")
        self.assertIsNone(omission)

    def test_coverage_reports_observed_events_and_collector_gaps(self) -> None:
        spool = self.spool()
        spool.health_transition("healthy", "bridge connected", bridge_identity="bridge-a")
        spool.append_message(event())
        spool.health_transition("down", "bridge disconnected", bridge_identity="bridge-a", guard_active=False)

        coverage = spool.coverage()

        self.assertEqual(coverage["observed_events"], 1)
        self.assertEqual(coverage["collector_state"], "down")
        self.assertEqual(
            [(gap["state"], gap["reason"], gap["guard_active"]) for gap in coverage["gaps"]],
            [("down", "bridge disconnected", False)],
        )

    def test_first_ingest_without_lifecycle_is_visible_as_unknown_coverage_gap(self) -> None:
        spool = self.spool()
        spool.append_message(event())

        coverage = spool.coverage()

        self.assertEqual(coverage["observed_events"], 1)
        self.assertEqual(coverage["collector_state"], "unknown")
        self.assertEqual(
            [(gap["state"], gap["reason"], gap["guard_active"], gap["observed_events"]) for gap in coverage["gaps"]],
            [("unknown", "target event observed before collector lifecycle was established", False, 1)],
        )

    def test_runner_persists_structured_coverage_not_raw_health_intervals(self) -> None:
        import inspect
        from whatsapp_tech_digest import run

        source = inspect.getsource(run.generate)
        self.assertIn("spool.coverage()", source)
        self.assertNotIn("spool.health()", source)


if __name__ == "__main__":
    unittest.main()
