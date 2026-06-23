from gdrive_sync.manifest import Manifest


def test_file_roundtrip(tmp_path):
    with Manifest(tmp_path / "m.sqlite3") as m:
        assert m.get_file("/x/a.txt") is None
        m.put_file("/x/a.txt", "fid1", 100, "abc", 1.5)
        rec = m.get_file("/x/a.txt")
        assert rec is not None
        assert (rec.file_id, rec.size, rec.md5, rec.mtime) == ("fid1", 100, "abc", 1.5)


def test_file_upsert(tmp_path):
    with Manifest(tmp_path / "m.sqlite3") as m:
        m.put_file("/x/a.txt", "fid1", 100, "abc", 1.0)
        m.put_file("/x/a.txt", "fid1", 200, "def", 2.0)
        rec = m.get_file("/x/a.txt")
        assert (rec.size, rec.md5, rec.mtime) == (200, "def", 2.0)


def test_folder_roundtrip_and_counts(tmp_path):
    with Manifest(tmp_path / "m.sqlite3") as m:
        assert m.get_folder("Backups") is None
        m.put_folder("Backups", "F1")
        m.put_folder("Backups/MacSync", "F2")
        assert m.get_folder("Backups") == "F1"
        m.put_file("/x/a.txt", "fid1", 1, "h", 0.0)
        files, folders = m.counts()
        assert files == 1
        assert folders == 2


def test_persistence_across_open(tmp_path):
    db = tmp_path / "m.sqlite3"
    with Manifest(db) as m:
        m.put_file("/x/a.txt", "fid1", 1, "h", 0.0)
    with Manifest(db) as m:
        assert m.get_file("/x/a.txt") is not None
