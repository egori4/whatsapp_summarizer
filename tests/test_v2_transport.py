import subprocess

import pytest

from whatsapp_digest.config import load_config
from whatsapp_digest.transport import (
    BRIDGE_SERVICE,
    COLLECTOR_SERVICE,
    TransportError,
    _wait_bridge_connected,
    discover_transport_paths,
    hermes_whatsapp_enabled,
    install_transport,
    render_transport_units,
    remove_transport,
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
  - id: first
    name: First
    group_jid: "120363429012626317@g.us"
    model: {}
    summarization: {}
    delivery:
      email:
        to: [user@example.com]
  - id: second
    name: Second
    group_jid: "120363400000000000@g.us"
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


def make_hermes(tmp_path, *, enabled=False):
    home = tmp_path / ".hermes"
    bridge = home / "hermes-agent" / "scripts" / "whatsapp-bridge" / "bridge.js"
    bridge.parent.mkdir(parents=True)
    bridge.write_text("// bridge", encoding="utf-8")
    node = home / "tools" / "node-26.7.0-linux-x64" / "bin" / "node"
    node.parent.mkdir(parents=True)
    node.write_text("", encoding="utf-8")
    session = home / "whatsapp" / "session"
    session.mkdir(parents=True)
    (home / ".env").write_text(
        f"WHATSAPP_ENABLED={'true' if enabled else 'false'}\n",
        encoding="utf-8",
    )
    return home, node, bridge, session


def test_discovery_uses_proven_legacy_session_path(tmp_path):
    home, node, bridge, session = make_hermes(tmp_path)
    paths = discover_transport_paths(hermes_home=home)
    assert paths.node == node
    assert paths.bridge_script == bridge
    assert paths.session_dir == session


def test_rendered_bridge_is_allowlist_only_and_uses_configured_groups(tmp_path):
    cfg = make_config(tmp_path)
    home, node, bridge, session = make_hermes(tmp_path)
    units = render_transport_units(
        cfg,
        python_executable="/opt/digest/.venv/bin/python",
        hermes_home=home,
    )
    assert 'Environment="WHATSAPP_GROUP_POLICY=allowlist"' in units.bridge_text
    assert "120363429012626317@g.us,120363400000000000@g.us" in units.bridge_text
    assert "WHATSAPP_ALLOWED_USERS=*" not in units.bridge_text
    assert f'"{node}"' in units.bridge_text
    assert f'"{bridge}"' in units.bridge_text
    assert f'--session "{session}"' in units.bridge_text
    assert "--port 3001" in units.bridge_text
    assert f"Requires={BRIDGE_SERVICE}" in units.collector_text
    assert '"/opt/digest/.venv/bin/python" -m whatsapp_digest.cli' in units.collector_text


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("true", True),
        ("1", True),
        ("yes", True),
        ("on", True),
        ("false", False),
        ("0", False),
        ("no", False),
        ("off", False),
    ],
)
def test_hermes_whatsapp_boolean_parsing(tmp_path, value, expected):
    home, *_ = make_hermes(tmp_path)
    (home / ".env").write_text(f"WHATSAPP_ENABLED={value}\n", encoding="utf-8")
    assert hermes_whatsapp_enabled(hermes_home=home) is expected


def test_install_refuses_competing_hermes_whatsapp(tmp_path):
    cfg = make_config(tmp_path)
    home, *_ = make_hermes(tmp_path, enabled=True)
    with pytest.raises(TransportError, match="still enabled"):
        install_transport(
            cfg,
            unit_dir=tmp_path / "systemd",
            hermes_home=home,
            executor=lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, "", ""),
        )


