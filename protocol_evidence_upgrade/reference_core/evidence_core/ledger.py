"""Deterministic replay of annotated events, not an AI extraction model.

The sidecar is deliberately separate from the preserved protocol v1.0 contract.
"""
from __future__ import annotations
from copy import deepcopy
from datetime import date, time
import re
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from .audit import digest

class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)

class Utterance(Strict):
    id: str = Field(min_length=1)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    text: str = Field(min_length=1)
    @model_validator(mode='after')
    def interval(self):
        if self.end_ms <= self.start_ms:
            raise ValueError('end_ms должен быть позже start_ms')
        return self

class State(Strict):
    action: str = Field(min_length=1)
    assignee_id: str | None = None
    due_date: str | None = None
    due_time: str | None = None
    @model_validator(mode='after')
    def dates(self):
        if self.due_date is not None:
            if date.fromisoformat(self.due_date).isoformat() != self.due_date:
                raise ValueError('Дата должна иметь формат YYYY-MM-DD')
        if self.due_time is not None:
            if self.due_date is None:
                raise ValueError('Время без даты недопустимо')
            if not re.fullmatch(r'[0-9]{2}:[0-9]{2}:[0-9]{2}', self.due_time) or time.fromisoformat(self.due_time).isoformat() != self.due_time:
                raise ValueError('Время должно иметь формат HH:MM:SS')
        return self

class Event(Strict):
    event_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    kind: Literal['create','propose','accept','reject','cancel']
    at_ms: int = Field(ge=0)
    evidence_ids: list[str] = Field(min_length=1)
    initial: State | None = None
    patch: dict[str, str | None] | None = None
    proposal_id: str | None = None
    @model_validator(mode='after')
    def shape(self):
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError('Дубликаты evidence_ids')
        if self.kind == 'create':
            if self.initial is None or self.patch is not None or self.proposal_id is not None:
                raise ValueError('create требует initial и не допускает patch/proposal_id')
        elif self.kind == 'propose':
            if not self.patch or self.initial is not None or self.proposal_id is not None:
                raise ValueError('propose требует непустой patch')
            if set(self.patch) - {'action','assignee_id','due_date','due_time'}:
                raise ValueError('Неизвестное поле patch')
        elif self.kind in ('accept','reject'):
            if not self.proposal_id or self.initial is not None or self.patch is not None:
                raise ValueError('accept/reject требует только proposal_id')
        elif self.initial is not None or self.patch is not None or self.proposal_id is not None:
            raise ValueError('cancel не допускает дополнительных изменений')
        return self

class Bundle(Strict):
    ledger_version: Literal['1.0'] = '1.0'
    mode: Literal['REAL','FIXTURE']
    meeting_id: str = Field(min_length=1)
    participants: list[str]
    utterances: list[Utterance]
    events: list[Event]


