from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from whatsapp_tech_digest import run
from whatsapp_tech_digest.models import DigestResult
from whatsapp_tech_digest.spool import DeliveryBlockedError, DurableSpool


TARGET = "123456789@g.us"


def _policy(path: Path, spool_path: Path) -> Path:
    data = json.loads(
        (Path(__file__).parents[1] / "config" / "digest.policy.example.json").read_text(
            encoding="utf-8"
        )
    )
    data["example_only"] = False
    data["target_group_jid"] = TARGET
    data["whatsapp"].update({"group_allow_from": [TARGET], "bridge_port": 17778})
    data["paths"] = {
        "spool": str(spool_path),
        "state_dir": str(spool_path.parent),
        "mode": "0700",
    }
    data["models"].update(
        {
            "provider": "hermes-openai-codex",
            "pipeline_mode": "one_pass",
            "final": "gpt-5.6-terra",
            "fallback": "gpt-5.6-terra",
            "endpoint": "local://hermes-cli",
        }
    )
    data["smtp"].update({"host": "smtp.invalid.test", "sender": "digest@invalid.test"})
    path.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
    path.chmod(0o600)
    return path


def _event(message_id: str, text: str, minute: int) -> dict[str, object]:
    return {
        "chat_jid": TARGET,
        "message_id": message_id,
        "participant": "15551234567@s.whatsapp.net",
        "timestamp": f"2026-09-19T12:{minute:02d}:00+00:00",
        "text": text,
    }


def _provenance(*revisions: tuple[str, int]) -> dict[str, object]:
    rows = [[message_id, change_seq] for message_id, change_seq in revisions]
    return {
        "schema_version": "actionable-provenance-v2",
        "topics": [
            {
                "source_revisions": rows,
                "raw_keep_revisions": rows,
                "reference_revisions": [],
                "question_revisions": [],
                "contributor_revisions": [],
                "question_resolution": None,
            }
        ],
        "unanswered": [],
    }


