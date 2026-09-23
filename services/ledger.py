"""Typed task-event ledger and adapter to protocol contract 1.0.

The ledger is a sidecar.  The public meeting snapshot remains the existing
contract 1.0, while this module provides deterministic replay for typed
create/propose/accept/reject/cancel events and temporal cutoffs.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import date, time
import hashlib
import json
import re
from typing import Any, Mapping


class LedgerError(ValueError):
    pass


_STATE_FIELDS = ("action", "assignee_id", "due_date", "due_time")
_KINDS = {"create", "propose", "accept", "reject", "cancel"}
_BUNDLE_FIELDS = {"ledger_version", "mode", "meeting_id", "participants", "utterances", "events"}
_UTTERANCE_FIELDS = {"id", "start_ms", "end_ms", "text"}
_EVENT_FIELDS = {"event_id", "task_id", "kind", "at_ms", "evidence_ids", "initial", "patch", "proposal_id"}


def _fail(message: str) -> None:
    raise LedgerError(message)


def _validate_state(state: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(state, Mapping) or not isinstance(state.get("action"), str) or not state["action"].strip():
        _fail("state.action is required")
    if set(state) - set(_STATE_FIELDS):
        _fail("state contains unsupported fields")
    value = {field: state.get(field) for field in _STATE_FIELDS}
    for field in ("assignee_id", "due_date", "due_time"):
        if value[field] is not None and not isinstance(value[field], str):
            _fail(f"state.{field} must be string or null")
    if value["due_date"] is not None:
        try:
            if date.fromisoformat(value["due_date"]).isoformat() != value["due_date"]:
                _fail("due_date must use YYYY-MM-DD")
        except ValueError as exc:
            raise LedgerError("invalid due_date") from exc
    if value["due_time"] is not None:
        if value["due_date"] is None or not re.fullmatch(r"[0-9]{2}:[0-9]{2}:[0-9]{2}", value["due_time"]):
            _fail("due_time requires HH:MM:SS and a due_date")
        try:
            if time.fromisoformat(value["due_time"]).isoformat() != value["due_time"]:
                _fail("invalid due_time")
        except ValueError as exc:
            raise LedgerError("invalid due_time") from exc
    return value


def _validate_utterances(raw: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(raw, list):
        _fail("utterances must be an array")
    result: dict[str, dict[str, Any]] = {}
    for item in raw:
        if not isinstance(item, Mapping) or set(item) != _UTTERANCE_FIELDS or not isinstance(item.get("id"), str) or not item["id"]:
            _fail("utterance id is required")
        try:
            start, end = item["start_ms"], item["end_ms"]
        except (KeyError, TypeError, ValueError) as exc:
            raise LedgerError("invalid utterance timestamps") from exc
        if any(isinstance(value, bool) or not isinstance(value, int) for value in (start, end)):
            _fail(f"invalid utterance {item.get('id')}")
        if start < 0 or end <= start or not isinstance(item.get("text"), str) or not item["text"].strip():
            _fail(f"invalid utterance {item.get('id')}")
        if item["id"] in result:
            _fail("duplicate utterance id")
        result[item["id"]] = {"id": item["id"], "start_ms": start, "end_ms": end, "text": item["text"]}
    return result


def validate_bundle(raw: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        _fail("ledger bundle must be an object")
    if set(raw) != _BUNDLE_FIELDS:
        _fail("ledger bundle has unsupported or missing fields")
    if raw.get("ledger_version") != "1.0" or raw.get("mode") not in {"REAL", "FIXTURE"}:
        _fail("unsupported ledger version or mode")
    meeting_id = raw.get("meeting_id")
    if not isinstance(meeting_id, str) or not meeting_id:
        _fail("meeting_id is required")
    participants = raw.get("participants")
    if not isinstance(participants, list) or any(not isinstance(item, str) or not item for item in participants):
        _fail("participants must be a list of non-empty ids")
    if len(set(participants)) != len(participants):
        _fail("duplicate participant id")
    utterances = _validate_utterances(raw.get("utterances"))
    events = raw.get("events")
    if not isinstance(events, list):
        _fail("events must be an array")
    seen: set[str] = set()
    previous_at = -1
    normalized: list[dict[str, Any]] = []
    for event in events:
        if not isinstance(event, Mapping):
            _fail("event must be an object")
        if set(event) - _EVENT_FIELDS:
            _fail("event contains unsupported fields")
        event_id, task_id, kind = event.get("event_id"), event.get("task_id"), event.get("kind")
        if not all(isinstance(item, str) and item for item in (event_id, task_id)) or kind not in _KINDS:
            _fail("event_id, task_id and valid kind are required")
        if event_id in seen:
            _fail("duplicate event_id")
        seen.add(event_id)
        try:
            at_ms = event["at_ms"]
        except (KeyError, TypeError, ValueError) as exc:
            raise LedgerError("event.at_ms must be an integer") from exc
        if isinstance(at_ms, bool) or not isinstance(at_ms, int):
            _fail("event.at_ms must be an integer")
        if at_ms < previous_at:
            _fail("events must be ordered chronologically")
        previous_at = at_ms
        evidence = event.get("evidence_ids")
        if not isinstance(evidence, list) or not evidence or any(not isinstance(uid, str) or not uid for uid in evidence) or len(set(evidence)) != len(evidence):
            _fail(f"invalid evidence_ids for {event_id}")
        for uid in evidence:
            if uid not in utterances:
                _fail(f"unknown evidence id {uid}")
            if utterances[uid]["end_ms"] > at_ms:
                _fail(f"evidence {uid} is spoken after event {event_id}")
        item = dict(event)
        item["at_ms"] = at_ms
        if kind == "create":
            if event.get("initial") is None or event.get("patch") is not None or event.get("proposal_id") is not None:
                _fail("create requires initial only")
            item["initial"] = _validate_state(event["initial"])
        elif kind == "propose":
            patch = event.get("patch")
            if not isinstance(patch, Mapping) or not patch or set(patch) - set(_STATE_FIELDS) or event.get("initial") is not None or event.get("proposal_id") is not None:
                _fail("propose requires a valid patch")
            for field, value in patch.items():
                if field == "action":
                    if not isinstance(value, str) or not value.strip():
                        _fail("patch.action must be a non-empty string")
                elif value is not None and not isinstance(value, str):
                    _fail(f"patch.{field} must be string or null")
            item["patch"] = dict(patch)
        elif kind in {"accept", "reject"}:
            if not isinstance(event.get("proposal_id"), str) or not event["proposal_id"] or event.get("initial") is not None or event.get("patch") is not None:
                _fail(f"{kind} requires proposal_id only")
        elif event.get("initial") is not None or event.get("patch") is not None or event.get("proposal_id") is not None:
            _fail("cancel does not accept changes")
        normalized.append(item)
    return {"ledger_version": "1.0", "mode": raw["mode"], "meeting_id": meeting_id, "participants": list(participants), "utterances": list(utterances.values()), "events": normalized}


def replay(raw: Mapping[str, Any], cutoff_ms: int | None = None) -> dict[str, Any]:
    bundle = validate_bundle(raw)
    if cutoff_ms is not None and (isinstance(cutoff_ms, bool) or not isinstance(cutoff_ms, int) or cutoff_ms < 0):
        _fail("cutoff_ms must be a non-negative integer")
    by_utterance = {item["id"]: item for item in bundle["utterances"]}

    def apply(until: int | None) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
        tasks: dict[str, dict[str, Any]] = {}
        proposals: dict[str, dict[str, Any]] = {}
        history: list[dict[str, Any]] = []
        for event in bundle["events"]:
            if until is not None and event["at_ms"] > until:
                break
            kind, task_id = event["kind"], event["task_id"]
            changed = False
            if kind == "create":
                if task_id in tasks:
                    _fail("duplicate task creation")
                state = _validate_state(event["initial"])
                if state["assignee_id"] is not None and state["assignee_id"] not in bundle["participants"]:
                    _fail("unknown assignee")
                tasks[task_id] = {"id": task_id, **state, "status": "agreed", "evidence": {field: list(event["evidence_ids"]) if state.get(field) is not None or field == "action" else [] for field in _STATE_FIELDS}, "last_effective_event": event["event_id"]}
                changed = True
            else:
                if task_id not in tasks:
                    _fail("change before task creation")
                task = tasks[task_id]
                if task["status"] == "cancelled":
                    _fail("change to cancelled task is not allowed")
                if kind == "propose":
                    proposed = {field: task[field] for field in _STATE_FIELDS}
                    proposed.update(event["patch"])
                    _validate_state(proposed)
                    if proposed["assignee_id"] is not None and proposed["assignee_id"] not in bundle["participants"]:
                        _fail("unknown assignee")
                    proposals[event["event_id"]] = {"task_id": task_id, "patch": event["patch"], "before": {key: task[key] for key in event["patch"]}, "evidence_ids": list(event["evidence_ids"]), "status": "pending"}
                elif kind in {"accept", "reject"}:
                    proposal = proposals.get(event["proposal_id"])
                    if proposal is None or proposal["task_id"] != task_id or proposal["status"] != "pending":
                        _fail("proposal not found or already handled")
                    if kind == "accept":
                        if any(task[key] != value for key, value in proposal["before"].items()):
                            _fail("proposal is stale")
                        merged = {field: task[field] for field in _STATE_FIELDS}
                        merged.update(proposal["patch"])
                        _validate_state(merged)
                        task.update(proposal["patch"])
                        for field, value in proposal["patch"].items():
                            task["evidence"][field] = list(dict.fromkeys(proposal["evidence_ids"] + event["evidence_ids"])) if value is not None else []
                        task["last_effective_event"] = event["event_id"]
                        changed = True
                    proposal["status"] = "accepted" if kind == "accept" else "rejected"
                else:
                    task["status"] = "cancelled"
                    task["last_effective_event"] = event["event_id"]
                    task["cancellation_evidence"] = list(event["evidence_ids"])
                    for proposal in proposals.values():
                        if proposal["task_id"] == task_id and proposal["status"] == "pending":
                            proposal["status"] = "invalidated_by_cancellation"
                    changed = True
            history.append({"event_id": event["event_id"], "task_id": task_id, "kind": kind, "at_ms": event["at_ms"], "evidence_ids": list(event["evidence_ids"]), "changed_current_state": changed})
        for task in tasks.values():
            task["pending_proposals"] = [{"proposal_id": pid, "patch": deepcopy(p["patch"])} for pid, p in proposals.items() if p["task_id"] == task["id"] and p["status"] == "pending"]
            task["questions"] = []
            if task["status"] != "cancelled":
                if task["assignee_id"] is None:
                    task["questions"].append("Кто отвечает?")
                if task["due_date"] is None:
                    task["questions"].append("К какому сроку?")
        return tasks, history

    all_tasks, all_history = apply(None)
    tasks, history = apply(cutoff_ms) if cutoff_ms is not None else (all_tasks, all_history)
    raw_bytes = json.dumps(bundle, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {"ledger_version": "1.0", "mode": bundle["mode"], "cutoff_ms": cutoff_ms, "source_sha256": hashlib.sha256(raw_bytes).hexdigest(), "tasks": list(tasks.values()), "events": history, "semantic_extraction": "NOT RUN", "notice": "Воспроизведение типизированных событий; не результат ASR или извлечения LLM."}


def to_protocol(raw: Mapping[str, Any], meeting: Mapping[str, Any], participants: list[dict[str, Any]], speaker_map: list[dict[str, Any]], utterances: list[dict[str, Any]], cutoff_ms: int | None = None) -> dict[str, Any]:
    """Adapt a replay snapshot to the preserved contract 1.0."""
    bundle = validate_bundle(raw)
    if bundle["meeting_id"] != meeting.get("id"):
        _fail("ledger meeting_id does not match protocol meeting")
    if bundle["mode"] not in {"REAL", "FIXTURE"}:
        _fail("ledger mode is invalid")
    participant_ids = {item.get("id") for item in participants}
    if set(bundle["participants"]) != participant_ids:
        _fail("ledger participants do not match protocol participants")
    source_utterances = {item["id"]: item for item in bundle["utterances"]}
    snapshot_utterances = {item.get("id"): item for item in utterances}
    if set(source_utterances) != set(snapshot_utterances):
        _fail("ledger evidence does not match protocol utterances")
    for uid, source in source_utterances.items():
        snapshot = snapshot_utterances[uid]
        if any(snapshot.get(field) != source[field] for field in ("start_ms", "end_ms", "text")):
            _fail(f"ledger evidence does not match utterance {uid}")
    result = replay(bundle, cutoff_ms)
    original_events = {event["event_id"]: event for event in bundle["events"]}
    tasks: list[dict[str, Any]] = []
    for reduced in result["tasks"]:
        deadline_evidence = list(dict.fromkeys(reduced["evidence"].get("due_date", []) + reduced["evidence"].get("due_time", [])))
        evidence = {"action": list(reduced["evidence"].get("action", [])), "assignee": list(reduced["evidence"].get("assignee_id", [])), "deadline": deadline_evidence}
        if not evidence["action"]:
            evidence["action"] = [reduced["last_effective_event"]]
        history: list[dict[str, Any]] = []
        for event in result["events"]:
            if event["task_id"] != reduced["id"]:
                continue
            source = original_events[event["event_id"]]
            kind = {"create": "created", "cancel": "cancelled"}.get(event["kind"], "deadline_changed")
            if event["kind"] == "propose" and source.get("patch", {}).get("assignee_id") is not None:
                kind = "assignee_changed"
            history.append({"event_id": event["event_id"], "kind": kind, "previous_value": None, "new_value": str(source.get("patch") or source.get("initial") or ""), "accepted_in_dialogue": bool(event["changed_current_state"]), "evidence_ids": event["evidence_ids"]})
        task = {"id": reduced["id"], "action": reduced["action"], "assignee_id": reduced["assignee_id"], "due_date": reduced["due_date"], "due_time": reduced["due_time"], "deadline_raw": None, "dialogue_status": "cancelled" if reduced["status"] == "cancelled" else "agreed", "review_status": "needs_review" if reduced["questions"] or reduced["pending_proposals"] else "draft", "evidence": evidence, "history": history}
        tasks.append(task)
    questions = []
    for task in tasks:
        if task["dialogue_status"] == "cancelled":
            continue
        if task["assignee_id"] is None:
            questions.append({"task_id": task["id"], "field": "assignee", "text": "Кто отвечает за это поручение?"})
        if task["due_date"] is None:
            questions.append({"task_id": task["id"], "field": "deadline", "text": "К какому сроку нужно выполнить поручение?"})
        if any(item["id"] == task["id"] and item["pending_proposals"] for item in result["tasks"]):
            questions.append({"task_id": task["id"], "field": "agreement", "text": "Подтвердить или отклонить предложенное изменение?"})
    summary = [
        {
            "text": task["action"] + (" (отменено)" if task["dialogue_status"] == "cancelled" else ""),
            "evidence_ids": list(dict.fromkeys(sum(task["evidence"].values(), []))),
        }
        for task in tasks
    ]
    return {"schema_version": "1.0", "mode": bundle["mode"], "meeting": dict(meeting), "participants": participants, "speaker_map": speaker_map, "utterances": utterances, "tasks": tasks, "summary": summary, "review_questions": questions, "warnings": [result["notice"]], "presence_intervals": []}


__all__ = ["LedgerError", "validate_bundle", "replay", "to_protocol"]
