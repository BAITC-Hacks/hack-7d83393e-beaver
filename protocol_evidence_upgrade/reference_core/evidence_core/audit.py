"""Structural protocol audit. It deliberately does NOT claim semantic accuracy."""
from __future__ import annotations
import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from jsonschema import Draft202012Validator, FormatChecker

SCHEMA = json.loads(Path(__file__).with_name('contract.schema.json').read_text(encoding='utf-8'))
VALIDATOR = Draft202012Validator(SCHEMA, format_checker=FormatChecker())


def digest(value: Any) -> str:
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


def audit(protocol: Any) -> dict:
    issues: list[dict] = []
    def issue(code: str, path: str, message: str, severity: str = 'error') -> None:
        issues.append({'code': code, 'path': path, 'message': message, 'severity': severity})
    for err in sorted(VALIDATOR.iter_errors(protocol), key=lambda e: str(list(e.path))):
        # Do not copy raw potentially sensitive field values into diagnostics.
        issue('SCHEMA', '/'.join(map(str, err.path)), f'Нарушено правило схемы: {err.validator}')
    result = {
        'report_schema': 'reference-audit/1.0',
        'scope': 'STRUCTURE_AND_REFERENCES_ONLY',
        'source_sha256': digest(protocol),
        'mode': protocol.get('mode') if isinstance(protocol, dict) else None,
        'semantic_support': 'NOT RUN',
        'asr': 'NOT RUN', 'diarization': 'NOT RUN',
        'issues': issues,
    }
    if issues:
        return {**result, 'status': 'FAIL', 'counts': {}}

    def unique(items: list[dict], field: str, path: str) -> set:
        seen: set = set()
        for n, item in enumerate(items):
            key = item[field]
            if key in seen:
                issue('DUPLICATE_ID', f'{path}/{n}/{field}', 'Идентификатор должен быть уникальным.')
            seen.add(key)
        return seen
    people = unique(protocol['participants'], 'id', 'participants')
    utterances = unique(protocol['utterances'], 'id', 'utterances')
    tasks = unique(protocol['tasks'], 'id', 'tasks')
    voices = unique(protocol['speaker_map'], 'speaker_id', 'speaker_map')
    try:
        ZoneInfo(protocol['meeting']['timezone'])
    except (ZoneInfoNotFoundError, ValueError):
        issue('TIMEZONE', 'meeting/timezone', 'Неизвестный часовой пояс.')
    if datetime.fromisoformat(protocol['meeting']['started_at'].replace('Z', '+00:00')).utcoffset() is None:
        issue('NAIVE_DATETIME', 'meeting/started_at', 'Нужен часовой пояс начала совещания.')
    for i, row in enumerate(protocol['speaker_map']):
        if row['participant_id'] is not None and row['participant_id'] not in people:
            issue('UNKNOWN_PARTICIPANT', f'speaker_map/{i}', 'Участник отсутствует в справочнике.')
        if row['confirmed'] and row['participant_id'] is None:
            issue('EMPTY_CONFIRMED_MAPPING', f'speaker_map/{i}', 'Нельзя подтвердить пустое соответствие.')
    for i, row in enumerate(protocol['utterances']):
        if row['end_ms'] <= row['start_ms']:
            issue('TIME_RANGE', f'utterances/{i}', 'Конец реплики должен быть позже начала.')
        if row['speaker_id'] is not None and row['speaker_id'] not in voices:
            issue('UNMAPPED_VOICE', f'utterances/{i}', 'Нет записи об этом голосе в speaker_map.')

    def refs(ids: list[str], path: str) -> None:
        for ref in ids:
            if ref not in utterances:
                issue('BROKEN_EVIDENCE', path, 'Ссылка ведёт на отсутствующую реплику.')
    history_ids: set[str] = set()
    questions: dict[str, set[str]] = {}
    for i, q in enumerate(protocol['review_questions']):
        if q['task_id'] not in tasks:
            issue('UNKNOWN_TASK', f'review_questions/{i}', 'Неизвестное поручение.')
        questions.setdefault(q['task_id'], set()).add(q['field'])
    for i, task in enumerate(protocol['tasks']):
        prefix = f'tasks/{i}'
        if task['assignee_id'] is not None and task['assignee_id'] not in people:
            issue('UNKNOWN_ASSIGNEE', prefix, 'Исполнитель отсутствует в участниках.')
        if task['due_time'] is not None and task['due_date'] is None:
            issue('TIME_WITHOUT_DATE', prefix, 'Время без даты требует уточнения.')
        for field, ids in task['evidence'].items():
            refs(ids, f'{prefix}/evidence/{field}')
        if task['assignee_id'] is not None and not task['evidence']['assignee']:
            issue('MISSING_FIELD_EVIDENCE', prefix + '/assignee_id', 'Нет основания исполнителя; человеческая правка требует отдельного аудита.')
        if (task['due_date'] is not None or task['due_time'] is not None) and not task['evidence']['deadline']:
            issue('MISSING_FIELD_EVIDENCE', prefix + '/due_date', 'Нет основания срока; человеческая правка требует отдельного аудита.')
        if task['dialogue_status'] != 'cancelled':
            for field, missing in [('assignee', task['assignee_id'] is None), ('deadline', task['due_date'] is None)]:
                if missing and field not in questions.get(task['id'], set()):
                    issue('MISSING_REVIEW_QUESTION', prefix, f'Нет вопроса для поля {field}.', 'warning')
        for j, event in enumerate(task['history']):
            refs(event['evidence_ids'], f'{prefix}/history/{j}')
            if event['event_id'] in history_ids:
                issue('DUPLICATE_EVENT', f'{prefix}/history/{j}', 'Повтор event_id.')
            history_ids.add(event['event_id'])
    for i, summary in enumerate(protocol['summary']):
        refs(summary['evidence_ids'], f'summary/{i}')
    for i, row in enumerate(protocol['presence_intervals']):
        if row['end_ms'] <= row['start_ms']:
            issue('TIME_RANGE', f'presence_intervals/{i}', 'Некорректный интервал.')
        if row['participant_id'] is not None and row['participant_id'] not in people:
            issue('UNKNOWN_PARTICIPANT', f'presence_intervals/{i}', 'Участник отсутствует в справочнике.')
    result['status'] = 'FAIL' if any(i['severity'] == 'error' for i in issues) else 'PASS'
    result['counts'] = {'tasks': len(tasks), 'utterances': len(utterances), 'participants': len(people), 'questions': len(protocol['review_questions'])}
    result['limitations'] = [
        'PASS означает только структурную согласованность и целостность ссылок.',
        'Существование реплики не доказывает, что она подтверждает смысл поля.',
        'SHA-256 — идентификатор содержимого, не подпись и не доказательство подлинности.',
        'Человеческие правки требуют отдельного журнала происхождения; этот аудитор не подтверждает их автоматически.',
    ]
    return result
