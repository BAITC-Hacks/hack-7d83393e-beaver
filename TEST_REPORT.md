# Test report

Последнее обновление: 23 сентября 2026 года. Отчёт описывает фактически
выполненные проверки в этой рабочей среде; отсутствие запуска помечено `NOT RUN`.

## Окружение

- Python 3.12.3, Linux x86_64, 16 CPU, 30 GiB RAM, 126 GiB свободного места.
- Обнаружена NVIDIA GeForce RTX 3070 Laptop GPU (8 GiB), но установленный
  `torch 2.3.1+cpu` не видит CUDA. Тяжёлые модели проверяются в CPU-профиле.
- Повторная проверка 23 сентября показала `nvidia-smi ERR!` и ошибку
  CTranslate2 `CUDA failed with error no CUDA-capable device is detected`;
  подробности сохранены в `reports/gpu/status.json`.
- Проверенные пакеты: `faster-whisper 1.2.1`, `pyannote.audio 3.3.2`,
  `ollama 0.6.2`, `python-docx 1.2.0`.

## Выполненные команды

| Команда | Результат | Фактическое ограничение |
|---|---|---|
| `.venv/bin/python scripts/cli.py test` | PASS, 18 тестов | fixture, sidecar ledger и data-level проверки; не доказывает качество моделей |
| `.venv/bin/python scripts/preflight.py --json` | NOT READY | ASR и Ollama готовы; pyannote неполный |
| `.venv/bin/python scripts/model_manifest.py` | PASS | записан `models/manifest.json`; Whisper smoke inference PASS, Ollama установлен, text extraction NOT RUN |
| `.venv/bin/python scripts/cli.py e2e` | NOT RUN | команда ограничена fixture smoke test; REAL audio/browser E2E не выполнен |
| локальный faster-whisper на `data/real/*_case.wav` | PASS, 3/3 | espeak-сгенерированная речь; это synthetic-speech smoke, не natural-audio оценка |

## Требования приёмки

| Требование | Статус | Артефакт или причина |
|---|---|---|
| Контрактные fixtures, evidence и null | PASS | `scripts/cli.py test` |
| DOCX из протокола и казахские буквы | PASS | `tests/acceptance/test_export_docx.py` |
| Реальный ASR inference | PASS (synthetic speech, 3/3) | `reports/real-asr/results.json`, локальный `faster-whisper-small`, CPU |
| Реальная диаризация | FAIL | в `models/pyannote-community-1` отсутствует `config.yaml` |
| Локальная LLM | NOT RUN | веса `qwen2.5:3b` остались локально, но временный Ollama runtime недоступен после очистки `/tmp`; предыдущий запуск schema extraction превысил 180-секундный тайм-аут |
| Русская, казахская и смешанная новая запись | NOT RUN | нет подтверждённого REAL E2E |
| Заблокированная сеть и браузерная проверка | NOT RUN | не выполнялись в этой среде |
| DOCX после реальной записи | NOT RUN | зависит от ASR, диаризации и LLM |

Источники и локальные digest моделей записаны в [`models/manifest.json`](models/manifest.json).
Manifest отражает состояние среды и не подменяет пробный inference.
