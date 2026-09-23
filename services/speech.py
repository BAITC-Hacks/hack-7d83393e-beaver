"""Local speech recognition and speaker diarization.

The module deliberately keeps model loading behind :func:`transcribe_and_diarize`.
There is no network fallback: ``REAL`` requires two local model directories and
``FIXTURE`` requires fixture data supplied by the caller.  The latter is useful
for exercising the rest of the application, but is always marked as synthetic.

The public return value is the speech part of API contract 1.0::

    {
        "utterances": [{"id", "start_ms", "end_ms", "speaker_id", "text"}],
        "detected_speakers": ["SPEAKER_00", ...],
        "warnings": [...],
    }

``language`` is returned as optional metadata when faster-whisper reports a
language.  It records detection only; the ASR task is always ``transcribe`` and
never ``translate``.
"""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


class SpeechError(RuntimeError):
    """Base class for errors raised by this module."""


class SpeechConfigurationError(SpeechError, ValueError):
    """The mode or model configuration cannot be used."""


class ModelUnavailableError(SpeechError, FileNotFoundError):
    """A required local model is absent or cannot be loaded."""


class AudioInputError(SpeechError, ValueError):
    """The input path is not a usable non-empty audio file."""


@dataclass(frozen=True)
class _SpeechOptions:
    mode: str
    asr_model_path: Path | None
    diarization_model_path: Path | None
    device: str
    compute_type: str
    language: str | None
    beam_size: int
    vad_filter: bool
    word_timestamps: bool
    fixture: Any = None


_SYNTHETIC_WARNING = "Синтетический пример, не результат распознавания."


def transcribe_and_diarize(path: str | os.PathLike[str], config: Mapping[str, Any] | Any | None = None) -> dict[str, Any]:
    """Transcribe an audio file and attach diarized speaker labels.

    ``config`` accepts either a mapping or an object with attributes.  In
    ``REAL`` mode both model paths must point to local directories.  In
    ``FIXTURE`` mode no model is loaded and fixture utterances must be supplied
    in ``fixture``/``fixture_data``/``fixture_utterances`` or by a local JSON
    ``fixture_path``.  A fixture is never silently used in ``REAL`` mode.
    """

    options = _read_options(config)
    if options.mode == "FIXTURE":
        return _fixture_result(path, options, config)

    audio_path = _validate_audio_path(path)
    asr_path = _require_local_model(options.asr_model_path, "faster-whisper")
    diar_path = _require_local_model(options.diarization_model_path, "pyannote")

    whisper = _load_whisper_model(asr_path, options)
    segments, info = _transcribe(whisper, audio_path, options)
    diarizer = _load_diarization_model(diar_path, options)
    diarization = _run_diarization(diarizer, audio_path)
    intervals, detected_speakers = _read_diarization(diarization)

    utterances, warnings = _assign_speakers(segments, intervals, options)
    if not utterances:
        warnings.append("ASR не вернул непустых реплик.")
    result: dict[str, Any] = {
        "utterances": utterances,
        "detected_speakers": detected_speakers,
        "warnings": _dedupe(warnings),
    }

    language = _get_value(info, "language")
    if language:
        # This key is speech metadata and is intentionally not copied into an
        # utterance: contract.schema.json permits only the five utterance keys.
        result["language"] = str(language)
    return result


def _read_options(config: Mapping[str, Any] | Any | None) -> _SpeechOptions:
    if config is None:
        config = {}

    def value(*names: str, default: Any = None) -> Any:
        for name in names:
            if isinstance(config, Mapping) and name in config:
                return config[name]
            if hasattr(config, name):
                return getattr(config, name)
        return default

    mode = str(value("mode", default="REAL")).upper()
    if mode not in {"REAL", "FIXTURE"}:
        raise SpeechConfigurationError("mode должен быть REAL или FIXTURE")

    asr = value("asr_model_path", "faster_whisper_model_path", "whisper_model_path")
    diar = value("diarization_model_path", "pyannote_model_path", "diarization_path")
    beam = value("beam_size", default=5)
    try:
        beam = int(beam)
    except (TypeError, ValueError) as exc:
        raise SpeechConfigurationError("beam_size должен быть целым числом") from exc
    if beam < 1:
        raise SpeechConfigurationError("beam_size должен быть положительным")

    vad_filter = value("vad_filter", default=True)
    word_timestamps = value("word_timestamps", default=True)
    if isinstance(vad_filter, str):
        vad_filter = vad_filter.strip().lower() in {"1", "true", "yes", "on"}
    else:
        vad_filter = bool(vad_filter)
    if isinstance(word_timestamps, str):
        word_timestamps = word_timestamps.strip().lower() in {"1", "true", "yes", "on"}
    else:
        word_timestamps = bool(word_timestamps)

    return _SpeechOptions(
        mode=mode,
        asr_model_path=_as_path(asr),
        diarization_model_path=_as_path(diar),
        device=str(value("device", default="cpu")),
        compute_type=str(value("compute_type", default="int8")),
        language=value("language", default=None),
        beam_size=beam,
        vad_filter=vad_filter,
        word_timestamps=word_timestamps,
        fixture=value("fixture", "fixture_data", "fixture_utterances", default=None),
    )


