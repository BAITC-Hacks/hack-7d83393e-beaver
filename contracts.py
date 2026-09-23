from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parent
SCHEMA_PATH = ROOT / "contract.schema.json"


def validate_protocol(protocol: dict[str, Any]) -> None:
    """Validate the shared v1.0 contract and its cross-record references.

    The JSON schema deliberately describes the shape of a snapshot only.  A
    valid shape can still contain an unknown participant, a dangling evidence
    id, or an interval with its end before its start.  Those are particularly
    dangerous here because the snapshot is also the boundary between model
    output and human approval, so keep the small set of semantic checks in
    the common validator rather than relying on each HTTP handler to repeat
    them.
    """
    try:
        import jsonschema
    except ImportError as exc:  # pragma: no cover - dependency diagnostic
        raise RuntimeError("jsonschema is required for protocol validation") from exc
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    try:
        jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker()).validate(protocol)
    except jsonschema.ValidationError as exc:
        # FastAPI maps ValueError to the application's 422 response.  Do not
        # expose jsonschema's internal path/details as a server error.
        raise ValueError(f"protocol schema validation failed: {exc.message}") from exc

    participants = protocol["participants"]
    participant_ids = [item["id"] for item in participants]
    if len(participant_ids) != len(set(participant_ids)):
        raise ValueError("duplicate participant id")

    utterances = protocol["utterances"]
    utterance_ids = [item["id"] for item in utterances]
    if len(utterance_ids) != len(set(utterance_ids)):
        raise ValueError("duplicate utterance id")
    utterance_set = set(utterance_ids)
    speaker_ids = {item["speaker_id"] for item in utterances if item["speaker_id"] is not None}
    for item in utterances:
        if item["end_ms"] <= item["start_ms"]:
            raise ValueError(f"invalid utterance interval: {item['id']}")

    seen_speakers: set[str] = set()
    for item in protocol["speaker_map"]:
        speaker_id = item["speaker_id"]
        if speaker_id in seen_speakers:
            raise ValueError(f"duplicate speaker mapping: {speaker_id}")
        seen_speakers.add(speaker_id)
        if speaker_id not in speaker_ids:
            raise ValueError(f"speaker mapping references unknown speaker: {speaker_id}")
        participant_id = item["participant_id"]
        if participant_id is not None and participant_id not in set(participant_ids):
            raise ValueError(f"unknown participant in speaker map: {participant_id}")
        if item["confirmed"] and participant_id is None:
            raise ValueError("a confirmed speaker mapping needs a participant")

    task_ids = [item["id"] for item in protocol["tasks"]]
    if len(task_ids) != len(set(task_ids)):
        raise ValueError("duplicate task id")
    task_set = set(task_ids)

    def refs(values: list[str], label: str) -> None:
        if len(values) != len(set(values)):
            raise ValueError(f"duplicate {label} evidence")
        if not set(values).issubset(utterance_set):
            raise ValueError(f"unknown {label} evidence")

    for task in protocol["tasks"]:
        if task["assignee_id"] is not None and task["assignee_id"] not in set(participant_ids):
            raise ValueError(f"unknown assignee: {task['assignee_id']}")
        if task["due_time"] is not None and task["due_date"] is None:
            raise ValueError(f"task {task['id']} has time without date")
        for field in ("action", "assignee", "deadline"):
            refs(task["evidence"][field], f"task {task['id']} {field}")
        if not task["evidence"]["action"]:
            raise ValueError(f"task {task['id']} action evidence missing")
        # A human correction may fill a previously-null field.  Its origin is
        # stored in audit_events and must not be fabricated as an audio quote;
        # model-created values still carry field-level evidence from extract().
        for event in task["history"]:
            refs(event["evidence_ids"], f"task {task['id']} history")

    for item in protocol["summary"]:
        refs(item["evidence_ids"], "summary")
    for question in protocol["review_questions"]:
        if question["task_id"] not in task_set:
            raise ValueError(f"review question references unknown task: {question['task_id']}")
    for interval in protocol["presence_intervals"]:
        if interval["end_ms"] <= interval["start_ms"]:
            raise ValueError("invalid presence interval")
        participant_id = interval["participant_id"]
        if participant_id is not None:
            if participant_id not in set(participant_ids):
                raise ValueError(f"unknown participant in presence interval: {participant_id}")
            if not interval["confirmed_by_user"]:
                raise ValueError("presence interval participant must be confirmed by user")


