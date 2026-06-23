from pathlib import Path

from gdrive_sync.config import Config, Source
from gdrive_sync.drive import DriveFile
from gdrive_sync.hashing import md5_file
from gdrive_sync.manifest import FileRecord, Manifest
from gdrive_sync.sync import Action, decide, run_sync, should_ignore


# --- decide() -----------------------------------------------------------
def _rec(size=10, md5="abc", mtime=100.0):
    return FileRecord(file_id="fid", size=size, md5=md5, mtime=mtime)


def test_decide_new_file():
    d = decide(10, 100.0, None, lambda: pytest_fail())
    assert d.action is Action.UPLOAD


def test_decide_size_changed_no_hash():
    called = []
    d = decide(20, 100.0, _rec(size=10), lambda: called.append(1) or "x")
    assert d.action is Action.UPDATE
    assert called == []  # размер уже отличается — хеш не считаем


def test_decide_unchanged_fast_path():
    called = []
    d = decide(10, 100.0, _rec(size=10, mtime=100.0), lambda: called.append(1) or "x")
    assert d.action is Action.SKIP
    assert called == []  # mtime совпал — без хеширования


def test_decide_mtime_changed_content_same():
    d = decide(10, 200.0, _rec(size=10, md5="same", mtime=100.0), lambda: "same")
    assert d.action is Action.SKIP
    assert d.touch_mtime is True


def test_decide_mtime_changed_content_differs():
    d = decide(10, 200.0, _rec(size=10, md5="old", mtime=100.0), lambda: "new")
    assert d.action is Action.UPDATE
    assert d.md5 == "new"


def pytest_fail():  # хелпер: вызов означал бы лишнее хеширование
    raise AssertionError("compute_md5 не должен был вызываться")


# --- should_ignore() ----------------------------------------------------
def test_ignore_file_glob():
    pats = ["*.tmp", ".DS_Store"]
    assert should_ignore("x.tmp", is_dir=False, patterns=pats)
    assert should_ignore(".DS_Store", is_dir=False, patterns=pats)
    assert not should_ignore("x.txt", is_dir=False, patterns=pats)


def test_ignore_dir_only_pattern():
    pats = [".git/"]
    assert should_ignore(".git", is_dir=True, patterns=pats)
    assert not should_ignore(".git", is_dir=False, patterns=pats)  # файл с таким именем не игнор


# --- run_sync() с фейковым Drive ---------------------------------------
class FakeDrive:
    def __init__(self):
        self.folders = {}  # (parent, name) -> id
        self.uploaded = []
        self.updated = []
        self._n = 0

    def ensure_folder(self, name, parent):
        key = (parent, name)
        if key in self.folders:
            return self.folders[key], False
        self._n += 1
        fid = f"folder-{self._n}"
        self.folders[key] = fid
        return fid, True

    def upload_file(self, local_path, name, parent_id, mtime):
        self._n += 1
        self.uploaded.append((name, parent_id))
        return DriveFile(
            id=f"file-{self._n}",
            name=name,
            md5=md5_file(local_path),
            size=Path(local_path).stat().st_size,
            modified_time=None,
        )

    def update_file(self, file_id, local_path, mtime):
        self.updated.append(file_id)
        return DriveFile(
            id=file_id,
            name=Path(local_path).name,
            md5=md5_file(local_path),
            size=Path(local_path).stat().st_size,
            modified_time=None,
        )


class _Resp:
    status = 403
    reason = "Forbidden"


def _http_error():
    from googleapiclient.errors import HttpError

    return HttpError(_Resp(), b'{"error": {"message": "boom"}}', uri="http://x")


class FailingDrive(FakeDrive):
    """Падает на создании любой папки — имитирует фатальную ошибку Drive."""

    def ensure_folder(self, name, parent):
        self._n += 1  # счётчик вызовов
        raise _http_error()


def _make_tree(root: Path):
    (root / "sub").mkdir(parents=True)
    (root / "a.txt").write_text("alpha")
    (root / "sub" / "b.txt").write_text("beta")
    (root / ".DS_Store").write_text("junk")
    gitdir = root / ".git"
    gitdir.mkdir()
    (gitdir / "config").write_text("should be ignored")


def _cfg(src: Path):
    return Config(
        dest_folder="Dest/Box",
        sources=[Source(path=src, name="src")],
        ignore_patterns=[".DS_Store", ".git/"],
    )


def test_run_sync_full_cycle(tmp_path):
    src = tmp_path / "src"
    _make_tree(src)
    cfg = _cfg(src)
    drive = FakeDrive()

    with Manifest(tmp_path / "m.sqlite3") as m:
        # первый прогон: оба файла новые
        stats = run_sync(cfg, drive, m, dry_run=False)
        assert stats.uploaded == 2
        assert stats.updated == 0
        assert stats.skipped == 0
        # папки: Dest, Dest/Box, Dest/Box/src, Dest/Box/src/sub = 4
        assert stats.folders_created == 4
        assert {n for n, _ in drive.uploaded} == {"a.txt", "b.txt"}

        # второй прогон без изменений: всё пропущено, новых папок нет
        stats = run_sync(cfg, drive, m, dry_run=False)
        assert (stats.uploaded, stats.updated, stats.skipped) == (0, 0, 2)
        assert stats.folders_created == 0


def test_run_sync_detects_content_change(tmp_path):
    src = tmp_path / "src"
    _make_tree(src)
    cfg = _cfg(src)
    drive = FakeDrive()

    with Manifest(tmp_path / "m.sqlite3") as m:
        run_sync(cfg, drive, m, dry_run=False)
        # меняем содержимое одного файла (другой размер)
        (src / "a.txt").write_text("alpha-changed-longer")
        stats = run_sync(cfg, drive, m, dry_run=False)
        assert stats.updated == 1
        assert stats.skipped == 1
        assert len(drive.updated) == 1


def test_run_sync_mtime_only_change_is_skip(tmp_path):
    src = tmp_path / "src"
    _make_tree(src)
    cfg = _cfg(src)
    drive = FakeDrive()

    with Manifest(tmp_path / "m.sqlite3") as m:
        run_sync(cfg, drive, m, dry_run=False)
        # тот же контент, но другой mtime
        import os

        os.utime(src / "a.txt", (12345.0, 12345.0))
        stats = run_sync(cfg, drive, m, dry_run=False)
        assert stats.updated == 0
        assert stats.skipped == 2
        # mtime в манифесте освежён → следующий прогон не пересчитывает
        rec = m.get_file(str(src / "a.txt"))
        assert rec.mtime == 12345.0


def test_fatal_drive_error_aborts_once(tmp_path):
    """Фатальная ошибка на папке назначения → 1 ошибка, без спама на файлы."""
    src = tmp_path / "src"
    _make_tree(src)
    cfg = _cfg(src)
    drive = FailingDrive()

    with Manifest(tmp_path / "m.sqlite3") as m:
        stats = run_sync(cfg, drive, m, dry_run=False)
        assert stats.errors == 1  # один аборт, а не по ошибке на каждый файл
        assert stats.uploaded == 0
        assert drive._n == 1  # ensure_folder вызван ровно один раз (упал сразу)


def test_dry_run_makes_no_drive_calls(tmp_path):
    src = tmp_path / "src"
    _make_tree(src)
    cfg = _cfg(src)

    with Manifest(tmp_path / "m.sqlite3") as m:
        stats = run_sync(cfg, drive=None, manifest=m, dry_run=True)
        assert stats.uploaded == 2
        assert stats.folders_created == 0  # в dry-run папки не создаём
        # манифест не записан
        assert m.counts() == (0, 0)