def replay(raw: dict[str, Any], cutoff_ms: int | None = None) -> dict:
    bundle = Bundle.model_validate(raw)
    if cutoff_ms is not None and (isinstance(cutoff_ms, bool) or not isinstance(cutoff_ms, int) or cutoff_ms < 0):
        raise ValueError('cutoff_ms должен быть неотрицательным целым числом')
    # Validate the entire ledger, including future events, then replay the selected prefix.
    ids = [u.id for u in bundle.utterances]
    if len(set(ids)) != len(ids) or len(set(bundle.participants)) != len(bundle.participants):
        raise ValueError('Повтор идентификатора реплики или участника')
    by_id = {u.id: u for u in bundle.utterances}
    seen: set[str] = set()
    previous = -1
    for event in bundle.events:
        if event.event_id in seen:
            raise ValueError('Повтор event_id')
        seen.add(event.event_id)
        if event.at_ms < previous:
            raise ValueError('События должны быть хронологически упорядочены')
        previous = event.at_ms
        for uid in event.evidence_ids:
            if uid not in by_id:
                raise ValueError('Неизвестное основание события')
            if by_id[uid].end_ms > event.at_ms:
                raise ValueError('Основание ещё не прозвучало к моменту события')

    def apply(until: int | None) -> tuple[dict, list]:
        tasks: dict[str, dict] = {}
        proposals: dict[str, dict] = {}
        history: list[dict] = []
        for event in bundle.events:
            if until is not None and event.at_ms > until:
                break
            changed = False
            if event.kind == 'create':
                if event.task_id in tasks:
                    raise ValueError('Повторное создание поручения')
                value = event.initial.model_dump()
                if value['assignee_id'] is not None and value['assignee_id'] not in bundle.participants:
                    raise ValueError('Неизвестный исполнитель')
                tasks[event.task_id] = {
                    'id': event.task_id, **value, 'status': 'agreed',
                    'evidence': {k: list(event.evidence_ids) if v is not None else [] for k, v in value.items()},
                    'last_effective_event': event.event_id,
                }
                changed = True
            else:
                if event.task_id not in tasks:
                    raise ValueError('Изменение до создания поручения')
                task = tasks[event.task_id]
                if task['status'] == 'cancelled':
                    raise ValueError('Изменение отменённого поручения требует отдельного восстановления')
                if event.kind == 'propose':
                    proposed = {key: task[key] for key in State.model_fields}
                    proposed.update(event.patch)
                    State.model_validate(proposed)
                    if proposed['assignee_id'] is not None and proposed['assignee_id'] not in bundle.participants:
                        raise ValueError('Неизвестный исполнитель')
                    proposals[event.event_id] = {
                        'task_id': event.task_id, 'patch': event.patch,
                        'before': {k: task[k] for k in event.patch},
                        'evidence_ids': event.evidence_ids, 'status': 'pending',
                    }
                elif event.kind in ('accept','reject'):
                    p = proposals.get(event.proposal_id)
                    if p is None or p['task_id'] != event.task_id or p['status'] != 'pending':
                        raise ValueError('Предложение не найдено или уже обработано')
                    if event.kind == 'accept':
                        if any(task[k] != v for k, v in p['before'].items()):
                            raise ValueError('Предложение устарело: изменяемое поле уже обновлено')
                        merged = {k: task[k] for k in State.model_fields}
                        merged.update(p['patch'])
                        State.model_validate(merged)
                        task.update(p['patch'])
                        for field, value in p['patch'].items():
                            task['evidence'][field] = list(dict.fromkeys(p['evidence_ids'] + event.evidence_ids)) if value is not None else []
                        task['last_effective_event'] = event.event_id
                        changed = True
                    p['status'] = 'accepted' if event.kind == 'accept' else 'rejected'
                elif event.kind == 'cancel':
                    task['status'] = 'cancelled'
                    task['last_effective_event'] = event.event_id
                    task['cancellation_evidence'] = list(event.evidence_ids)
                    for p in proposals.values():
                        if p['task_id'] == event.task_id and p['status'] == 'pending':
                            p['status'] = 'invalidated_by_cancellation'
                    changed = True
            history.append({
                'event_id': event.event_id, 'task_id': event.task_id,
                'kind': event.kind, 'at_ms': event.at_ms,
                'evidence_ids': event.evidence_ids,
                'changed_current_state': changed,
            })
        for task in tasks.values():
            task['pending_proposals'] = [
                {'proposal_id': pid, 'patch': deepcopy(p['patch'])}
                for pid, p in proposals.items()
                if p['task_id'] == task['id'] and p['status'] == 'pending'
            ]
            task['questions'] = []
            if task['status'] != 'cancelled':
                if task['assignee_id'] is None:
                    task['questions'].append('Кто отвечает?')
                if task['due_date'] is None:
                    task['questions'].append('К какому сроку?')
        return tasks, history

    all_tasks, all_history = apply(None)
    tasks, history = (all_tasks, all_history) if cutoff_ms is None else apply(cutoff_ms)
    return {
        'ledger_version': '1.0', 'mode': bundle.mode, 'cutoff_ms': cutoff_ms,
        'source_sha256': digest(raw), 'tasks': list(tasks.values()), 'events': history,
        'semantic_extraction': 'NOT RUN',
        'notice': 'Воспроизведение заранее размеченных событий; не результат ASR или извлечения LLM.',
    }
