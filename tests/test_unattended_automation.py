from __future__ import annotations

import json
import os
import stat
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import Mock, patch

from whatsapp_tech_digest.systemd_units import build_service_unit, build_timer_unit
from whatsapp_tech_digest.unattended import CredentialError, run_unattended


def _policy(path: Path, *, password_env: str = "DIGEST_SMTP_PASSWORD") -> Path:
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "example_only": False,
                "target_group_jid": "123456789@g.us",
                "silence": {
                    "collector_consume": True,
                    "bridge_deny_all_post": True,
                    "operation_scope": ["send", "edit", "media", "poll", "location", "typing", "read", "progress", "unknown"],
                },
                "whatsapp": {
                    "mode": "allowlist",
                    "group_policy": "allowlist",
                    "group_allow_from": ["123456789@g.us"],
                    "require_mention": False,
                    "unauthorized_dm": "ignore",
                    "send_read_receipts": False,
                    "bridge_port": 17778,
                },
                "schedule": {
                    "timezone": "America/Toronto",
                    "expression": "0 8 * * *",
                    "activate_only_after_dst_contract": True,
                },
                "retention": {"raw_days": 7, "digest_days": 90},
                "paths": {"spool": "/owner-only/spool.sqlite3", "state_dir": "/owner-only", "mode": "0700"},
                "runtime": {"lock_seconds": 60, "max_runtime_seconds": 600},
                "health": {"heartbeat_seconds": 300},
                "contacts": {"fallback": "display_name"},
                "redaction": {"enabled": True},
                "models": {
                    "provider": "hermes-openai-codex",
                    "pipeline_mode": "one_pass",
                    "preclassifier": "gpt-5.6-terra",
                    "preclassifier_digest": None,
                    "final": "gpt-5.6-terra",
                    "final_digest": None,
                    "fallback": "gpt-5.6-terra",
                    "fallback_digest": None,
                    "timeout_seconds": 60,
                    "batch_size": 32,
                    "context_limit": 10000,
                    "noise_threshold": 0.9,
                    "endpoint": "local://hermes-cli",
                    "api_key_env": None,
                    "reasoning_effort": "high",
                    "max_output_tokens": 1000,
                    "max_output_chars": 10000,
                    "classifier_instruction": "fixture",
                    "final_instruction": "fixture",
                },
                "external_fallback": {"enabled": False, "approved": False, "reduced_bundle_only": False},
                "smtp": {
                    "host": "smtp.relay.test",
                    "port": 587,
                    "sender": "digest@corp.test",
                    "recipient": "owner@corp.test",
                    "password_env": password_env,
                    "timeout_seconds": 20,
                },
            }
        ),
        encoding="utf-8",
    )
    return path


def _credential_source(path: Path, name: str = "QUESTFOLIO_SMTP_PASSWORD", value: str = "fixture-secret") -> Path:
    path.write_text(f"{name}={value}\nUNRELATED_SECRET=must-not-be-imported\n", encoding="utf-8")
    path.chmod(0o600)
    return path


class UnattendedAutomationTests(TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.temp_path = Path(self.temporary_directory.name)

    def test_run_unattended_maps_only_declared_source_secret_and_restores_environment(self) -> None:
        policy = _policy(self.temp_path / "policy.json")
        source = _credential_source(self.temp_path / "smtp.env")
        runner = Mock(return_value=0)

        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(
                run_unattended(
                    policy,
                    self.temp_path / "spool.sqlite3",
                    self.temp_path / "run.lock",
                    source,
                    "QUESTFOLIO_SMTP_PASSWORD",
                    runner=runner,
                ),
                0,
            )

            runner.assert_called_once_with(
                [
                    "--policy", str(policy),
                    "--spool", str(self.temp_path / "spool.sqlite3"),
                    "--lock", str(self.temp_path / "run.lock"),
                    "--execution-mode", "delivery-capable",
                ]
            )
            self.assertNotIn("DIGEST_SMTP_PASSWORD", os.environ)
            self.assertNotIn("UNRELATED_SECRET", os.environ)

    def test_run_unattended_rejects_weak_credential_source_before_runner(self) -> None:
        policy = _policy(self.temp_path / "policy.json")
        source = _credential_source(self.temp_path / "smtp.env")
        source.chmod(0o644)
        runner = Mock()

        with self.assertRaisesRegex(CredentialError, "owner-only"):
            run_unattended(
                policy,
                self.temp_path / "spool.sqlite3",
                self.temp_path / "run.lock",
                source,
                "QUESTFOLIO_SMTP_PASSWORD",
                runner=runner,
            )

        runner.assert_not_called()

    def test_run_unattended_rejects_source_key_mismatch_before_runner(self) -> None:
        policy = _policy(self.temp_path / "policy.json")
        source = _credential_source(self.temp_path / "smtp.env", name="OTHER_PASSWORD")
        runner = Mock()

        with self.assertRaisesRegex(CredentialError, "unavailable"):
            run_unattended(
                policy,
                self.temp_path / "spool.sqlite3",
                self.temp_path / "run.lock",
                source,
                "QUESTFOLIO_SMTP_PASSWORD",
                runner=runner,
            )

        runner.assert_not_called()

    def test_service_unit_has_an_explicit_secret_bridge_and_hardening(self) -> None:
        unit = build_service_unit(
            project_root="/opt/whatsapp-tech-digest",
            policy_path="/owner-only/digest.policy.json",
            spool_path="/owner-only/spool.sqlite3",
            lock_path="/owner-only/run.lock",
            credential_source="/owner-only/reused-smtp.env",
            credential_key="QUESTFOLIO_SMTP_PASSWORD",
        )

        self.assertIn("ExecStart=/opt/whatsapp-tech-digest/.venv/bin/python -m whatsapp_tech_digest.unattended", unit)
        self.assertNotIn("--execution-mode delivery-capable", unit)
        self.assertIn("--credential-source /owner-only/reused-smtp.env", unit)
        self.assertIn("--credential-key QUESTFOLIO_SMTP_PASSWORD", unit)
        self.assertIn("UMask=0077", unit)
        self.assertIn("NoNewPrivileges=true", unit)
        self.assertIn("PrivateTmp=true", unit)
        self.assertNotIn("PrivateDevices=true", unit)
        self.assertIn("ProtectSystem=full", unit)
        self.assertIn("ProtectHome=read-only", unit)
        self.assertIn("RestrictSUIDSGID=true", unit)
        self.assertIn("LockPersonality=true", unit)
        self.assertIn("RestrictRealtime=true", unit)
        self.assertIn("RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6", unit)
        self.assertIn("SystemCallArchitectures=native", unit)
        self.assertNotIn("CapabilityBoundingSet=", unit)
        self.assertIn("ReadWritePaths=/owner-only", unit)
        self.assertNotIn("EnvironmentFile=", unit)
        self.assertNotIn("/home/", unit)
        self.assertIn("Environment=PATH=%h/.local/bin:/usr/local/bin:/usr/bin:/bin", unit)

    def test_timer_unit_is_daily_toronto_wall_clock_and_never_catches_up_late(self) -> None:
        unit = build_timer_unit()

        self.assertIn("OnCalendar=*-*-* 08:00:00 America/Toronto", unit)
        self.assertIn("Persistent=false", unit)
        self.assertIn("Unit=whatsapp-tech-digest.service", unit)
