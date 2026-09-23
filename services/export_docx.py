"""Render a protocol snapshot as a local DOCX document.

The renderer deliberately contains no model or network calls.  It accepts the
JSON-compatible dictionary described by ``contract.schema.json`` and writes a
human-reviewable document.  The small amount of defensive normalisation here
is useful at the HTTP boundary, where an omitted optional field should still
produce a readable draft rather than an exception from the exporter.
"""

from __future__ import annotations

from io import BytesIO
from typing import Any, Iterable, Mapping, Sequence


def _text(value: Any, empty: str = "—") -> str:
    """Return a display value without turning ``None`` into the word null."""

    if value is None or value == "":
        return empty
    if isinstance(value, bool):
        return "да" if value else "нет"
    return str(value)


def _timestamp(ms: Any) -> str:
    """Format a millisecond offset as ``MM:SS`` or ``HH:MM:SS``."""

    try:
        total_ms = max(0, int(ms))
    except (TypeError, ValueError):
        return "?"
    total_seconds, milliseconds = divmod(total_ms, 1000)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    # Evidence links are easier to scan when short clips retain milliseconds.
    if milliseconds:
        return f"{minutes:02d}:{seconds:02d}.{milliseconds:03d}"
    return f"{minutes:02d}:{seconds:02d}"


def _status(protocol: Mapping[str, Any]) -> tuple[str, bool]:
    """Resolve the explicit protocol approval marker.

    The contract stores review state on tasks, while integrations sometimes
    add a top-level marker.  A document is a draft unless an explicit
    top-level approval value is present.  This prevents a set of reviewed
    tasks from being presented as an approved meeting record accidentally.
    """

    meeting = protocol.get("meeting")
    explicit = protocol.get("approval_status", protocol.get("status"))
    if explicit is None and isinstance(meeting, Mapping):
        explicit = meeting.get("approval_status")
    if explicit is None:
        explicit = protocol.get("review_status")
    approved = protocol.get("approved")
    if isinstance(approved, bool):
        return ("УТВЕРЖДЁН" if approved else "ЧЕРНОВИК"), approved
    if isinstance(explicit, str) and explicit.lower() in {
        "approved",
        "approve",
        "утверждён",
        "утвержден",
    }:
        return "УТВЕРЖДЁН", True
    return "ЧЕРНОВИК", False


