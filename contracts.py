from __future__ import annotations

import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
SCHEMA_PATH = ROOT / "contract.schema.json"


def validate_protocol(protocol: dict[str, Any]) -> None:
    """Validate the shared v1.0 contract and fail loudly when it is broken."""
    try:
        import jsonschema
    except ImportError as exc:  # pragma: no cover - dependency diagnostic
        raise RuntimeError("jsonschema is required for protocol validation") from exc
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator(schema).validate(protocol)


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
