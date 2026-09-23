#!/usr/bin/env python3
"""Create a local model capability manifest without network access.

The manifest records what is present on this machine.  It intentionally does
not call Hugging Face or an LLM service and never turns an import into an
inference PASS.  Run it after every model preparation or environment change.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def _path_from_env(*names: str, default: Path) -> Path:
    for name in names:
        value = os.environ.get(name)
        if value:
            return Path(value).expanduser()
    return default


def _sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
    except OSError:
        return None
    return digest.hexdigest()


def _directory_digest(path: Path) -> str | None:
    """Hash relative file names and contents in stable order."""
    if not path.is_dir():
        return None
    digest = hashlib.sha256()
    files = sorted(item for item in path.rglob("*") if item.is_file())
    if not files:
        return None
    for item in files:
        relative = item.relative_to(path).as_posix().encode()
        file_digest = _sha256(item)
        if file_digest is None:
            return None
        digest.update(relative + b"\0" + file_digest.encode() + b"\n")
    return digest.hexdigest()


def _package_versions() -> dict[str, str | None]:
    packages = ("faster-whisper", "pyannote.audio", "ollama", "torch", "python-docx")
    result: dict[str, str | None] = {}
    for package in packages:
        try:
            result[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            result[package] = None
    return result


def _hardware() -> dict[str, Any]:
    result: dict[str, Any] = {
        "python": platform.python_version(),
        "system": platform.platform(),
        "cpu_count": os.cpu_count(),
    }
    try:
        import torch

        result["torch"] = torch.__version__
        result["cuda_available"] = bool(torch.cuda.is_available())
        result["cuda_device"] = torch.cuda.get_device_name(0) if torch.cuda.is_available() else None
    except Exception as exc:  # pragma: no cover - depends on optional torch
        result["torch_error"] = f"{type(exc).__name__}: {exc}"
    try:
        usage = shutil.disk_usage(ROOT)
        result["disk_free_bytes"] = usage.free
    except OSError:
        pass
    return result


def _ollama(model: str) -> dict[str, Any]:
    configured = os.environ.get("OLLAMA_BIN")
    executable = configured if configured and Path(configured).is_file() else shutil.which("ollama")
    result: dict[str, Any] = {"model": model, "executable": executable, "installed": False}
    if not executable:
        result["message"] = "команда ollama не найдена"
        return result
    try:
        process = subprocess.run(
            [executable, "list"], capture_output=True, text=True, timeout=10, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        result["message"] = f"ollama list не выполнен: {exc}"
        return result
    result["returncode"] = process.returncode
    names = []
    for line in process.stdout.splitlines()[1:]:
        fields = line.split()
        if fields:
            names.append(fields[0])
    result["installed_models"] = names
    result["installed"] = process.returncode == 0 and model in names
    if not result["installed"]:
        result["message"] = "модель не найдена в локальном ollama list"
    return result


def _model_entry(
    *,
    name: str,
    source: str,
    model_id: str,
    revision: str | None,
    license_name: str,
    path: Path,
    required_files: tuple[str, ...],
    inference: str = "NOT RUN",
) -> dict[str, Any]:
    missing = [file for file in required_files if not (path / file).is_file()]
    present = path.is_dir() and not missing and bool(list(path.iterdir()))
    return {
        "name": name,
        "source": source,
        "model_id": model_id,
        "revision": revision,
        "license": license_name,
        "local_path": str(path),
        "directory_sha256": _directory_digest(path),
        "required_files": list(required_files),
        "missing_files": missing,
        "preflight": "PASS" if present else "FAIL",
        "inference": inference,
    }


def _asr_inference(path: Path, project_root: Path) -> str:
    audio = project_root / "data" / "synthetic" / "ru_sine.wav"
    if not path.is_dir() or not audio.is_file():
        return "NOT RUN"
    try:
        from faster_whisper import WhisperModel

        device = os.environ.get("WHISPER_DEVICE", "cpu")
        compute_type = os.environ.get("WHISPER_COMPUTE_TYPE", "int8" if device == "cpu" else "float16")
        model = WhisperModel(str(path), device=device, compute_type=compute_type, local_files_only=True)
        list(model.transcribe(str(audio), task="transcribe", beam_size=1, vad_filter=True)[0])
        return "PASS"
    except Exception as exc:  # pragma: no cover - native model runtime
        return f"FAIL: {type(exc).__name__}: {exc}"


def _diarization_inference(path: Path, project_root: Path) -> str:
    audio = project_root / "data" / "synthetic" / "ru_sine.wav"
    model = path / "segmentation" / "model_int8.onnx"
    if not model.is_file() or not audio.is_file():
        return "NOT RUN"
    try:
        import numpy as np
        import onnxruntime as ort
        import soundfile as sf
        samples, rate = sf.read(audio, dtype="float32")
        if rate != 16000:
            return "NOT RUN"
        if samples.ndim > 1:
            samples = samples.mean(axis=1)
        output = ort.InferenceSession(str(model)).run(None, {"waveform": samples[np.newaxis, np.newaxis, :]})[0]
        return "PASS" if output.ndim == 3 and output.shape[-1] == 7 else "FAIL"
    except Exception as exc:
        return f"FAIL: {type(exc).__name__}: {exc}"


def build_manifest(project_root: Path = ROOT) -> dict[str, Any]:
    asr_path = _path_from_env(
        "ASR_MODEL_PATH", "WHISPER_MODEL_PATH", default=project_root / "models" / "faster-whisper-small"
    )
    diar_path = _path_from_env(
        "DIARIZATION_MODEL_PATH", "PYANNOTE_MODEL_PATH", default=project_root / "models" / "pyannote-community-1"
    )
    llm_model = os.environ.get("OLLAMA_MODEL", "qwen2.5:3b")
    ollama = _ollama(llm_model)
    return {
        "schema_version": "1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "offline": True,
        "hardware": _hardware(),
        "packages": _package_versions(),
        "models": [
            _model_entry(
                name="asr",
                source="Hugging Face",
                model_id="Systran/faster-whisper-small",
                revision=None,
                license_name="MIT (model card)",
                path=asr_path,
                required_files=("config.json", "model.bin", "tokenizer.json"),
                inference=_asr_inference(asr_path, project_root),
            ),
            _model_entry(
                name="diarization",
                source="Hugging Face",
                model_id="FredrikKarlssonSpeech/pyannote-speaker-diarization-onnx" if (diar_path / "segmentation" / "model_int8.onnx").is_file() else "pyannote/speaker-diarization-community-1",
                revision=None,
                license_name="CC-BY-4.0",
                path=diar_path,
                required_files=("segmentation/model_int8.onnx",) if (diar_path / "segmentation" / "model_int8.onnx").is_file() else ("config.yaml",),
                inference=_diarization_inference(diar_path, project_root),
            ),
            {
                "name": "llm",
                "source": "Ollama local runtime",
                "model_id": llm_model,
                "revision": None,
                "license": "check the model card before redistribution",
                "local_path": None,
                "directory_sha256": None,
                "preflight": "PASS" if ollama["installed"] else "FAIL",
                "inference": "NOT RUN",
                "ollama": ollama,
            },
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "models" / "manifest.json")
    parser.add_argument("--stdout", action="store_true", help="print JSON without writing it")
    args = parser.parse_args(argv)
    manifest = build_manifest()
    encoded = json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    if args.stdout:
        print(encoded, end="")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
        print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
