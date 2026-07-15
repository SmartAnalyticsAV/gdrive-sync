"""Чтение и валидация TOML-конфига, а также пути приложения.

Конфиг описывает только намерение (что и куда зеркалить). Состояние —
кэш OAuth-токена и SQLite-манифест — живёт в каталоге приложения
(~/.config/gdrive-sync/ по умолчанию, переопределяется $GDRIVE_SYNC_HOME).
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

# OAuth scope. drive.file даёт доступ только к файлам, созданным приложением,
# и не требует верификации приложения у Google. Корневую папку назначения
# создаёт само приложение (см. config.example.toml).
SCOPES = ["https://www.googleapis.com/auth/drive.file"]


class ConfigError(Exception):
    """Конфиг отсутствует, не парсится или не проходит валидацию."""


@dataclass(frozen=True)
class Source:
    """Локальный корень для зеркалирования."""

    path: Path
    name: str  # имя соответствующей папки под dest_folder на Drive


@dataclass(frozen=True)
class Config:
    dest_folder: str  # путь назначения на Drive относительно "Мой диск"
    sources: list[Source]
    ignore_patterns: list[str] = field(default_factory=list)
    # белый список: файлы под этими путями бэкапятся, даже если подпадают под маски
    whitelist: list[Path] = field(default_factory=list)


def app_home() -> Path:
    """Каталог приложения для секретов и состояния.

    По умолчанию — подпапка .gdrive-sync/ в текущей рабочей директории
    (рядом с config.toml). Целиком закрыта в .gitignore. Переопределяется
    через $GDRIVE_SYNC_HOME.
    """
    override = os.environ.get("GDRIVE_SYNC_HOME")
    if override:
        return Path(override).expanduser()
    return Path(".gdrive-sync")


def token_path() -> Path:
    return app_home() / "token.json"


def client_secret_path() -> Path:
    """Путь к OAuth client_secret (Desktop app), скачанному из Google Cloud."""
    override = os.environ.get("GDRIVE_SYNC_CLIENT_SECRET")
    if override:
        return Path(override).expanduser()
    return app_home() / "client_secret.json"


def manifest_path() -> Path:
    return app_home() / "manifest.sqlite3"


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ConfigError(message)


def load_config(path: str | os.PathLike[str]) -> Config:
    """Прочитать и провалидировать TOML-конфиг."""
    cfg_path = Path(path).expanduser()
    _require(cfg_path.is_file(), f"Конфиг не найден: {cfg_path}")

    try:
        with cfg_path.open("rb") as fh:
            data = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"Не удалось разобрать TOML {cfg_path}: {exc}") from exc

    drive = data.get("drive", {})
    dest_folder = drive.get("dest_folder")
    _require(
        isinstance(dest_folder, str) and dest_folder.strip(),
        "В [drive] не задан непустой строковый dest_folder",
    )
    dest_folder = dest_folder.strip().strip("/")
    _require(bool(dest_folder), "dest_folder не может быть пустым после нормализации")

    raw_sources = data.get("sources", [])
    _require(
        isinstance(raw_sources, list) and raw_sources,
        "Нужен хотя бы один [[sources]] с полем path",
    )

    sources: list[Source] = []
    seen_names: set[str] = set()
    for i, item in enumerate(raw_sources):
        _require(isinstance(item, dict), f"sources[{i}] должен быть таблицей")
        raw_path = item.get("path")
        _require(
            isinstance(raw_path, str) and raw_path.strip(),
            f"sources[{i}].path должен быть непустой строкой",
        )
        local = Path(raw_path).expanduser()
        name = item.get("name") or local.name
        _require(
            isinstance(name, str) and name.strip(),
            f"sources[{i}].name должен быть непустой строкой",
        )
        name = name.strip()
        _require(
            "/" not in name,
            f"sources[{i}].name не должен содержать '/': {name!r}",
        )
        _require(
            name not in seen_names,
            f"Дублирующееся имя назначения {name!r} в sources — задай уникальный name",
        )
        seen_names.add(name)
        sources.append(Source(path=local, name=name))

    ignore = data.get("ignore", {})
    if not isinstance(ignore, dict):
        ignore = {}
    patterns = ignore.get("patterns", [])
    _require(
        isinstance(patterns, list) and all(isinstance(p, str) for p in patterns),
        "[ignore].patterns должен быть списком строк",
    )

    raw_whitelist = ignore.get("whitelist", [])
    _require(
        isinstance(raw_whitelist, list) and all(isinstance(p, str) for p in raw_whitelist),
        "[ignore].whitelist должен быть списком строк (путей)",
    )
    whitelist = [Path(p).expanduser() for p in raw_whitelist]

    return Config(
        dest_folder=dest_folder,
        sources=sources,
        ignore_patterns=patterns,
        whitelist=whitelist,
    )
