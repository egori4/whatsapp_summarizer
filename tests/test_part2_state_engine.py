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
