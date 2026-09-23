import json
import unittest
from pathlib import Path

from services.ledger import LedgerError, replay, to_protocol


ROOT = Path(__file__).resolve().parents[1]


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.ledger = json.loads((ROOT / "protocol_evidence_upgrade" / "reference_core" / "fixtures" / "ledger_mixed.json").read_text(encoding="utf-8"))
        self.snapshot = json.loads((ROOT / "examples" / "01_mixed_deadline.json").read_text(encoding="utf-8"))
        # The public example is intentionally a compact three-utterance
        # contract fixture, while ledger_mixed is the seven-utterance sidecar
        # fixture.  Align only this in-memory adapter input; keep both golden
        # files unchanged.
        self.snapshot["meeting"]["id"] = self.ledger["meeting_id"]
        self.snapshot["participants"] = [{"id": pid, "name": pid} for pid in self.ledger["participants"]]
        self.snapshot["utterances"] = [
            {"id": row["id"], "start_ms": row["start_ms"], "end_ms": row["end_ms"], "speaker_id": None, "text": row["text"]}
            for row in self.ledger["utterances"]
        ]

    def test_replay_accepts_and_rejects_without_rewriting_history(self):
        result = replay(self.ledger)
        task = next(item for item in result["tasks"] if item["id"] == "t1")
        self.assertEqual(task["due_date"], "2026-09-28")
        self.assertEqual([event["kind"] for event in result["events"]], ["create", "propose", "accept", "propose", "reject", "create"])

    def test_cutoff_does_not_include_later_decision(self):
        result = replay(self.ledger, cutoff_ms=16000)
        task = next(item for item in result["tasks"] if item["id"] == "t1")
        self.assertEqual(task["due_date"], "2026-09-25")
        self.assertEqual([event["event_id"] for event in result["events"]], ["e1", "e2"])

    def test_adapter_preserves_snapshot_contract(self):
        meeting = dict(self.snapshot["meeting"])
        meeting["id"] = self.ledger["meeting_id"]
        utterances = [{**row, "speaker_id": None} for row in self.ledger["utterances"]]
        protocol = to_protocol(self.ledger, meeting, self.snapshot["participants"], [], utterances)
        self.assertEqual(protocol["schema_version"], "1.0")
        self.assertEqual(protocol["tasks"][0]["due_date"], "2026-09-28")
        self.assertTrue(protocol["tasks"][0]["history"])

    def test_evidence_after_event_is_rejected(self):
        invalid = json.loads(json.dumps(self.ledger))
        invalid["events"][0]["at_ms"] = 1
        with self.assertRaises(LedgerError):
            replay(invalid)

    def test_schema_rejects_coerced_utterance_timestamps_and_extra_fields(self):
        for mutation in (
            lambda bundle: bundle["utterances"][0].update(start_ms="0"),
            lambda bundle: bundle["utterances"][0].update(end_ms=6000.5),
            lambda bundle: bundle["utterances"][0].update(start_ms=True),
            lambda bundle: bundle["events"][0]["initial"].update(extra="must reject"),
            lambda bundle: bundle["events"][0].update(extra="must reject"),
            lambda bundle: bundle.update(extra="must reject"),
        ):
            invalid = json.loads(json.dumps(self.ledger))
            mutation(invalid)
            with self.assertRaises(LedgerError):
                replay(invalid)

    def test_cancel_invalidates_pending_proposals(self):
        invalid = json.loads(json.dumps(self.ledger))
        invalid["events"] = [event for event in invalid["events"] if event["event_id"] in {"e1", "e2", "e3"}]
        invalid["events"].extend(
            [
                {
                    "event_id": "pending",
                    "task_id": "t1",
                    "kind": "propose",
                    "at_ms": 35000,
                    "evidence_ids": ["u4"],
                    "patch": {"due_date": "2026-09-30"},
                },
                {
                    "event_id": "cancel",
                    "task_id": "t1",
                    "kind": "cancel",
                    "at_ms": 40000,
                    "evidence_ids": ["u6"],
                },
            ]
        )
        task = next(item for item in replay(invalid)["tasks"] if item["id"] == "t1")
        self.assertEqual(task["status"], "cancelled")
        self.assertEqual(task["pending_proposals"], [])

    def test_adapter_does_not_ask_for_fields_on_cancelled_task(self):
        invalid = json.loads(json.dumps(self.ledger))
        invalid["events"].append(
            {
                "event_id": "cancel-t2",
                "task_id": "t2",
                "kind": "cancel",
                "at_ms": 48000,
                "evidence_ids": ["u7"],
            }
        )
        protocol = to_protocol(
            invalid,
            self.snapshot["meeting"],
            self.snapshot["participants"],
            self.snapshot["speaker_map"],
            self.snapshot["utterances"],
        )
        task = next(item for item in protocol["tasks"] if item["id"] == "t2")
        self.assertEqual(task["dialogue_status"], "cancelled")
        self.assertFalse(any(question["task_id"] == "t2" for question in protocol["review_questions"]))
        self.assertIn("отменено", next(item["text"] for item in protocol["summary"] if item["evidence_ids"] == ["u7"]))


if __name__ == "__main__":
    unittest.main()