class Phase3ReviewFindingTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.root.chmod(0o700)
        self.live_path = self.root / "live.sqlite3"
        self.copy_path = self.root / "copy.sqlite3"
        self.policy_path = _policy(self.root / "policy.json", self.live_path)
        self.live = DurableSpool(self.live_path, TARGET)
        self.addCleanup(self.live.close)
        for message_id, text, minute in (
            ("first", "Apply release R1 using the documented procedure.", 1),
            ("second", "Keep the rollback command available for release R1.", 2),
        ):
            self.live.append_message(_event(message_id, text, minute))

    def _render(
        self, *, bounded_cutoff: int | None = None, suffix: str = ""
    ) -> tuple[Path, Path, str]:
        copied = DurableSpool(self.copy_path, TARGET)
        self.addCleanup(copied.close)
        self.live.connection.backup(copied.connection)
        artifact = self.root / f"reviewed{suffix}.md"
        envelope = self.root / f"review{suffix}.envelope.json"
        result = DigestResult(
            "Technical Updates\n\nApply release R1 using the documented procedure.",
            ["first", "second"],
            "gpt-5.6-terra",
            False,
            _provenance(("first", 1), ("second", 2)),
        )
        with patch("whatsapp_tech_digest.run.build_model", return_value=object()), patch(
            "whatsapp_tech_digest.run.summarize_actionable", return_value=result
        ):
            output = run.generate(
                self.policy_path,
                self.copy_path,
                execution_mode="render-only",
                review_manifest=envelope,
                review_artifact=artifact,
                bounded_cutoff=bounded_cutoff,
            )
        copied.close()
        self.copy_path.unlink(missing_ok=True)
        for tail in ("-wal", "-shm"):
            Path(str(self.copy_path) + tail).unlink(missing_ok=True)
        return artifact, envelope, output

    def _forge(self, envelope: Path, **overrides: object) -> None:
        """Rewrite envelope facts and recompute both unkeyed digests the way the code does."""
        data = json.loads(envelope.read_text(encoding="utf-8"))
        data.update(overrides)
        identity = json.dumps(
            {
                "schema": "reviewed-artifact-v2",
                "checkpoint_seq": data["checkpoint_seq"],
                "cutoff_seq": data["cutoff_seq"],
                "content_hash": data["artifact_hash"],
                "provenance_hash": data["provenance_hash"],
                "config_hash": data["policy_hash"],
                "window_kind": data["window_kind"],
                "run_type": data["run_type"],
                "candidate_count": data["candidate_count"],
                "source_count": data["source_count"],
                "model_id": data["model_id"],
                "coverage_snapshot": json.dumps(
                    data["coverage_metadata"], sort_keys=True, separators=(",", ":")
                ),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        data["review_id"] = hashlib.sha256(identity.encode()).hexdigest()
        data["envelope_hash"] = run._portable_envelope_hash(data)
        envelope.write_text(run._canonical_json(data) + "\n", encoding="utf-8")
        envelope.chmod(0o600)

    def test_bounded_window_stays_deliverable_when_a_later_correction_exists(self) -> None:
        """A later edit is the defining condition of a backlog; it must stay pending, not block."""
        self.live.append_message(_event("first", "Release R1 guidance was corrected.", 5))
        self.live.append_message(_event("later", "Release R2 changes the deployment advice.", 6))
        artifact, envelope, output = self._render(bounded_cutoff=2)
        self.assertIn("Bounded historical backlog window", output)
        sender = Mock(return_value="accepted")
        with patch("whatsapp_tech_digest.run.send", sender):
            self.assertEqual(
                run.deliver_reviewed(self.policy_path, self.live_path, artifact, envelope),
                "accepted",
            )
        self.assertEqual(sender.call_count, 1)
        self.assertEqual(self.live.snapshot()["checkpoint_seq"], 2)
        pending = self.live.pending_events(self.live.snapshot())[0]
        self.assertEqual(
            sorted((event["message_id"], event["change_seq"]) for event in pending),
            [("first", 3), ("later", 4)],
        )

    def test_bounded_window_still_rejects_a_prefix_source_revoked_after_the_render(self) -> None:
        artifact, envelope, _output = self._render(bounded_cutoff=2)
        self.live.append_message({**_event("first", "", 5), "text": None, "revoked": True})
        sender = Mock(return_value="accepted")
        with patch("whatsapp_tech_digest.run.send", sender), self.assertRaises(
            DeliveryBlockedError
        ):
            run.deliver_reviewed(self.policy_path, self.live_path, artifact, envelope)
        sender.assert_not_called()
        self.assertEqual(self.live.snapshot()["checkpoint_seq"], 0)

    def test_bounded_render_rejects_a_revoked_prefix_source_before_the_model_call(self) -> None:
        self.live.append_message({**_event("first", "", 5), "text": None, "revoked": True})
        copied = DurableSpool(self.copy_path, TARGET)
        self.addCleanup(copied.close)
        self.live.connection.backup(copied.connection)
        model = Mock()
        with patch("whatsapp_tech_digest.run.build_model", model), self.assertRaises(
            DeliveryBlockedError
        ):
            run.generate(
                self.policy_path,
                self.copy_path,
                execution_mode="render-only",
                review_manifest=self.root / "revoked.envelope.json",
                review_artifact=self.root / "revoked.md",
                bounded_cutoff=2,
            )
        model.assert_not_called()

    def test_forged_counts_and_run_type_are_rejected_against_live_sources(self) -> None:
        for overrides in (
            {"candidate_count": 99, "source_count": 99},
            {"candidate_count": 1},
            {"source_count": 5},
            {"run_type": "catch-up"},
        ):
            with self.subTest(overrides=tuple(sorted(overrides))):
                artifact, envelope, _output = self._render(suffix="-forged")
                self._forge(envelope, **overrides)
                sender = Mock(return_value="accepted")
                with patch("whatsapp_tech_digest.run.send", sender), self.assertRaises(
                    DeliveryBlockedError
                ):
                    run.deliver_reviewed(self.policy_path, self.live_path, artifact, envelope)
                sender.assert_not_called()
                self.assertEqual(self.live.snapshot()["checkpoint_seq"], 0)
                artifact.unlink(missing_ok=True)
                envelope.unlink(missing_ok=True)
                self.live.connection.execute("DELETE FROM reviewed_artifacts")

    def test_delivery_records_live_coverage_rather_than_envelope_coverage(self) -> None:
        artifact, envelope, _output = self._render()
        self._forge(envelope, coverage_metadata={
            "observed_events": 0, "collector_state": "forged", "gaps": []
        })
        with patch("whatsapp_tech_digest.run.send", return_value="accepted"):
            self.assertEqual(
                run.deliver_reviewed(self.policy_path, self.live_path, artifact, envelope),
                "accepted",
            )
        recorded = json.loads(
            self.live.connection.execute(
                "SELECT coverage_snapshot FROM digest_runs"
            ).fetchone()["coverage_snapshot"]
        )
        self.assertEqual(recorded, self.live.coverage())
        self.assertNotEqual(recorded["collector_state"], "forged")

    def test_transport_failure_allows_one_retry_but_success_and_uncertainty_do_not(self) -> None:
        artifact, envelope, _output = self._render()
        with patch("whatsapp_tech_digest.run.send", return_value="failed"):
            self.assertEqual(
                run.deliver_reviewed(self.policy_path, self.live_path, artifact, envelope),
                "failed",
            )
        self.assertEqual(self.live.snapshot()["checkpoint_seq"], 0)
        retry = Mock(return_value="accepted")
        with patch("whatsapp_tech_digest.run.send", retry):
            self.assertEqual(
                run.deliver_reviewed(self.policy_path, self.live_path, artifact, envelope),
                "accepted",
            )
        self.assertEqual(retry.call_count, 1)
        self.assertEqual(self.live.snapshot()["checkpoint_seq"], 2)
        replay = Mock(return_value="accepted")
        with patch("whatsapp_tech_digest.run.send", replay), self.assertRaises(
            DeliveryBlockedError
        ):
            run.deliver_reviewed(self.policy_path, self.live_path, artifact, envelope)
        replay.assert_not_called()

    def test_unresolved_delivery_blocks_any_further_reviewed_attempt(self) -> None:
        artifact, envelope, _output = self._render()
        with patch("whatsapp_tech_digest.run.send", return_value="unknown"):
            self.assertEqual(
                run.deliver_reviewed(self.policy_path, self.live_path, artifact, envelope),
                "unknown",
            )
        blocked = Mock(return_value="accepted")
        with patch("whatsapp_tech_digest.run.send", blocked), self.assertRaises(
            DeliveryBlockedError
        ):
            run.deliver_reviewed(self.policy_path, self.live_path, artifact, envelope)
        blocked.assert_not_called()
        checkpoint = self.live.connection.execute(
            "SELECT checkpoint_seq,delivery_state FROM checkpoints WHERE id=1"
        ).fetchone()
        self.assertEqual(tuple(checkpoint), (0, "unknown"))

    def test_bounded_cutoff_must_name_a_change_in_the_target_group(self) -> None:
        other = DurableSpool(self.root / "other.sqlite3", "987654321@g.us")
        self.addCleanup(other.close)
        self.live.connection.backup(other.connection)
        other.connection.execute("DELETE FROM digest_runs")
        with self.assertRaisesRegex(DeliveryBlockedError, "durable change"):
            other.snapshot(cutoff_seq=2)

    def test_legacy_isolated_row_delivery_api_is_removed(self) -> None:
        self.assertFalse(hasattr(DurableSpool, "reviewed_artifact"))


if __name__ == "__main__":
    unittest.main()
