from __future__ import annotations

import shutil
import uuid
import copy
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from config import settings
from contracts import empty_protocol, ledger_to_protocol, validate_protocol
from storage import Storage


settings.ensure_dirs()
storage = Storage(settings.db_path)
app = FastAPI(title="Meeting assistant", version="0.1.0")
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")


@app.middleware("http")
async def security_headers(request, call_next):
    response = await call_next(request)
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "media-src 'self' blob:; connect-src 'self'; object-src 'none'; "
        "base-uri 'none'; frame-ancestors 'none'",
    )
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    return response


def _public_error(prefix: str, exc: BaseException) -> str:
    """Keep paths, URLs, and transcript snippets out of API/job errors."""
    return f"{prefix}: {type(exc).__name__}"


def _mode(value: str | None) -> str:
    mode = (value or settings.mode).upper()
    if mode not in {"REAL", "FIXTURE"}:
        raise HTTPException(400, "mode must be REAL or FIXTURE")
    return mode


def _meeting_from_input(payload: dict[str, Any], meeting_id: str) -> dict[str, Any]:
    title = str(payload.get("title") or "").strip()
    started_at = str(payload.get("started_at") or "").strip()
    timezone = str(payload.get("timezone") or "").strip()
    if not title or not started_at or not timezone:
        raise HTTPException(422, "title, started_at and timezone are required")
    try:
        datetime.fromisoformat(started_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(422, "started_at must be an ISO date-time") from exc
    participants = payload.get("participants") or []
    if not isinstance(participants, list):
        raise HTTPException(422, "participants must be an array")
    normalized = []
    participant_ids: set[str] = set()
    for item in participants:
        if not isinstance(item, dict) or not str(item.get("id") or "").strip() or not str(item.get("name") or "").strip():
            raise HTTPException(422, "each participant needs id and name")
        participant_id = str(item["id"]).strip()
        if participant_id in participant_ids:
            raise HTTPException(422, f"duplicate participant id: {participant_id}")
        participant_ids.add(participant_id)
        normalized.append({"id": participant_id, "name": str(item["name"]).strip()})
    return {
        "id": meeting_id,
        "started_at": started_at,
        "timezone": timezone,
        "title": title,
        "participants": normalized,
        "mode": _mode(payload.get("mode")),
        "approval_status": "draft",
        "audio_path": None,
        "fixture_path": str(Path(__file__).parent / "examples" / "01_mixed_deadline.json") if _mode(payload.get("mode")) == "FIXTURE" else None,
    }


def _protocol_or_404(meeting_id: str) -> dict[str, Any]:
    protocol = storage.get_protocol(meeting_id)
    if not protocol:
        raise HTTPException(404, "protocol is not ready")
    return protocol


def _call_speech(audio_path: str, meeting: dict[str, Any]) -> dict[str, Any]:
    from services.speech import transcribe_and_diarize

    return transcribe_and_diarize(audio_path, {
        "mode": meeting["mode"],
        "whisper_model_path": str(settings.whisper_model_path),
        "device": settings.whisper_device,
        "compute_type": settings.whisper_compute_type,
        "pyannote_model_path": str(settings.pyannote_model_path),
        "fixture_path": meeting.get("fixture_path"),
    })


def _process_audio(job_id: str, meeting_id: str) -> None:
    meeting = storage.get_meeting(meeting_id)
    if not meeting or not meeting.get("audio_path"):
        storage.update_job(job_id, status="error", stage="speech", error="meeting or audio not found")
        return
    try:
        storage.update_job(job_id, status="running", stage="speech")
        speech = _call_speech(meeting["audio_path"], meeting)
        protocol = empty_protocol({k: meeting[k] for k in ("id", "started_at", "timezone", "title")}, meeting["mode"])
        protocol["participants"] = meeting["participants"]
        protocol["utterances"] = speech.get("utterances", [])
        protocol["speaker_map"] = []
        protocol["warnings"] = list(speech.get("warnings", []))
        # Validate the model boundary before persisting it.  A malformed ASR
        # result must produce an explicit failed job, never a protocol that
        # later becomes exportable or approvable.
        validate_protocol(protocol)
        meeting["detected_speakers"] = speech.get("detected_speakers", [])
        storage.save_meeting(meeting_id, meeting)
        storage.save_protocol(meeting_id, protocol)
        storage.add_audit(meeting_id, "speech_completed", {"detected_speakers": speech.get("detected_speakers", [])})
        storage.update_job(job_id, status="done", stage="speech")
    except Exception as exc:
        storage.update_job(job_id, status="error", stage="speech", error=_public_error("speech processing failed", exc))


@app.get("/")
def index() -> FileResponse:
    return FileResponse(Path(__file__).parent / "static" / "index.html")


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "mode": settings.mode,
        "whisper_model_path": str(settings.whisper_model_path),
        "whisper_model_exists": settings.whisper_model_path.exists(),
        "pyannote_model_path": str(settings.pyannote_model_path),
        "pyannote_model_exists": settings.pyannote_model_path.exists(),
        "ollama_host": settings.ollama_host,
        "ollama_model": settings.ollama_model,
        "camera_enabled": settings.camera_enabled,
    }


