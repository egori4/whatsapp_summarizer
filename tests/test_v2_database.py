from datetime import datetime, timedelta, timezone

from whatsapp_digest.database import DigestDatabase


def test_message_insert_dedupe_edit_and_delete(tmp_path):
    with DigestDatabase(tmp_path / "db.sqlite") as db:
        common = dict(
            message_id="m1",
            group_jid="123-456@g.us",
            sender_id="user@s.whatsapp.net",
            display_name="User",
            occurred_at="2026-09-26T12:00:00+00:00",
            text="Version 1",
        )
        action, seq1 = db.upsert_message(**common)
        assert action == "inserted"
        assert seq1 == 1

        action, seq_same = db.upsert_message(**common)
        assert action == "unchanged"
        assert seq_same == seq1
        assert db.latest_seq() == 1

        common["text"] = "Version 2"
        action, seq2 = db.upsert_message(**common)
        assert action == "updated"
        assert seq2 == 2

        action, seq3 = db.mark_deleted("m1")
        assert action == "updated"
        assert seq3 == 3
        row = db.select_changes("123-456@g.us", 0, 3)[0]
        assert row["deleted"] == 1


def test_discovered_group_contains_only_metadata(tmp_path):
    with DigestDatabase(tmp_path / "db.sqlite") as db:
        db.discover_group("123-456@g.us", "Engineering", seen_at="2026-09-26T12:00:00+00:00")
        rows = db.list_discovered_groups()
        assert len(rows) == 1
        assert set(rows[0].keys()) == {"group_jid", "display_name", "first_seen", "last_seen"}


def test_pending_count_uses_checkpoint(tmp_path):
    with DigestDatabase(tmp_path / "db.sqlite") as db:
        for i in range(3):
            db.upsert_message(
                message_id=f"m{i}",
                group_jid="123-456@g.us",
                sender_id=None,
                display_name=None,
                occurred_at=f"2026-09-26T12:0{i}:00+00:00",
                text=f"msg {i}",
            )
        assert db.pending_count("wf", "123-456@g.us") == 3
        db.set_checkpoint("wf", 2, run_id="r1")
        assert db.pending_count("wf", "123-456@g.us") == 1


def test_fixed_cutoff_leaves_later_arrival_pending(tmp_path):
    with DigestDatabase(tmp_path / "db.sqlite") as db:
        db.upsert_message(
            message_id="m1", group_jid="123-456@g.us", sender_id=None, display_name=None,
            occurred_at="2026-09-26T12:00:00+00:00", text="first"
        )
        cutoff = db.latest_seq()
        db.upsert_message(
            message_id="m2", group_jid="123-456@g.us", sender_id=None, display_name=None,
            occurred_at="2026-09-26T12:01:00+00:00", text="second"
        )
        selected = db.select_changes("123-456@g.us", 0, cutoff)
        assert [row["message_id"] for row in selected] == ["m1"]


def test_retention_never_purges_pending_messages(tmp_path):
    old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    with DigestDatabase(tmp_path / "db.sqlite") as db:
        db.upsert_message(
            message_id="pending", group_jid="123-456@g.us", sender_id=None, display_name=None,
            occurred_at=old, text="pending", changed_at=old, collected_at=old
        )
        removed = db.purge_processed_messages({"wf": "123-456@g.us"}, 7)
        assert removed == 0
        assert db.pending_count("wf", "123-456@g.us") == 1


def test_retention_purges_only_processed_old_messages(tmp_path):
    old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    with DigestDatabase(tmp_path / "db.sqlite") as db:
        db.upsert_message(
            message_id="old", group_jid="123-456@g.us", sender_id=None, display_name=None,
            occurred_at=old, text="done", changed_at=old, collected_at=old
        )
        db.upsert_message(
            message_id="pending", group_jid="123-456@g.us", sender_id=None, display_name=None,
            occurred_at="2026-09-26T12:00:00+00:00", text="pending"
        )
        db.set_checkpoint("wf", 1)
        removed = db.purge_processed_messages({"wf": "123-456@g.us"}, 7)
        assert removed == 1
        assert db.pending_count("wf", "123-456@g.us") == 1
