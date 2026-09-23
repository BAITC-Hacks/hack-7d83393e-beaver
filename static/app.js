let meeting = null;
let protocol = null;
let audioUrl = null;

const $ = (id) => document.getElementById(id);
const banner = (text, error = false) => { $('banner').textContent = text; $('banner').className = `banner ${error ? 'error' : ''}`; };
const fail = (error) => banner(error?.message || String(error), true);
async function api(url, options = {}) {
  const response = await fetch(url, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || `${response.status} ${response.statusText}`);
  return data;
}

$('meeting-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  try {
    const form = new FormData(event.target);
    const participants = String(form.get('participants') || '').split(',').map((item) => item.trim()).filter(Boolean).map((item) => {
      const [id, ...name] = item.split(':'); return { id: id.trim(), name: name.join(':').trim() };
    });
    meeting = await api('/api/meetings', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({
      title: form.get('title'), started_at: new Date(form.get('started_at')).toISOString(), timezone: form.get('timezone'), mode: form.get('mode'), participants
    })});
    $('audio-card').classList.remove('hidden');
    if (meeting.mode === 'FIXTURE') banner('Синтетический пример, не результат распознавания');
  } catch (error) { fail(error); }
});

$('audio-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  try {
    const file = $('audio-file').files[0];
    if (!file) throw new Error('Выберите аудиофайл');
    audioUrl = URL.createObjectURL(file);
    let audio = document.querySelector('audio');
    if (!audio) { audio = document.createElement('audio'); audio.controls = true; $('review-card').prepend(audio); }
    audio.src = audioUrl;
    const form = new FormData(); form.append('file', file);
    const job = await api(`/api/meetings/${meeting.id}/audio`, { method: 'POST', body: form });
    $('job-status').textContent = 'Запись загружена. Обработка…';
    let state;
    do { await new Promise((resolve) => setTimeout(resolve, 700)); state = await api(`/api/jobs/${job.job_id}`); $('job-status').textContent = `${state.stage}: ${state.status}`; } while (!['done', 'error'].includes(state.status));
    if (state.status === 'error') throw new Error(state.error || 'Ошибка обработки');
    protocol = await api(`/api/meetings/${meeting.id}/protocol`);
    $('review-card').classList.remove('hidden'); renderSpeakers();
  } catch (error) { fail(error); }
});

function renderSpeakers() {
  const speakers = meeting.detected_speakers || [...new Set(protocol.utterances.map((u) => u.speaker_id).filter(Boolean))];
  $('speaker-map').innerHTML = '<h3>Подтверждение говорящих</h3>' + (speakers.length ? speakers.map((speaker) => `<div class="speaker"><code>${speaker}</code><select data-speaker="${speaker}"><option value="">Не подтверждено</option>${meeting.participants.map((p) => `<option value="${p.id}">${p.name}</option>`).join('')}</select><button class="save-speaker" data-speaker="${speaker}">Сохранить</button></div>`).join('') : '<p class="muted">Голоса не обнаружены.</p>');
  document.querySelectorAll('.save-speaker').forEach((button) => button.addEventListener('click', async () => {
    try {
      const rows = [...document.querySelectorAll('[data-speaker]')].filter((el) => el.tagName === 'SELECT').map((select) => ({speaker_id: select.dataset.speaker, participant_id: select.value || null, confirmed: Boolean(select.value)}));
      protocol = await api(`/api/meetings/${meeting.id}/speaker-map`, {method: 'PUT', headers: {'Content-Type':'application/json'}, body: JSON.stringify({speaker_map: rows})});
      banner('Связь голоса с участником сохранена секретарём.');
    } catch (error) { fail(error); }
  }));
}

$('extract').addEventListener('click', async () => { try { protocol = await api(`/api/meetings/${meeting.id}/extract`, {method:'POST'}); renderProtocol(); } catch (error) { fail(error); } });
$('approve').addEventListener('click', async () => { try { await api(`/api/meetings/${meeting.id}/approve`, {method:'POST'}); $('export').classList.remove('hidden'); $('export').href = `/api/meetings/${meeting.id}/export.docx`; banner('Протокол утверждён секретарём.'); renderProtocol(); } catch (error) { fail(error); } });

function renderProtocol() {
  const html = [`<audio controls id="player"></audio>`, '<h3>Поручения</h3>'];
  if (!protocol.tasks.length) html.push('<p class="muted">Поручений пока нет.</p>');
  protocol.tasks.forEach((task) => {
    const assignee = task.assignee_id || '<span class="unknown">Не определён — нужен вопрос</span>';
    const deadline = task.due_date ? `${task.due_date}${task.due_time ? ` ${task.due_time}` : ''}` : '<span class="unknown">Не определён — нужен вопрос</span>';
    const evidence = (task.evidence.action || []).map((id) => `<button class="evidence" data-utterance="${id}">${id}</button>`).join('');
    html.push(`<article class="task"><strong>${task.action}</strong><p>Ответственный: ${assignee}<br>Срок: ${deadline}</p><p>${evidence}</p></article>`);
  });
  html.push('<h3>Вопросы</h3>', protocol.review_questions.length ? `<ul>${protocol.review_questions.map((q) => `<li>${q.text}</li>`).join('')}</ul>` : '<p class="muted">Вопросов нет.</p>');
  $('protocol').innerHTML = html.join('');
  const player = $('player'); if (audioUrl) player.src = audioUrl;
  document.querySelectorAll('.evidence').forEach((button) => button.addEventListener('click', () => {
    const utterance = protocol.utterances.find((item) => item.id === button.dataset.utterance);
    if (utterance && player) { player.currentTime = utterance.start_ms / 1000; player.play().catch(() => {}); }
  }));
}