def _evidence_index(protocol: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {
        str(item.get("id")): item
        for item in protocol.get("utterances", [])
        if isinstance(item, Mapping) and item.get("id") is not None
    }


def _evidence_lines(
    evidence_ids: Iterable[Any], utterances: Mapping[str, Mapping[str, Any]]
) -> list[str]:
    """Turn evidence IDs into cited transcript lines.

    Missing IDs remain visible in the document.  Silently dropping them would
    make an invalid protocol look complete and would hide an integration bug.
    """

    lines: list[str] = []
    for raw_id in evidence_ids:
        evidence_id = str(raw_id)
        utterance = utterances.get(evidence_id)
        if utterance is None:
            lines.append(f"[{evidence_id}; реплика не найдена]")
            continue
        start = _timestamp(utterance.get("start_ms"))
        end = _timestamp(utterance.get("end_ms"))
        speaker = _text(utterance.get("speaker_id"))
        text = _text(utterance.get("text"))
        lines.append(f"[{evidence_id}, {start}–{end}, {speaker}] {text}")
    return lines


def _add_paragraph(document: Any, text: str = "", *, bold: bool = False) -> Any:
    paragraph = document.add_paragraph()
    run = paragraph.add_run(text)
    run.bold = bold
    return paragraph


def _add_field_table(document: Any, fields: Sequence[tuple[str, Any]]) -> Any:
    table = document.add_table(rows=0, cols=2)
    table.style = "Table Grid"
    for label, value in fields:
        cells = table.add_row().cells
        cells[0].text = str(label)
        cells[1].text = _text(value)
    return table


def render(protocol: Mapping[str, Any]) -> bytes:
    """Render ``protocol`` and return a DOCX byte string.

    ``python-docx`` is imported lazily so preflight and the rest of the app can
    still report a useful missing-dependency error.  The returned bytes can be
    sent directly as an HTTP attachment or written by the caller.
    """

    if not isinstance(protocol, Mapping):
        raise TypeError("protocol must be a mapping")
    try:
        from docx import Document
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        from docx.shared import Pt
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise RuntimeError(
            "Экспорт DOCX требует пакет python-docx. Установите зависимости "
            "проекта перед запуском."
        ) from exc

    document = Document()
    section = document.sections[0]
    section.top_margin = Pt(45)
    section.bottom_margin = Pt(45)
    section.left_margin = Pt(55)
    section.right_margin = Pt(55)

    # DejaVu Sans is commonly available on Linux and includes all Kazakh
    # Cyrillic letters.  Word substitutes a matching font when unavailable.
    styles = document.styles
    for style_name in ("Normal", "Body Text"):
        try:
            style = styles[style_name]
            style.font.name = "DejaVu Sans"
            style.font.size = Pt(10)
            style._element.rPr.rFonts.set(
                "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}eastAsia",
                "DejaVu Sans",
            )
        except (KeyError, AttributeError):
            pass

    status, is_approved = _status(protocol)
    meeting = protocol.get("meeting") or {}
    title = _text(meeting.get("title"), "Совещание")
    heading = document.add_heading(title, level=0)
    heading.alignment = WD_ALIGN_PARAGRAPH.CENTER
    status_paragraph = document.add_paragraph()
    status_paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    status_run = status_paragraph.add_run(status)
    status_run.bold = True
    status_run.font.size = Pt(13)

    if protocol.get("mode") == "FIXTURE":
        fixture = document.add_paragraph()
        fixture.alignment = WD_ALIGN_PARAGRAPH.CENTER
        fixture.add_run("Синтетический пример, не ASR").italic = True

    document.add_heading("Сведения о совещании", level=1)
    _add_field_table(
        document,
        [
            ("ID", meeting.get("id")),
            ("Дата и время", meeting.get("started_at")),
            ("Часовой пояс", meeting.get("timezone")),
            ("Режим", protocol.get("mode")),
        ],
    )

    participants = protocol.get("participants") or []
    document.add_heading("Участники", level=1)
    if participants:
        for participant in participants:
            if isinstance(participant, Mapping):
                _add_paragraph(
                    document,
                    f"{_text(participant.get('name'))} (id: {_text(participant.get('id'))})",
                )
    else:
        _add_paragraph(document, "Участники не указаны.")

    speaker_map = protocol.get("speaker_map") or []
    if speaker_map:
        document.add_heading("Подтверждение говорящих", level=2)
        participant_names = {
            str(item.get("id")): item.get("name")
            for item in participants
            if isinstance(item, Mapping) and item.get("id") is not None
        }
        for mapping in speaker_map:
            if not isinstance(mapping, Mapping):
                continue
            participant_id = mapping.get("participant_id")
            participant_name = participant_names.get(str(participant_id), participant_id)
            confirmed = "подтверждено" if mapping.get("confirmed") else "не подтверждено"
            _add_paragraph(
                document,
                f"{_text(mapping.get('speaker_id'))} → {_text(participant_name)} ({confirmed})",
            )

    document.add_heading("Саммари", level=1)
    summary = protocol.get("summary") or []
    utterances = _evidence_index(protocol)
    if summary:
        for item in summary:
            if not isinstance(item, Mapping):
                continue
            _add_paragraph(document, _text(item.get("text")), bold=True)
            ids = item.get("evidence_ids") or []
            lines = _evidence_lines(ids, utterances)
            for line in lines:
                _add_paragraph(document, f"Основание: {line}")
    else:
        _add_paragraph(document, "Саммари не сформировано.")

    document.add_heading("Поручения", level=1)
    tasks = protocol.get("tasks") or []
    if tasks:
        for index, task in enumerate(tasks, start=1):
            if not isinstance(task, Mapping):
                continue
            document.add_heading(
                f"{index}. {_text(task.get('action'), 'Поручение без описания')}",
                level=2,
            )
            assignee = task.get("assignee_name", task.get("assignee_id"))
            deadline = task.get("deadline")
            if deadline is None:
                date = task.get("due_date")
                time = task.get("due_time")
                if date and time:
                    deadline = f"{date} {time}"
                elif date:
                    deadline = date
                else:
                    deadline = task.get("deadline_raw")
            _add_field_table(
                document,
                [
                    ("ID", task.get("id")),
                    ("Исполнитель", assignee),
                    ("Срок", deadline),
                    ("Статус диалога", task.get("dialogue_status")),
                    ("Статус проверки", task.get("review_status")),
                ],
            )
            evidence = task.get("evidence") or {}
            if evidence:
                document.add_paragraph("Основания", style="Heading 3")
                for field in ("action", "assignee", "deadline"):
                    ids = evidence.get(field) or []
                    if not ids:
                        _add_paragraph(document, f"{field}: —")
                        continue
                    _add_paragraph(document, f"{field}:", bold=True)
                    for line in _evidence_lines(ids, utterances):
                        _add_paragraph(document, f"  {line}")
            history = task.get("history") or []
            if history:
                document.add_paragraph("История", style="Heading 3")
                for event in history:
                    if not isinstance(event, Mapping):
                        continue
                    accepted = "согласовано" if event.get("accepted_in_dialogue") else "не согласовано"
                    detail = (
                        f"{_text(event.get('event_id'))}: {_text(event.get('kind'))}; "
                        f"{_text(event.get('previous_value'))} → {_text(event.get('new_value'))}; "
                        f"{accepted}"
                    )
                    _add_paragraph(document, detail)
                    for line in _evidence_lines(event.get("evidence_ids") or [], utterances):
                        _add_paragraph(document, f"  Основание: {line}")
    else:
        _add_paragraph(document, "Поручения не обнаружены.")

    document.add_heading("Вопросы на проверку", level=1)
    questions = protocol.get("review_questions") or []
    if questions:
        for question in questions:
            if isinstance(question, Mapping):
                _add_paragraph(
                    document,
                    f"[{_text(question.get('task_id'))}, {_text(question.get('field'))}] "
                    f"{_text(question.get('text'))}",
                )
    else:
        _add_paragraph(document, "Нет нерешённых вопросов.")

    document.add_heading("Транскрипт и evidence", level=1)
    if utterances:
        for utterance in protocol.get("utterances", []):
            if not isinstance(utterance, Mapping):
                continue
            start = _timestamp(utterance.get("start_ms"))
            end = _timestamp(utterance.get("end_ms"))
            _add_paragraph(
                document,
                f"[{_text(utterance.get('id'))}, {start}–{end}, "
                f"{_text(utterance.get('speaker_id'))}] { _text(utterance.get('text')) }",
            )
    else:
        _add_paragraph(document, "Транскрипт отсутствует.")

    warnings = protocol.get("warnings") or []
    if warnings:
        document.add_heading("Предупреждения", level=1)
        for warning in warnings:
            _add_paragraph(document, f"• {_text(warning)}")

    # Make the approval state machine visible in the file metadata too, while
    # retaining the human-readable banner above.
    properties = document.core_properties
    properties.title = title
    properties.subject = f"Протокол совещания — {status}"
    properties.comments = "Утверждено секретарём" if is_approved else "Требует проверки секретаря"

    output = BytesIO()
    document.save(output)
    return output.getvalue()


def write(protocol: Mapping[str, Any], output_path: str) -> None:
    """Render and write a protocol to ``output_path``."""

    with open(output_path, "wb") as stream:
        stream.write(render(protocol))


__all__ = ["render", "write"]