def _as_path(value: Any) -> Path | None:
    if value is None or value == "":
        return None
    if not isinstance(value, (str, os.PathLike)):
        raise SpeechConfigurationError("Путь к модели должен быть локальным путём")
    raw = os.fspath(value)
    if "://" in raw:
        raise SpeechConfigurationError(f"Внешние URL моделей запрещены: {raw}")
    return Path(raw).expanduser().resolve()


def _validate_audio_path(path: str | os.PathLike[str]) -> Path:
    if not isinstance(path, (str, os.PathLike)):
        raise AudioInputError("Путь к аудио должен быть строкой или Path")
    audio = Path(path).expanduser().resolve()
    if not audio.exists():
        raise AudioInputError(f"Аудиофайл не найден: {audio}")
    if not audio.is_file():
        raise AudioInputError(f"Ожидался файл аудио, получено: {audio}")
    try:
        size = audio.stat().st_size
    except OSError as exc:
        raise AudioInputError(f"Не удалось прочитать аудиофайл: {audio}") from exc
    if size <= 0:
        raise AudioInputError(f"Аудиофайл пуст: {audio}")
    return audio


def _require_local_model(path: Path | None, name: str) -> Path:
    if path is None:
        raise ModelUnavailableError(f"Не задан локальный путь модели {name}")
    if not path.exists():
        raise ModelUnavailableError(f"Локальная модель {name} не найдена: {path}")
    if not path.is_dir():
        raise ModelUnavailableError(f"Путь модели {name} не является каталогом: {path}")
    if not any(path.iterdir()):
        raise ModelUnavailableError(f"Каталог локальной модели {name} пуст: {path}")
    return path


def _load_whisper_model(path: Path, options: _SpeechOptions) -> Any:
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise ModelUnavailableError(
            "Пакет faster-whisper не установлен; REAL режим не может продолжить работу"
        ) from exc
    try:
        return WhisperModel(
            str(path),
            device=options.device,
            compute_type=options.compute_type,
            local_files_only=True,
        )
    except TypeError:
        # Older faster-whisper releases do not expose local_files_only.  The
        # path has already been validated as local, so retrying without this
        # optional keyword does not introduce a model download.
        try:
            return WhisperModel(
                str(path), device=options.device, compute_type=options.compute_type
            )
        except Exception as exc:  # pragma: no cover - depends on native runtime
            raise ModelUnavailableError(f"Не удалось загрузить модель faster-whisper: {path}") from exc
    except Exception as exc:  # pragma: no cover - depends on native runtime
        raise ModelUnavailableError(f"Не удалось загрузить модель faster-whisper: {path}") from exc


