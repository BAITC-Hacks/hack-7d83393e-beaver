let meeting = null;
let protocol = null;
let audioUrl = null;

const $ = (id) => document.getElementById(id);
const esc = (value) => String(value ?? '').replace(/[&<>\"']/g, (char) => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[char]));
const unknown = (text = 'Не определено — требуется уточнение') => `<span class="unknown">${esc(text)}</span>`;

function setBanner(text, error = false) {
  $('banner').textContent = text;
  $('banner').className = `banner${error ? ' error' : ''}`;
}
function fail(error) { setBanner(`Ошибка: ${error?.message || String(error)}`, true); }
function setState(state, text) {
  $('protocol-state').className = `state ${state}`;
  $('protocol-state').textContent = `Состояние: ${text}`;
}

async function api(url, options = {}) {
  let response;
  try { response = await fetch(url, options); } catch (error) { throw new Error(`сервер недоступен (${error.message})`); }
  const type = response.headers.get('content-type') || '';
  const data = type.includes('json') ? await response.json().catch(() => ({})) : await response.text().catch(() => '');
  if (!response.ok) {
    const detail = typeof data === 'string' ? data : (data.detail || data.error);
    throw new Error(detail || `${response.status} ${response.statusText}`);
  }
  return data;
}

function participantName(id) { return (meeting?.participants || []).find((person) => person.id === id)?.name || id; }
function evidenceButtons(ids, title = 'Открыть основание') {
  return (ids || []).map((id) => `<button type="button" class="evidence" data-utterance="${esc(id)}" title="${esc(title)}">${esc(id)}</button>`).join(' ');
}
function updateFixtureBanner(mode) { $('fixture-banner').classList.toggle('hidden', mode !== 'FIXTURE'); }

function renderParticipants() {
  const participants = meeting?.participants || protocol?.participants || [];
  $('participants').innerHTML = participants.length
    ? `<ul class="participant-list">${participants.map((person) => `<li><code>${esc(person.id)}</code> ${esc(person.name)}</li>`).join('')}</ul>`
    : `<p class="empty-state">${unknown('Участники не указаны')}</p>`;
}

function renderSpeakers() {
  const ids = [...new Set([...(meeting?.detected_speakers || []), ...(protocol?.utterances || []).map((item) => item.speaker_id).filter(Boolean), ...(protocol?.speaker_map || []).map((item) => item.speaker_id)])];
  const mappings = new Map((protocol?.speaker_map || []).map((item) => [item.speaker_id, item]));
  const participants = meeting?.participants || protocol?.participants || [];
  $('speaker-map').innerHTML = `<h3>Голоса и участники</h3><p class="muted">Диаризация выделяет speaker_id. Связь с человеком подтверждает секретарь; это не автоматическая идентификация.</p>` + (ids.length
    ? `<div class="speaker-list">${ids.map((id) => { const mapping = mappings.get(id) || {}; return `<div class="speaker"><code>${esc(id)}</code><select data-speaker="${esc(id)}" aria-label="Участник для ${esc(id)}"><option value="">Не подтверждено</option>${participants.map((person) => `<option value="${esc(person.id)}"${mapping.participant_id === person.id && mapping.confirmed ? ' selected' : ''}>${esc(person.name)}</option>`).join('')}</select></div>`; }).join('')}</div><button id="save-speakers" type="button">Подтвердить выбранные связи</button>`
    : `<p class="empty-state">${unknown('Голоса не обнаружены')}</p>`);
  const save = $('save-speakers');
  if (save) save.addEventListener('click', async () => {
    save.disabled = true;
    try {
      const rows = [...$('speaker-map').querySelectorAll('select[data-speaker]')].map((select) => ({speaker_id: select.dataset.speaker, participant_id: select.value || null, confirmed: Boolean(select.value)}));
      protocol = await api(`/api/meetings/${meeting.id}/speaker-map`, {method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({speaker_map: rows})});
      setBanner('Связь голоса с участником сохранена после подтверждения секретарём.');
      renderSpeakers();
    } catch (error) { fail(error); } finally { save.disabled = false; }
  });
}

function formatDeadline(task) {
  if (!task.due_date) return unknown();
  return `${esc(task.due_date)}${task.due_time ? ` ${esc(task.due_time)}` : ` ${unknown('время не задано')}`}`;
}

function renderTranscript() {
  const rows = protocol?.utterances || [];
  return `<section><h3>Транскрипт</h3>${rows.length ? `<div class="transcript">${rows.map((item) => `<article class="utterance" id="utterance-${esc(item.id)}"><button type="button" class="utterance-jump" data-utterance="${esc(item.id)}">${esc(item.id)}</button> <span class="muted">${esc(item.start_ms)}–${esc(item.end_ms)} мс</span><p><strong>${item.speaker_id ? esc(item.speaker_id) : unknown('speaker не определён')}</strong>: ${esc(item.text)}</p></article>`).join('')}</div>` : `<p class="empty-state">${unknown('Реплики не обнаружены')}</p>`}</section>`;
}

function taskEditor(task) {
  const participants = meeting?.participants || protocol?.participants || [];
  return `<div class="task-edit"><label>Исполнитель<select data-assignee="${esc(task.id)}"><option value="">Не определён</option>${participants.map((person) => `<option value="${esc(person.id)}"${task.assignee_id === person.id ? ' selected' : ''}>${esc(person.name)}</option>`).join('')}</select></label><label>Срок<input type="date" data-due-date="${esc(task.id)}" value="${esc(task.due_date || '')}"></label><label>Время<input type="time" step="1" data-due-time="${esc(task.id)}" value="${esc(task.due_time || '')}"></label><button type="button" class="save-task" data-task="${esc(task.id)}">Сохранить исправление</button><span class="muted">Изменение внесено человеком и не считается цитатой из аудио.</span></div>`;
}

function renderTasks() {
  const tasks = protocol?.tasks || [];
  if (!tasks.length) return `<section><h3>Поручения</h3><p class="empty-state">${unknown('Поручений пока нет')}</p></section>`;
  return `<section><h3>Поручения</h3>${tasks.map((task) => { const ev = task.evidence || {}; const history = task.history || []; return `<article class="task" id="task-${esc(task.id)}"><div class="task-heading"><strong>${esc(task.action)}</strong><span class="status-pill ${esc(task.review_status || 'draft')}">${esc(task.review_status || 'draft')}</span></div><p>Ответственный: ${task.assignee_id ? esc(participantName(task.assignee_id)) : unknown()}<br>Срок: ${formatDeadline(task)}${task.deadline_raw ? `<br><span class="muted">Как сказано: ${esc(task.deadline_raw)}</span>` : ''}</p><p class="evidence-line"><strong>Основание:</strong> ${evidenceButtons(ev.action, 'Открыть реплику действия') || unknown('нет evidence')}</p>${ev.assignee?.length ? `<p class="evidence-line">Исполнитель: ${evidenceButtons(ev.assignee)}</p>` : ''}${ev.deadline?.length ? `<p class="evidence-line">Срок: ${evidenceButtons(ev.deadline)}</p>` : ''}${task.review_status !== 'approved' ? taskEditor(task) : ''}${history.length ? `<details class="history"><summary>История изменений (${history.length})</summary>${history.map((event) => `<div class="history-row"><strong>${esc(event.kind)}</strong>: ${esc(event.previous_value ?? '—')} → ${esc(event.new_value ?? '—')}<br><span class="muted">${event.accepted_in_dialogue ? 'Принято в диалоге' : 'Не принято в диалоге'}</span> · ${evidenceButtons(event.evidence_ids, 'Открыть основание изменения')}</div>`).join('')}</details>` : ''}</article>`; }).join('')}</section>`;
}

function renderSummaryAndQuestions() {
  const summary = protocol?.summary || []; const questions = protocol?.review_questions || []; const warnings = protocol?.warnings || [];
  return `<section><h3>Саммари</h3>${summary.length ? `<ul class="summary">${summary.map((item) => `<li>${esc(item.text)} ${evidenceButtons(item.evidence_ids)}</li>`).join('')}</ul>` : `<p class="empty-state">${unknown('Саммари пока нет')}</p>`}</section><section><h3>Вопросы для уточнения</h3>${questions.length ? `<ul class="questions">${questions.map((item) => `<li><strong>${esc(item.field)}</strong>: ${esc(item.text)} <span class="muted">(${esc(item.task_id)})</span></li>`).join('')}</ul>` : '<p class="empty-state">Вопросов нет.</p>'}</section>${warnings.length ? `<section class="warnings"><h3>Предупреждения</h3><ul>${warnings.map((item) => `<li>${esc(item)}</li>`).join('')}</ul></section>` : ''}`;
}

function bindEvidence() {
  document.querySelectorAll('[data-utterance]').forEach((button) => button.addEventListener('click', () => {
    const id = button.dataset.utterance; const item = (protocol?.utterances || []).find((row) => row.id === id); const player = $('player');
    if (!item) return fail(new Error(`реплика ${id} не найдена`));
    document.querySelectorAll('.utterance.focused').forEach((row) => row.classList.remove('focused'));
    const row = [...document.querySelectorAll('.utterance')].find((candidate) => candidate.id === `utterance-${id}`);
    if (row) { row.classList.add('focused'); row.scrollIntoView({behavior: 'smooth', block: 'center'}); }
    if (player && Number.isFinite(Number(item.start_ms))) { player.currentTime = Number(item.start_ms) / 1000; player.play().catch(() => {}); }
  }));
}

function renderProtocol() {
  if (!protocol) return;
  const approved = meeting?.approval_status === 'approved' || (protocol.tasks || []).length > 0 && protocol.tasks.every((task) => task.review_status === 'approved');
  setState(approved ? 'approved' : (protocol.tasks?.length ? 'draft' : 'empty'), approved ? 'утверждён' : (protocol.tasks?.length ? 'черновик' : 'данных пока нет'));
  $('protocol').innerHTML = `<audio controls id="player" aria-label="Запись совещания"></audio>${renderTranscript()}${renderTasks()}${renderSummaryAndQuestions()}`;
  if (audioUrl) $('player').src = audioUrl;
  $('approve').classList.toggle('hidden', approved || !(protocol.tasks || []).length);
  $('export').classList.remove('hidden'); $('export').textContent = approved ? 'Скачать DOCX' : 'Скачать DOCX (черновик)'; $('export').href = `/api/meetings/${meeting.id}/export.docx`;
  bindEvidence();
  document.querySelectorAll('.save-task').forEach((button) => button.addEventListener('click', () => saveTask(button)));
}

async function saveTask(button) {
  const id = button.dataset.task; const assignee = document.querySelector(`[data-assignee="${CSS.escape(id)}"]`); const date = document.querySelector(`[data-due-date="${CSS.escape(id)}"]`); const time = document.querySelector(`[data-due-time="${CSS.escape(id)}"]`);
  button.disabled = true;
  try {
    const task = await api(`/api/meetings/${meeting.id}/tasks/${id}`, {method: 'PATCH', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({assignee_id: assignee?.value || null, due_date: date?.value || null, due_time: time?.value ? `${time.value}:00` : null})});
    const index = protocol.tasks.findIndex((item) => item.id === id); if (index >= 0) protocol.tasks[index] = task;
    setBanner('Исправление сохранено и помечено как требующее проверки.'); renderProtocol();
  } catch (error) { fail(error); } finally { button.disabled = false; }
}

function renderCatchup(data, start, end) {
  const result = $('catchup-result'); const items = data?.items || []; const later = data?.later_changes || [];
  result.innerHTML = `<p><strong>Подтверждённый интервал:</strong> ${esc(start)}–${esc(end)} мс</p>${items.length ? `<ul>${items.map((item) => `<li>${esc(item.text || item.kind)} ${evidenceButtons(item.evidence_ids)}</li>`).join('')}</ul>` : '<p class="empty-state">В выбранном интервале доступных событий нет.</p>'}${later.length ? `<h4>Более поздние изменения</h4><ul>${later.map((item) => `<li>${esc(item.text || item.kind)} ${evidenceButtons(item.evidence_ids)}</li>`).join('')}</ul>` : ''}`;
  bindEvidence();
}

$('meeting-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  try {
    const form = new FormData(event.target); const participants = String(form.get('participants') || '').split(',').map((item) => item.trim()).filter(Boolean).map((item) => { const [id, ...name] = item.split(':'); return {id: id.trim(), name: name.join(':').trim()}; });
    meeting = await api('/api/meetings', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({title: form.get('title'), started_at: new Date(form.get('started_at')).toISOString(), timezone: form.get('timezone'), mode: form.get('mode'), participants})});
    protocol = null; updateFixtureBanner(meeting.mode); $('audio-card').classList.remove('hidden'); $('review-card').classList.add('hidden'); $('job-status').textContent = 'Ожидается файл записи.'; setBanner('Совещание создано. Загрузите запись для пакетной обработки.');
  } catch (error) { fail(error); }
});

