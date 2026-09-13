from __future__ import annotations

import json
import unittest

from whatsapp_tech_digest.actionable_render import render_actionable as extracted_render_actionable
from whatsapp_tech_digest.actionable_schema import actionable_schema
from whatsapp_tech_digest.actionable_validate import actionable_source_map, protected_values
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
                    "question": "Has anyone upgraded Controller KVM from 10.10.6 toward 10.14.0?",
                    "question_source_ref": ref("upgrade-question", 1),
                    "question_source_kind": "QUESTION",
                    "situation": "Intermediate 10.11.0 fails with package exit code 100.",
                    "recommendation": "Upgrade directly to 10.14.0.",
                    "actions": [],
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
                    "question": "How can I check stale ARP after replacing the appliance?",
                    "question_source_ref": ref("mac-question", 4),
                    "question_source_kind": "QUESTION",
                    "situation": "",
                    "recommendation": "",
                    "actions": [],
                    "specifics": [
                        {"source_ref": ref("commands", 7), "value": "/info/l3/arp/dump"},
                        {"source_ref": ref("commands", 7), "value": "/oper/garp <ip_address>"},
                    ],
                    "limitation": "I did not see the MAC address in Monitoring.",
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
        self.assertIn("question_source_ref", required)
        self.assertIn("question_source_kind", required)
        unanswered_required = actionable_schema(self.sources())["properties"]["unanswered"]["items"]["required"]
        self.assertIn("question_source_kind", unanswered_required)

    def test_unanswered_cannot_reuse_a_source_assigned_to_a_topic(self):
        response = self.response()
        response["unanswered"] = [{
            "topic": "Duplicate question",
            "question_source_ref": "S001",
            "question_source_kind": "QUESTION",
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

        self.assertTrue(rendered.startswith("# Technical Updates — Sep 1–2"))
        self.assertNotIn("RAW MESSAGES WORTH KEEPING", rendered)
        self.assertNotIn("Situation:", rendered)
        self.assertNotIn("Recommendation:", rendered)
        self.assertIn("Upgrade directly to 10.14.0.", rendered)
        self.assertIn(
            "Question asked: Has anyone upgraded Controller KVM from 10.10.6 toward 10.14.0?",
            rendered,
        )
        self.assertIn("Question asked: How can I check stale ARP after replacing the appliance?", rendered)
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

    def test_administrative_announcement_cannot_render_an_invented_question(self):
        sources = [{
            "message_id": "announcement", "change_seq": 1, "display_name": "Coordinator",
            "timestamp": "2026-09-01T13:00:00+00:00",
            "text": "The regional call is cancelled. Please use the time to catch up and raise urgent findings.",
        }]
        response = {
            "dispositions": {"S001": "INCLUDE"},
            "topics": [{
                "topic": "Regional call", "title": "Regional Call Cancelled",
                "source_refs": ["S001"], "raw_keep_refs": ["S001"],
                "question": "", "question_source_ref": None, "question_source_kind": None,
                "situation": "The regional call is cancelled.",
                "recommendation": "Please use the time to catch up and raise urgent findings.",
                "actions": [],
                "specifics": [], "limitation": "", "reference_refs": [], "confidence": "confirmed",
            }],
            "unanswered": [],
        }

        rendered = render_actionable(json.dumps(response), sources)

        self.assertIn("Regional Call Cancelled", rendered)
        self.assertNotIn("Question asked:", rendered)
        self.assertNotIn("Asked by:", rendered)

    def test_administrative_announcement_preserves_direct_assignments_and_reports_source(self):
        sources = [
            {
                "message_id": "planning", "change_seq": 1, "display_name": "Coordinator",
                "timestamp": "2026-09-01T13:00:00+00:00",
                "text": "The training schedule and topics are taking shape from team suggestions. Questions? They may be collected later. Detailed subjects will be published closer to the dates.",
            },
            {
                "message_id": "ai", "change_seq": 2, "display_name": "Coordinator",
                "timestamp": "2026-09-01T13:01:00+00:00",
                "text": "The AI Team was specifically asked to lead an AI-tools session for daily and complex tasks and should receive most of the presentation time. Other regional members may volunteer.",
            },
        ]
        response = {
            "dispositions": {"S001": "INCLUDE", "S002": "INCLUDE"},
            "topics": [{
                "topic": "Training planning", "title": "Training Planning and Presenter Nominations",
                "source_refs": ["S001", "S002"], "raw_keep_refs": ["S001", "S002"],
                "question": "", "question_source_ref": None, "question_source_kind": None,
                "situation": "The training schedule and topics are taking shape from team suggestions.",
                "recommendation": "", "actions": [
                    "The AI Team was specifically asked to lead an AI-tools session for daily and complex tasks and should receive most of the presentation time.",
                    "Other regional members may volunteer.",
                ],
                "specifics": [], "limitation": "", "reference_refs": [], "confidence": "confirmed",
            }],
            "unanswered": [],
        }

        rendered = render_actionable(json.dumps(response), sources)

        self.assertIn("## Training Planning and Presenter Nominations", rendered)
        self.assertIn("Actions / follow-up:", rendered)
        self.assertIn("- The AI Team was specifically asked to lead an AI-tools session", rendered)
        self.assertIn("- Other regional members may volunteer.", rendered)
        self.assertIn("Reported by: Coordinator", rendered)
        self.assertNotIn("Asked by:", rendered)
        self.assertNotIn("Contributors:", rendered)

    def test_manager_critical_cancellation_cannot_be_silently_excluded(self):
        sources = [
            {
                "message_id": "regional-cancel", "change_seq": 1,
                "display_name": "Coordinator", "timestamp": "2026-09-01T13:00:00+00:00",
                "text": "The regional reliability call is cancelled.",
            },
            {
                "message_id": "slides-action", "change_seq": 2,
                "display_name": "Coordinator", "timestamp": "2026-09-01T13:01:00+00:00",
                "text": "Prepare the reliability slides before next week.",
            },
        ]
        response = {
            "dispositions": {"S001": "EXCLUDE", "S002": "INCLUDE"},
            "topics": [{
                "topic": "Reliability slides", "title": "Reliability slides",
                "source_refs": ["S002"], "raw_keep_refs": ["S002"],
                "question": "", "question_source_ref": None, "question_source_kind": None,
                "situation": "", "recommendation": "",
                "actions": ["Prepare the reliability slides before next week."],
                "specifics": [], "limitation": "", "reference_refs": [],
                "confidence": "confirmed",
            }],
            "unanswered": [],
        }

        with self.assertRaisesRegex(ModelFailure, "status change"):
            render_actionable(json.dumps(response), sources)

    def test_unanswered_administrative_question_is_not_forced_into_an_update(self):
        sources = [
            {
                "message_id": "schedule-question", "change_seq": 1,
                "display_name": "Engineer A", "timestamp": "2026-09-01T13:00:00+00:00",
                "text": "Anyone know if the API training session is still scheduled for Friday?",
            },
        ]
        response = {
            "dispositions": {"S001": "INCLUDE"},
            "topics": [],
            "unanswered": [{
                "topic": "Training schedule", "question_source_ref": "S001",
                "question_source_kind": "QUESTION", "context_refs": [],
                "reason": "No answer was given.",
            }],
        }

        rendered = render_actionable(json.dumps(response), sources)

        self.assertIn("Anyone know if the API training session is still scheduled for Friday?", rendered)

    def test_speculative_administrative_chatter_may_be_excluded(self):
        sources = [
            {
                "message_id": "rumour", "change_seq": 1,
                "display_name": "Engineer A", "timestamp": "2026-09-01T13:00:00+00:00",
                "text": "Maybe the maintenance window got rescheduled again, who knows.",
            },
            {
                "message_id": "slides-action", "change_seq": 2,
                "display_name": "Coordinator", "timestamp": "2026-09-01T13:01:00+00:00",
                "text": "Prepare the reliability slides before next week.",
            },
        ]
        response = {
            "dispositions": {"S001": "EXCLUDE", "S002": "INCLUDE"},
            "topics": [{
                "topic": "Reliability slides", "title": "Reliability slides",
                "source_refs": ["S002"], "raw_keep_refs": ["S002"],
                "question": "", "question_source_ref": None, "question_source_kind": None,
                "situation": "", "recommendation": "",
                "actions": ["Prepare the reliability slides before next week."],
                "specifics": [], "limitation": "", "reference_refs": [],
                "confidence": "confirmed",
            }],
            "unanswered": [],
        }

        rendered = render_actionable(json.dumps(response), sources)

        self.assertIn("Prepare the reliability slides before next week.", rendered)

    def test_manager_critical_clause_survives_unrelated_trailing_chatter(self):
        sources = [
            {
                "message_id": "deadline", "change_seq": 1,
                "display_name": "Coordinator", "timestamp": "2026-09-01T13:00:00+00:00",
                "text": "Our deployment review meeting is confirmed, by the way the coffee machine is broken.",
            },
        ]
        response = {
            "dispositions": {"S001": "INCLUDE"},
            "topics": [{
                "topic": "Deployment review", "title": "Deployment review",
                "source_refs": ["S001"], "raw_keep_refs": ["S001"],
                "question": "", "question_source_ref": None, "question_source_kind": None,
                "situation": "Our deployment review meeting is confirmed", "recommendation": "",
                "actions": [], "specifics": [], "limitation": "", "reference_refs": [],
                "confidence": "confirmed",
            }],
            "unanswered": [],
        }

        rendered = render_actionable(json.dumps(response), sources)

        self.assertIn("Our deployment review meeting is confirmed", rendered)
        self.assertNotIn("coffee machine", rendered)

    def test_status_change_is_retained_without_requiring_verbatim_quotation(self):
        sources = [
            {
                "message_id": "regional-cancel", "change_seq": 1,
                "display_name": "Coordinator", "timestamp": "2026-09-01T13:00:00+00:00",
                "text": "The regional reliability call is cancelled.",
            },
            {
                "message_id": "slides-action", "change_seq": 2,
                "display_name": "Coordinator", "timestamp": "2026-09-01T13:01:00+00:00",
                "text": "Prepare the reliability slides before next week.",
            },
        ]
        response = {
            "dispositions": {"S001": "INCLUDE", "S002": "INCLUDE"},
            "topics": [{
                "topic": "Reliability slides", "title": "Reliability slides",
                "source_refs": ["S001", "S002"], "raw_keep_refs": ["S001", "S002"],
                "question": "", "question_source_ref": None, "question_source_kind": None,
                "situation": "", "recommendation": "",
                "actions": ["Prepare the reliability slides before next week."],
                "specifics": [], "limitation": "", "reference_refs": [],
                "confidence": "confirmed",
            }],
            "unanswered": [],
        }

        rendered = render_actionable(json.dumps(response), sources)

        self.assertIn("Prepare the reliability slides before next week.", rendered)

    def test_status_change_outside_the_technical_vocabulary_cannot_be_excluded(self):
        sources = [
            {
                "message_id": "standup-cancel", "change_seq": 1,
                "display_name": "Coordinator", "timestamp": "2026-09-01T13:00:00+00:00",
                "text": "The Friday standup is cancelled.",
            },
            {
                "message_id": "slides-action", "change_seq": 2,
                "display_name": "Coordinator", "timestamp": "2026-09-01T13:01:00+00:00",
                "text": "Prepare the reliability slides before next week.",
            },
        ]
        response = {
            "dispositions": {"S001": "EXCLUDE", "S002": "INCLUDE"},
            "topics": [{
                "topic": "Reliability slides", "title": "Reliability slides",
                "source_refs": ["S002"], "raw_keep_refs": ["S002"],
                "question": "", "question_source_ref": None, "question_source_kind": None,
                "situation": "", "recommendation": "",
                "actions": ["Prepare the reliability slides before next week."],
                "specifics": [], "limitation": "", "reference_refs": [],
                "confidence": "confirmed",
            }],
            "unanswered": [],
        }

        with self.assertRaisesRegex(ModelFailure, "status change"):
            render_actionable(json.dumps(response), sources)

    def test_reader_omits_redundant_field_guidance_and_reference_identifier(self):
        response = self.response()
        response["topics"][0]["specifics"] = [{"source_ref": "S003", "value": "1136675"}]
        response["topics"][0]["limitation"] = "Intermediate 10.11.0 fails with package exit code 100."
        rendered = render_actionable(json.dumps(response), self.sources())
        self.assertNotIn("Key details: 1136675", rendered)
        self.assertNotIn("Field guidance; not formally verified.", rendered)
        self.assertIn("Limitation: I did not see the MAC address in Monitoring.", rendered)

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
                "question": "Is it the same for physical interfaces?",
                "question_source_ref": "S001",
                "question_source_kind": "QUESTION",
                "situation": "", "recommendation": "The same behavior applies to physical interfaces.",
                "actions": [],
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
                "question": "After replacing an Alteon, can stale ARP entries prevent VIPs from working?",
                "question_source_ref": "S002", "question_source_kind": "QUESTION",
                "situation": "",
                "recommendation": "Inspect ARP and send GARP for the affected IP.",
                "actions": [],
                "specifics": [], "limitation": "", "reference_refs": [], "confidence": "confirmed",
            }], "unanswered": [],
        }
        rendered = render_actionable(json.dumps(response), sources)
        self.assertIn(
            "Question asked: After replacing an Alteon, can stale ARP entries prevent VIPs from working?",
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
                "question": "", "question_source_ref": None, "question_source_kind": None,
                "situation": "", "recommendation": "Use migration tool https://code.example.invalid/migrate.",
                "actions": [],
                "specifics": [{"source_ref": "S001", "value": "migration tool"}],
                "limitation": "", "reference_refs": ["S001"], "confidence": "documented",
            }],
            "unanswered": [{
                "topic": "Historical traffic migration", "question_source_ref": "S002", "question_source_kind": "QUESTION",
                "context_refs": [], "reason": "No reusable answer was established.",
            }],
        }
        rendered = render_actionable(json.dumps(response), sources)
        self.assertIn("Unanswered / incomplete topics", rendered)
        self.assertIn("How can we migrate historical traffic statistics?", rendered)

    def test_unanswered_omits_numeric_sender_metadata(self):
        sources = [{
            "message_id": "answer", "change_seq": 1, "display_name": "Engineer B",
            "timestamp": "2026-09-01T13:00:00+00:00", "text": "Use /info/interface/dump.",
        }, {
            "message_id": "q", "change_seq": 2, "participant": "19995550123@s.whatsapp.net",
            "display_name": "19995550123", "timestamp": "2026-09-01T13:01:00+00:00",
            "text": "Which command checks the interface state?",
        }]
        response = {
            "dispositions": {"S001": "INCLUDE", "S002": "INCLUDE"},
            "topics": [{
                "topic": "Interface command", "title": "Inspection command",
                "source_refs": ["S001"], "raw_keep_refs": ["S001"],
                "question": "", "question_source_ref": None, "question_source_kind": None,
                "situation": "", "recommendation": "Use /info/interface/dump.",
                "actions": [],
                "specifics": [{"source_ref": "S001", "value": "/info/interface/dump"}],
                "limitation": "", "reference_refs": [], "confidence": "confirmed",
            }],
            "unanswered": [{
                "topic": "Interface state", "question_source_ref": "S002", "question_source_kind": "QUESTION", "context_refs": [],
                "reason": "No reusable answer was established.",
            }],
        }
        rendered = render_actionable(json.dumps(response), sources)
        self.assertNotIn("Asked by:", rendered)
        self.assertNotIn("19995550123", rendered)

    def test_unanswered_accepts_an_independently_detected_technical_issue(self):
        sources = [{
            "message_id": "issue", "change_seq": 1, "display_name": "Engineer A",
            "timestamp": "2026-09-01T13:00:00+00:00",
            "text": "The deployment behavior fails after enabling the policy, but no resolution was established.",
        }, {
            "message_id": "context", "change_seq": 2, "display_name": "Engineer B",
            "timestamp": "2026-09-01T13:01:00+00:00",
            "text": "The deployment was reviewed during the session.",
        }]
        response = {
            "dispositions": {"S001": "INCLUDE", "S002": "INCLUDE"},
            "topics": [{
                "topic": "Deployment review", "title": "Observed behavior",
                "source_refs": ["S002"], "raw_keep_refs": ["S002"],
                "question": "", "question_source_ref": None, "question_source_kind": None,
                "situation": "The deployment was reviewed during the session.",
                "recommendation": "", "actions": [], "specifics": [], "limitation": "",
                "reference_refs": [], "confidence": "field_guidance",
            }],
            "unanswered": [{
                "topic": "Unresolved observed behavior", "question_source_ref": "S001", "question_source_kind": "ISSUE",
                "context_refs": [], "reason": "No reusable resolution was established.",
            }],
        }
        rendered = render_actionable(json.dumps(response), sources)
        self.assertIn("Unanswered / incomplete topics", rendered)
        self.assertIn("The deployment behavior fails after enabling the policy", rendered)

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
                "question": "", "question_source_ref": None, "question_source_kind": None,
                "situation": "Document packet-reporting configuration before investigating protocol gaps.",
                "recommendation": "", "specifics": [],
                "actions": [],
                "limitation": "", "reference_refs": [], "confidence": "field_guidance",
            }],
            "unanswered": [{
                "topic": "Packet reporting", "question_source_ref": "S002", "question_source_kind": "ISSUE", "context_refs": [],
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
                "question": "Which command checks the interface state?",
                "question_source_ref": "S001",
                "question_source_kind": "QUESTION",
                "situation": "", "recommendation": "Use /info/interface/dump.",
                "actions": [],
                "specifics": [{"source_ref": "S002", "value": "/info/interface/dump"}],
                "limitation": "", "reference_refs": [], "confidence": "confirmed",
            }], "unanswered": [],
        }
        rendered = render_actionable(json.dumps(response), sources)
        self.assertIn("Asked by: Engineer A", rendered)
        self.assertIn("Contributors: Engineer B", rendered)

    def test_attribution_omits_numeric_sender_metadata(self):
        sources = [
            {
                "message_id": "q", "change_seq": 1, "participant": "19995550123@s.whatsapp.net",
                "display_name": "19995550123", "timestamp": "2026-09-01T13:00:00+00:00",
                "text": "Which command checks the interface state?",
            },
            {
                "message_id": "a", "change_seq": 2, "display_name": "Engineer B",
                "timestamp": "2026-09-01T13:01:00+00:00", "text": "Use /info/interface/dump.",
            },
        ]
        response = {
            "dispositions": {"S001": "INCLUDE", "S002": "INCLUDE"},
            "topics": [{
                "topic": "Interface state", "title": "Inspection command",
                "source_refs": ["S001", "S002"], "raw_keep_refs": ["S002"],
                "question": "Which command checks the interface state?",
                "question_source_ref": "S001", "question_source_kind": "QUESTION",
                "situation": "", "recommendation": "Use /info/interface/dump.",
                "actions": [],
                "specifics": [{"source_ref": "S002", "value": "/info/interface/dump"}],
                "limitation": "", "reference_refs": [], "confidence": "confirmed",
            }], "unanswered": [],
        }
        rendered = render_actionable(json.dumps(response), sources)
        self.assertNotIn("Asked by:", rendered)
        self.assertNotIn("19995550123", rendered)
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

    def test_unanswered_rejects_context_disposition_for_question_or_context_refs(self):
        response = self.response()
        response["dispositions"]["S005"] = "CONTEXT"
        response["unanswered"] = [{
            "topic": "Unresolved behavior", "question_source_ref": "S005",
            "question_source_kind": "ISSUE", "context_refs": ["S008"],
            "reason": "No reusable resolution was established.",
        }]
        with self.assertRaisesRegex(ModelFailure, "unanswered refs must be included or uncertain"):
            render_actionable(json.dumps(response), self.sources())

    def test_unanswered_rejects_question_ref_repeated_as_context(self):
        response = self.response()
        response["dispositions"]["S005"] = "INCLUDE"
        response["unanswered"] = [{
            "topic": "Unresolved behavior", "question_source_ref": "S005",
            "question_source_kind": "ISSUE", "context_refs": ["S005"],
            "reason": "No reusable resolution was established.",
        }]
        with self.assertRaisesRegex(ModelFailure, "unanswered question ref cannot be context"):
            render_actionable(json.dumps(response), self.sources())

    def test_topic_question_requires_a_semantic_question_citation(self):
        response = self.response()
        response["topics"][0]["question_source_ref"] = None
        with self.assertRaisesRegex(ModelFailure, "question must cite a source with semantic kind QUESTION"):
            render_actionable(json.dumps(response), self.sources())

    def test_administrative_source_cannot_be_cited_as_a_question(self):
        sources = [{
            "message_id": "announcement", "change_seq": 1, "display_name": "Coordinator",
            "timestamp": "2026-09-01T13:00:00+00:00",
            "text": "The regional call is cancelled. Prepare the slides before next week.",
        }]
        response = {
            "dispositions": {"S001": "INCLUDE"},
            "topics": [{
                "topic": "Regional call", "title": "Regional Call Cancelled",
                "source_refs": ["S001"], "raw_keep_refs": ["S001"],
                "question": "Should the regional call be cancelled?", "question_source_ref": "S001",
                "question_source_kind": "ANNOUNCEMENT", "situation": "The regional call is cancelled.",
                "recommendation": "Prepare the slides before next week.",
                "actions": [],
                "specifics": [], "limitation": "", "reference_refs": [], "confidence": "confirmed",
            }],
            "unanswered": [],
        }
        with self.assertRaisesRegex(ModelFailure, "question source must have semantic kind QUESTION"):
            render_actionable(json.dumps(response), sources)

    def test_reader_facing_model_text_rejects_opaque_mention_placeholders(self):
        for placeholder in ("[mentioned participant]", "[participant identifier]"):
            with self.subTest(placeholder=placeholder):
                response = self.response()
                response["topics"][0]["actions"] = [f"{placeholder} should prepare the slides."]
                with self.assertRaisesRegex(ModelFailure, "reader-facing identity placeholder"):
                    render_actionable(json.dumps(response), self.sources())

    def test_reader_can_use_the_locally_sanitized_exact_mention_excerpt(self):
        sources = [{
            "message_id": "assignment", "change_seq": 1, "display_name": "Coordinator",
            "timestamp": "2026-09-01T13:00:00+00:00",
            "text": "@123456789 should prepare the slides.",
            "redacted_text": "[mentioned participant] should prepare the slides.",
        }]
        response = {
            "dispositions": {"S001": "INCLUDE"}, "unanswered": [],
            "topics": [{
                "topic": "Presentation", "title": "Presentation assignment",
                "source_refs": ["S001"], "raw_keep_refs": ["S001"],
                "question": "", "question_source_ref": None, "question_source_kind": None,
                "situation": "", "recommendation": "", "actions": ["a participant should prepare the slides."],
                "specifics": [], "limitation": "", "reference_refs": [], "confidence": "confirmed",
            }],
        }
        rendered = render_actionable(json.dumps(response), sources)
        self.assertIn("a participant should prepare the slides.", rendered)
        self.assertNotIn("123456789", rendered)
        self.assertNotIn("[mentioned participant]", rendered)

    def test_topic_requires_actions_field_to_match_schema(self):
        response = self.response()
        response["topics"][0].pop("actions", None)
        with self.assertRaisesRegex(ModelFailure, "actionable topic schema drift"):
            render_actionable(json.dumps(response), self.sources())

    def test_unanswered_source_text_neutralizes_opaque_mentions(self):
        sources = [
            {"message_id": "issue", "change_seq": 1, "display_name": "Engineer A", "timestamp": "2026-09-01T13:00:00+00:00", "text": "Which CLI behavior should @123456789 inspect?"},
            {"message_id": "update", "change_seq": 2, "display_name": "Engineer B", "timestamp": "2026-09-01T13:01:00+00:00", "text": "Use /info/interface/dump."},
        ]
        response = {
            "dispositions": {"S001": "INCLUDE", "S002": "INCLUDE"},
            "topics": [{
                "topic": "Inspection", "title": "Inspection command", "source_refs": ["S002"],
                "raw_keep_refs": ["S002"], "question": "", "question_source_ref": None,
                "question_source_kind": None, "situation": "", "recommendation": "Use /info/interface/dump.",
                "actions": [], "specifics": [{"source_ref": "S002", "value": "/info/interface/dump"}],
                "limitation": "", "reference_refs": [], "confidence": "confirmed",
            }],
            "unanswered": [{"topic": "Unresolved behavior", "question_source_ref": "S001", "question_source_kind": "QUESTION", "context_refs": [], "reason": "No reusable conclusion was established."}],
        }
        rendered = render_actionable(json.dumps(response), sources)
        self.assertNotIn("@123456789", rendered)
        self.assertIn("a participant", rendered)
        self.assertIn("/info/interface/dump", rendered)

    def test_unanswered_source_text_neutralizes_redacted_mention_placeholder(self):
        sources = [
            {"message_id": "issue", "change_seq": 1, "display_name": "Engineer A", "timestamp": "2026-09-01T13:00:00+00:00", "text": "Which CLI behavior should someone inspect?", "redacted_text": "Which CLI behavior should [mentioned participant] inspect?"},
            {"message_id": "update", "change_seq": 2, "display_name": "Engineer B", "timestamp": "2026-09-01T13:01:00+00:00", "text": "Use /info/interface/dump."},
        ]
        response = {
            "dispositions": {"S001": "INCLUDE", "S002": "INCLUDE"},
            "topics": [{
                "topic": "Inspection", "title": "Inspection command", "source_refs": ["S002"],
                "raw_keep_refs": ["S002"], "question": "", "question_source_ref": None,
                "question_source_kind": None, "situation": "", "recommendation": "Use /info/interface/dump.",
                "actions": [], "specifics": [{"source_ref": "S002", "value": "/info/interface/dump"}],
                "limitation": "", "reference_refs": [], "confidence": "confirmed",
            }],
            "unanswered": [{"topic": "Unresolved behavior", "question_source_ref": "S001", "question_source_kind": "QUESTION", "context_refs": [], "reason": "No reusable conclusion was established."}],
        }
        rendered = render_actionable(json.dumps(response), sources)
        self.assertNotIn("[mentioned participant]", rendered)
        self.assertIn("a participant", rendered)

    def test_reader_facing_model_text_rejects_numeric_opaque_mentions(self):
        response = self.response()
        response["topics"][0]["actions"] = ["@123456789 should prepare the slides."]
        with self.assertRaisesRegex(ModelFailure, "reader-facing identity placeholder"):
            render_actionable(json.dumps(response), self.sources())

    def test_reader_facing_model_text_rejects_participant_jids(self):
        response = self.response()
        response["topics"][0]["actions"] = ["15551234567@s.whatsapp.net should prepare the slides."]
        with self.assertRaisesRegex(ModelFailure, "reader-facing identity placeholder"):
            render_actionable(json.dumps(response), self.sources())

    def test_unanswered_source_text_neutralizes_participant_jids(self):
        sources = [{
            "message_id": "question", "change_seq": 1, "display_name": "Engineer A",
            "timestamp": "2026-09-01T13:00:00+00:00",
            "text": "Which CLI behavior should 15551234567@s.whatsapp.net inspect?",
        }]
        response = {
            "dispositions": {"S001": "INCLUDE"}, "topics": [],
            "unanswered": [{
                "topic": "cli-behavior", "question_source_ref": "S001",
                "question_source_kind": "QUESTION", "context_refs": [],
                "reason": "No reusable answer was established.",
            }],
        }
        rendered = render_actionable(json.dumps(response), sources)
        self.assertNotIn("15551234567", rendered)
        self.assertNotIn("s.whatsapp.net", rendered)
        self.assertIn("a participant", rendered)

    def test_unanswered_source_text_neutralizes_a_jid_before_sentence_punctuation(self):
        sources = [{
            "message_id": "issue", "change_seq": 1, "display_name": "Engineer A",
            "timestamp": "2026-09-01T13:00:00+00:00",
            "text": "The DNS sync error persists, ping 15550000004@s.whatsapp.net.",
        }]
        response = {
            "dispositions": {"S001": "INCLUDE"}, "topics": [],
            "unanswered": [{
                "topic": "dns-sync", "question_source_ref": "S001",
                "question_source_kind": "ISSUE", "context_refs": [],
                "reason": "No reusable resolution was established.",
            }],
        }
        rendered = render_actionable(json.dumps(response), sources)
        self.assertNotIn("15550000004", rendered)
        self.assertNotIn("s.whatsapp.net", rendered)
        self.assertIn("a participant", rendered)

    def test_internal_unanswered_reason_does_not_trigger_reader_identity_validation(self):
        sources = [{
            "message_id": "question", "change_seq": 1, "display_name": "Engineer A",
            "timestamp": "2026-09-01T13:00:00+00:00",
            "text": "Which CLI command checks interface state?",
        }]
        response = {
            "dispositions": {"S001": "INCLUDE"}, "topics": [],
            "unanswered": [{
                "topic": "interface-state", "question_source_ref": "S001",
                "question_source_kind": "QUESTION", "context_refs": [],
                "reason": "Ask @15550000005 for context.",
            }],
        }
        rendered = render_actionable(json.dumps(response), sources)
        self.assertNotIn("15550000005", rendered)
        self.assertIn("No reusable answer was established", rendered)

    def test_reader_rejects_an_unsupported_topic_narrative(self):
        sources = [{
            "message_id": "announcement", "change_seq": 1, "display_name": "Coordinator",
            "timestamp": "2026-09-01T13:00:00+00:00",
            "text": "The regional call is cancelled.",
        }]
        response = {
            "dispositions": {"S001": "INCLUDE"},
            "topics": [{
                "topic": "Migration planning", "title": "Migration workshop",
                "source_refs": ["S001"], "raw_keep_refs": ["S001"],
                "question": "", "question_source_ref": None, "question_source_kind": None,
                "situation": "A migration workshop was scheduled for next week.",
                "recommendation": "", "actions": [], "specifics": [], "limitation": "",
                "reference_refs": [], "confidence": "confirmed",
            }],
            "unanswered": [],
        }

        with self.assertRaisesRegex(ModelFailure, "unsupported narrative"):
            render_actionable(json.dumps(response), sources)

    def test_reader_rejects_an_unsupported_technical_identifier(self):
        sources = [{
            "message_id": "migration", "change_seq": 1, "display_name": "Coordinator",
            "timestamp": "2026-09-01T13:00:00+00:00",
            "text": "The DPX500 migration session was rescheduled for next week.",
        }]
        response = {
            "dispositions": {"S001": "INCLUDE"},
            "topics": [{
                "topic": "Migration planning", "title": "Migration session",
                "source_refs": ["S001"], "raw_keep_refs": ["S001"],
                "question": "", "question_source_ref": None, "question_source_kind": None,
                "situation": "The DPX50 migration session was rescheduled for next week.",
                "recommendation": "", "actions": [], "specifics": [], "limitation": "",
                "reference_refs": [], "confidence": "confirmed",
            }],
            "unanswered": [],
        }

        with self.assertRaisesRegex(ModelFailure, "unsupported protected value.*DPX50"):
            render_actionable(json.dumps(response), sources)

    def test_reader_rejects_an_unsupported_action(self):
        response = self.response()
        response["topics"][0]["actions"] = ["Schedule an additional workshop for the regional team."]
        with self.assertRaisesRegex(ModelFailure, "unsupported action"):
            render_actionable(json.dumps(response), self.sources())

    def test_grounding_does_not_accept_a_shared_word_prefix(self):
        sources = [{
            "message_id": "migration", "change_seq": 1, "display_name": "Coordinator",
            "timestamp": "2026-09-01T13:00:00+00:00", "text": "The migration was cancelled.",
        }]
        response = {
            "dispositions": {"S001": "INCLUDE"},
            "topics": [{
                "topic": "Update", "title": "Update", "source_refs": ["S001"],
                "raw_keep_refs": ["S001"], "question": "", "question_source_ref": None,
                "question_source_kind": None, "situation": "The migraine was cancelled.",
                "recommendation": "", "actions": [], "specifics": [], "limitation": "",
                "reference_refs": [], "confidence": "confirmed",
            }],
            "unanswered": [],
        }

        with self.assertRaisesRegex(ModelFailure, "unsupported narrative"):
            render_actionable(json.dumps(response), sources)

    def test_grounding_does_not_stitch_excerpts_across_sources(self):
        sources = [
            {
                "message_id": "first", "change_seq": 1, "display_name": "Coordinator",
                "timestamp": "2026-09-01T13:00:00+00:00", "text": "The migration was",
            },
            {
                "message_id": "second", "change_seq": 2, "display_name": "Coordinator",
                "timestamp": "2026-09-01T13:01:00+00:00", "text": "cancelled tomorrow.",
            },
        ]
        response = {
            "dispositions": {"S001": "INCLUDE", "S002": "INCLUDE"},
            "topics": [{
                "topic": "Update", "title": "Update", "source_refs": ["S001", "S002"],
                "raw_keep_refs": ["S001", "S002"], "question": "", "question_source_ref": None,
                "question_source_kind": None, "situation": "The migration was cancelled tomorrow.",
                "recommendation": "", "actions": [], "specifics": [], "limitation": "",
                "reference_refs": [], "confidence": "confirmed",
            }],
            "unanswered": [],
        }

        with self.assertRaisesRegex(ModelFailure, "unsupported narrative"):
            render_actionable(json.dumps(response), sources)

    def test_grounding_rejects_an_excerpt_that_drops_a_preceding_negator(self):
        sources = [{
            "message_id": "instruction", "change_seq": 1, "display_name": "Coordinator",
            "timestamp": "2026-09-01T13:00:00+00:00",
            "text": "Please do not restart the cluster before the upgrade completes.",
        }]
        response = {
            "dispositions": {"S001": "INCLUDE"}, "unanswered": [],
            "topics": [{
                "topic": "Upgrade", "title": "Upgrade instruction", "source_refs": ["S001"],
                "raw_keep_refs": ["S001"], "question": "", "question_source_ref": None,
                "question_source_kind": None, "situation": "", "recommendation": "",
                "actions": ["restart the cluster before the upgrade completes."],
                "specifics": [], "limitation": "", "reference_refs": [], "confidence": "confirmed",
            }],
        }
        with self.assertRaisesRegex(ModelFailure, "unsupported action"):
            render_actionable(json.dumps(response), sources)

    def test_grounding_rejects_a_midword_excerpt(self):
        sources = [{
            "message_id": "instruction", "change_seq": 1, "display_name": "Coordinator",
            "timestamp": "2026-09-01T13:00:00+00:00", "text": "Restart the cluster.",
        }]
        response = {
            "dispositions": {"S001": "INCLUDE"}, "unanswered": [],
            "topics": [{
                "topic": "Cluster", "title": "Cluster", "source_refs": ["S001"],
                "raw_keep_refs": ["S001"], "question": "", "question_source_ref": None,
                "question_source_kind": None, "situation": "estart the clus",
                "recommendation": "", "actions": [], "specifics": [], "limitation": "",
                "reference_refs": [], "confidence": "confirmed",
            }],
        }
        with self.assertRaisesRegex(ModelFailure, "unsupported narrative"):
            render_actionable(json.dumps(response), sources)

    def test_protected_identifier_forms_are_exact_tokens(self):
        identifiers = {"3PAR", "SRX_345", "v2", "802.11ax", "C++17", "DPX50"}
        self.assertTrue(identifiers <= protected_values("Use 3PAR SRX_345 v2 802.11ax C++17 DPX50."))
        self.assertNotIn("DPX50", protected_values("Use DPX500."))
        self.assertFalse(any(
            value.startswith("/")
            for value in protected_values("See https://support.example.invalid/answer/1136675")
        ))
        self.assertIn(
            "https://example.invalid/Function_(mathematics)",
            protected_values("See https://example.invalid/Function_(mathematics) for details."),
        )

    def test_semantic_question_label_cannot_override_a_nonquestion_source(self):
        sources = [{
            "message_id": "announcement", "change_seq": 1, "display_name": "Coordinator",
            "timestamp": "2026-09-01T13:00:00+00:00",
            "text": "The regional call is cancelled. Prepare the slides before next week.",
        }]
        response = {
            "dispositions": {"S001": "INCLUDE"},
            "topics": [{
                "topic": "Regional call", "title": "Regional call cancelled",
                "source_refs": ["S001"], "raw_keep_refs": ["S001"],
                "question": "Should the regional call be cancelled?", "question_source_ref": "S001",
                "question_source_kind": "QUESTION", "situation": "The regional call is cancelled.",
                "recommendation": "Prepare the slides before next week.", "actions": [],
                "specifics": [], "limitation": "", "reference_refs": [], "confidence": "confirmed",
            }],
            "unanswered": [],
        }

        with self.assertRaisesRegex(ModelFailure, "question source is not question-like"):
            render_actionable(json.dumps(response), sources)

    def test_technical_directive_cannot_be_recast_as_a_topic_question(self):
        sources = [{
            "message_id": "directive", "change_seq": 1, "display_name": "Coordinator",
            "timestamp": "2026-09-01T13:00:00+00:00",
            "text": "Do not reboot the server during the migration window.",
        }]
        response = {
            "dispositions": {"S001": "INCLUDE"},
            "topics": [{
                "topic": "Migration window", "title": "Migration window",
                "source_refs": ["S001"], "raw_keep_refs": ["S001"],
                "question": "Do not reboot the server during the migration window.",
                "question_source_ref": "S001", "question_source_kind": "QUESTION",
                "situation": "Do not reboot the server during the migration window.",
                "recommendation": "", "actions": [], "specifics": [], "limitation": "",
                "reference_refs": [], "confidence": "confirmed",
            }],
            "unanswered": [],
        }
        with self.assertRaisesRegex(ModelFailure, "question source is not question-like"):
            render_actionable(json.dumps(response), sources)

    def test_relative_clause_statement_cannot_be_recast_as_a_topic_question(self):
        sources = [{
            "message_id": "statement", "change_seq": 1, "display_name": "Coordinator",
            "timestamp": "2026-09-01T13:00:00+00:00",
            "text": "The upgrade is done, which resolves the DNS issue.",
        }]
        response = {
            "dispositions": {"S001": "INCLUDE"},
            "topics": [{
                "topic": "Upgrade", "title": "Upgrade",
                "source_refs": ["S001"], "raw_keep_refs": ["S001"],
                "question": "The upgrade is done, which resolves the DNS issue.",
                "question_source_ref": "S001", "question_source_kind": "QUESTION",
                "situation": "The upgrade is done, which resolves the DNS issue.",
                "recommendation": "", "actions": [], "specifics": [], "limitation": "",
                "reference_refs": [], "confidence": "confirmed",
            }],
            "unanswered": [],
        }
        with self.assertRaisesRegex(ModelFailure, "question source is not question-like"):
            render_actionable(json.dumps(response), sources)

    def test_relative_clause_statement_is_not_an_unanswered_question(self):
        sources = [{
            "message_id": "statement", "change_seq": 1, "display_name": "Engineer A",
            "timestamp": "2026-09-01T13:00:00+00:00",
            "text": "We rolled back the config, which restored the cluster sync.",
        }]
        response = {
            "dispositions": {"S001": "INCLUDE"}, "topics": [],
            "unanswered": [{
                "topic": "config-rollback", "question_source_ref": "S001",
                "question_source_kind": "QUESTION", "context_refs": [],
                "reason": "No reusable answer was established.",
            }],
        }
        with self.assertRaisesRegex(ModelFailure, "unanswered source is not a technical question or issue"):
            render_actionable(json.dumps(response), sources)

    def test_interrogative_wh_questions_are_still_detected(self):
        for text in (
            "Which CLI command checks interface state?",
            "What upgrade path does the appliance support?",
            "Any idea why the GSLB sync stops after the upgrade?",
        ):
            sources = [{
                "message_id": "question", "change_seq": 1, "display_name": "Engineer A",
                "timestamp": "2026-09-01T13:00:00+00:00", "text": text,
            }]
            response = {
                "dispositions": {"S001": "INCLUDE"}, "topics": [],
                "unanswered": [{
                    "topic": "open-question", "question_source_ref": "S001",
                    "question_source_kind": "QUESTION", "context_refs": [],
                    "reason": "No reusable answer was established.",
                }],
            }
            with self.subTest(text=text):
                self.assertIn(text, render_actionable(json.dumps(response), sources))

    def test_unanswered_internal_topic_is_not_reader_facing(self):
        sources = [{
            "message_id": "question", "change_seq": 1, "display_name": "Engineer A",
            "timestamp": "2026-09-01T13:00:00+00:00",
            "text": "Which CLI command checks interface state?",
        }]
        response = {"dispositions": {"S001": "INCLUDE"}, "topics": [], "unanswered": [{
            "topic": "internal-unanswered-topic", "question_source_ref": "S001",
            "question_source_kind": "QUESTION", "context_refs": [],
            "reason": "No reusable answer was established.",
        }]}

        rendered = render_actionable(json.dumps(response), sources)
        self.assertNotIn("internal-unanswered-topic", rendered)

    def test_unanswered_issue_requires_independent_technical_problem_signal(self):
        sources = [{
            "message_id": "announcement", "change_seq": 1, "display_name": "Coordinator",
            "timestamp": "2026-09-01T13:00:00+00:00", "text": "The maintenance window is closed.",
        }]
        response = {
            "dispositions": {"S001": "INCLUDE"}, "topics": [],
            "unanswered": [{
                "topic": "maintenance-window", "question_source_ref": "S001",
                "question_source_kind": "ISSUE", "context_refs": [],
                "reason": "No reusable resolution was established.",
            }],
        }
        with self.assertRaisesRegex(ModelFailure, "unanswered source is not a technical question or issue"):
            render_actionable(json.dumps(response), sources)

    def test_resolved_issue_cannot_render_as_unanswered(self):
        sources = [{
            "message_id": "resolved", "change_seq": 1, "display_name": "Engineer A",
            "timestamp": "2026-09-01T13:00:00+00:00",
            "text": "The DNS sync error was fixed this morning by the config rollback.",
        }]
        response = {
            "dispositions": {"S001": "INCLUDE"}, "topics": [],
            "unanswered": [{
                "topic": "dns-sync", "question_source_ref": "S001",
                "question_source_kind": "ISSUE", "context_refs": [],
                "reason": "No reusable resolution was established.",
            }],
        }
        with self.assertRaisesRegex(ModelFailure, "unanswered source is not a technical question or issue"):
            render_actionable(json.dumps(response), sources)

    def test_unanswered_only_digest_is_valid(self):
        sources = [{
            "message_id": "question", "change_seq": 1, "display_name": "Engineer A",
            "timestamp": "2026-09-01T13:00:00+00:00", "text": "Which command checks interface state?",
        }]
        response = {
            "dispositions": {"S001": "INCLUDE"}, "topics": [],
            "unanswered": [{
                "topic": "interface-state", "question_source_ref": "S001",
                "question_source_kind": "QUESTION", "context_refs": [],
                "reason": "No reusable answer was established.",
            }],
        }
        rendered = render_actionable(json.dumps(response), sources)
        self.assertIn("Unanswered / incomplete topics", rendered)
        self.assertIn("Which command checks interface state?", rendered)

    def test_unanswered_count_is_bounded(self):
        sources = [
            {
                "message_id": f"question-{index}", "change_seq": index,
                "display_name": "Engineer A", "timestamp": "2026-09-01T13:00:00+00:00",
                "text": f"Which CLI command checks interface state for port {index}?",
            }
            for index in range(1, 10)
        ]
        response = {
            "dispositions": {f"S{index:03d}": "INCLUDE" for index in range(1, 10)},
            "topics": [],
            "unanswered": [
                {
                    "topic": f"interface-{index}", "question_source_ref": f"S{index:03d}",
                    "question_source_kind": "QUESTION", "context_refs": [],
                    "reason": "No reusable answer was established.",
                }
                for index in range(1, 10)
            ],
        }
        with self.assertRaisesRegex(ModelFailure, "actionable topics or unanswered list invalid"):
            render_actionable(json.dumps(response), sources)
        self.assertEqual(actionable_schema(sources)["properties"]["unanswered"]["maxItems"], 8)

    def test_specific_already_shown_in_question_is_not_repeated(self):
        response = self.response()
        response["topics"][0]["specifics"] = [{"source_ref": "S001", "value": "10.10.6"}]
        rendered = render_actionable(json.dumps(response), self.sources())
        self.assertNotIn("Key details: 10.10.6", rendered)

    def test_recommendation_and_distinct_actions_are_both_rendered(self):
        response = self.response()
        response["topics"][0]["actions"] = ["Upgrade directly to 10.14.0."]
        response["topics"][0]["recommendation"] = "Intermediate 10.11.0 fails with package exit code 100."
        rendered = render_actionable(json.dumps(response), self.sources())
        self.assertIn("Actions / follow-up:", rendered)
        self.assertIn("Action / follow-up: Intermediate 10.11.0 fails with package exit code 100.", rendered)

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
