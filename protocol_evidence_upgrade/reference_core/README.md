# Protocol Evidence — исполняемый reference core

**Это не готовый ассистент совещаний.** Здесь нет ASR, диаризации, LLM и YOLO.
Стенд работает с синтетическими размеченными событиями и импортированными
JSON-снимками. Он позволяет интегратору переиспользовать проверенную логику,
а не заново писать только спецификацию.

## Что работает
- Structural audit снимка по исходному contract 1.0: типы, даты, ссылки,
  идентификаторы, интервалы, основания непустых полей.
- Typed event replay: create/propose/accept/reject/cancel; текущая версия,
  временной срез, устаревшие предложения, основание изменённого значения.
- Вопросы по отсутствующим исполнителю и сроку.
- FastAPI HTTP-сервис, локальные HTML/CSS/JS, ползунок времени,
  подсветка реплик, импорт JSON, отчёт и DOCX из сохранённого снимка.
- 80 unit/contract/API/export проверок. Список — reports/reference-results.json.

Снимок 1.0 и событийный sidecar в этом стенде — отдельные примеры.
Автоматический адаптер между ними и извлечение событий из речи НЕ реализованы.
Структурный PASS не проверяет смысловую поддержку. Для человеческих правок
необходимо расширение отдельного журнала; аудитор не подставляет им evidence.
Промышленная аутентификация, роли, база встреч, уведомления и lifecycle jobs
не реализованы. Сервер предназначен только для loopback-стенда.

## Запуск (Python 3.11+; фактически проверено 3.13.5)

```bash
python -m venv .venv
# Linux/macOS:
source .venv/bin/activate
# Windows вместо строки выше: .venv\Scripts\activate
python -m pip install -r requirements.txt
python scripts/verify_reference.py
python -m evidence_core serve --port 8765
```

Открыть http://127.0.0.1:8765. Остановить сервер: Ctrl+C.
Терминал с сервером должен оставаться запущенным.
Зависимости были доступны в среде проверки; новый online-install lock-набора
в чистой среде здесь не проверялся. requirements.txt фиксирует реально
использованные версии, но не является полным transitive lock с хешами.

```bash
python -m evidence_core verify fixtures/01_mixed_deadline.json
python -m evidence_core replay fixtures/ledger_mixed.json --at-ms 16000
```

Временной срез 16000 сохраняет первоначальный срок; 22000 применяет перенос.
У 40000 более позднее предложение отклонено, принятый срок не меняется.

## HTTP
GET /api/health — liveness, явно models_loaded=false.
GET /api/capabilities — реализованные и отсутствующие модули.
POST /api/audit — JSON-снимок 1.0.
POST /api/replay — {"bundle": <ledger>, "cutoff_ms": 16000}.
POST /api/export.docx — JSON-снимок, возвращает черновик DOCX.
GET /openapi.json — локальное описание API. Внешние Swagger/CDN не подключены.
Предел JSON — 2 МБ. Тексты отображаются как текст, не HTML.

## Проверка браузером
Дополнительно нужен playwright и доступный Chromium:

```bash
python -m pip install -r requirements-browser.txt
# Использовать установленный Chromium или:
python -m playwright install chromium
# При работающем сервере:
python scripts/browser_check.py
```

В среде сборки прямое браузерное обращение к loopback заблокировано
политикой Chromium: ERR_BLOCKED_BY_ADMINISTRATOR. Политика не изменялась.
Поэтому live-browser HTTP E2E = NOT RUN.

Отдельно выполнен browser component test с настоящим FastAPI через in-process
TestClient и тестовый fetch adapter (не сетевой запрос из браузера):

```bash
python scripts/browser_component_check.py
```

Он проверил ползунок, ссылки, кнопки, DOCX, смену fixture и безопасный вывод
недоверенного текста. Это не заменяет live-browser или offline-full E2E.
Скриншот reports/ui-reference.png получен именно этим способом.
HTTP-сервер отдельно поднимался и отвечал на реальный loopback health-запрос.

## Ограничения отчёта
Модели/качество языков/natural audio/offline полного процесса: NOT RUN.
Время и число тестов относятся только к reference core.
SHA-256 — отпечаток данных, не цифровая подпись.
Синтетические материалы не имеют реальных персональных данных.
Код не загружает и не вызывает модели. Работающий интерфейс не закрывает P0.
