"""Потоковый MD5 локальных файлов.

Drive отдаёт md5Checksum для бинарных файлов, что делает MD5 надёжным
источником истины для детектора изменений (в отличие от mtime).
"""

from __future__ import annotations

import hashlib
from pathlib import Path

_CHUNK = 1024 * 1024  # 1 MiB


def md5_file(path: str | Path) -> str:
    """Посчитать MD5 файла, читая его кусками (не держим весь файл в памяти)."""
    digest = hashlib.md5()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()
