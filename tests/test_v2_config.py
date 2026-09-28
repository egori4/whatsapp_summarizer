from pathlib import Path
import textwrap

import pytest

from whatsapp_digest.config import ConfigError, load_config


BASE = """
paths:
  database: ./data/messages.db
  log: ./logs/digest.log
retention:
  processed_raw_days: 7
  log_days: 30
hermes:
  command: hermes
  timeout_seconds: 600
email:
  host: smtp.example.com
  port: 587
  sender: digest@example.com
  password_env: DIGEST_PASSWORD
defaults:
  timezone: America/Toronto
workflows:
  - id: global-ps
    name: Global PS
    group_jid: "12345-67890@g.us"
    schedule:
      type: daily
      at: "08:00"
    model:
      provider: inherit
      name: inherit
      reasoning: inherit
    summarization:
      instructions: Technical summary.
    delivery:
      email:
        to: [user@example.com]
        attach_raw_messages: true
"""


def write_config(tmp_path: Path, body: str = BASE) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return path


def test_relative_paths_are_anchored_to_config_directory(tmp_path, monkeypatch):
    cfg_path = write_config(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    cfg = load_config(cfg_path)
    assert cfg.database_path == (tmp_path / "data/messages.db").resolve()
    assert cfg.log_path == (tmp_path / "logs/digest.log").resolve()


def test_model_inheritance_and_optional_sections(tmp_path):
    cfg = load_config(write_config(tmp_path))
    workflow = cfg.workflows[0]
    assert workflow.model == {"provider": "inherit", "name": "inherit", "reasoning": "inherit"}
    assert workflow.sections == ()


def test_partial_model_override_and_weekly_schedule(tmp_path):
    body = BASE.replace(
        "type: daily\n      at: \"08:00\"",
        "type: weekly\n      days: [mon, wed, fri]\n      at: \"08:00\"",
    ).replace("reasoning: inherit", "reasoning: low")
    cfg = load_config(write_config(tmp_path, body))
    workflow = cfg.workflows[0]
    assert workflow.schedule["type"] == "weekly"
    assert workflow.schedule["days"] == ["mon", "wed", "fri"]
    assert workflow.model["reasoning"] == "low"


def test_monthly_schedule(tmp_path):
    body = BASE.replace(
        "type: daily\n      at: \"08:00\"",
        "type: monthly\n      day: 15\n      at: \"08:00\"",
    )
    cfg = load_config(write_config(tmp_path, body))
    assert cfg.workflows[0].schedule["day"] == 15


def test_ollama_override(tmp_path):
    body = BASE.replace("provider: inherit", "provider: ollama").replace("name: inherit", "name: qwen3.5:9b")
    cfg = load_config(write_config(tmp_path, body))
    assert cfg.workflows[0].model["provider"] == "ollama"
    assert cfg.workflows[0].model["name"] == "qwen3.5:9b"


def test_duplicate_workflow_id_rejected(tmp_path):
    extra = """
  - id: global-ps
    name: Other
    group_jid: "99999-11111@g.us"
    model: {}
    summarization: {}
    delivery:
      email:
        to: [other@example.com]
"""
    with pytest.raises(ConfigError, match="duplicate workflow id"):
        load_config(write_config(tmp_path, BASE + extra))


def test_duplicate_group_jid_rejected(tmp_path):
    extra = """
  - id: second
    name: Other
    group_jid: "12345-67890@g.us"
    model: {}
    summarization: {}
    delivery:
      email:
        to: [other@example.com]
"""
    with pytest.raises(ConfigError, match="duplicate group JID"):
        load_config(write_config(tmp_path, BASE + extra))


@pytest.mark.parametrize("jid", ["alice", "12345@s.whatsapp.net", "*@g.us", ""])
def test_invalid_group_jid_rejected(tmp_path, jid):
    body = BASE.replace('"12345-67890@g.us"', f'"{jid}"')
    with pytest.raises(ConfigError):
        load_config(write_config(tmp_path, body))


def test_duplicate_section_id_rejected(tmp_path):
    body = BASE.replace(
        "instructions: Technical summary.",
        """instructions: Technical summary.
      sections:
        - id: actions
          title: Actions
        - id: actions
          title: More Actions""",
    )
    with pytest.raises(ConfigError, match="duplicate section id"):
        load_config(write_config(tmp_path, body))


def test_retention_must_be_non_negative(tmp_path):
    body = BASE.replace("processed_raw_days: 7", "processed_raw_days: -1")
    with pytest.raises(ConfigError, match="processed_raw_days"):
        load_config(write_config(tmp_path, body))


def test_email_defaults_are_normalized(tmp_path):
    cfg = load_config(write_config(tmp_path))
    assert cfg.email["host"] == "smtp.example.com"
    assert cfg.email["port"] == 587
    assert cfg.email["sender"] == "digest@example.com"
    assert cfg.email["username"] is None
    assert cfg.email["starttls"] is True
    assert cfg.email["timeout_seconds"] == 30


def test_invalid_email_subject_placeholder_is_rejected(tmp_path):
    body = BASE.replace(
        "attach_raw_messages: true",
        'attach_raw_messages: true\n        subject: "{recipient}"',
    )
    with pytest.raises(ConfigError, match="unsupported placeholder"):
        load_config(write_config(tmp_path, body))


def test_email_starttls_must_be_boolean(tmp_path):
    body = BASE.replace(
        "password_env: DIGEST_PASSWORD",
        'password_env: DIGEST_PASSWORD\n  starttls: "yes"',
    )
    with pytest.raises(ConfigError, match="email.starttls"):
        load_config(write_config(tmp_path, body))


@pytest.mark.parametrize("workflow_id", ["../bad", "bad/name", "space id", "-leading"])
def test_unsafe_workflow_id_rejected(tmp_path, workflow_id):
    body = BASE.replace("id: global-ps", f"id: {workflow_id}")
    with pytest.raises(ConfigError, match="letters, numbers"):
        load_config(write_config(tmp_path, body))


def test_context_defaults_enabled_48h(tmp_path):
    cfg = load_config(write_config(tmp_path))
    assert cfg.context == {
        "enabled": True,
        "lookback": "48h",
        "lookback_seconds": 48 * 3600,
        "max_messages": 100,
    }


def test_context_can_be_disabled_and_customized(tmp_path):
    body = BASE.replace(
        "defaults:\n  timezone: America/Toronto",
        "context:\n  enabled: false\n  lookback: 3d\n  max_messages: 150\ndefaults:\n  timezone: America/Toronto",
    )
    cfg = load_config(write_config(tmp_path, body))
    assert cfg.context["enabled"] is False
    assert cfg.context["lookback"] == "3d"
    assert cfg.context["max_messages"] == 150


def test_context_lookback_cannot_exceed_retention(tmp_path):
    body = BASE.replace(
        "defaults:\n  timezone: America/Toronto",
        "context:\n  enabled: true\n  lookback: 8d\ndefaults:\n  timezone: America/Toronto",
    )
    with pytest.raises(ConfigError, match="exceeds retention"):
        load_config(write_config(tmp_path, body))


def test_missing_config_defaults_reports_context_keys(tmp_path):
    from whatsapp_digest.config import missing_config_defaults
    path = write_config(tmp_path)
    assert missing_config_defaults(path) == (
        'context.enabled', 'context.lookback', 'context.max_messages'
    )


def test_config_upgrade_writes_review_file_and_preserves_existing_values(tmp_path):
    import stat
    import yaml
    from whatsapp_digest.config import upgrade_config
    body = BASE.replace(
        'defaults:\n  timezone: America/Toronto',
        'context:\n  lookback: 24h\ndefaults:\n  timezone: America/Toronto',
    )
    path = write_config(tmp_path, body)
    original = path.read_text()
    path.chmod(0o600)
    result = upgrade_config(path)
    assert result.applied is False
    assert result.output_path == path.with_name('config.yaml.upgraded')
    assert result.backup_path is None
    assert result.added == ('context.enabled', 'context.max_messages')
    upgraded = yaml.safe_load(result.output_path.read_text())
    assert upgraded['context'] == {'lookback': '24h', 'enabled': True, 'max_messages': 100}
    assert stat.S_IMODE(result.output_path.stat().st_mode) == 0o600
    assert path.read_text() == original


def test_config_upgrade_apply_backs_up_and_is_idempotent(tmp_path):
    import yaml
    from whatsapp_digest.config import upgrade_config, missing_config_defaults
    path = write_config(tmp_path)
    original = path.read_text()
    result = upgrade_config(path, apply=True)
    assert result.applied is True
    assert result.backup_path is not None
    assert result.backup_path.read_text() == original
    upgraded = yaml.safe_load(path.read_text())
    assert upgraded['context'] == {'enabled': True, 'lookback': '48h', 'max_messages': 100}
    assert missing_config_defaults(path) == ()
    again = upgrade_config(path, apply=True)
    assert again.added == ()
    assert again.backup_path is None
