import subprocess

from whatsapp_digest.config import load_config
from whatsapp_digest.systemd import (
    install_schedules,
    on_calendar,
    render_units,
    remove_schedules,
    schedule_env_file,
)


CONFIG = """
paths:
  database: ./data/messages.db
  log: ./logs/digest.log
retention: {}
hermes: {}
whatsapp:
  bridge_url: http://127.0.0.1:3001
email:
  host: smtp.example.com
  sender: digest@example.com
  password_env: DIGEST_PASSWORD
defaults:
  timezone: America/Toronto
workflows:
  - id: daily
    name: Daily
    group_jid: "12345-1@g.us"
    schedule:
      type: daily
      at: "08:00"
    model: {}
    summarization: {}
    delivery:
      email:
        to: [user@example.com]
  - id: manual
    name: Manual
    group_jid: "12345-2@g.us"
    model: {}
    summarization: {}
    delivery:
      email:
        to: [user@example.com]
"""


def make_config(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(CONFIG, encoding="utf-8")
    return load_config(path)


def test_calendar_generation():
    assert on_calendar({
        "type": "daily", "at": "08:00", "timezone": "America/Toronto"
    }) == "*-*-* 08:00:00 America/Toronto"
    assert on_calendar({
        "type": "weekly", "days": ["mon", "wed", "fri"],
        "at": "09:30", "timezone": "America/Toronto"
    }) == "Mon,Wed,Fri *-*-* 09:30:00 America/Toronto"
    assert on_calendar({
        "type": "monthly", "day": 7,
        "at": "07:15", "timezone": "UTC"
    }) == "*-*-07 07:15:00 UTC"


def test_rendered_service_uses_same_cli_runner_and_optional_env_file(tmp_path):
    cfg = make_config(tmp_path)
    unit = render_units(
        cfg,
        cfg.workflow("daily"),
        python_executable="/opt/digest/.venv/bin/python",
    )
    assert "run \"daily\" --scheduled" in unit.service_text
    assert "-m whatsapp_digest.cli" in unit.service_text
    assert str(cfg.source_path) in unit.service_text
    assert f"WorkingDirectory={cfg.source_path.parent}" in unit.service_text
    assert f'WorkingDirectory="{cfg.source_path.parent}"' not in unit.service_text
    assert f"EnvironmentFile=-{schedule_env_file(cfg)}" in unit.service_text
    assert "Persistent=true" in unit.timer_text
    assert "America/Toronto" in unit.timer_text


def test_install_writes_only_scheduled_workflows(tmp_path):
    cfg = make_config(tmp_path)
    unit_dir = tmp_path / "systemd"
    calls = []

    def fake(cmd, **kwargs):
        calls.append(cmd)
        if cmd[:3] == ["systemctl", "--user", "is-active"]:
            return subprocess.CompletedProcess(cmd, 0, "active\n", "")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    units = install_schedules(
        cfg,
        unit_dir=unit_dir,
        python_executable="/opt/digest/.venv/bin/python",
        executor=fake,
    )
    assert [unit.workflow_id for unit in units] == ["daily"]
    assert (unit_dir / "whatsapp-digest-daily.service").exists()
    assert (unit_dir / "whatsapp-digest-daily.timer").exists()
    assert not (unit_dir / "whatsapp-digest-manual.timer").exists()
    assert any(cmd[:3] == ["systemd-analyze", "--user", "verify"] for cmd in calls)
    assert ["systemctl", "--user", "daemon-reload"] in calls
    assert ["systemctl", "--user", "enable", "--now", "whatsapp-digest-daily.timer"] in calls


def test_install_removes_stale_generated_timer(tmp_path):
    cfg = make_config(tmp_path)
    unit_dir = tmp_path / "systemd"
    unit_dir.mkdir()
    (unit_dir / "whatsapp-digest-old.timer").write_text("old", encoding="utf-8")
    (unit_dir / "whatsapp-digest-old.service").write_text("old", encoding="utf-8")
    calls = []

    def fake(cmd, **kwargs):
        calls.append(cmd)
        if cmd[:3] == ["systemctl", "--user", "is-active"]:
            return subprocess.CompletedProcess(cmd, 0, "active\n", "")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    install_schedules(cfg, unit_dir=unit_dir, executor=fake)
    assert not (unit_dir / "whatsapp-digest-old.timer").exists()
    assert not (unit_dir / "whatsapp-digest-old.service").exists()
    assert ["systemctl", "--user", "disable", "--now", "whatsapp-digest-old.timer"] in calls


def test_remove_schedules_removes_generated_units(tmp_path):
    cfg = make_config(tmp_path)
    unit_dir = tmp_path / "systemd"
    unit_dir.mkdir()
    (unit_dir / "whatsapp-digest-daily.timer").write_text("timer", encoding="utf-8")
    (unit_dir / "whatsapp-digest-daily.service").write_text("service", encoding="utf-8")

    def fake(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, "", "")

    removed = remove_schedules(cfg, unit_dir=unit_dir, executor=fake)
    assert removed == ["whatsapp-digest-daily"]
    assert list(unit_dir.iterdir()) == []


def test_install_fails_if_timer_does_not_become_active(tmp_path):
    cfg = make_config(tmp_path)
    unit_dir = tmp_path / "systemd"

    def fake(cmd, **kwargs):
        if cmd[:3] == ["systemctl", "--user", "is-active"]:
            return subprocess.CompletedProcess(cmd, 3, "inactive\n", "")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    from whatsapp_digest.systemd import ScheduleError
    import pytest

    with pytest.raises(ScheduleError, match="not active after install"):
        install_schedules(
            cfg,
            unit_dir=unit_dir,
            python_executable="/opt/digest/.venv/bin/python",
            executor=fake,
        )


def test_install_fails_on_unit_verification_error(tmp_path):
    cfg = make_config(tmp_path)
    unit_dir = tmp_path / "systemd"

    def fake(cmd, **kwargs):
        if cmd[:3] == ["systemd-analyze", "--user", "verify"]:
            return subprocess.CompletedProcess(cmd, 1, "", "bad unit setting")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    from whatsapp_digest.systemd import ScheduleError
    import pytest

    with pytest.raises(ScheduleError, match="bad unit setting"):
        install_schedules(
            cfg,
            unit_dir=unit_dir,
            python_executable="/opt/digest/.venv/bin/python",
            executor=fake,
        )


def test_render_units_preserves_virtualenv_python_symlink(tmp_path):
    cfg = make_config(tmp_path)
    target = tmp_path / "usr-bin-python"
    target.write_text("", encoding="utf-8")
    venv_python = tmp_path / ".venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.symlink_to(target)

    unit = render_units(
        cfg,
        cfg.workflow("daily"),
        python_executable=venv_python,
    )

    assert f'ExecStart="{venv_python}" ' in unit.service_text
    assert f'ExecStart="{target}" ' not in unit.service_text
