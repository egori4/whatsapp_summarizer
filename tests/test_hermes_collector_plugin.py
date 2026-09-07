from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "tests"))
from test_offline_digest import policy_data

PLUGIN_PATH = PROJECT_ROOT / "hermes_plugin" / "whatsapp_tech_digest_collector" / "__init__.py"
TARGET = "10000000-00000002@g.us"


def load_plugin_module():
    spec = importlib.util.spec_from_file_location("test_digest_collector_plugin", PLUGIN_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeContext:
    def __init__(self, settings):
        self.settings = settings
        self.hooks = {}

    def get_config(self, key, default=None):
        return self.settings.get(key, default)

    def register_hook(self, name, callback):
        self.hooks[name] = callback


def make_event(*, chat_id=TARGET, message_id="fixture-message", sender_id="15551234567:2@s.whatsapp.net", timestamp=1_725_000_000, text="fixture-only content"):
    return SimpleNamespace(
        text=text,
        message_id=message_id,
        user_id=sender_id,
        user_name="fixture sender",
        source=SimpleNamespace(chat_id=chat_id, user_id=sender_id, user_name="fixture sender"),
        raw_message={"timestamp": timestamp},
    )


class HermesCollectorPluginTests(unittest.TestCase):
    def live_safe_policy(self, state_dir: Path) -> Path:
        data = policy_data()
        data["example_only"] = False
        data["target_group_jid"] = TARGET
        data["whatsapp"] = {**data["whatsapp"], "group_allow_from": [TARGET], "bridge_port": 17778}
        data["paths"] = {"spool": str(state_dir / "spool.sqlite3"), "state_dir": str(state_dir), "mode": "0700"}
        data["models"] = {
            **data["models"],
            "preclassifier_digest": "sha256:" + "a" * 64,
            "final_digest": "sha256:" + "b" * 64,
            "fallback_digest": "sha256:" + "c" * 64,
        }
        data["smtp"] = {**data["smtp"], "host": "smtp.invalid.test", "sender": "digest@invalid.test"}
        policy = state_dir / "digest.policy.json"
        policy.write_text(json.dumps(data), encoding="utf-8")
        policy.chmod(0o600)
        return policy

    def register(self, policy: Path):
        ctx = FakeContext({"project_root": str(PROJECT_ROOT), "policy_path": str(policy)})
        load_plugin_module().register(ctx)
        return ctx.hooks["pre_gateway_dispatch"]

    def test_target_is_spooled_and_returns_skip_action_dict(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state"
            state.mkdir(mode=0o700)
            hook = self.register(self.live_safe_policy(state))
            result = hook(event=make_event())
            self.assertEqual(result, {"action": "skip", "reason": "whatsapp_tech_digest_collected"})
            import sqlite3
            with sqlite3.connect(state / "spool.sqlite3") as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 1)

    def test_target_hook_spools_when_gateway_invokes_it_from_a_different_thread(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state"
            state.mkdir(mode=0o700)
            hook = self.register(self.live_safe_policy(state))
            result, errors = [], []

            def invoke() -> None:
                try:
                    result.append(hook(event=make_event()))
                except Exception as exc:  # assertion below reports the live failure
                    errors.append(exc)

            worker = threading.Thread(target=invoke)
            worker.start()
            worker.join()
            self.assertEqual(errors, [])
            self.assertEqual(result, [{"action": "skip", "reason": "whatsapp_tech_digest_collected"}])
            import sqlite3
            with sqlite3.connect(state / "spool.sqlite3") as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 1)

    def test_target_hook_closes_per_event_spool_handle(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state"
            state.mkdir(mode=0o700)
            from whatsapp_tech_digest.spool import DurableSpool
            original_close = DurableSpool.close
            closed = []

            def tracking_close(instance):
                closed.append(instance)
                return original_close(instance)

            with patch.object(DurableSpool, "close", tracking_close):
                hook = self.register(self.live_safe_policy(state))
                closed.clear()
                self.assertEqual(hook(event=make_event()), {"action": "skip", "reason": "whatsapp_tech_digest_collected"})

            self.assertEqual(len(closed), 1)

    def test_non_target_returns_allow_without_creating_spool_record(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state"
            state.mkdir(mode=0o700)
            hook = self.register(self.live_safe_policy(state))
            result = hook(event=make_event(chat_id="other-group@g.us"))
            self.assertEqual(result, {"action": "allow", "reason": "non_target"})
            import sqlite3
            with sqlite3.connect(state / "spool.sqlite3") as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 0)

    def test_target_spool_write_failure_returns_fail_closed_skip(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state"
            state.mkdir(mode=0o700)
            hook = self.register(self.live_safe_policy(state))
            from whatsapp_tech_digest.spool import DurableSpool

            with patch.object(DurableSpool, "append_message", side_effect=OSError("fixture-only write failure")):
                result = hook(event=make_event())

            self.assertEqual(result, {"action": "skip", "reason": "whatsapp_tech_digest_collection_failed"})
            import sqlite3
            with sqlite3.connect(state / "spool.sqlite3") as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 0)

    def test_invalid_target_event_returns_fail_closed_skip(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state"
            state.mkdir(mode=0o700)
            hook = self.register(self.live_safe_policy(state))
            self.assertEqual(
                hook(event=make_event(sender_id="")),
                {"action": "skip", "reason": "whatsapp_tech_digest_collection_failed"},
            )


if __name__ == "__main__":
    unittest.main()
