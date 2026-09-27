import pytest

from whatsapp_digest.locking import WorkflowBusyError, workflow_lock


def test_workflow_lock_blocks_duplicate_process(tmp_path):
    db = tmp_path / "data" / "messages.db"
    with workflow_lock(db, "tests"):
        with pytest.raises(WorkflowBusyError, match="already running"):
            with workflow_lock(db, "tests"):
                pass