def empty_protocol(meeting: dict[str, Any], mode: str) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "mode": mode,
        "meeting": meeting,
        "participants": [],
        "speaker_map": [],
        "utterances": [],
        "tasks": [],
        "summary": [],
        "review_questions": [],
        "warnings": [],
        "presence_intervals": [],
    }


def ledger_to_protocol(bundle: Mapping[str, Any], protocol: Mapping[str, Any]) -> dict[str, Any]:
    """Adapt a typed sidecar ledger into the preserved protocol 1.0 snapshot.

    The sidecar is intentionally kept separate from ``contract.schema.json``.
    This adapter accepts only the sidecar's typed events and requires their
    utterance ids, timestamps and text to match the already processed audio
    snapshot.  It never parses display strings or invents missing evidence.
    The original ledger must be persisted separately when replay is needed.
    """
    if not isinstance(bundle, Mapping) or not isinstance(protocol, Mapping):
        raise ValueError("ledger and protocol must be objects")
    if bundle.get("ledger_version", "1.0") != "1.0":
        raise ValueError("unsupported ledger version")
    if bundle.get("mode") not in {"REAL", "FIXTURE"}:
        raise ValueError("ledger mode must be REAL or FIXTURE")
    meeting = protocol.get("meeting")
    if not isinstance(meeting, Mapping) or bundle.get("meeting_id") != meeting.get("id"):
        raise ValueError("ledger meeting_id does not match protocol meeting")
    if bundle["mode"] != protocol.get("mode"):
        raise ValueError("ledger mode does not match protocol mode")

    participants = protocol.get("participants")
    raw_participants = bundle.get("participants")
    if not isinstance(participants, list) or not isinstance(raw_participants, list):
        raise ValueError("ledger participants must be arrays")
    participant_ids = [item.get("id") for item in participants if isinstance(item, Mapping)]
    if any(not isinstance(item, str) or not item for item in raw_participants):
        raise ValueError("ledger participant ids must be non-empty strings")
    if len(raw_participants) != len(set(raw_participants)) or set(raw_participants) != set(participant_ids):
        raise ValueError("ledger participants do not match protocol participants")

    protocol_utterances = protocol.get("utterances")
    raw_utterances = bundle.get("utterances")
    if not isinstance(protocol_utterances, list) or not isinstance(raw_utterances, list):
        raise ValueError("ledger utterances must be arrays")
    by_id = {item.get("id"): item for item in protocol_utterances if isinstance(item, Mapping)}
    if len(by_id) != len(protocol_utterances) or len(raw_utterances) != len(by_id):
        raise ValueError("ledger utterances do not match protocol utterances")
    for row in raw_utterances:
        if not isinstance(row, Mapping) or set(row) != {"id", "start_ms", "end_ms", "text"}:
            raise ValueError("ledger utterance has an unsupported shape")
        source = by_id.get(row["id"])
        if source is None or any(source.get(field) != row[field] for field in ("start_ms", "end_ms", "text")):
            raise ValueError(f"ledger evidence does not match utterance {row.get('id')}")
        if row["end_ms"] <= row["start_ms"]:
            raise ValueError(f"invalid ledger utterance interval: {row['id']}")

    events = bundle.get("events")
    if not isinstance(events, list):
        raise ValueError("ledger events must be an array")
    allowed_state = {"action", "assignee_id", "due_date", "due_time"}
    event_ids: set[str] = set()
    seen_time = -1
    proposals: dict[str, dict[str, Any]] = {}
    states: dict[str, dict[str, Any]] = {}
    field_evidence: dict[str, dict[str, list[str]]] = {}
    histories: dict[str, list[dict[str, Any]]] = {}
    task_order: list[str] = []
    utterance_end = {row["id"]: row["end_ms"] for row in raw_utterances}

    def _state(raw: Any) -> dict[str, Any]:
        if not isinstance(raw, Mapping) or set(raw) - allowed_state or "action" not in raw:
            raise ValueError("ledger state has an unsupported shape")
        value = {field: raw.get(field) for field in allowed_state}
        if not isinstance(value["action"], str) or not value["action"].strip():
            raise ValueError("ledger action must be non-empty")
        if value["assignee_id"] is not None and value["assignee_id"] not in set(participant_ids):
            raise ValueError(f"unknown ledger assignee: {value['assignee_id']}")
        if value["due_date"] is not None:
            try:
                if date.fromisoformat(value["due_date"]).isoformat() != value["due_date"]:
                    raise ValueError("non-canonical date")
            except (TypeError, ValueError) as exc:
                raise ValueError("ledger due_date must be YYYY-MM-DD") from exc
        if value["due_time"] is not None:
            if value["due_date"] is None or not isinstance(value["due_time"], str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d", value["due_time"]):
                raise ValueError("ledger due_time requires a valid date and HH:MM:SS")
        return value

    def _display(state: Mapping[str, Any]) -> str | None:
        if state.get("due_date") is None:
            return None
        return f"{state['due_date']} {state['due_time']}" if state.get("due_time") else str(state["due_date"])

    def _history(task_id: str, event_id: str, kind: str, previous: str | None, new: str | None, accepted: bool, evidence: list[str]) -> None:
        histories[task_id].append({
            "event_id": event_id,
            "kind": kind,
            "previous_value": previous,
            "new_value": new,
            "accepted_in_dialogue": accepted,
            "evidence_ids": list(dict.fromkeys(evidence)),
        })

    for event in events:
        if not isinstance(event, Mapping):
            raise ValueError("ledger event must be an object")
        event_id = event.get("event_id")
        task_id = event.get("task_id")
        kind = event.get("kind")
        at_ms = event.get("at_ms")
        evidence = event.get("evidence_ids")
        if not isinstance(event_id, str) or not event_id or event_id in event_ids:
            raise ValueError("ledger event_id must be unique")
        if not isinstance(task_id, str) or not task_id or kind not in {"create", "propose", "accept", "reject", "cancel"}:
            raise ValueError("invalid ledger event identity or kind")
        if isinstance(at_ms, bool) or not isinstance(at_ms, int) or at_ms < 0 or at_ms < seen_time:
            raise ValueError("ledger events must be chronologically ordered")
        if not isinstance(evidence, list) or not evidence or len(evidence) != len(set(evidence)) or not set(evidence).issubset(utterance_end):
            raise ValueError(f"invalid evidence for ledger event {event_id}")
        if any(utterance_end[uid] > at_ms for uid in evidence):
            raise ValueError(f"ledger event {event_id} occurs before its evidence")
        event_ids.add(event_id)
        seen_time = at_ms

        if kind == "create":
            if task_id in states or event.get("patch") is not None or event.get("proposal_id") is not None:
                raise ValueError(f"invalid create event {event_id}")
            state = _state(event.get("initial"))
            states[task_id] = state
            field_evidence[task_id] = {field: (list(evidence) if state.get(field) is not None else []) for field in allowed_state}
            histories[task_id] = []
            task_order.append(task_id)
            _history(task_id, event_id, "created", None, state["action"], True, list(evidence))
            continue

        if task_id not in states:
            raise ValueError(f"ledger event {event_id} references unknown task")
        if kind == "propose":
            patch = event.get("patch")
            if not isinstance(patch, Mapping) or not patch or set(patch) - allowed_state:
                raise ValueError(f"invalid proposal event {event_id}")
            before = dict(states[task_id])
            proposed = dict(before)
            proposed.update(patch)
            _state(proposed)
            if event_id in proposals:
                raise ValueError(f"duplicate proposal {event_id}")
            proposals[event_id] = {"task_id": task_id, "patch": dict(patch), "before": before, "evidence": list(evidence), "status": "pending"}
            if "action" in patch:
                raise ValueError("contract 1.0 cannot represent action changes from the sidecar")
            if "assignee_id" in patch:
                _history(task_id, event_id + ":assignee", "assignee_changed", before["assignee_id"], proposed["assignee_id"], False, list(evidence))
            if "due_date" in patch or "due_time" in patch:
                _history(task_id, event_id + ":deadline", "deadline_changed", _display(before), _display(proposed), False, list(evidence))
            continue

        if kind in {"accept", "reject"}:
            proposal_id = event.get("proposal_id")
            proposal = proposals.get(proposal_id)
            if not proposal or proposal["task_id"] != task_id or proposal["status"] != "pending":
                raise ValueError(f"ledger event {event_id} references an inactive proposal")
            proposal["status"] = "accepted" if kind == "accept" else "rejected"
            before = dict(states[task_id])
            if kind == "accept":
                for field, value in proposal["before"].items():
                    if states[task_id][field] != value:
                        raise ValueError(f"stale proposal {proposal_id}")
                after = dict(before)
                after.update(proposal["patch"])
                _state(after)
                states[task_id] = after
                for field in proposal["patch"]:
                    field_evidence[task_id][field] = list(dict.fromkeys(proposal["evidence"] + list(evidence))) if after[field] is not None else []
            else:
                # Keep the proposed value visible in history while leaving
                # the current state untouched after a rejection.
                after = dict(before)
                after.update(proposal["patch"])
            combined = list(dict.fromkeys(proposal["evidence"] + list(evidence)))
            if "assignee_id" in proposal["patch"]:
                _history(task_id, event_id + ":assignee", "assignee_changed", before["assignee_id"], after["assignee_id"], kind == "accept", combined)
            if "due_date" in proposal["patch"] or "due_time" in proposal["patch"]:
                _history(task_id, event_id + ":deadline", "deadline_changed", _display(before), _display(after), kind == "accept", combined)
            continue

        if event.get("initial") is not None or event.get("patch") is not None or event.get("proposal_id") is not None:
            raise ValueError(f"invalid cancel event {event_id}")
        _history(task_id, event_id, "cancelled", _display(states[task_id]), None, True, list(evidence))
        for proposal in proposals.values():
            if proposal["task_id"] == task_id and proposal["status"] == "pending":
                proposal["status"] = "invalidated_by_cancellation"
        states[task_id]["_cancelled"] = True

    names = {item["id"]: item["name"] for item in participants}
    tasks: list[dict[str, Any]] = []
    questions: list[dict[str, Any]] = []
    for task_id in task_order:
        state = states[task_id]
        cancelled = bool(state.pop("_cancelled", False))
        pending = [item for item in proposals.values() if item["task_id"] == task_id and item["status"] == "pending"]
        evidence = field_evidence[task_id]
        deadline_evidence = list(dict.fromkeys(evidence["due_date"] + evidence["due_time"]))
        task = {
            "id": task_id,
            "action": state["action"],
            "assignee_id": state["assignee_id"],
            "due_date": state["due_date"],
            "due_time": state["due_time"],
            "deadline_raw": None,
            "dialogue_status": "cancelled" if cancelled else "agreed",
            "review_status": "needs_review" if pending or state["assignee_id"] is None or state["due_date"] is None else "draft",
            "evidence": {"action": evidence["action"], "assignee": evidence["assignee_id"], "deadline": deadline_evidence},
            "history": histories[task_id],
        }
        tasks.append(task)
        if not cancelled:
            if state["assignee_id"] is None:
                questions.append({"task_id": task_id, "field": "assignee", "text": f"Кто отвечает за {state['action'].lower()}?"})
            if state["due_date"] is None:
                questions.append({"task_id": task_id, "field": "deadline", "text": f"К какому сроку нужно {state['action'].lower()}?"})
            if pending:
                questions.append({"task_id": task_id, "field": "agreement", "text": "Подтвердить или отклонить предложенное изменение срока?"})

    summary: list[dict[str, Any]] = []
    for task in tasks:
        wording = task["action"]
        if task["assignee_id"] is not None:
            wording += f" — {names[task['assignee_id']]}"
        if task["due_date"] is not None:
            wording += f" до {task['due_date']}"
            if task["due_time"]:
                wording += f" {task['due_time']}"
        if task["dialogue_status"] == "cancelled":
            wording += " (отменено)"
        summary.append({"text": wording, "evidence_ids": list(dict.fromkeys(sum(task["evidence"].values(), [])))})

    adapted = {
        "schema_version": "1.0",
        "mode": bundle["mode"],
        "meeting": dict(meeting),
        "participants": [dict(item) for item in participants],
        "speaker_map": [dict(item) for item in protocol.get("speaker_map", [])],
        "utterances": [dict(item) for item in protocol_utterances],
        "tasks": tasks,
        "summary": summary,
        "review_questions": questions,
        "warnings": list(protocol.get("warnings", [])) + ["Снимок собран из типизированного журнала событий."],
        "presence_intervals": [dict(item) for item in protocol.get("presence_intervals", [])],
    }
    validate_protocol(adapted)
    return adapted