@app.post("/api/meetings")
def create_meeting(payload: dict[str, Any]) -> dict[str, Any]:
    meeting_id = str(uuid.uuid4())
    meeting = _meeting_from_input(payload, meeting_id)
    storage.save_meeting(meeting_id, meeting)
    return meeting


@app.post("/api/meetings/{meeting_id}/audio")
async def upload_audio(meeting_id: str, background_tasks: BackgroundTasks, file: UploadFile = File(...)) -> dict[str, str]:
    meeting = storage.get_meeting(meeting_id)
    if not meeting:
        raise HTTPException(404, "meeting not found")
    if not file.filename:
        raise HTTPException(422, "audio file is required")
    suffix = Path(file.filename).suffix.lower() or ".audio"
    recordings_dir = (settings.data_dir / "recordings").resolve()
    target = (recordings_dir / f"{meeting['id']}{suffix}").resolve()
    if target.parent != recordings_dir:
        raise HTTPException(422, "invalid audio destination")
    with target.open("wb") as output:
        shutil.copyfileobj(file.file, output)
    meeting["audio_path"] = str(target)
    storage.save_meeting(meeting_id, meeting)
    job_id = str(uuid.uuid4())
    storage.create_job(job_id, meeting_id)
    background_tasks.add_task(_process_audio, job_id, meeting_id)
    return {"job_id": job_id}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict[str, Any]:
    job = storage.get_job(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    return job


@app.get("/api/meetings/{meeting_id}/protocol")
def get_protocol(meeting_id: str) -> dict[str, Any]:
    return _protocol_or_404(meeting_id)


@app.put("/api/meetings/{meeting_id}/speaker-map")
def confirm_speaker_map(meeting_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    if not storage.get_meeting(meeting_id):
        raise HTTPException(404, "meeting not found")
    protocol = _protocol_or_404(meeting_id)
    speaker_map = payload.get("speaker_map")
    if not isinstance(speaker_map, list):
        raise HTTPException(422, "speaker_map must be an array")
    protocol["speaker_map"] = speaker_map
    validate_protocol(protocol)
    storage.save_protocol(meeting_id, protocol)
    storage.add_audit(meeting_id, "speaker_map_confirmed", {"speaker_map": speaker_map})
    return protocol


@app.post("/api/meetings/{meeting_id}/extract")
def extract_protocol(meeting_id: str) -> dict[str, Any]:
    meeting = storage.get_meeting(meeting_id)
    protocol = _protocol_or_404(meeting_id)
    if not meeting:
        raise HTTPException(404, "meeting not found")
    try:
        from services.protocol import extract
        result = extract(
            meeting={k: meeting[k] for k in ("id", "started_at", "timezone", "title")},
            participants=meeting["participants"],
            speaker_map=protocol.get("speaker_map", []),
            utterances=protocol.get("utterances", []),
            config={
                "mode": meeting["mode"],
                "ollama_host": settings.ollama_host,
                "ollama_model": settings.ollama_model,
                "schema_path": str(Path(__file__).parent / "contract.schema.json"),
            },
        )
    except Exception as exc:
        raise HTTPException(502, _public_error("protocol extraction failed", exc)) from exc
    protocol.update({k: result.get(k, protocol.get(k, [])) for k in ("tasks", "summary", "review_questions", "warnings")})
    validate_protocol(protocol)
    storage.save_protocol(meeting_id, protocol)
    storage.add_audit(meeting_id, "protocol_extracted", {"task_count": len(protocol["tasks"])})
    return protocol


@app.post("/api/meetings/{meeting_id}/ledger")
def ingest_ledger(meeting_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Attach a typed event sidecar without changing contract 1.0 itself."""
    meeting = storage.get_meeting(meeting_id)
    if not meeting:
        raise HTTPException(404, "meeting not found")
    protocol = _protocol_or_404(meeting_id)
    # Accept either the raw sidecar bundle or {"ledger": bundle}; the latter
    # lets callers add transport metadata without making it part of the
    # versioned ledger schema.
    ledger = payload.get("ledger") if set(payload) == {"ledger"} else payload
    if not isinstance(ledger, dict):
        raise HTTPException(422, "ledger must be an object")
    try:
        # The meeting row contains operational fields (mode, audio path,
        # approval state).  Keep those out of the preserved 1.0 snapshot
        # before the sidecar adapter validates its exact meeting shape.
        snapshot_protocol = copy.deepcopy(protocol)
        snapshot_protocol["meeting"] = {
            key: meeting[key] for key in ("id", "started_at", "timezone", "title")
        }
        adapted = ledger_to_protocol(ledger, snapshot_protocol)
    except ValueError as exc:
        raise HTTPException(422, f"ledger validation failed: {exc}") from exc
    storage.save_ledger(meeting_id, ledger)
    storage.save_protocol(meeting_id, adapted)
    storage.add_audit(
        meeting_id,
        "ledger_attached",
        {"event_count": len(ledger.get("events", [])), "task_count": len(adapted["tasks"])},
    )
    return adapted


@app.get("/api/meetings/{meeting_id}/ledger")
def get_ledger(meeting_id: str) -> dict[str, Any]:
    if not storage.get_meeting(meeting_id):
        raise HTTPException(404, "meeting not found")
    ledger = storage.get_ledger(meeting_id)
    if ledger is None:
        raise HTTPException(404, "ledger is not attached")
    return ledger


@app.post("/api/meetings/{meeting_id}/ledger/replay")
def replay_ledger(meeting_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Replay a typed evidence ledger and adapt it to snapshot contract 1.0."""
    meeting = storage.get_meeting(meeting_id)
    current = _protocol_or_404(meeting_id)
    if not meeting:
        raise HTTPException(404, "meeting not found")
    ledger = payload.get("ledger", payload)
    cutoff = payload.get("cutoff_ms") if isinstance(payload, dict) else None
    try:
        from services.ledger import replay, to_protocol, validate_bundle
        normalized = validate_bundle(ledger)
        sidecar = replay(normalized, cutoff)
        adapted = to_protocol(
            normalized,
            meeting={k: meeting[k] for k in ("id", "started_at", "timezone", "title")},
            participants=meeting["participants"],
            speaker_map=current.get("speaker_map", []),
            utterances=current.get("utterances", []),
            cutoff_ms=cutoff,
        )
        validate_protocol(adapted)
    except Exception as exc:
        raise HTTPException(422, f"ledger replay failed: {type(exc).__name__}: {exc}") from exc
    storage.save_ledger(meeting_id, normalized)
    # A historical replay is a read of the ledger.  Persisting it as the
    # current protocol would silently roll the meeting back to an earlier
    # state, so only a full replay may replace the stored snapshot.
    if cutoff is None:
        storage.save_protocol(meeting_id, adapted)
    storage.add_audit(meeting_id, "ledger_replayed", {"cutoff_ms": cutoff, "event_count": len(normalized["events"])})
    return {"snapshot": adapted, "ledger": sidecar}


@app.patch("/api/meetings/{meeting_id}/tasks/{task_id}")
def edit_task(meeting_id: str, task_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    if not storage.get_meeting(meeting_id):
        raise HTTPException(404, "meeting not found")
    protocol = _protocol_or_404(meeting_id)
    task = next((item for item in protocol["tasks"] if item["id"] == task_id), None)
    if not task:
        raise HTTPException(404, "task not found")
    allowed = {"action", "assignee_id", "due_date", "due_time", "deadline_raw", "dialogue_status"}
    changes = {key: value for key, value in payload.items() if key in allowed}
    if not changes:
        raise HTTPException(422, "no editable task fields supplied")
    previous = {key: task.get(key) for key in changes}
    task.update(changes)
    task["review_status"] = "needs_review"
    validate_protocol(protocol)
    storage.save_protocol(meeting_id, protocol)
    # The human correction is a storage audit event, never synthetic audio
    # evidence.  Record it only after the edited snapshot passed validation.
    storage.add_audit(meeting_id, "human_task_edit", {"task_id": task_id, "previous": previous, "changes": changes})
    return task


@app.post("/api/meetings/{meeting_id}/approve")
def approve(meeting_id: str) -> dict[str, Any]:
    meeting = storage.get_meeting(meeting_id)
    if not meeting:
        raise HTTPException(404, "meeting not found")
    protocol = _protocol_or_404(meeting_id)
    for task in protocol["tasks"]:
        task["review_status"] = "approved"
    validate_protocol(protocol)
    meeting["approval_status"] = "approved"
    storage.save_meeting(meeting_id, meeting)
    storage.save_protocol(meeting_id, protocol)
    storage.add_audit(meeting_id, "protocol_approved", {})
    return {"approved": True, "protocol": protocol}


@app.get("/api/meetings/{meeting_id}/export.docx")
def export_docx(meeting_id: str):
    meeting = storage.get_meeting(meeting_id)
    protocol = _protocol_or_404(meeting_id)
    try:
        from services.export_docx import render
        export_protocol = copy.deepcopy(protocol)
        # approval_status is storage metadata, not part of contract 1.0.
        # Pass it only to the presentation layer so DOCX is labelled honestly.
        export_protocol["approval_status"] = (meeting or {}).get("approval_status", "draft")
        content = render(export_protocol)
    except Exception as exc:
        raise HTTPException(500, _public_error("DOCX export failed", exc)) from exc
    output = (settings.data_dir / f"{meeting['id']}.docx").resolve()
    if output.parent != settings.data_dir.resolve():
        raise HTTPException(500, "invalid export destination")
    output.write_bytes(content)
    return FileResponse(output, media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document", filename="meeting-protocol.docx")


@app.post("/api/meetings/{meeting_id}/catch-up")
def catch_up(meeting_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    protocol = _protocol_or_404(meeting_id)
    try:
        start_ms = int(payload["start_ms"])
        end_ms = int(payload["end_ms"])
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(422, "start_ms and end_ms must be integers") from exc
    from services.protocol import catch_up as protocol_catch_up
    return protocol_catch_up(protocol, start_ms, end_ms)


@app.exception_handler(ValueError)
async def value_error_handler(_, exc: ValueError) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": str(exc)})
