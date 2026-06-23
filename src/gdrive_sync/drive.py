"""Тонкая обёртка над Google Drive API v3 с retry/backoff.

Покрывает ровно то, что нужно one-way mirror:
  * ensure_folder  — найти или создать папку под родителем (идемпотентно);
  * upload_file    — files.create с медиа (resumable для крупных файлов);
  * update_file    — files.update содержимого СУЩЕСТВУЮЩЕГО fileId (на месте,
                     без delete+create — сохраняет fileId, доступы и ревизии).

modifiedTime на стороне Drive проставляется равным mtime локального файла,
но источник истины детектора изменений — md5Checksum (см. sync.py).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, TypeVar

from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

FOLDER_MIME = "application/vnd.google-apps.folder"
RESUMABLE_THRESHOLD = 5 * 1024 * 1024  # 5 MiB
_FIELDS = "id, name, md5Checksum, size, modifiedTime"
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}
_MAX_ATTEMPTS = 5
_T = TypeVar("_T")


@dataclass(frozen=True)
class DriveFile:
    id: str
    name: str
    md5: str | None
    size: int | None
    modified_time: str | None

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> "DriveFile":
        size = data.get("size")
        return cls(
            id=data["id"],
            name=data.get("name", ""),
            md5=data.get("md5Checksum"),
            size=int(size) if size is not None else None,
            modified_time=data.get("modifiedTime"),
        )


def _is_retryable(exc: HttpError) -> bool:
    status = getattr(exc.resp, "status", None)
    if status in _RETRYABLE_STATUS:
        return True
    # 403 с причиной rateLimitExceeded/userRateLimitExceeded — тоже временная.
    if status == 403:
        content = exc.content or b""
        return b"ateLimitExceeded" in content  # ловит оба варианта причины
    return False


def _with_retry(thunk: Callable[[], _T], *, sleep: Callable[[float], None] = time.sleep) -> _T:
    """Выполнить вызов API, повторяя при временных ошибках с экспон. backoff."""
    delay = 1.0
    last: HttpError | None = None
    for attempt in range(_MAX_ATTEMPTS):
        try:
            return thunk()
        except HttpError as exc:
            if not _is_retryable(exc) or attempt == _MAX_ATTEMPTS - 1:
                raise
            last = exc
            sleep(delay)
            delay = min(delay * 2, 60.0)
    assert last is not None  # недостижимо
    raise last


def explain_error(exc: HttpError) -> str:
    """Краткое человекочитаемое описание HttpError (без громоздкого repr)."""
    status = getattr(getattr(exc, "resp", None), "status", "?")
    try:
        reason = exc.reason or ""
    except Exception:  # noqa: BLE001 — reason может бросить, если нет тела
        reason = ""
    return f"{status} {reason}".strip()


def _rfc3339(mtime: float) -> str:
    """epoch-секунды → RFC 3339 UTC (формат, который принимает Drive)."""
    return datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()


def _escape_query(value: str) -> str:
    """Экранирование строкового литерала для query Drive API."""
    return value.replace("\\", "\\\\").replace("'", "\\'")


class DriveClient:
    def __init__(self, service: Any) -> None:
        self._svc = service

    def _find_child(
        self, name: str, parent_id: str, *, folders_only: bool
    ) -> DriveFile | None:
        q = (
            f"name = '{_escape_query(name)}' and "
            f"'{parent_id}' in parents and trashed = false"
        )
        if folders_only:
            q += f" and mimeType = '{FOLDER_MIME}'"
        else:
            q += f" and mimeType != '{FOLDER_MIME}'"

        resp = _with_retry(
            lambda: self._svc.files()
            .list(
                q=q,
                spaces="drive",
                fields=f"files({_FIELDS})",
                pageSize=10,
            )
            .execute()
        )
        files = resp.get("files", [])
        return DriveFile.from_api(files[0]) if files else None

    def ensure_folder(self, name: str, parent_id: str) -> tuple[str, bool]:
        """Найти или создать папку name под parent_id.

        Возвращает (folderId, created), где created=True, если папка была
        создана на этом вызове (для точной статистики).
        """
        existing = self._find_child(name, parent_id, folders_only=True)
        if existing is not None:
            return existing.id, False

        meta = {"name": name, "mimeType": FOLDER_MIME, "parents": [parent_id]}
        created = _with_retry(
            lambda: self._svc.files().create(body=meta, fields="id").execute()
        )
        return created["id"], True

    def find_file(self, name: str, parent_id: str) -> DriveFile | None:
        """Найти НЕ-папку с таким именем под родителем (или None)."""
        return self._find_child(name, parent_id, folders_only=False)

    def upload_file(
        self, local_path: str | Path, name: str, parent_id: str, mtime: float
    ) -> DriveFile:
        """Создать новый файл (files.create) с содержимым local_path."""
        local_path = str(local_path)
        size = Path(local_path).stat().st_size
        media = MediaFileUpload(
            local_path, resumable=size >= RESUMABLE_THRESHOLD
        )
        meta = {
            "name": name,
            "parents": [parent_id],
            "modifiedTime": _rfc3339(mtime),
        }
        created = _with_retry(
            lambda: self._svc.files()
            .create(body=meta, media_body=media, fields=_FIELDS)
            .execute()
        )
        return DriveFile.from_api(created)

    def update_file(
        self, file_id: str, local_path: str | Path, mtime: float
    ) -> DriveFile:
        """Заменить содержимое существующего fileId (на месте, не delete+create)."""
        local_path = str(local_path)
        size = Path(local_path).stat().st_size
        media = MediaFileUpload(
            local_path, resumable=size >= RESUMABLE_THRESHOLD
        )
        meta = {"modifiedTime": _rfc3339(mtime)}
        updated = _with_retry(
            lambda: self._svc.files()
            .update(fileId=file_id, body=meta, media_body=media, fields=_FIELDS)
            .execute()
        )
        return DriveFile.from_api(updated)
