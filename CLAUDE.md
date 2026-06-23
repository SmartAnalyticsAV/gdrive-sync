# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Что это

`gdrive-sync` — CLI-утилита одностороннего зеркалирования локальных папок (macOS)
на Google Drive. Источник истины — локальная ФС; на Drive только заливаются новые
и изменённые файлы. Удаления локально на Drive **не распространяются** (режим
аддитивного бэкапа). Python 3.11+, управление зависимостями — `uv`, CLI — Typer.

## Команды

```bash
uv sync                       # окружение + зависимости (вкл. dev-группу с pytest)
uv run pytest                 # все тесты
uv run pytest tests/test_sync.py::test_decide_unchanged_fast_path   # один тест
uv run gdrive-sync --help     # CLI: auth | sync | status
uv run gdrive-sync sync --dry-run -c config.toml   # план без изменений (офлайн)
```

Тесты полностью офлайн: Drive API замокан (`FakeDrive` в `tests/test_sync.py`),
сетевых вызовов нет.

## Архитектура

Поток данных в [src/gdrive_sync/](src/gdrive_sync/):

`cli.py` → `config.load_config` (TOML) → `auth.get_credentials` + `auth.build_service`
→ `DriveClient` (`drive.py`) + `Manifest` (`manifest.py`) → `sync.run_sync` (движок).

- **[sync.py](src/gdrive_sync/sync.py)** — ядро. Чистая функция `decide()` (без I/O,
  кроме ленивого `compute_md5`) принимает решение SKIP/UPLOAD/UPDATE; класс `_Engine`
  обходит дерево (`os.walk`, `followlinks=False`) и применяет действия. Логика диффа
  отделена от Drive намеренно — чтобы тестировать без сети.
- **[drive.py](src/gdrive_sync/drive.py)** — тонкая обёртка Drive API v3 с
  `_with_retry` (экспон. backoff на 429/5xx и 403 rateLimitExceeded).
- **[manifest.py](src/gdrive_sync/manifest.py)** — SQLite-состояние (autocommit).
- **[config.py](src/gdrive_sync/config.py)** — модель/валидация TOML + пути приложения
  и `SCOPES`.

## Ключевые проектные решения (не менять, не разобравшись)

- **Детектор изменений: размер + MD5, а не mtime.** После заливки `modifiedTime`
  файла на Drive = момент загрузки, а не локальный mtime, поэтому наивное сравнение
  времён ненадёжно (плюс рассинхрон часов/таймзоны). MD5 (`md5Checksum` от Drive) —
  источник истины. `mtime` — лишь быстрый пред-фильтр: при совпадении size+mtime файл
  пропускается без хеширования (в `decide()`); при сдвиге mtime, но совпавшем MD5,
  stored mtime в манифесте освежается, чтобы не хешировать впредь.
- **Обновление через `files.update` того же `fileId`, не delete+create.** Сохраняет
  ссылки/доступы и историю ревизий, исключает гонку.
- **Манифест обязателен.** В Drive имя файла не уникально в папке и не является
  идентификатором — без `путь→fileId` нельзя надёжно «найти» уже залитый файл.
  Папки кэшируются по их Drive-пути (`folders.drive_path`), файлы — по абсолютному
  локальному пути (`files.local_path`).
- **OAuth scope `drive.file`** (узкий, без верификации приложения у Google):
  приложение видит только то, что само создало. Следствие — корневую папку
  назначения создаёт само приложение; вручную её в вебе создавать нельзя (станет
  невидимой). Менять scope на `drive` только осознанно.
- **`dry-run` офлайн.** Решения принимаются по манифесту; `DriveClient` не нужен
  (в CLI передаётся `None`). Любая правка движка не должна тянуть Drive в dry-run.

## Состояние и креды (в проекте, но вне git)

Каталог приложения — `.gdrive-sync/` рядом с `config.toml` (cwd-relative;
переопределяется `$GDRIVE_SYNC_HOME`): `client_secret.json`
(или `$GDRIVE_SYNC_CLIENT_SECRET`), `token.json`, `manifest.sqlite3`. Вся папка
`.gdrive-sync/`, как и `config.toml`, закрыта в `.gitignore` — не коммить секреты и
локальный конфиг. Дефолт cwd-relative выбран намеренно: и `config.toml`, и состояние
ищутся относительно директории запуска (запускать из корня проекта).
