from __future__ import annotations

import copy
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from whatsapp_tech_digest import run
from whatsapp_tech_digest.models import DigestResult
from whatsapp_tech_digest.spool import DeliveryBlockedError, DurableSpool


TARGET = "123456789@g.us"


def _policy(path: Path, spool_path: Path, *, target: str = TARGET) -> Path:
    data = json.loads(
        (Path(__file__).parents[1] / "config" / "digest.policy.example.json").read_text(
            encoding="utf-8"
        )
    )
    data["example_only"] = False
    data["target_group_jid"] = target
    data["whatsapp"].update(
        {"group_allow_from": [target], "bridge_port": 17778}
    )
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
    data["smtp"].update(
        {"host": "smtp.invalid.test", "sender": "digest@invalid.test"}
    )
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


class Phase3BoundedPortabilityTests(unittest.TestCase):
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
            ("later", "Release R2 supersedes the earlier deployment advice.", 3),
        ):
            self.live.append_message(_event(message_id, text, minute))

    def _copy_live(self) -> DurableSpool:
        copied = DurableSpool(self.copy_path, TARGET)
        self.live.connection.backup(copied.connection)
        self.addCleanup(copied.close)
        return copied

    def _render_bundle(self) -> tuple[DurableSpool, Path, Path, str]:
        copied = self._copy_live()
        artifact = self.root / "reviewed.md"
        envelope = self.root / "review.envelope.json"
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
                bounded_cutoff=2,
            )
        return copied, artifact, envelope, output

    def test_explicit_cutoff_selects_one_nonempty_contiguous_prefix(self) -> None:
        self.assertEqual(self.live.snapshot(cutoff_seq=2), {"checkpoint_seq": 0, "cutoff_seq": 2})
        events, _run_type, _omission = self.live.pending_events(
            self.live.snapshot(cutoff_seq=2)
        )
        self.assertEqual([event["change_seq"] for event in events], [1, 2])
        self.assertEqual(
            [event["change_seq"] for event in self.live.pending_events(self.live.snapshot())[0]],
            [1, 2, 3],
        )
        self.live.connection.execute(
            "UPDATE message_versions SET committed_at='2020-01-01T00:00:00+00:00' WHERE change_seq<=2"
        )
        historical, run_type, omission = self.live.pending_events(
            self.live.snapshot(cutoff_seq=2), include_historical_prefix=True
        )
        self.assertEqual([event["change_seq"] for event in historical], [1, 2])
        self.assertEqual((run_type, omission), ("bounded-backlog", None))
        for cutoff in (0, -1, 4, True):
            with self.subTest(cutoff=cutoff), self.assertRaises((ValueError, DeliveryBlockedError)):
                self.live.snapshot(cutoff_seq=cutoff)
        for cutoff in (0, 4):
            with self.subTest(runner_cutoff=cutoff):
                model = Mock()
                with patch("whatsapp_tech_digest.run.build_model", model), self.assertRaises(
                    DeliveryBlockedError
                ):
                    run.generate(
                        self.policy_path,
                        self.live_path,
                        execution_mode="render-only",
                        review_manifest=self.root / f"invalid-{cutoff}.json",
                        review_artifact=self.root / f"invalid-{cutoff}.md",
                        bounded_cutoff=cutoff,
                    )
                model.assert_not_called()

        revoked_path = self.root / "revoked.sqlite3"
        revoked = DurableSpool(revoked_path, TARGET)
        self.addCleanup(revoked.close)
        revoked.append_message(_event("gone", "Temporary source.", 4))
        revoked.record_run(revoked.snapshot(), "normal", "accepted", output="fixture")
        revoked.append_message({**_event("gone", "", 5), "text": None, "revoked": True})
        with self.assertRaisesRegex(DeliveryBlockedError, "empty"):
            revoked.snapshot(cutoff_seq=2)

    def test_render_writes_owner_only_historical_artifact_and_portable_envelope(self) -> None:
        copied, artifact, envelope, output = self._render_bundle()
        self.assertEqual(artifact.read_text(encoding="utf-8"), output)
        self.assertIn("Bounded historical backlog window", output)
        self.assertIn("Later changes remain pending", output)
        self.assertEqual(artifact.stat().st_mode & 0o777, 0o600)
        self.assertEqual(envelope.stat().st_mode & 0o777, 0o600)
        data = json.loads(envelope.read_text(encoding="utf-8"))
        self.assertEqual(data["schema_version"], "portable-review-envelope-v1")
        self.assertEqual(data["window_kind"], "bounded-historical-backlog")
        self.assertEqual((data["checkpoint_seq"], data["cutoff_seq"]), (0, 2))
        self.assertEqual(data["artifact_hash"], hashlib.sha256(output.encode()).hexdigest())
        self.assertEqual(data["provenance"], _provenance(("first", 1), ("second", 2)))
        serialized = envelope.read_text(encoding="utf-8")
        for forbidden in (
            "Apply release R1",
            "recipient",
            "password",
            "smtp",
            "15551234567",
        ):
            self.assertNotIn(forbidden, serialized)
        self.assertEqual(copied.snapshot()["checkpoint_seq"], 0)

    def test_portable_delivery_validates_live_spool_and_advances_only_live_cutoff(self) -> None:
        copied, artifact, envelope, _output = self._render_bundle()
        smtp_calls: list[tuple[object, ...]] = []
        with patch(
            "whatsapp_tech_digest.run.build_model",
            side_effect=AssertionError("portable delivery must not construct a model"),
        ), patch(
            "whatsapp_tech_digest.run.generate",
            side_effect=AssertionError("portable delivery must not render against live state"),
        ), patch(
            "whatsapp_tech_digest.run.send",
            side_effect=lambda *args: smtp_calls.append(args) or "accepted",
        ):
            self.assertEqual(
                run.deliver_reviewed(self.policy_path, self.live_path, artifact, envelope),
                "accepted",
            )

        self.assertEqual(len(smtp_calls), 1)
        self.assertEqual(self.live.snapshot()["checkpoint_seq"], 2)
        self.assertEqual(copied.snapshot()["checkpoint_seq"], 0)
        digest = dict(self.live.connection.execute("SELECT * FROM digest_runs").fetchone())
        self.assertEqual((digest["checkpoint_seq"], digest["cutoff_seq"]), (0, 2))
        self.assertEqual(digest["run_type"], "bounded-backlog")
        pending = self.live.pending_events(self.live.snapshot())[0]
        self.assertEqual([event["message_id"] for event in pending], ["later"])
        replay_sender = Mock(return_value="accepted")
        with patch("whatsapp_tech_digest.run.send", replay_sender), self.assertRaises(
            DeliveryBlockedError
        ):
            run.deliver_reviewed(self.policy_path, self.live_path, artifact, envelope)
        replay_sender.assert_not_called()

    def test_altered_absent_or_weak_envelope_and_artifact_fail_before_smtp(self) -> None:
        for case in (
            "absent", "weak", "weak-artifact", "altered-envelope", "altered-artifact"
        ):
            with self.subTest(case=case):
                copied, artifact, envelope, _output = self._render_bundle()
                del copied
                candidate = envelope
                if case == "absent":
                    candidate = self.root / "missing-envelope.json"
                elif case == "weak":
                    envelope.chmod(0o644)
                elif case == "weak-artifact":
                    artifact.chmod(0o644)
                elif case == "altered-envelope":
                    data = json.loads(envelope.read_text(encoding="utf-8"))
                    data["candidate_count"] += 1
                    envelope.write_text(json.dumps(data), encoding="utf-8")
                    envelope.chmod(0o600)
                else:
                    artifact.write_text(artifact.read_text(encoding="utf-8") + "\nchanged", encoding="utf-8")
                    artifact.chmod(0o600)
                sender = Mock(return_value="accepted")
                with patch("whatsapp_tech_digest.run.send", sender), self.assertRaises(
                    (DeliveryBlockedError, PermissionError)
                ):
                    run.deliver_reviewed(self.policy_path, self.live_path, artifact, candidate)
                sender.assert_not_called()
                self.assertEqual(self.live.snapshot()["checkpoint_seq"], 0)
                envelope.unlink(missing_ok=True)
                artifact.unlink(missing_ok=True)
                self.copy_path.unlink(missing_ok=True)

    def test_wrong_policy_checkpoint_cutoff_and_provenance_fail_before_smtp(self) -> None:
        mutations = ("policy", "checkpoint", "cutoff", "metadata", "missing", "revoked")
        for case in mutations:
            with self.subTest(case=case):
                _copied, artifact, envelope, _output = self._render_bundle()
                if case == "policy":
                    policy = json.loads(self.policy_path.read_text(encoding="utf-8"))
                    policy["runtime"]["lock_seconds"] += 1
                    self.policy_path.write_text(json.dumps(policy, sort_keys=True), encoding="utf-8")
                    self.policy_path.chmod(0o600)
                elif case == "checkpoint":
                    self.live.record_run(
                        self.live.snapshot(cutoff_seq=1), "normal", "accepted", output="fixture"
                    )
                elif case == "cutoff":
                    data = json.loads(envelope.read_text(encoding="utf-8"))
                    data["cutoff_seq"] = 3
                    data["envelope_hash"] = run._portable_envelope_hash(data)
                    envelope.write_text(run._canonical_json(data) + "\n", encoding="utf-8")
                    envelope.chmod(0o600)
                elif case == "metadata":
                    data = json.loads(envelope.read_text(encoding="utf-8"))
                    data["model_id"] = "forged-model"
                    data["envelope_hash"] = run._portable_envelope_hash(data)
                    envelope.write_text(run._canonical_json(data) + "\n", encoding="utf-8")
                    envelope.chmod(0o600)
                elif case == "missing":
                    self.live.connection.execute(
                        "DELETE FROM message_versions WHERE message_id='first'"
                    )
                    self.live.connection.execute("DELETE FROM messages WHERE message_id='first'")
                else:
                    self.live.append_message(
                        {**_event("first", "", 6), "text": None, "revoked": True}
                    )
                sender = Mock(return_value="accepted")
                with patch("whatsapp_tech_digest.run.send", sender), self.assertRaises(
                    DeliveryBlockedError
                ):
                    run.deliver_reviewed(self.policy_path, self.live_path, artifact, envelope)
                sender.assert_not_called()
                artifact.unlink(missing_ok=True)
                envelope.unlink(missing_ok=True)
                self.copy_path.unlink(missing_ok=True)
                if case == "policy":
                    self.policy_path = _policy(self.root / "policy.json", self.live_path)
                elif case == "checkpoint":
                    self.live.connection.execute(
                        "UPDATE checkpoints SET checkpoint_seq=0,delivery_state='clear' WHERE id=1"
                    )
                    self.live.connection.execute("DELETE FROM digest_runs")
                elif case in {"missing", "revoked"}:
                    self.live.close()
                    self.live_path.unlink()
                    for suffix in ("-wal", "-shm"):
                        Path(str(self.live_path) + suffix).unlink(missing_ok=True)
                    self.live = DurableSpool(self.live_path, TARGET)
                    for message_id, text, minute in (
                        ("first", "Apply release R1 using the documented procedure.", 1),
                        ("second", "Keep the rollback command available for release R1.", 2),
                        ("later", "Release R2 supersedes the earlier deployment advice.", 3),
                    ):
                        self.live.append_message(_event(message_id, text, minute))

    def test_wrong_target_binding_fails_before_smtp(self) -> None:
        _copied, artifact, envelope, _output = self._render_bundle()
        other_target = "987654321@g.us"
        other_spool = self.root / "other.sqlite3"
        other_policy = _policy(
            self.root / "other-policy.json", other_spool, target=other_target
        )
        DurableSpool(other_spool, other_target).close()
        sender = Mock(return_value="accepted")
        with patch("whatsapp_tech_digest.run.send", sender), self.assertRaisesRegex(
            DeliveryBlockedError, "target binding"
        ):
            run.deliver_reviewed(other_policy, other_spool, artifact, envelope)
        sender.assert_not_called()

    def test_replay_and_smtp_failure_or_uncertainty_never_overadvance(self) -> None:
        for state, expected_delivery_state, retryable in (
            ("failed", "clear", True), ("unknown", "unknown", False)
        ):
            with self.subTest(state=state):
                _copied, artifact, envelope, _output = self._render_bundle()
                with patch("whatsapp_tech_digest.run.send", return_value=state):
                    self.assertEqual(
                        run.deliver_reviewed(self.policy_path, self.live_path, artifact, envelope),
                        state,
                    )
                checkpoint = self.live.connection.execute(
                    "SELECT checkpoint_seq,delivery_state FROM checkpoints WHERE id=1"
                ).fetchone()
                self.assertEqual(tuple(checkpoint), (0, expected_delivery_state))
                sender = Mock(return_value=state)
                if retryable:
                    with patch("whatsapp_tech_digest.run.send", sender):
                        self.assertEqual(
                            run.deliver_reviewed(
                                self.policy_path, self.live_path, artifact, envelope
                            ),
                            state,
                        )
                    self.assertEqual(sender.call_count, 1)
                else:
                    with patch("whatsapp_tech_digest.run.send", sender), self.assertRaises(
                        DeliveryBlockedError
                    ):
                        run.deliver_reviewed(
                            self.policy_path, self.live_path, artifact, envelope
                        )
                    sender.assert_not_called()
                self.assertEqual(
                    tuple(self.live.connection.execute(
                        "SELECT checkpoint_seq,delivery_state FROM checkpoints WHERE id=1"
                    ).fetchone()),
                    (0, expected_delivery_state),
                )
                self.live.connection.execute(
                    "UPDATE checkpoints SET checkpoint_seq=0,delivery_state='clear' WHERE id=1"
                )
                self.live.connection.execute("DELETE FROM digest_runs")
                self.live.connection.execute("DELETE FROM reviewed_artifacts")
                artifact.unlink(missing_ok=True)
                envelope.unlink(missing_ok=True)
                self.copy_path.unlink(missing_ok=True)

    def test_cli_and_unattended_paths_cannot_infer_bounded_recovery(self) -> None:
        with patch("whatsapp_tech_digest.run.generate") as generate, self.assertRaises(SystemExit):
            run.main(
                [
                    "--policy", str(self.policy_path),
                    "--spool", str(self.live_path),
                    "--lock", str(self.root / "run.lock"),
                    "--execution-mode", "delivery-capable",
                    "--bounded-cutoff", "2",
                ]
            )
        generate.assert_not_called()

        from whatsapp_tech_digest.unattended import run_unattended

        credential = self.root / "smtp.env"
        credential.write_text("OWNER_SMTP_PASSWORD=fixture-secret\n", encoding="utf-8")
        credential.chmod(0o600)
        runner = Mock(return_value=0)
        run_unattended(
            self.policy_path,
            self.live_path,
            self.root / "run.lock",
            credential,
            "OWNER_SMTP_PASSWORD",
            runner=runner,
        )
        argv = runner.call_args.args[0]
        self.assertNotIn("--bounded-cutoff", argv)
        self.assertNotIn("--review-manifest", argv)


if __name__ == "__main__":
    unittest.main()
