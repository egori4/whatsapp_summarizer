from __future__ import annotations

import json
import re
import tempfile
import unittest
from datetime import date
from pathlib import Path

from whatsapp_tech_digest.actionable_render import actionable_provenance, render_actionable
from whatsapp_tech_digest.models import stage_zero
from whatsapp_tech_digest.spool import DurableSpool


TARGET = "10000000-00000002@g.us"
FIXTURE = Path(__file__).parent / "fixtures" / "phase3_semantic_pilot.json"


class Phase3SemanticPilotTests(unittest.TestCase):
    def test_three_reviewed_daily_candidates_meet_phase3_contract(self) -> None:
        fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.assertEqual(fixture["source"], "fully_synthetic_deidentified_semantic_fixture")
        self.assertEqual(len(fixture["days"]), 3)
        serialized = json.dumps(fixture)
        self.assertNotRegex(serialized, r"(?:@g\.us|@s\.whatsapp\.net|https?://|\+?\d{10,})")

        with tempfile.TemporaryDirectory() as directory:
            spool = DurableSpool(Path(directory) / "spool.sqlite3", TARGET)
            self.addCleanup(spool.close)
            rendered_days = []

            for day in fixture["days"]:
                for source in day["events"]:
                    spool.append_message({
                        **source,
                        "chat_jid": TARGET,
                        "participant": "synthetic-reviewer",
                    })
                snapshot = spool.snapshot()
                pending, run_type, omission = spool.pending_events(snapshot)
                self.assertIsNone(omission)
                pending_revisions = {
                    (str(item["message_id"]), int(item["change_seq"])) for item in pending
                }
                window_timestamps = [item["timestamp"] for item in pending]
                sources = [
                    *[
                        {
                            **item,
                            "carried_forward": True,
                            "tracked_item": True,
                            "digest_window_timestamps": window_timestamps,
                        }
                        for item in spool.carry_forward_events(snapshot)
                        if (str(item["message_id"]), int(item["change_seq"])) not in pending_revisions
                    ],
                    *pending,
                ]
                selected = [
                    item for item in stage_zero(sources)
                    if not item.get("mechanical_ack") and not item.get("untrusted_policy_override")
                ]
                response = json.dumps(day["candidate"])
                rendered = render_actionable(response, selected)
                provenance = actionable_provenance(response, selected)
                rendered_days.append(rendered)

                actual_titles = [
                    line.removeprefix("## ")
                    for line in rendered.splitlines()
                    if line.startswith("## ")
                ]
                self.assertEqual(actual_titles, day["expect"]["titles"])
                self.assertTrue(all(
                    title == " ".join(title.split())
                    and not re.search(r"[_{}\[\]<>]|\bS\d{3}\b", title)
                    for title in actual_titles
                ))
                for expected in day["expect"]["included_text"]:
                    self.assertIn(expected, rendered)
                for excluded in day["expect"]["excluded_text"]:
                    self.assertNotIn(excluded, rendered)
                current_day = date.fromisoformat(day["day"])
                self.assertEqual(
                    rendered.splitlines()[0],
                    f"# Technical Updates — {current_day.strftime('%b')} {current_day.day}",
                )

                spool.record_run(
                    snapshot,
                    run_type,
                    "accepted",
                    output=rendered,
                    source_count=len(sources),
                    provenance=provenance,
                )
                states = {
                    state["message_id"]: state["status"]
                    for state in spool.question_states()
                }
                self.assertEqual(states, day["expect"]["question_states"])

            self.assertEqual(len(rendered_days), 3)
            self.assertTrue(all("## " in rendered for rendered in rendered_days))


if __name__ == "__main__":
    unittest.main()
