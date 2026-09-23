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
from contracts import empty_protocol, validate_protocol
from storage import Storage


settings.ensure_dirs()
storage = Storage(settings.db_path)
app = FastAPI(title="Meeting assistant", version="0.1.0")
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")


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
    for item in participants:
        if not isinstance(item, dict) or not str(item.get("id") or "").strip() or not str(item.get("name") or "").strip():
            raise HTTPException(422, "each participant needs id and name")
        normalized.append({"id": str(item["id"]), "name": str(item["name"])})
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
        meeting["detected_speakers"] = speech.get("detected_speakers", [])
        storage.save_meeting(meeting_id, meeting)
        storage.save_protocol(meeting_id, protocol)
        storage.add_audit(meeting_id, "speech_completed", {"detected_speakers": speech.get("detected_speakers", [])})
        storage.update_job(job_id, status="done", stage="speech")
    except Exception as exc:
        storage.update_job(job_id, status="error", stage="speech", error=f"{type(exc).__name__}: {exc}")


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
    target = settings.data_dir / "recordings" / f"{meeting_id}{suffix}"
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
        raise HTTPException(502, f"protocol extraction failed: {type(exc).__name__}: {exc}") from exc
    protocol.update({k: result.get(k, protocol.get(k, [])) for k in ("tasks", "summary", "review_questions", "warnings")})
    validate_protocol(protocol)
    storage.save_protocol(meeting_id, protocol)
    storage.add_audit(meeting_id, "protocol_extracted", {"task_count": len(protocol["tasks"])})
    return protocol


@app.patch("/api/meetings/{meeting_id}/tasks/{task_id}")
def edit_task(meeting_id: str, task_id: str, payload: dict[str, Any]) -> dict[str, Any]:
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
    storage.add_audit(meeting_id, "human_task_edit", {"task_id": task_id, "previous": previous, "changes": changes})
    validate_protocol(protocol)
    storage.save_protocol(meeting_id, protocol)
    return task


@app.post("/api/meetings/{meeting_id}/approve")
def approve(meeting_id: str) -> dict[str, Any]:
    meeting = storage.get_meeting(meeting_id)
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
        raise HTTPException(500, f"DOCX export failed: {type(exc).__name__}: {exc}") from exc
    output = settings.data_dir / f"{meeting_id}.docx"
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
