# Контракт модулей, версия 1.0

`contract.schema.json` определяет полный снимок протокола. Эталоны examples/
показывают структуру, а не результаты работы моделей. Интерфейсы Python могут
использовать TypedDict/dataclasses/Pydantic по решению интегратора.

## Модули
- speech.transcribe_and_diarize(path, config) -> dict:
  {utterances: [...], detected_speakers: [...], warnings: [...]}.
  utterances имеют id, start_ms, end_ms, speaker_id, text.
  speaker_id может быть null. speaker_map подтверждает человек отдельно.
- protocol.extract(meeting, participants, speaker_map, utterances, config)
  -> {tasks, summary, review_questions, warnings}.
  Вопросы только по нерешённым реквизитам. Провал JSON-валидации — явная ошибка.
- protocol.catch_up(protocol, start_ms, end_ms) -> {items, warnings}.
  items ссылаются на реплики/решения выбранного интервала. Начать с фильтрации
  уже извлечённых событий; новая LLM не нужна. Согласованное изменение вне
  интервала показывается отдельно как более позднее, а не как услышанное там.
- export_docx.render(protocol) -> bytes.
  Не вызывает LLM; только представление утверждённых структур/черновика.
- vision.observe(frame, monotonic_ms) -> {visible_count, zones}.
  zones: [{zone_id, state: visible|not_observed|unknown}]. Выделяет события
  начала/конца интервала после настраиваемого гистерезиса. Никаких ФИО.

## Предлагаемые HTTP endpoints
Интегратор может реализовать синхронное ядро в рабочем процессе и простой
опрос статуса. Не создавать распределённую очередь ради одного совещания.
- POST /api/meetings: создать совещание, участников, дату/часовой пояс.
- POST /api/meetings/{id}/audio: принять файл и вернуть job_id.
- GET /api/jobs/{job_id}: queued|running|done|error, этап, реальная ошибка.
- GET /api/meetings/{id}/protocol: снимок по схеме 1.0.
- PUT /api/meetings/{id}/speaker-map: подтверждение связи голосов и участников.
- POST /api/meetings/{id}/extract: извлечь/пересчитать черновик.
- PATCH /api/meetings/{id}/tasks/{task_id}: человеческое исправление с аудитом;
  не представлять его как фразу из аудио.
- POST /api/meetings/{id}/approve: явное утверждение секретарём.
- GET /api/meetings/{id}/export.docx: файл с корректной маркировкой.
- POST /api/meetings/{id}/catch-up: {start_ms, end_ms}, только доступный протокол.

Только loopback-сервер по умолчанию. Не считать выбор имени в интерфейсе
аутентификацией. Сервис извлечения привязан к разрешённому локальному адресу.
Не позволять пользователю задавать произвольный URL для скачивания моделей.
