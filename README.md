# Пакет заданий для агентной сборки: «Совещание без потерянных поручений»

Дата подготовки: 23 сентября 2026 года.

**Статус: локальный прототип и проверочный контур собраны.** Есть FastAPI/SQLite,
локальный интерфейс, контрактный протокол, fixture-путь до DOCX, manifest моделей
и явные ошибки REAL-режима. В текущем отчёте ASR-вес найден, комплект pyannote
неполный, а Ollama отсутствует; поэтому реальный сквозной сценарий не объявляется
готовым. См. [`TEST_REPORT.md`](TEST_REPORT.md). Локальная `qwen2.5:3b`
скачана и доступна Ollama; строгий text extraction отмечен в отчёте как
`NOT RUN` после тайм-аута.

## Начало работы
Передать интегратору `MASTER_PROMPT.md`, затем дать каждому исполнителю
`AGENTS.md`, `contract.schema.json`, `API.md` и его файл из `agent_tasks/`.
Только интегратор меняет общие контракты и зависимости. У агентов разные ветки
или рабочие каталоги. Не требуется установка нового оркестратора ради спринта.
Название/версия конкретной Luna не были заданы: платформа-специфические команды
здесь намеренно не выдуманы.

## Продукт
Обязательная основа: локальный ввод аудио, русский/казахский/смешанная речь,
диаризация, подтверждение соответствия голосов участникам, автоматические
поручения, саммари, редактирование секретарём и DOCX.
Две отличительные функции: вопросы о недостающих реквизитах и история
согласованных изменений со ссылками на речь.
Опционально: YOLO предлагает интервал для карточки «Что обсуждали за это время».
Никаких эмоций, рейтингов внимания, распознавания лиц или голосовой биометрии.

## Условия спринта
120 минут — бюджет команды, не обещание завершения. Веса и рабочее окружение
нужно проверить в начале. Не заменять сломавшуюся модель облачным API.
Если локальный ASR/диаризация не заработали, явно указать невыполненный пункт.
Воспроизведение эталонного JSON — отдельный режим FIXTURE, а не работающий ИИ.

## Файлы
- `MASTER_PROMPT.md`: общее задание интегратору.
- `AGENTS.md`: общие правила и границы ответственности.
- `SPRINT.md`: бюджет этапов и правила сокращения объёма.
- `API.md`: минимальный интерфейс модулей и HTTP.
- `agent_tasks/`: четыре основных задания и один опциональный видеомодуль.
- `contract.schema.json`: JSON Schema протокола версии 1.0.
- `examples/`: три полностью вымышленных эталона, НЕ результаты распознавания.
- `ACCEPTANCE.md`: проверки реального приложения.
- `DEMO.md`: сценарий защиты и реплики для самостоятельной записи.
- `SOURCES.md`: первичная документация компонентов.
- `check_examples.py`: проверка схемы и ссылочной целостности эталонов.
- `scripts/cli.py`: команды `bootstrap`, `doctor`, `run`, `test`, `e2e`, `stop`.
- `scripts/model_manifest.py`: офлайн-инвентаризация моделей и digest каталога.
- `models/manifest.json`: фактическое состояние весов в текущей среде.
- `TEST_REPORT.md`, `reports/test-results.json`: фактические результаты проверок.
- `requirements-traceability.json`: матрица требований, команд и фактических статусов.

Для проверки эталонов нужен Python с пакетом `jsonschema`.

## Локальная проверка и экспорт

Создание окружения и установка Python-зависимостей выполняются в режиме
`BOOTSTRAP_ONLINE`:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python scripts/cli.py bootstrap
```

`bootstrap` устанавливает только Python-пакеты. Он не скачивает модели.
Запуск приложения выполняется отдельно в режиме `RUN_OFFLINE`:

```bash
.venv/bin/python scripts/cli.py doctor
.venv/bin/python scripts/cli.py run --host 127.0.0.1 --port 8000
# в другом терминале
.venv/bin/python scripts/cli.py stop
```

Откройте `http://127.0.0.1:8000`. Для проверки UI без моделей выберите режим
`FIXTURE`; постоянный баннер помечает синтетический результат.

Единая CLI-точка для повторяемых проверок:

```bash
.venv/bin/python scripts/cli.py doctor
.venv/bin/python scripts/cli.py test
.venv/bin/python scripts/cli.py e2e
.venv/bin/python scripts/cli.py verify --profile core --offline --out reports/core
.venv/bin/python scripts/cli.py verify --profile full --offline --out reports/full
.venv/bin/python scripts/cli.py stop
```

`verify --profile full` возвращает код 2, пока обязательные REAL-модули не
проверены. `evaluate --input <dir> --out <dir>` создаёт отчёт с `NOT RUN`, если
новый набор реальных данных не передан. Модельный manifest находится в
`models/manifest.json`, а отчёт запуска — в `reports/`.

