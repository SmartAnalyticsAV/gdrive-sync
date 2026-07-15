"""Движок синхронизации: обход локального дерева → диф → применение.

Детектор изменений (на файл):
  * нет в манифесте               → UPLOAD (новый);
  * размер отличается             → UPDATE;
  * размер совпал, mtime совпал   → SKIP (без хеширования — быстрый путь);
  * размер совпал, mtime отличается → считаем MD5:
        отличается  → UPDATE;
        совпадает   → SKIP, но освежаем mtime в манифесте (не хешировать впредь).

mtime — лишь дешёвый пред-фильтр; MD5 — источник истины. Удалённые локально
файлы на Drive не трогаем (режим аддитивного бэкапа).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from enum import Enum
from fnmatch import fnmatch
from pathlib import Path
from typing import Callable

from googleapiclient.errors import HttpError

from .config import Config
from .drive import DriveClient, explain_error
from .hashing import md5_file
from .manifest import Manifest

log = logging.getLogger("gdrive_sync")

# Допуск сравнения mtime (секунды): сглаживает разницу точности ФС.
_MTIME_EPS = 0.001


class Action(str, Enum):
    UPLOAD = "upload"
    UPDATE = "update"
    SKIP = "skip"


@dataclass
class Decision:
    action: Action
    reason: str
    md5: str | None = None  # локальный MD5, если он был посчитан
    touch_mtime: bool = False  # SKIP, но stored mtime надо обновить


@dataclass
class SyncStats:
    uploaded: int = 0
    updated: int = 0
    skipped: int = 0
    folders_created: int = 0
    errors: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "uploaded": self.uploaded,
            "updated": self.updated,
            "skipped": self.skipped,
            "folders_created": self.folders_created,
            "errors": self.errors,
        }


def _mtime_close(a: float, b: float) -> bool:
    return abs(a - b) <= _MTIME_EPS


def should_ignore(name: str, *, is_dir: bool, patterns: list[str]) -> bool:
    """Матчинг ignore-маски по basename.

    Маска с завершающим '/' применяется только к папкам; остальные — к любому.
    """
    for pat in patterns:
        if pat.endswith("/"):
            if is_dir and fnmatch(name, pat[:-1]):
                return True
        elif fnmatch(name, pat):
            return True
    return False


def _path_under(path: Path, root: Path) -> bool:
    """path равен root или лежит внутри него."""
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def on_whitelist_path(path: Path, roots: list[Path]) -> bool:
    """path «на пути белого списка»: лежит под корнем ИЛИ является его предком.

    Вариант «предок» нужен, чтобы не отрезать папку, ВНУТРИ которой есть
    вайтлистнутый путь (иначе os.walk туда не спустится). Для файла срабатывает
    только «лежит под корнем».
    """
    return any(_path_under(path, r) or _path_under(r, path) for r in roots)


def decide(
    size: int,
    mtime: float,
    record,  # manifest.FileRecord | None
    compute_md5: Callable[[], str],
) -> Decision:
    """Чистая логика выбора действия (без I/O, кроме ленивого compute_md5)."""
    if record is None:
        return Decision(Action.UPLOAD, "новый файл")
    if size != record.size:
        return Decision(Action.UPDATE, "изменился размер")
    if _mtime_close(mtime, record.mtime):
        return Decision(Action.SKIP, "не изменился")
    # размер тот же, но mtime сдвинулся — проверяем содержимое
    md5 = compute_md5()
    if md5 != record.md5:
        return Decision(Action.UPDATE, "изменилось содержимое", md5=md5)
    return Decision(
        Action.SKIP, "содержимое совпало, обновляю mtime", md5=md5, touch_mtime=True
    )


class _Engine:
    def __init__(
        self,
        config: Config,
        drive: DriveClient,
        manifest: Manifest,
        *,
        dry_run: bool,
    ) -> None:
        self.config = config
        self.drive = drive
        self.manifest = manifest
        self.dry_run = dry_run
        self.stats = SyncStats()
        self.dest_parts = config.dest_folder.split("/")

    def _ensure_folder_path(self, parts: list[str]) -> str:
        """Гарантировать дерево папок на Drive, кэшируя folderId в манифесте."""
        # 'root' — алиас "Моего диска" как РОДИТЕЛЯ. При scope drive.file читать
        # сам root нельзя (files.get(fileId='root') → 404), но 'root' как parent
        # в files.create допустим — так и создаём верхний уровень.
        parent = "root"
        accum: list[str] = []
        for part in parts:
            accum.append(part)
            key = "/".join(accum)
            cached = self.manifest.get_folder(key)
            if cached is not None:
                parent = cached
                continue
            folder_id, created = self.drive.ensure_folder(part, parent)
            self.manifest.put_folder(key, folder_id)
            if created:
                self.stats.folders_created += 1
                log.info("создана папка: %s", key)
            parent = folder_id
        return parent

    def _process_file(self, local_file: Path, name: str, dir_parts: list[str]) -> None:
        st = local_file.stat()
        key = str(local_file)
        record = self.manifest.get_file(key)
        decision = decide(
            st.st_size, st.st_mtime, record, lambda: md5_file(local_file)
        )

        if decision.action is Action.SKIP:
            self.stats.skipped += 1
            log.debug("skip: %s (%s)", local_file, decision.reason)
            if decision.touch_mtime and not self.dry_run and record is not None:
                self.manifest.put_file(
                    key, record.file_id, st.st_size, record.md5, st.st_mtime
                )
            return

        log.info("%s: %s (%s)", decision.action.value, local_file, decision.reason)
        if self.dry_run:
            if decision.action is Action.UPLOAD:
                self.stats.uploaded += 1
            else:
                self.stats.updated += 1
            return

        if decision.action is Action.UPLOAD:
            parent_id = self._ensure_folder_path(dir_parts)
            df = self.drive.upload_file(local_file, name, parent_id, st.st_mtime)
            self.stats.uploaded += 1
        else:  # UPDATE — обновляем содержимое существующего fileId на месте
            assert record is not None
            df = self.drive.update_file(record.file_id, local_file, st.st_mtime)
            self.stats.updated += 1

        # MD5 берём из ответа Drive; фолбэк — то, что посчитали при решении.
        md5 = df.md5 or decision.md5 or md5_file(local_file)
        self.manifest.put_file(key, df.id, st.st_size, md5, st.st_mtime)

    def _walk_source(self, source) -> None:
        root = source.path
        if not root.is_dir():
            log.warning("источник не найден или не папка, пропускаю: %s", root)
            return

        base_parts = self.dest_parts + [source.name]
        patterns = self.config.ignore_patterns
        whitelist = self.config.whitelist

        # followlinks=False — не ходим по симлинк-папкам (защита от циклов).
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            # фильтруем dirnames на месте, чтобы не спускаться в игнор-папки;
            # но папки «на пути белого списка» не режем.
            dirnames[:] = [
                d
                for d in dirnames
                if not should_ignore(d, is_dir=True, patterns=patterns)
                or on_whitelist_path(Path(dirpath) / d, whitelist)
            ]
            rel_dir = Path(dirpath).relative_to(root)
            dir_parts = base_parts + list(rel_dir.parts)

            for fname in sorted(filenames):
                local_file = Path(dirpath) / fname
                # ignore-маски действуют, КРОМЕ файлов из белого списка
                if should_ignore(
                    fname, is_dir=False, patterns=patterns
                ) and not on_whitelist_path(local_file, whitelist):
                    continue
                # Битый симлинк (target отсутствует) — не ошибка, просто пропускаем.
                if local_file.is_symlink() and not local_file.exists():
                    log.debug("пропущен битый симлинк: %s", local_file)
                    continue
                try:
                    self._process_file(local_file, fname, dir_parts)
                except OSError as exc:
                    self.stats.errors += 1
                    log.error("ошибка с файлом %s: %s", local_file, exc)
                except HttpError as exc:
                    self.stats.errors += 1
                    log.error("сбой загрузки %s: %s", local_file, explain_error(exc))
                except Exception as exc:  # noqa: BLE001 — продолжаем синк
                    self.stats.errors += 1
                    log.error("сбой загрузки %s: %s", local_file, exc)

    def run(self) -> SyncStats:
        # Папку назначения создаём ОДИН раз заранее: это кэширует дерево и даёт
        # ранний аборт с одним понятным сообщением при фатальной ошибке Drive
        # (API выключен, нет доступа, протух токен) — вместо спама на каждый файл.
        if not self.dry_run:
            try:
                self._ensure_folder_path(self.dest_parts)
            except HttpError as exc:
                self.stats.errors += 1
                log.error(
                    "не удалось подготовить папку назначения «%s» на Drive: %s",
                    self.config.dest_folder,
                    explain_error(exc),
                )
                return self.stats

        for source in self.config.sources:
            log.info("источник: %s → %s/%s", source.path, self.config.dest_folder, source.name)
            self._walk_source(source)
        return self.stats


def run_sync(
    config: Config,
    drive: DriveClient,
    manifest: Manifest,
    *,
    dry_run: bool = False,
) -> SyncStats:
    """Выполнить синхронизацию всех источников из конфига."""
    return _Engine(config, drive, manifest, dry_run=dry_run).run()
