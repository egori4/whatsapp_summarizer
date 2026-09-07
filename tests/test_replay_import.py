from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from whatsapp_tech_digest.spool import DurableSpool

TARGET = "10000000-00000002@g.us"


class WhatsAppExportReplayTests(unittest.TestCase):
    def test_replay_parses_multiline_export_and_appends_deterministic_events(self) -> None:
        from whatsapp_tech_digest.replay import replay_whatsapp_export

        exported = """[3:12 AM, 9/1/2026] +1 555 010 0001: Photo
yes
[5:43 AM, 9/1/2026] +1 555 010 0002: Hi Team
Do we have a configuration migration tool?
[9/3/2026 11:30 PM] Egor: Please check the physical port MAC address.
"""
        with tempfile.TemporaryDirectory() as directory:
            spool = DurableSpool(Path(directory) / "spool.sqlite3", TARGET)
            first = replay_whatsapp_export(exported, spool, timezone_name="America/Toronto")
            second = replay_whatsapp_export(exported, spool, timezone_name="America/Toronto")

            self.assertEqual(first, second)
            self.assertEqual(len(first), 3)
            self.assertEqual([event["message_id"] for event in first], [
                "replay-v1-ca66b45639a527c6cfa04dc1",
                "replay-v1-4f7d5cf113424181a1ceb6ba",
                "replay-v1-3ea7936c7f1f7c7360031917",
            ])
            self.assertEqual(first[0]["text"], "Photo\nyes")
            self.assertEqual(first[1]["text"], "Hi Team\nDo we have a configuration migration tool?")
            self.assertEqual(first[0]["display_name"], "participant-d9984351")
            self.assertEqual(first[2]["display_name"], "Egor")
            self.assertTrue(all(event["participant"].startswith("replay-") for event in first))
            self.assertTrue(all(event["chat_jid"] == TARGET for event in first))
            self.assertTrue(all(event["timestamp"].endswith("+00:00") for event in first))
            self.assertEqual(len(spool.messages()), 3)

    def test_replay_rejects_text_without_a_whatsapp_header(self) -> None:
        from whatsapp_tech_digest.replay import ReplayFormatError, replay_whatsapp_export

        with tempfile.TemporaryDirectory() as directory:
            spool = DurableSpool(Path(directory) / "spool.sqlite3", TARGET)
            with self.assertRaises(ReplayFormatError):
                replay_whatsapp_export("not a WhatsApp export", spool, timezone_name="America/Toronto")
            self.assertEqual(spool.messages(), [])


if __name__ == "__main__":
    unittest.main()
