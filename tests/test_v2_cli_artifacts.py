import os
import stat

from whatsapp_digest.cli import _save_dry_run_artifacts
from whatsapp_digest.runner import RunResult


def test_dry_run_artifacts_are_private_and_complete(tmp_path):
    db_path = tmp_path / "data" / "messages.db"
    db_path.parent.mkdir()
    result = RunResult(
        run_id="run-123",
        workflow_id="tests",
        status="dry-run-success",
        message_count=2,
        cutoff_seq=2,
        window_start="a",
        window_end="b",
        rendered_text="TEXT\n",
        rendered_html="<html>HTML</html>\n",
        raw_text="RAW\n",
    )
    root = _save_dry_run_artifacts(db_path, result)
    assert root is not None
    assert (root / "digest.txt").read_text() == "TEXT\n"
    assert (root / "digest.html").read_text() == "<html>HTML</html>\n"
    assert (root / "raw_messages.txt").read_text() == "RAW\n"
    assert stat.S_IMODE(root.stat().st_mode) & 0o077 == 0
    for name in ("digest.txt", "digest.html", "raw_messages.txt"):
        assert stat.S_IMODE((root / name).stat().st_mode) & 0o077 == 0