async function pollJob(jobId) {
  let state;
  do { await new Promise((resolve) => setTimeout(resolve, 700)); state = await api(`/api/jobs/${jobId}`); $('job-status').textContent = `${state.stage || 'обработка'}: ${state.status || 'unknown'}${state.error ? ` — ${state.error}` : ''}`; } while (!['done', 'error'].includes(state.status));
  if (state.status === 'error') throw new Error(state.error || 'сервер сообщил об ошибке обработки');
}

$('audio-form').addEventListener('submit', async (event) => {
  event.preventDefault(); const button = $('upload-button'); button.disabled = true;
  try {
    const file = $('audio-file').files[0]; if (!file) throw new Error('выберите аудиофайл'); audioUrl = URL.createObjectURL(file); const form = new FormData(); form.append('file', file); $('job-status').textContent = 'Загрузка…'; const job = await api(`/api/meetings/${meeting.id}/audio`, {method: 'POST', body: form}); await pollJob(job.job_id); protocol = await api(`/api/meetings/${meeting.id}/protocol`); $('review-card').classList.remove('hidden'); renderParticipants(); renderSpeakers(); renderProtocol();
  } catch (error) { fail(error); } finally { button.disabled = false; }
});

$('extract').addEventListener('click', async () => { const button = $('extract'); button.disabled = true; try { setState('loading', 'извлечение…'); protocol = await api(`/api/meetings/${meeting.id}/extract`, {method: 'POST'}); renderProtocol(); } catch (error) { setState('error', 'ошибка'); fail(error); } finally { button.disabled = false; } });
$('approve').addEventListener('click', async () => { const button = $('approve'); button.disabled = true; try { const result = await api(`/api/meetings/${meeting.id}/approve`, {method: 'POST'}); meeting.approval_status = 'approved'; protocol = result.protocol || protocol; setBanner('Протокол утверждён секретарём.'); renderProtocol(); } catch (error) { fail(error); } finally { button.disabled = false; } });
$('catchup-form').addEventListener('submit', async (event) => { event.preventDefault(); const start = Number($('catchup-start').value); const end = Number($('catchup-end').value); if (!Number.isInteger(start) || !Number.isInteger(end) || start < 0 || end <= start) return fail(new Error('конец интервала должен быть больше начала')); const button = $('catchup-button'); button.disabled = true; try { const result = await api(`/api/meetings/${meeting.id}/catch-up`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({start_ms: start, end_ms: end})}); renderCatchup(result, start, end); } catch (error) { fail(error); } finally { button.disabled = false; } });
