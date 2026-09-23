#!/usr/bin/env python3
"""Check local model files and runtime dependencies without network access.

The command is intentionally observational: it never downloads weights and it
does not send audio or transcript data anywhere.  Use ``--strict`` in a CI or
offline acceptance gate when every required component must be available.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import wave
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _check_dependency(module: str, label: str | None = None) -> dict[str, Any]:
    try:
        present = importlib.util.find_spec(module) is not None
    except ModuleNotFoundError:
        present = False
    return {"name": label or module, "module": module, "available": present}


def _check_path(path: Path, label: str, required_files: tuple[str, ...] = ()) -> dict[str, Any]:
    """Inspect a local model directory without resolving anything online.

    ``Path.exists()`` alone is too weak for a model preflight: a Hugging Face
    model directory containing only ``README.md`` would otherwise be reported
    as ready.  The required file list is deliberately small and format based;
    inference still remains the authoritative model check.
    """
    result: dict[str, Any] = {
        "name": label,
        "path": str(path),
        "available": False,
        "is_directory": path.is_dir() if path.exists() else False,
    }
    if not path.exists():
        result["message"] = "каталог не найден"
        return result
    if not path.is_dir():
        result["message"] = "путь не является каталогом"
        return result
    missing = [name for name in required_files if not (path / name).is_file()]
    result["required_files"] = list(required_files)
    result["missing_files"] = missing
    result["available"] = not missing and any(path.iterdir())
    if missing:
        result["message"] = "неполный комплект весов: " + ", ".join(missing)
    return result


def _audio_check(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {"name": "audio", "available": None, "message": "не задан --audio"}
    result: dict[str, Any] = {"name": "audio", "path": str(path), "available": False}
    if not path.is_file():
        result["message"] = "файл не найден"
        return result
    try:
        with wave.open(str(path), "rb") as source:
            result.update(
                available=True,
                format="wav",
                channels=source.getnchannels(),
                sample_width=source.getsampwidth(),
                sample_rate=source.getframerate(),
                frames=source.getnframes(),
            )
            return result
    except (wave.Error, EOFError):
        # ffprobe is only used to inspect a local file.  No URL is accepted.
        ffprobe = shutil.which("ffprobe")
        if ffprobe:
            probe = subprocess.run(
                [ffprobe, "-v", "error", "-show_entries", "format=format_name", "-of", "default=nw=1:nk=1", str(path)],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            if probe.returncode == 0 and probe.stdout.strip():
                result.update(available=True, format=probe.stdout.strip())
                return result
        result["message"] = "не удалось прочитать WAV; ffprobe недоступен или формат не поддержан"
        return result


def _ollama_check(model: str) -> dict[str, Any]:
    configured = os.environ.get("OLLAMA_BIN")
    executable = configured if configured and Path(configured).is_file() else shutil.which("ollama")
    result: dict[str, Any] = {
        "name": "local_llm",
        "model": model,
        "executable": executable,
        "available": False,
    }
    if not executable:
        result["message"] = "команда ollama не найдена"
        return result
    try:
        process = subprocess.run(
            [executable, "list"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        result["message"] = f"ollama list не выполнен: {exc}"
        return result
    result["command_returncode"] = process.returncode
    names = []
    for line in process.stdout.splitlines()[1:]:
        fields = line.split()
        if fields:
            names.append(fields[0])
    result["installed_models"] = names
    result["available"] = process.returncode == 0 and model in names
    if not result["available"]:
        result["message"] = "модель не найдена в локальном ollama list"
    return result


def run_preflight(
    *,
    project_root: Path = PROJECT_ROOT,
    asr_model: Path | None = None,
    diarization_model: Path | None = None,
    llm_model: str = "qwen2.5:3b",
    audio: Path | None = None,
) -> dict[str, Any]:
    """Return a JSON-compatible local readiness report."""

    asr_model = asr_model or Path(
        os.environ.get("ASR_MODEL_PATH")
        or os.environ.get("WHISPER_MODEL_PATH")
        or project_root / "models" / "faster-whisper-small"
    )
    diarization_model = diarization_model or Path(
        os.environ.get("DIARIZATION_MODEL_PATH")
        or os.environ.get("PYANNOTE_MODEL_PATH")
        or project_root / "models" / "pyannote-community-1"
    )
    checks = [
        _check_path(asr_model, "asr_weights", ("config.json", "model.bin", "tokenizer.json")),
        _check_path(diarization_model, "diarization_weights", ("config.yaml",)),
        _ollama_check(llm_model),
        _audio_check(audio),
    ]
    dependencies = [
        _check_dependency("fastapi", "FastAPI"),
        _check_dependency("docx", "python-docx"),
        _check_dependency("faster_whisper", "faster-whisper"),
        _check_dependency("pyannote.audio", "pyannote.audio"),
        _check_dependency("jsonschema", "jsonschema"),
        _check_dependency("soundfile", "soundfile"),
        _check_dependency("torch", "torch"),
    ]
    checks.extend(dependencies)
    unavailable = [item["name"] for item in checks if item.get("available") is False]
    return {
        "project_root": str(project_root),
        "offline": True,
        "checks": checks,
        "ready": not unavailable,
        "unavailable": unavailable,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--asr-model", type=Path)
    parser.add_argument("--diarization-model", type=Path)
    parser.add_argument("--llm-model", default=os.environ.get("OLLAMA_MODEL", "qwen2.5:3b"))
    parser.add_argument("--audio", type=Path, help="локальный аудиофайл для проверки чтения")
    parser.add_argument("--json", action="store_true", help="вывести JSON вместо таблицы")
    parser.add_argument("--strict", action="store_true", help="завершиться с кодом 1 при отсутствии компонента")
    args = parser.parse_args(argv)
    report = run_preflight(
        asr_model=args.asr_model,
        diarization_model=args.diarization_model,
        llm_model=args.llm_model,
        audio=args.audio,
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print("Локальный preflight (исходящие соединения не используются)")
        for item in report["checks"]:
            state = "PASS" if item.get("available") else ("SKIP" if item.get("available") is None else "FAIL")
            suffix = f" — {item.get('message')}" if item.get("message") else ""
            print(f"{state:4} {item['name']}: {item.get('path', item.get('model', ''))}{suffix}")
        print("READY" if report["ready"] else "NOT READY")
    return 0 if report["ready"] or not args.strict else 1


if __name__ == "__main__":
    raise SystemExit(main())
