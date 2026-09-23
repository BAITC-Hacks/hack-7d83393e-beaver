from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def _bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    mode: str = os.getenv("APP_MODE", "REAL").upper()
    data_dir: Path = Path(os.getenv("DATA_DIR", str(ROOT / "data")))
    whisper_model_path: Path = Path(os.getenv("WHISPER_MODEL_PATH", str(ROOT / "models" / "faster-whisper-small")))
    whisper_device: str = os.getenv("WHISPER_DEVICE", "cpu")
    whisper_compute_type: str = os.getenv("WHISPER_COMPUTE_TYPE", "int8")
    pyannote_model_path: Path = Path(os.getenv("PYANNOTE_MODEL_PATH", str(ROOT / "models" / "pyannote-community-1")))
    ollama_host: str = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434")
    ollama_model: str = os.getenv("OLLAMA_MODEL", "qwen2.5:3b")
    camera_enabled: bool = _bool("CAMERA_ENABLED")

    @property
    def db_path(self) -> Path:
        return self.data_dir / "meetings.sqlite3"

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "recordings").mkdir(parents=True, exist_ok=True)


settings = Settings()