@contextmanager
def _huggingface_offline() -> Iterable[None]:
    """Prevent pyannote/huggingface from resolving files over the network."""

    names = ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE")
    old = {name: os.environ.get(name) for name in names}
    os.environ.update({name: "1" for name in names})
    try:
        yield
    finally:
        for name, value in old.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def _load_diarization_model(path: Path, options: _SpeechOptions) -> Any:
    # A small, open ONNX export of the community-1 segmentation component is
    # accepted as a local alternative when the original gated PyTorch
    # pipeline is unavailable.  It produces powerset speaker activity; the
    # stable slot labels are intentionally still subject to secretary mapping.
    onnx_path = path / "segmentation" / "model_int8.onnx"
    if onnx_path.is_file():
        try:
            import onnxruntime as ort
            import numpy as np
            import soundfile as sf
        except ImportError as exc:
            raise ModelUnavailableError("ONNX diarization requires onnxruntime, numpy and soundfile") from exc

        class _OnnxDiarizer:
            def __init__(self) -> None:
                providers = ["CPUExecutionProvider"]
                if options.device != "cpu" and "CUDAExecutionProvider" in ort.get_available_providers():
                    providers.insert(0, "CUDAExecutionProvider")
                self.session = ort.InferenceSession(str(onnx_path), providers=providers)
                self.np = np
                self.sf = sf

            def __call__(self, audio_path: str) -> list[tuple[int, int, str]]:
                audio, rate = self.sf.read(audio_path, dtype="float32")
                if audio.ndim > 1:
                    audio = audio.mean(axis=1)
                if rate != 16000:
                    from scipy.signal import resample_poly
                    import math
                    divisor = math.gcd(int(rate), 16000)
                    audio = resample_poly(audio, 16000 // divisor, int(rate) // divisor).astype("float32")
                waveform = audio[self.np.newaxis, self.np.newaxis, :]
                scores = self.session.run(None, {"waveform": waveform})[0][0]
                labels = self.np.argmax(scores, axis=-1)
                frame_ms = 1000.0 * len(audio) / max(1, len(labels))
                active: list[tuple[int, int, str]] = []
                current: tuple[int, int, str] | None = None
                powerset = {1: "SPEAKER_00", 2: "SPEAKER_01", 4: "SPEAKER_02", 3: "SPEAKER_00", 5: "SPEAKER_00", 6: "SPEAKER_01"}
                for index, raw_label in enumerate(labels):
                    label = powerset.get(int(raw_label))
                    start, end = round(index * frame_ms), round((index + 1) * frame_ms)
                    if label is None:
                        if current is not None:
                            active.append(current)
                            current = None
                        continue
                    if current is not None and current[2] == label and current[1] >= start - 40:
                        current = (current[0], end, label)
                    else:
                        if current is not None:
                            active.append(current)
                        current = (start, end, label)
                if current is not None:
                    active.append(current)
                return [item for item in active if item[1] - item[0] >= 120]

        return _OnnxDiarizer()
    try:
        from pyannote.audio import Pipeline
    except ImportError as exc:
        raise ModelUnavailableError(
            "Пакет pyannote.audio не установлен; REAL режим не может продолжить работу"
        ) from exc
    try:
        # community-1 is loaded from a prepared local directory.  Offline
        # environment variables are set even though older pyannote versions do
        # not accept a local_files_only keyword.
        with _huggingface_offline():
            pipeline = Pipeline.from_pretrained(str(path))
    except Exception as exc:  # pragma: no cover - depends on native runtime
        raise ModelUnavailableError(f"Не удалось загрузить модель pyannote: {path}") from exc

    if options.device and options.device != "cpu" and hasattr(pipeline, "to"):
        try:
            import torch

            pipeline.to(torch.device(options.device))
        except ImportError as exc:  # pragma: no cover - optional GPU runtime
            raise ModelUnavailableError("Для device != cpu нужен установленный torch") from exc
        except Exception as exc:  # pragma: no cover - optional GPU runtime
            raise ModelUnavailableError(f"Не удалось переключить pyannote на {options.device}") from exc
    return pipeline


def _transcribe(model: Any, audio_path: Path, options: _SpeechOptions) -> tuple[list[Any], Any]:
    kwargs: dict[str, Any] = {
        "task": "transcribe",
        "beam_size": options.beam_size,
        "vad_filter": options.vad_filter,
        "word_timestamps": options.word_timestamps,
    }
    if options.language:
        kwargs["language"] = str(options.language)
    try:
        segments, info = model.transcribe(str(audio_path), **kwargs)
        return list(segments), info
    except Exception as exc:  # pragma: no cover - depends on native runtime
        raise SpeechError(f"Ошибка локального ASR для {audio_path}: {exc}") from exc


def _run_diarization(model: Any, audio_path: Path) -> Any:
    try:
        # Keep the offline guard active for invocation too.  A pyannote
        # pipeline normally resolves all components during construction, but
        # some releases defer a component lookup until the first call.
        with _huggingface_offline():
            return model(str(audio_path))
    except Exception as exc:  # pragma: no cover - depends on native runtime
        raise SpeechError(f"Ошибка локальной диаризации для {audio_path}: {exc}") from exc


def _read_diarization(result: Any) -> tuple[list[tuple[int, int, str]], list[str]]:
    if isinstance(result, list) and all(isinstance(item, tuple) and len(item) == 3 for item in result):
        intervals = [(int(start), int(end), str(label)) for start, end, label in result if int(end) > int(start)]
        return intervals, sorted({label for _start, _end, label in intervals})
    annotation = getattr(result, "speaker_diarization", result)
    if not hasattr(annotation, "itertracks"):
        raise SpeechError("pyannote не вернул Annotation с itertracks()")
    intervals: list[tuple[int, int, str]] = []
    speakers: list[str] = []
    try:
        tracks = annotation.itertracks(yield_label=True)
        for item in tracks:
            if len(item) != 3:
                continue
            segment, _track, label = item
            start = _ms(getattr(segment, "start", None))
            end = _ms(getattr(segment, "end", None))
            if end <= start:
                continue
            speaker = str(label)
            intervals.append((start, end, speaker))
            if speaker not in speakers:
                speakers.append(speaker)
    except Exception as exc:  # pragma: no cover - depends on pyannote version
        raise SpeechError(f"Не удалось прочитать интервалы pyannote: {exc}") from exc
    intervals.sort(key=lambda item: (item[0], item[1], item[2]))
    speakers.sort()
    return intervals, speakers


def _assign_speakers(
    segments: Sequence[Any],
    intervals: Sequence[tuple[int, int, str]],
    options: _SpeechOptions,
) -> tuple[list[dict[str, Any]], list[str]]:
    del options  # kept in the signature for a stable internal seam for tests
    warnings: list[str] = []
    utterances: list[dict[str, Any]] = []
    next_id = 1
    for segment in segments:
        start = _ms(_get_value(segment, "start"))
        end = _ms(_get_value(segment, "end"))
        text = str(_get_value(segment, "text", default="") or "").strip()
        if end <= start:
            if text:
                warnings.append(f"Пропущена реплика с некорректными метками: {start}-{end} мс.")
            continue
        if not text:
            continue
        words = _get_value(segment, "words", default=None)
        pieces, piece_warning = _split_segment_with_words(start, end, text, words, intervals)
        if pieces is None:
            speaker, ambiguous = _speaker_for_interval(start, end, intervals)
            if ambiguous:
                speaker = None
                warnings.append(
                    f"Спорное сопоставление говорящего для интервала {start}-{end} мс; speaker_id оставлен null."
                )
            elif speaker is None and intervals:
                warnings.append(f"Для реплики {start}-{end} мс не найден говорящий.")
            elif speaker is None and not intervals:
                warnings.append("Диаризация не вернула интервалов; speaker_id оставлен null.")
            pieces = [(start, end, speaker, text)]
        elif piece_warning:
            warnings.append(piece_warning)

        for piece_start, piece_end, speaker, piece_text in pieces:
            if piece_end <= piece_start or not piece_text.strip():
                continue
            utterances.append(
                {
                    "id": f"u{next_id}",
                    "start_ms": piece_start,
                    "end_ms": piece_end,
                    "speaker_id": speaker,
                    "text": piece_text.strip(),
                }
            )
            next_id += 1
    return utterances, warnings


def _split_segment_with_words(
    start: int,
    end: int,
    text: str,
    words: Any,
    intervals: Sequence[tuple[int, int, str]],
) -> tuple[list[tuple[int, int, str | None, str]] | None, str | None]:
    if not words:
        return None, None
    parsed: list[tuple[int, int, str, str]] = []
    try:
        for word in words:
            word_text = str(_get_value(word, "word", "text", default="") or "").strip()
            word_start = _ms(_get_value(word, "start"))
            word_end = _ms(_get_value(word, "end"))
            if word_text and word_end > word_start:
                parsed.append((max(start, word_start), min(end, word_end), "", word_text))
    except (TypeError, ValueError):
        return None, None
    if not parsed:
        return None, None

    groups: list[tuple[int, int, str | None, list[str]]] = []
    for word_start, word_end, _empty, word_text in parsed:
        speaker, ambiguous = _speaker_for_interval(word_start, word_end, intervals)
        if ambiguous:
            speaker = None
        if groups and groups[-1][2] == speaker:
            gs, ge, gspeaker, words_text = groups[-1]
            groups[-1] = (gs, word_end, gspeaker, words_text + [word_text])
        else:
            groups.append((word_start, word_end, speaker, [word_text]))

    # Split only when each piece has a real time range.  If words cannot be
    # tied to a speaker, preserving the time ranges while leaving speaker_id
    # null is safer than inventing a speaker.
    pieces = [(gs, ge, speaker, " ".join(words_text)) for gs, ge, speaker, words_text in groups]
    warning = None
    labels = {speaker for _s, _e, speaker, _t in pieces if speaker is not None}
    if len(labels) > 1:
        warning = f"Реплика {start}-{end} мс разделена по временным меткам нескольких говорящих."
    if any(speaker is None for _s, _e, speaker, _t in pieces):
        warning = (warning + " " if warning else "") + "Часть слов не имеет однозначного speaker_id."
    return pieces, warning


def _speaker_for_interval(
    start: int, end: int, intervals: Sequence[tuple[int, int, str]]
) -> tuple[str | None, bool]:
    if end <= start:
        return None, True
    overlaps: dict[str, int] = {}
    for diar_start, diar_end, speaker in intervals:
        overlap = max(0, min(end, diar_end) - max(start, diar_start))
        if overlap:
            overlaps[speaker] = overlaps.get(speaker, 0) + overlap
    if not overlaps:
        return None, False
    ranked = sorted(overlaps.items(), key=lambda item: (-item[1], item[0]))
    best_speaker, best_overlap = ranked[0]
    total = end - start
    # A short edge overlap is insufficient evidence.  Equal/near-equal
    # overlaps remain unknown so the UI can ask a secretary to resolve them.
    if best_overlap * 2 <= total:
        return None, True
    if len(ranked) > 1 and best_overlap == ranked[1][1]:
        return None, True
    if len(ranked) > 1 and (best_overlap - ranked[1][1]) * 5 < total:
        return None, True
    return best_speaker, False


def _fixture_result(path: str | os.PathLike[str], options: _SpeechOptions, config: Any) -> dict[str, Any]:
    fixture = options.fixture
    if fixture is None:
        fixture_path = _config_value(config, "fixture_path", default=None)
        if fixture_path is not None:
            fixture_file = _as_path(fixture_path)
            if fixture_file is None or not fixture_file.is_file():
                raise SpeechConfigurationError(f"Файл fixture не найден: {fixture_path}")
            try:
                fixture = json.loads(fixture_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise SpeechConfigurationError(f"Не удалось прочитать fixture: {fixture_file}") from exc
    if fixture is None:
        raise SpeechConfigurationError(
            "FIXTURE требует fixture/fixture_data/fixture_utterances или fixture_path; REAL fallback запрещён"
        )
    if isinstance(fixture, Mapping):
        raw_utterances = fixture.get("utterances", [])
        detected = fixture.get("detected_speakers", [])
        fixture_warnings = fixture.get("warnings", [])
        language = fixture.get("language")
    else:
        raw_utterances = fixture
        detected = []
        fixture_warnings = []
        language = None
    if not isinstance(raw_utterances, Sequence) or isinstance(raw_utterances, (str, bytes)):
        raise SpeechConfigurationError("fixture utterances должны быть списком")

    utterances: list[dict[str, Any]] = []
    inferred_speakers: list[str] = []
    for index, raw in enumerate(raw_utterances, start=1):
        if not isinstance(raw, Mapping):
            raise SpeechConfigurationError(f"fixture utterance #{index} должен быть объектом")
        text = str(raw.get("text", "") or "").strip()
        try:
            start = int(raw["start_ms"])
            end = int(raw["end_ms"])
        except (KeyError, TypeError, ValueError) as exc:
            raise SpeechConfigurationError(f"fixture utterance #{index} имеет неверные метки") from exc
        if start < 0 or end <= start or not text:
            raise SpeechConfigurationError(f"fixture utterance #{index} имеет неверные данные")
        speaker = raw.get("speaker_id")
        if speaker is not None:
            speaker = str(speaker)
            if speaker not in inferred_speakers:
                inferred_speakers.append(speaker)
        utterances.append(
            {
                "id": str(raw.get("id") or f"u{index}"),
                "start_ms": start,
                "end_ms": end,
                "speaker_id": speaker,
                "text": text,
            }
        )
    speakers = [str(item) for item in detected if item is not None]
    for speaker in inferred_speakers:
        if speaker not in speakers:
            speakers.append(speaker)
    warnings = [str(item) for item in fixture_warnings]
    warnings.insert(0, _SYNTHETIC_WARNING)
    result: dict[str, Any] = {
        "utterances": utterances,
        "detected_speakers": sorted(set(speakers)),
        "warnings": _dedupe(warnings),
    }
    if language:
        result["language"] = str(language)
    return result


def _config_value(config: Any, name: str, default: Any = None) -> Any:
    if isinstance(config, Mapping):
        return config.get(name, default)
    return getattr(config, name, default)


def _get_value(value: Any, *names: str, default: Any = None) -> Any:
    for name in names:
        if isinstance(value, Mapping) and name in value:
            return value[name]
        if hasattr(value, name):
            return getattr(value, name)
    return default


def _ms(value: Any) -> int:
    if value is None:
        raise ValueError("Отсутствует временная метка")
    return max(0, int(round(float(value) * 1000)))


def _dedupe(items: Iterable[str]) -> list[str]:
    result: list[str] = []
    for item in items:
        if item not in result:
            result.append(item)
    return result


__all__ = [
    "AudioInputError",
    "ModelUnavailableError",
    "SpeechConfigurationError",
    "SpeechError",
    "transcribe_and_diarize",
]
