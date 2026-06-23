# gdrive-sync

Одностороннее зеркало локальных папок на Google Drive: указываешь в конфиге
папку назначения на Drive и список локальных корней — приложение заливает новые
и изменённые файлы. Удалённые локально файлы на Drive не трогаются (режим
аддитивного бэкапа).

## Как это работает

- **Детектор изменений** — размер + MD5 (`md5Checksum`, который Drive отдаёт даром).
  `mtime` используется лишь как быстрый пред-фильтр: если размер и mtime совпали с
  записанными — файл пропускается без хеширования.
- **Обновление на месте** — изменённый файл заливается через `files.update` того же
  `fileId` (сохраняются ссылки/доступы и история ревизий), а не delete+create.
- **Манифест** — локальная SQLite-база (`путь → fileId, size, md5, mtime`), потому что
  в Drive имя файла не уникально и не является идентификатором.

## Установка и запуск

```bash
uv sync                       # создать окружение и поставить зависимости
uv run gdrive-sync --help
```

### Настройка Google Cloud (по шагам)

Делается один раз в [Google Cloud Console](https://console.cloud.google.com).
Все шаги обязательны — пропуск любого даёт ошибку при `auth`/`sync` (см. «Грабли» ниже).

1. **Создай проект** (или выбери существующий) — вверху в переключателе проектов.
2. **Включи Google Drive API**: APIs & Services → Library → найди «Google Drive API»
   → **Enable**. (Без этого — `403 accessNotConfigured` при первом `sync`.)
3. **Настрой OAuth consent screen**: APIs & Services → OAuth consent screen →
   User type **External** → заполни обязательные поля (App name, support email,
   developer email).
4. **Добавь себя в Test users**: на том же OAuth consent screen (вкладка **Audience**
   в новом интерфейсе) → **Test users** → Add users → впиши свой Google-аккаунт.
   (Без этого — `403 access_denied` «приложение не прошло проверку».)
5. **Создай OAuth client ID**: APIs & Services → Credentials → **Create credentials**
   → **OAuth client ID** → Application type **Desktop app** → Create → **Download JSON**.
   ⚠️ Именно OAuth client ID, **НЕ** service account — это разные вещи.
6. **Положи JSON** в `.gdrive-sync/client_secret.json` (рядом с `config.toml`; папку
   создай сам или укажи путь к файлу через `GDRIVE_SYNC_CLIENT_SECRET`).
7. **Авторизуйся**: `uv run gdrive-sync auth`. Откроется браузер (если нет — скопируй
   URL из терминала). На экране «приложение не проверено» жми **Дополнительно →
   Перейти на страницу … (небезопасно)** — для своего же приложения это нормально.
   Разреши доступ → терминал напишет «Авторизация успешна».

> **Токен в режиме Testing живёт ~7 дней.** Чтобы не переавторизовываться каждую
> неделю — на OAuth consent screen нажми **Publish app → In production**. Scope
> `drive.file` несенситивный, поэтому проверка приложения Google **не требуется**.

### Грабли, через которые мы прошли

| Симптом | Причина | Фикс |
|---|---|---|
| `ValueError: Client secrets must be for a web or installed app` | Скачан ключ **service account**, а не OAuth client | Создай **OAuth client ID → Desktop app** (шаг 5) |
| `403 accessNotConfigured … Drive API has not been used` | Drive API не включён в проекте | Включи Drive API (шаг 2), подожди 1–3 мин |
| `403 access_denied` «приложение не прошло проверку» | Аккаунт не в **Test users** | Добавь себя в Test users (шаг 4) |
| Экран «приложение не проверено» | Consent screen в режиме Testing | **Дополнительно → Перейти (небезопасно)** — это норма |
| Раз в неделю снова просит `auth` | Testing-режим: refresh-токен живёт 7 дней | **Publish app → In production** |

> **Про папку назначения.** Scope `drive.file` означает, что приложение видит и
> трогает **только то, что создало само**. Поэтому `dest_folder` (напр.
> `MyOwn/Backups/MacSync`) **не создавай вручную** в веб-интерфейсе — всё дерево
> папок от корня `gdrive-sync` строит сам при первом `sync`. Если создашь вручную —
> приложение её «не увидит» и заведёт свою (будет дубль с тем же именем).

> Секреты и состояние (`client_secret.json`, `token.json`, `manifest.sqlite3`)
> живут в `.gdrive-sync/` внутри проекта и закрыты в `.gitignore`. Расположение
> переопределяется переменной `GDRIVE_SYNC_HOME`.

### Конфиг

Скопируй [config.example.toml](config.example.toml) в `config.toml` и отредактируй.

```bash
uv run gdrive-sync sync --dry-run     # показать план без изменений
uv run gdrive-sync sync               # выполнить синхронизацию
uv run gdrive-sync status             # состояние: конфиг, манифест, токен
```

`--dry-run` работает офлайн (по манифесту) и к Drive не обращается.