Команды проверки не отправляют аудио или протоколы наружу. В текущей среде:
Python `3.12.3`, Linux x86_64, 16 CPU, 30 GiB RAM, 126 GiB свободного места;
обнаружена NVIDIA GeForce RTX 3070 Laptop GPU (8 GiB), но установленный
`torch 2.3.1+cpu` не использует CUDA; Ollama работает в CPU-профиле. Точный список пакетов и digest записаны в
`models/manifest.json`. Экспорт требует `python-docx` (MIT), проверка схемы —
`jsonschema` (MIT). Лицензии локальных моделей указаны в manifest и требуют
отдельной проверки перед распространением.

Подготовка весов выполняется заранее, пока сеть разрешена, с явным указанием
локальных каталогов. Например, для faster-whisper:

```bash
.venv/bin/pip install huggingface_hub
.venv/bin/python - <<'PY'
from huggingface_hub import snapshot_download
snapshot_download("Systran/faster-whisper-small", local_dir="models/faster-whisper-small")
PY
ollama pull qwen2.5:3b
```

Для pyannote `community-1` требуется вручную принять условия модели и получить
разрешённый локальный snapshot; токены не записываются в репозиторий. Пример
подготовки выполняется только в online shell. После подготовки запуск не
скачивает модели: задайте `ASR_MODEL_PATH`/`WHISPER_MODEL_PATH`,
`DIARIZATION_MODEL_PATH`/`PYANNOTE_MODEL_PATH`, `OLLAMA_BIN` и `OLLAMA_MODEL`, затем проверьте
окружение офлайн:

```bash
.venv/bin/python scripts/model_manifest.py
.venv/bin/python scripts/preflight.py --json --strict
.venv/bin/python scripts/cli.py test
```

Для GPU-профиля faster-whisper нужны CUDA-библиотеки CTranslate2 и явные
параметры запуска:

```bash
.venv/bin/pip install nvidia-cublas-cu12 nvidia-cuda-nvrtc-cu12
export LD_LIBRARY_PATH="$PWD/.venv/lib/python3.12/site-packages/nvidia/cublas/lib:$PWD/.venv/lib/python3.12/site-packages/nvidia/cuda_nvrtc/lib:$LD_LIBRARY_PATH"
export WHISPER_DEVICE=cuda WHISPER_COMPUTE_TYPE=float16
```

`preflight.py` проверяет обязательные файлы весов, локальный `ollama list`,
чтение WAV или локального файла через `ffprobe`, а также импорты FastAPI, ASR,
диаризации, экспорта и служебных пакетов. Он не скачивает зависимости или
модели и не принимает URL. Наличие обязательных файлов ещё не является
доказательством inference: это отдельно фиксируется в manifest и отчёте.
Сквозной fixture-путь сервера проверяется `tests/test_app.py`; он не заменяет
обязательную проверку новой реальной записи.

Для типизированной истории есть sidecar [`ledger.schema.json`](ledger.schema.json)
и `services/ledger.py`. Endpoint `POST /api/meetings/{id}/ledger/replay`
воспроизводит create/propose/accept/reject/cancel по префиксу времени и явно
адаптирует результат обратно к snapshot-контракту 1.0.

`scripts/cli.py e2e` запускает только локальный fixture smoke test и явно печатает
`REAL audio/browser E2E = NOT RUN`. Браузерная проверка, три новые записи и
offline acceptance требуют отдельного запуска и не получают PASS автоматически.

Три обязательных сценария для ручного отчёта:

| Сценарий | Вход | Что сохранить | Статус в этом пакете |
|---|---|---|---|
| RU | новая русская запись | транскрипт, говорящие, DOCX и сравнение с ручным эталоном | NOT RUN |
| KK | новая казахская запись | те же артефакты и буквы Ә Ғ Қ Ң Ө Ұ Ү Һ І | NOT RUN |
| MIX | новая смешанная запись | те же артефакты, перенос срока и вопросы по null | NOT RUN |

| Требование | Реализовано | Чем проверено |
|---|---|---|
| DOCX из протокола без LLM | Да, `services/export_docx.py` | `tests/acceptance/test_export_docx.py` |
| Дата, участники, саммари и поручения | Да | data-level DOCX-тест |
| Evidence с временными метками и история | Да | data-level DOCX-тест |
| Черновик/утверждение | Да, по явному маркеру протокола | data-level DOCX-тест |
| Казахские буквы | Да, Unicode и шрифт документа | data-level DOCX-тест |
| Реальный ASR, диаризация, LLM | Интерфейсы и явные ошибки есть | обязательный REAL-тест, NOT RUN |
| Работа при заблокированной сети | Дизайн preflight/export локальный | ручной offline-тест, NOT RUN |

Известные ограничения: экспорт не проверяет смысловую правильность evidence и
не заменяет утверждение секретаря; отсутствующий ID evidence намеренно помечается
в документе как ошибка; `python-docx`, локальные модели и Ollama должны быть
установлены отдельно. Синтетические JSON в `examples/` не считаются результатом
ASR и не заменяют три записи из таблицы.
