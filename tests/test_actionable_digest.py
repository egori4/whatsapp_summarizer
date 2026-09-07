from __future__ import annotations

import json
import unittest

from whatsapp_tech_digest.actionable_render import render_actionable as extracted_render_actionable
from whatsapp_tech_digest.actionable_schema import actionable_schema
from whatsapp_tech_digest.actionable_validate import actionable_source_map
from whatsapp_tech_digest.models import ModelFailure, StaticModel, render_actionable, summarize_actionable


class ActionableDigestTests(unittest.TestCase):
    def sources(self):
        return [
            {
                "message_id": "upgrade-question",
                "change_seq": 1,
                "display_name": "Engineer A",
                "timestamp": "2026-09-01T13:00:00+00:00",
                "text": "Has anyone upgraded Controller KVM from 10.10.6 toward 10.14.0? Intermediate 10.11.0 fails with package exit code 100.",
            },
            {
                "message_id": "upgrade-answer",
                "change_seq": 2,
                "display_name": "Engineer B",
                "timestamp": "2026-09-01T13:02:00+00:00",
                "text": "Upgrade directly to 10.14.0.",
            },
            {
                "message_id": "upgrade-kb",
                "change_seq": 3,
                "display_name": "Engineer C",
                "timestamp": "2026-09-01T13:03:00+00:00",
                "text": "Reference: https://support.example.invalid/answer/1136675",
            },
            {
                "message_id": "mac-question",
                "change_seq": 4,
                "display_name": "Engineer D",
                "timestamp": "2026-09-02T14:00:00+00:00",
                "text": "How can I check stale ARP after replacing the appliance?",
            },
            {
                "message_id": "weak-answer",
                "change_seq": 5,
                "display_name": "Engineer E",
                "timestamp": "2026-09-02T14:01:00+00:00",
                "text": "I think Monitoring may show it.",
            },
            {
                "message_id": "correction",
                "change_seq": 6,
                "display_name": "Engineer D",
                "timestamp": "2026-09-02T14:02:00+00:00",
                "text": "I did not see the MAC address in Monitoring.",
            },
            {
                "message_id": "commands",
                "change_seq": 7,
                "display_name": "Engineer F",
                "timestamp": "2026-09-02T14:03:00+00:00",
                "text": "Inspect ARP with /info/l3/arp/dump and refresh an IP with /oper/garp <ip_address>.",
            },
            {
                "message_id": "thanks",
                "change_seq": 8,
                "display_name": "Engineer D",
                "timestamp": "2026-09-02T14:04:00+00:00",
                "text": "Thanks!",
            },
        ]

    def response(self):
        refs = {
            "upgrade-question": "S001", "upgrade-answer": "S002", "upgrade-kb": "S003",
            "mac-question": "S004", "weak-answer": "S005", "correction": "S006",
            "commands": "S007", "thanks": "S008",
        }
        ref = lambda message_id, seq: refs[message_id]
        return {
            "dispositions": {
                ref("upgrade-question", 1): "INCLUDE",
                ref("upgrade-answer", 2): "INCLUDE",
                ref("upgrade-kb", 3): "INCLUDE",
                ref("mac-question", 4): "INCLUDE",
                ref("weak-answer", 5): "EXCLUDE",
                ref("correction", 6): "CONTEXT",
                ref("commands", 7): "INCLUDE",
                ref("thanks", 8): "EXCLUDE",
            },
            "topics": [
                {
                    "topic": "Controller KVM upgrade",
                    "title": "Direct upgrade path",
                    "source_refs": [ref("upgrade-question", 1), ref("upgrade-answer", 2), ref("upgrade-kb", 3)],
                    "raw_keep_refs": [ref("upgrade-question", 1), ref("upgrade-answer", 2), ref("upgrade-kb", 3)],
                    "question": "Can Controller KVM upgrade directly to 10.14.0 after intermediate-package failures?",
                    "situation": "Intermediate upgrades from 10.10.6 toward 10.14.0 can fail at 10.11.0 with package exit code 100.",
                    "recommendation": "Upgrade directly to 10.14.0.",
                    "specifics": [
                        {"source_ref": ref("upgrade-answer", 2), "value": "10.14.0"},
                    ],
                    "limitation": "",
                    "reference_refs": [ref("upgrade-kb", 3)],
                    "confidence": "field_guidance",
                },
                {
                    "topic": "Appliance replacement",
                    "title": "Stale ARP checks",
                    "source_refs": [ref("mac-question", 4), ref("correction", 6), ref("commands", 7)],
                    "raw_keep_refs": [ref("commands", 7)],
                    "question": "How should stale ARP be checked after an appliance replacement?",
                    "situation": "Use these checks when stale ARP is suspected after appliance replacement.",
                    "recommendation": "Inspect ARP state and send a gratuitous ARP for the affected IP.",
                    "specifics": [
                        {"source_ref": ref("commands", 7), "value": "/info/l3/arp/dump"},
                        {"source_ref": ref("commands", 7), "value": "/oper/garp <ip_address>"},
                    ],
                    "limitation": "The Monitoring suggestion was not confirmed by the requester.",
                    "reference_refs": [],
                    "confidence": "field_guidance",
                },
            ],
            "unanswered": [],
        }

    def test_extracted_actionable_modules_preserve_legacy_render_contract(self):
        response = json.dumps(self.response())
        sources = self.sources()

        self.assertEqual(render_actionable(response, sources), extracted_render_actionable(response, sources))
        self.assertEqual(set(actionable_source_map(sources)), set(self.response()["dispositions"]))
        self.assertEqual(actionable_schema(sources)["required"], ["dispositions", "topics", "unanswered"])

    def test_actionable_schema_does_not_request_locally_derived_attribution_fields(self):
        required = actionable_schema(self.sources())["properties"]["topics"]["items"]["required"]
        self.assertNotIn("reason_kept", required)
        self.assertNotIn("question_refs", required)
        self.assertNotIn("contributor_refs", required)

    def test_unanswered_cannot_reuse_a_source_assigned_to_a_topic(self):
        response = self.response()
        response["unanswered"] = [{
            "topic": "Duplicate question",
            "question_source_ref": "S001",
            "context_refs": [],
            "reason": "The topic source is already represented above.",
        }]
        with self.assertRaisesRegex(ModelFailure, "source belongs to multiple topics"):
            render_actionable(json.dumps(response), self.sources())

    def test_actionable_topic_rejects_duplicate_specifics(self):
        response = self.response()
        response["topics"][0]["specifics"].append({"source_ref": "S002", "value": "10.14.0"})
        with self.assertRaisesRegex(ModelFailure, "duplicate specifics"):
            render_actionable(json.dumps(response), self.sources())

    def test_validated_actionable_result_carries_revision_only_provenance(self):
        result = summarize_actionable(self.sources(), StaticModel(json.dumps(self.response())), StaticModel(json.dumps(self.response())))

        self.assertEqual(result.provenance["schema_version"], "actionable-provenance-v1")
        self.assertEqual(result.provenance["topics"][0]["source_revisions"], [["upgrade-question", 1], ["upgrade-answer", 2], ["upgrade-kb", 3]])
        serialized = json.dumps(result.provenance)
        for source in self.sources():
            self.assertNotIn(source["text"], serialized)

    def test_thread_renderer_emits_a_compact_reader_facing_digest_without_raw_evidence(self):
        rendered = render_actionable(json.dumps(self.response()), self.sources())

        self.assertTrue(rendered.startswith("Technical Updates — Sep 1–2"))
        self.assertNotIn("RAW MESSAGES WORTH KEEPING", rendered)
        self.assertNotIn("Situation:", rendered)
        self.assertNotIn("Recommendation:", rendered)
        self.assertIn("Upgrade directly to 10.14.0.", rendered)
        self.assertIn(
            "Question asked: Can Controller KVM upgrade directly to 10.14.0 after intermediate-package failures?",
            rendered,
        )
        self.assertIn("Question asked: How should stale ARP be checked after an appliance replacement?", rendered)
        self.assertIn("Commands: /info/l3/arp/dump; /oper/garp <ip_address>", rendered)
        self.assertIn("Asked by: Engineer A", rendered)
        self.assertIn("Contributors: Engineer B, Engineer C", rendered)
        self.assertNotIn("I think Monitoring may show it.", rendered)
        self.assertEqual(rendered.count("Upgrade directly to 10.14.0."), 1)

    def test_optional_topic_fields_are_omitted_instead_of_padded(self):
        response = self.response()
        topic = response["topics"][0]
        topic["situation"] = ""
        topic["recommendation"] = ""
        topic["specifics"] = []
        topic["limitation"] = ""
        rendered = render_actionable(json.dumps(response), self.sources())
        block = rendered.split("\n\n")[1]
        self.assertNotIn("Situation:", block)
        self.assertNotIn("Recommendation:", block)
        self.assertNotIn("Commands:", block)
        self.assertNotIn("Limitation:", block)
        self.assertIn("Reference: https://support.example.invalid/answer/1136675", block)

    def test_reader_omits_redundant_field_guidance_and_reference_identifier(self):
        response = self.response()
        response["topics"][0]["specifics"] = [{"source_ref": "S003", "value": "1136675"}]
        response["topics"][0]["limitation"] = "Field guidance only."
        rendered = render_actionable(json.dumps(response), self.sources())
        self.assertNotIn("Key details: 1136675", rendered)
        self.assertNotIn("Field guidance; not formally verified.", rendered)
        self.assertIn("Limitation: The Monitoring suggestion was not confirmed by the requester.", rendered)

    def test_thread_renderer_rejects_invented_version_and_command(self):
        response = self.response()
        response["topics"][0]["recommendation"] = "Upgrade directly to 10.15.0 with /oper/fabricate."
        with self.assertRaisesRegex(ModelFailure, "unsupported protected value.*10\\.15\\.0"):
            render_actionable(json.dumps(response), self.sources())

    def test_thread_question_can_use_topic_context_without_keyword_matching(self):
        sources = [
            {
                "message_id": "q", "change_seq": 1, "display_name": "Engineer A",
                "timestamp": "2026-09-01T13:00:00+00:00", "text": "Is it the same for physical interfaces?",
            },
            {
                "message_id": "a", "change_seq": 2, "display_name": "Engineer B",
                "timestamp": "2026-09-01T13:01:00+00:00", "text": "The same behavior applies to physical interfaces.",
            },
        ]
        response = {
            "dispositions": {"S001": "INCLUDE", "S002": "INCLUDE"},
            "topics": [{
                "topic": "Physical interfaces", "title": "Behavior confirmation",
                "source_refs": ["S001", "S002"], "raw_keep_refs": ["S002"],
                "question": "Does the behavior also apply to physical interfaces?",
                "situation": "", "recommendation": "Apply the same behavior to physical interfaces.",
                "specifics": [], "limitation": "",
                "reference_refs": [],
                "confidence": "confirmed",
            }],
            "unanswered": [],
        }
        self.assertIn("Asked by: Engineer A", render_actionable(json.dumps(response), sources))

    def test_topic_renders_a_concise_source_grounded_question_summary(self):
        sources = [
            {"message_id": "mac", "change_seq": 1, "display_name": "Engineer A", "timestamp": "2026-09-01T13:00:00+00:00", "text": "How can I check the physical-port MAC address without pcap?"},
            {"message_id": "arp", "change_seq": 2, "display_name": "Engineer B", "timestamp": "2026-09-01T13:01:00+00:00", "text": "After replacing an Alteon, can stale ARP entries prevent VIPs from working?"},
            {"message_id": "answer", "change_seq": 3, "display_name": "Engineer C", "timestamp": "2026-09-01T13:02:00+00:00", "text": "Inspect ARP and send GARP for the affected IP."},
        ]
        response = {
            "dispositions": {"S001": "INCLUDE", "S002": "INCLUDE", "S003": "INCLUDE"},
            "topics": [{
                "topic": "Alteon replacement", "title": "Stale ARP recovery",
                "source_refs": ["S001", "S002", "S003"], "raw_keep_refs": ["S003"],
                "question": "Can stale ARP prevent VIPs from working after an Alteon replacement?",
                "situation": "VIPs can fail after appliance replacement because of stale ARP entries.",
                "recommendation": "Inspect ARP and send GARP for the affected IP.",
                "specifics": [], "limitation": "", "reference_refs": [], "confidence": "confirmed",
            }], "unanswered": [],
        }
        rendered = render_actionable(json.dumps(response), sources)
        self.assertIn(
            "Question asked: Can stale ARP prevent VIPs from working after an Alteon replacement?",
            rendered,
        )
        self.assertNotIn("Question asked: How can I check the physical-port MAC address", rendered)

    def test_unanswered_topic_uses_sources_not_assigned_to_a_retained_thread(self):
        sources = [
            {"message_id": "tool", "change_seq": 1, "display_name": "Engineer A", "timestamp": "2026-09-01T13:00:00+00:00", "text": "Use migration tool https://code.example.invalid/migrate."},
            {"message_id": "gap", "change_seq": 2, "display_name": "Engineer B", "timestamp": "2026-09-01T13:01:00+00:00", "text": "How can we migrate historical traffic statistics?"},
        ]
        response = {
            "dispositions": {"S001": "INCLUDE", "S002": "INCLUDE"},
            "topics": [{
                "topic": "Migration utility", "title": "Configuration migration",
                "source_refs": ["S001"], "raw_keep_refs": ["S001"],
                "question": "Which tool migrates configuration?",
                "situation": "", "recommendation": "Use the migration tool.",
                "specifics": [{"source_ref": "S001", "value": "migration tool"}],
                "limitation": "", "reference_refs": ["S001"], "confidence": "documented",
            }],
            "unanswered": [{
                "topic": "Historical traffic migration", "question_source_ref": "S002",
                "context_refs": [], "reason": "No reusable answer was established.",
            }],
        }
        rendered = render_actionable(json.dumps(response), sources)
        self.assertIn("Unanswered / incomplete topics", rendered)
        self.assertIn("How can we migrate historical traffic statistics?", rendered)

    def test_unanswered_may_be_a_technical_issue_statement_without_question_form(self):
        sources = [{
            "message_id": "topic", "change_seq": 1, "display_name": "Engineer A",
            "timestamp": "2026-09-01T13:00:00+00:00",
            "text": "Document packet-reporting configuration before investigating protocol gaps.",
        }, {
            "message_id": "issue", "change_seq": 2, "display_name": "Engineer B",
            "timestamp": "2026-09-01T13:01:00+00:00",
            "text": "Packet reporting differs between IPv4 and IPv6 after the policy change.",
        }]
        response = {
            "dispositions": {"S001": "INCLUDE", "S002": "INCLUDE"},
            "topics": [{
                "topic": "Packet reporting", "title": "Protocol behavior gap",
                "source_refs": ["S001"], "raw_keep_refs": ["S001"],
                "question": "Which packet-reporting configuration should be documented?",
                "situation": "Document packet-reporting configuration before investigating protocol gaps.",
                "recommendation": "Document packet-reporting configuration.", "specifics": [],
                "limitation": "", "reference_refs": [], "confidence": "field_guidance",
            }],
            "unanswered": [{
                "topic": "Packet reporting", "question_source_ref": "S002", "context_refs": [],
                "reason": "No reusable conclusion was established.",
            }],
        }
        rendered = render_actionable(json.dumps(response), sources)
        self.assertIn("Question / unresolved issue: Packet reporting differs", rendered)

    def test_attribution_is_derived_from_thread_locally(self):
        sources = [
            {"message_id": "q", "change_seq": 1, "display_name": "Engineer A", "timestamp": "2026-09-01T13:00:00+00:00", "text": "Which command checks the interface state?"},
            {"message_id": "a", "change_seq": 2, "display_name": "Engineer B", "timestamp": "2026-09-01T13:01:00+00:00", "text": "Use /info/interface/dump."},
        ]
        response = {
            "dispositions": {"S001": "INCLUDE", "S002": "INCLUDE"},
            "topics": [{
                "topic": "Interface state", "title": "Inspection command",
                "source_refs": ["S001", "S002"], "raw_keep_refs": ["S002"],
                "question": "Which command checks interface state?",
                "situation": "", "recommendation": "Use /info/interface/dump.",
                "specifics": [{"source_ref": "S002", "value": "/info/interface/dump"}],
                "limitation": "", "reference_refs": [], "confidence": "confirmed",
            }], "unanswered": [],
        }
        rendered = render_actionable(json.dumps(response), sources)
        self.assertIn("Asked by: Engineer A", rendered)
        self.assertIn("Contributors: Engineer B", rendered)

    def test_specifics_are_canonicalized_to_exact_protected_source_values(self):
        response = self.response()
        response["topics"][1]["specifics"][0]["value"] = "Run /info/l3/arp/dump now"
        rendered = render_actionable(json.dumps(response), self.sources())
        self.assertIn("Commands: /info/l3/arp/dump; /oper/garp <ip_address>", rendered)
        self.assertNotIn("Run /info/l3/arp/dump now", rendered)

    def test_specific_reference_must_already_belong_to_its_topic(self):
        response = self.response()
        response["topics"][1]["source_refs"].remove("S007")
        response["topics"][1]["raw_keep_refs"] = ["S004"]
        with self.assertRaisesRegex(ModelFailure, "specific source ref is outside topic"):
            render_actionable(json.dumps(response), self.sources())

    def test_exact_specific_phrase_is_reduced_to_its_protected_command(self):
        response = self.response()
        response["topics"][1]["specifics"][0]["value"] = "Inspect ARP with /info/l3/arp/dump"
        rendered = render_actionable(json.dumps(response), self.sources())
        self.assertIn("Commands: /info/l3/arp/dump; /oper/garp <ip_address>", rendered)
        self.assertNotIn("Commands: Inspect ARP with", rendered)

    def test_context_source_can_ground_a_specific_without_being_raw_kept(self):
        response = self.response()
        response["dispositions"]["S002"] = "CONTEXT"
        response["topics"][0]["raw_keep_refs"].remove("S002")
        rendered = render_actionable(json.dumps(response), self.sources())
        self.assertIn("Upgrade directly to 10.14.0.", rendered)
        self.assertNotIn("Key details: 10.14.0", rendered)

    def test_unreferenced_nonexcluded_disposition_is_safely_ignored(self):
        response = self.response()
        response["dispositions"]["S008"] = "CONTEXT"
        rendered = render_actionable(json.dumps(response), self.sources())
        self.assertNotIn("Thanks!", rendered)

    def test_actionable_failure_reports_bounded_local_validation_reason(self):
        invalid = StaticModel('{"wrong":"shape"}')
        with self.assertRaisesRegex(ModelFailure, "actionable final schema drift"):
            summarize_actionable(self.sources(), invalid, invalid)

    def test_actionable_summary_repairs_one_invalid_candidate_once(self):
        fallback = StaticModel(json.dumps(self.response()))
        result = summarize_actionable(self.sources(), StaticModel("not JSON"), fallback)
        self.assertIn("Technical Updates", result.text)
        self.assertTrue(result.degraded)


if __name__ == "__main__":
    unittest.main()