def test_install_writes_verifies_enables_and_checks_both_services(tmp_path, monkeypatch):
    cfg = make_config(tmp_path)
    home, *_ = make_hermes(tmp_path)
    root = tmp_path / "systemd"
    calls = []

    states = iter(["inactive", "inactive", "active", "active"])

    def fake(cmd, **kwargs):
        calls.append(cmd)
        if cmd[:3] == ["systemctl", "--user", "is-active"]:
            state = next(states)
            code = 0 if state == "active" else 3
            return subprocess.CompletedProcess(cmd, code, state + "\n", "")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr("whatsapp_digest.transport._bridge_health", lambda _cfg: "unreachable")
    monkeypatch.setattr("whatsapp_digest.transport._wait_bridge_connected", lambda _cfg: None)

    units = install_transport(
        cfg,
        unit_dir=root,
        python_executable="/opt/digest/.venv/bin/python",
        hermes_home=home,
        executor=fake,
    )
    assert units.allowed_groups == (
        "120363429012626317@g.us",
        "120363400000000000@g.us",
    )
    assert (root / BRIDGE_SERVICE).exists()
    assert (root / COLLECTOR_SERVICE).exists()
    assert any(cmd[:3] == ["systemd-analyze", "--user", "verify"] for cmd in calls)
    assert ["systemctl", "--user", "enable", BRIDGE_SERVICE] in calls
    assert ["systemctl", "--user", "start", BRIDGE_SERVICE] in calls
    assert ["systemctl", "--user", "enable", COLLECTOR_SERVICE] in calls
    assert ["systemctl", "--user", "start", COLLECTOR_SERVICE] in calls


def test_remove_transport_removes_owned_units(tmp_path):
    root = tmp_path / "systemd"
    root.mkdir()
    for name in (BRIDGE_SERVICE, COLLECTOR_SERVICE):
        (root / name).write_text("unit", encoding="utf-8")

    def fake(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, "", "")

    removed = remove_transport(unit_dir=root, executor=fake)
    assert set(removed) == {BRIDGE_SERVICE, COLLECTOR_SERVICE}
    assert list(root.iterdir()) == []


def test_install_refuses_unmanaged_bridge_endpoint(tmp_path, monkeypatch):
    cfg = make_config(tmp_path)
    home, *_ = make_hermes(tmp_path)

    monkeypatch.setattr("whatsapp_digest.transport._bridge_health", lambda _cfg: "connected")

    def fake(cmd, **kwargs):
        if cmd[:3] == ["systemctl", "--user", "is-active"]:
            return subprocess.CompletedProcess(cmd, 3, "inactive\n", "")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    with pytest.raises(TransportError, match="manual bridge"):
        install_transport(
            cfg,
            unit_dir=tmp_path / "systemd",
            hermes_home=home,
            executor=fake,
        )


def test_wait_bridge_connected_retries_until_ready(tmp_path):
    cfg = make_config(tmp_path)
    states = iter(["unreachable", "responding", "connected"])
    sleeps = []

    _wait_bridge_connected(
        cfg,
        timeout_seconds=1,
        interval_seconds=0.01,
        health_check=lambda _cfg: next(states),
        sleeper=lambda seconds: sleeps.append(seconds),
    )

    assert sleeps == [0.01, 0.01]


def test_wait_bridge_connected_fails_closed(tmp_path):
    cfg = make_config(tmp_path)

    with pytest.raises(TransportError, match="did not become connected"):
        _wait_bridge_connected(
            cfg,
            timeout_seconds=0.001,
            interval_seconds=0,
            health_check=lambda _cfg: "unreachable",
            sleeper=lambda _seconds: None,
        )


def test_reinstall_restarts_active_transport_services(tmp_path, monkeypatch):
    cfg = make_config(tmp_path)
    home, *_ = make_hermes(tmp_path)
    root = tmp_path / "systemd"
    calls = []

    monkeypatch.setattr("whatsapp_digest.transport._wait_bridge_connected", lambda _cfg: None)
    monkeypatch.setattr("whatsapp_digest.transport._bridge_health", lambda _cfg: "connected")

    def fake(cmd, **kwargs):
        calls.append(cmd)
        if cmd[:3] == ["systemctl", "--user", "is-active"]:
            return subprocess.CompletedProcess(cmd, 0, "active\n", "")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    install_transport(
        cfg,
        unit_dir=root,
        python_executable="/opt/digest/.venv/bin/python",
        hermes_home=home,
        executor=fake,
    )

    assert ["systemctl", "--user", "restart", BRIDGE_SERVICE] in calls
    assert ["systemctl", "--user", "restart", COLLECTOR_SERVICE] in calls
