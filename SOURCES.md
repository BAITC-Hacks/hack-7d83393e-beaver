# Первичная документация, проверена 23 сентября 2026 года

Это источники технических возможностей, а не доказательства качества
конкретной сборки на ваших совещаниях.

- faster-whisper: https://github.com/SYSTRAN/faster-whisper
  Есть загрузка модели из локального каталога; загрузка по имени может вызвать
  автоматическое скачивание, поэтому путь и полную комплектацию проверять.
- pyannote community-1: https://huggingface.co/pyannote/speaker-diarization-community-1
  Подтверждение условий до получения весов; описан локальный офлайн-запуск.
- Ollama structured outputs: https://docs.ollama.com/capabilities/structured-outputs
  Передача JSON Schema в format; после вывода нужна серверная валидация.
- Ollama FAQ: https://docs.ollama.com/faq
  OLLAMA_NO_CLOUD=1 или disable_ollama_cloud; перезапуск после настройки.
- Ultralytics tracking: https://docs.ultralytics.com/modes/track/
  Локальный трекинг, ByteTrack; ID трека не является удостоверением личности.
- python-docx: https://python-docx.readthedocs.io/en/latest/
  Создание и обновление DOCX.

Фиксировать лицензии конкретных библиотек И весов. Не путать локальную
доступность с разрешением на любое коммерческое использование.
