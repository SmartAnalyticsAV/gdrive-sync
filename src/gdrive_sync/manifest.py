"""Локальное состояние синка в SQLite.

Зачем манифест: в Google Drive имя файла НЕ идентификатор — в одной папке
может быть несколько файлов с одинаковым именем. Манифест хранит однозначное
соответствие путь→fileId и кэширует size/md5/mtime, чтобы:
  * не разрешать имена в fileId запросами на каждый прогон;
  * не пересчитывать MD5, если size и mtime не менялись (быстрый пред-фильтр).

Два пространства ключей:
  folders — ключ = путь папки на стороне Drive (напр. "Backups/MacSync/Important");
  files   — ключ = абсолютный локальный путь файла.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType


@dataclass(frozen=True)
class FileRecord:
    file_id: str
    size: int
    md5: str
    mtime: float


_SCHEMA = """
CREATE TABLE IF NOT EXISTS folders (
    drive_path TEXT PRIMARY KEY,
    folder_id  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS files (
    local_path TEXT PRIMARY KEY,
    file_id    TEXT NOT NULL,
    size       INTEGER NOT NULL,
    md5        TEXT NOT NULL,
    mtime      REAL NOT NULL
);
"""


class Manifest:
    """Тонкая обёртка над SQLite-соединением (autocommit)."""

    def __init__(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        # isolation_level=None → autocommit: каждое изменение фиксируется сразу,
        # так прерванный прогон не теряет уже загруженные файлы.
        self._conn = sqlite3.connect(str(path), isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)

    # --- folders --------------------------------------------------------
    def get_folder(self, drive_path: str) -> str | None:
        row = self._conn.execute(
            "SELECT folder_id FROM folders WHERE drive_path = ?", (drive_path,)
        ).fetchone()
        return row["folder_id"] if row else None

    def put_folder(self, drive_path: str, folder_id: str) -> None:
        self._conn.execute(
            "INSERT INTO folders (drive_path, folder_id) VALUES (?, ?) "
            "ON CONFLICT(drive_path) DO UPDATE SET folder_id = excluded.folder_id",
            (drive_path, folder_id),
        )

    # --- files ----------------------------------------------------------
    def get_file(self, local_path: str) -> FileRecord | None:
        row = self._conn.execute(
            "SELECT file_id, size, md5, mtime FROM files WHERE local_path = ?",
            (local_path,),
        ).fetchone()
        if row is None:
            return None
        return FileRecord(
            file_id=row["file_id"],
            size=row["size"],
            md5=row["md5"],
            mtime=row["mtime"],
        )

    def put_file(
        self, local_path: str, file_id: str, size: int, md5: str, mtime: float
    ) -> None:
        self._conn.execute(
            "INSERT INTO files (local_path, file_id, size, md5, mtime) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(local_path) DO UPDATE SET "
            "file_id = excluded.file_id, size = excluded.size, "
            "md5 = excluded.md5, mtime = excluded.mtime",
            (local_path, file_id, size, md5, mtime),
        )

    # --- misc -----------------------------------------------------------
    def counts(self) -> tuple[int, int]:
        """(файлов, папок) в манифесте."""
        files = self._conn.execute("SELECT COUNT(*) AS c FROM files").fetchone()["c"]
        folders = self._conn.execute("SELECT COUNT(*) AS c FROM folders").fetchone()["c"]
        return files, folders

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "Manifest":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()
