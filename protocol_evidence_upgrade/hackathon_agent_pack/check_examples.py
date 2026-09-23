"""Validate synthetic examples only; does not test any AI or application."""
from pathlib import Path
import copy
import json
import sys
import unittest
try:
    from jsonschema import Draft202012Validator, FormatChecker
except ImportError:
    raise SystemExit("Install jsonschema in your development environment first.")

ROOT = Path(__file__).resolve().parent
SCHEMA = json.loads((ROOT / "contract.schema.json").read_text(encoding="utf-8"))
VALIDATOR = Draft202012Validator(SCHEMA, format_checker=FormatChecker())

def validate(data):
    VALIDATOR.validate(data)
    participants = [p["id"] for p in data["participants"]]
    utterances = [u["id"] for u in data["utterances"]]
    tasks = [t["id"] for t in data["tasks"]]
    for values in (participants, utterances, tasks):
        if len(values) != len(set(values)):
            raise ValueError("Duplicate entity ID")
    participant_ids, utterance_ids, task_ids = map(set, (participants, utterances, tasks))
    for u in data["utterances"]:
        if u["end_ms"] <= u["start_ms"]:
            raise ValueError("Non-positive utterance duration")
    for mapping in data["speaker_map"]:
        if mapping["participant_id"] is not None and mapping["participant_id"] not in participant_ids:
            raise ValueError("Unknown participant in speaker map")
    def refs(values):
        if not set(values).issubset(utterance_ids):
            raise ValueError("Unknown evidence utterance")
    for task in data["tasks"]:
        if task["assignee_id"] is not None and task["assignee_id"] not in participant_ids:
            raise ValueError("Unknown assignee")
        for field in ("action", "assignee", "deadline"):
            refs(task["evidence"][field])
        if task["assignee_id"] is not None and not task["evidence"]["assignee"]:
            raise ValueError("Missing assignee evidence")
        if task["due_date"] is not None and not task["evidence"]["deadline"]:
            raise ValueError("Missing deadline evidence")
        if task["due_time"] is not None and task["due_date"] is None:
            raise ValueError("Time without date")
        for event in task["history"]:
            refs(event["evidence_ids"])
    for item in data["summary"]:
        refs(item["evidence_ids"])
    for question in data["review_questions"]:
        if question["task_id"] not in task_ids:
            raise ValueError("Unknown task in question")
    for interval in data["presence_intervals"]:
        if interval["end_ms"] <= interval["start_ms"]:
            raise ValueError("Non-positive interval duration")
        person = interval["participant_id"]
        if person is not None and (person not in participant_ids or not interval["confirmed_by_user"]):
            raise ValueError("Unconfirmed or unknown participant in interval")

class ExampleChecks(unittest.TestCase):
    def setUp(self):
        self.examples = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((ROOT/"examples").glob("*.json"))]

    def test_schema_valid(self):
        Draft202012Validator.check_schema(SCHEMA)

    def test_all_synthetic_examples(self):
        self.assertEqual(len(self.examples), 3)
        for data in self.examples:
            with self.subTest(meeting=data["meeting"]["id"]):
                self.assertEqual(data["mode"], "FIXTURE")
                validate(data)

    def test_rejects_unknown_assignee(self):
        data = copy.deepcopy(self.examples[0])
        data["tasks"][0]["assignee_id"] = "invented-person"
        with self.assertRaises(ValueError):
            validate(data)

    def test_rejects_nonexistent_evidence(self):
        data = copy.deepcopy(self.examples[0])
        data["tasks"][0]["evidence"]["action"] = ["missing-utterance"]
        with self.assertRaises(ValueError):
            validate(data)

    def test_missing_fields_preserved(self):
        task = self.examples[1]["tasks"][0]
        self.assertIsNone(task["assignee_id"])
        self.assertIsNone(task["due_date"])
        self.assertEqual(len(self.examples[1]["review_questions"]), 2)

    def test_past_action_has_no_new_task(self):
        self.assertEqual(self.examples[2]["tasks"], [])

if __name__ == "__main__":
    print("CHECKING FIXTURES ONLY — NOT ASR, LLM, YOLO OR APPLICATION QUALITY.", flush=True)
    unittest.main(verbosity=2)
