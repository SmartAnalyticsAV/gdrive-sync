#!/bin/sh
# Обёртка для запуска gdrive-sync по расписанию (cron/launchd).
#
# Зачем скрипт, а не команда прямо в crontab:
#   * cron даёт минимальный PATH — uv в нём не находится → задаём PATH явно;
#   * cron стартует в произвольном cwd, а config.toml и .gdrive-sync/ ищутся
#     относительно cwd → переходим в корень проекта;
#   * весь вывод (и ошибки) — в лог с отметкой времени.
#
# Запуск строго --non-interactive: в фоне некому проходить OAuth в браузере.
# Если токен вдруг станет недействителен — будет понятная ошибка в логе, а не
# зависший процесс (см. auth.get_credentials).

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

# корень проекта = папка на уровень выше этого скрипта
cd "$(dirname "$0")/.." || exit 1

LOG=".gdrive-sync/cron.log"
echo "===== $(date '+%Y-%m-%d %H:%M:%S') gdrive-sync sync =====" >> "$LOG"
uv run gdrive-sync sync --non-interactive >> "$LOG" 2>&1
