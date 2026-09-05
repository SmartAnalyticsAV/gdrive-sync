import pytest

from gdrive_sync.config import ConfigError, load_config

VALID = """
[drive]
dest_folder = "Backups/MacSync"

[[sources]]
path = "/tmp/a"

[[sources]]
path = "/tmp/b"
name = "bee"

[ignore]
patterns = [".DS_Store", "*.tmp"]
"""


def _write(tmp_path, text):
    p = tmp_path / "config.toml"
    p.write_text(text)
    return p


def test_load_valid(tmp_path):
    cfg = load_config(_write(tmp_path, VALID))
    assert cfg.dest_folder == "Backups/MacSync"
    assert [s.name for s in cfg.sources] == ["a", "bee"]
    assert cfg.ignore_patterns == [".DS_Store", "*.tmp"]


def test_dest_folder_normalized(tmp_path):
    cfg = load_config(_write(tmp_path, '[drive]\ndest_folder = "/Foo/Bar/"\n[[sources]]\npath="/x"\n'))
    assert cfg.dest_folder == "Foo/Bar"


def test_missing_file(tmp_path):
    with pytest.raises(ConfigError, match="не найден"):
        load_config(tmp_path / "nope.toml")


def test_missing_dest_folder(tmp_path):
    with pytest.raises(ConfigError, match="dest_folder"):
        load_config(_write(tmp_path, '[[sources]]\npath = "/tmp/a"\n'))


def test_no_sources(tmp_path):
    with pytest.raises(ConfigError, match="sources"):
        load_config(_write(tmp_path, '[drive]\ndest_folder = "X"\n'))


def test_duplicate_names(tmp_path):
    text = '[drive]\ndest_folder="X"\n[[sources]]\npath="/a/data"\n[[sources]]\npath="/b/data"\n'
    with pytest.raises(ConfigError, match="Дублирующееся"):
        load_config(_write(tmp_path, text))


def test_whitelist_parsed(tmp_path):
    text = (
        '[drive]\ndest_folder="X"\n[[sources]]\npath="/a"\n'
        '[ignore]\npatterns=["*.log"]\nwhitelist=["/a/keep", "~/notes"]\n'
    )
    cfg = load_config(_write(tmp_path, text))
    assert len(cfg.whitelist) == 2
    assert str(cfg.whitelist[0]) == "/a/keep"
    assert not str(cfg.whitelist[1]).startswith("~")  # expanduser применён


def test_whitelist_defaults_empty(tmp_path):
    cfg = load_config(_write(tmp_path, VALID))
    assert cfg.whitelist == []


def test_whitelist_bad_type(tmp_path):
    text = '[drive]\ndest_folder="X"\n[[sources]]\npath="/a"\n[ignore]\nwhitelist="/a"\n'
    with pytest.raises(ConfigError, match="whitelist"):
        load_config(_write(tmp_path, text))


def test_machine_explicit(tmp_path):
    text = '[drive]\ndest_folder="X"\nmachine="Foo"\n[[sources]]\npath="/a"\n'
    assert load_config(_write(tmp_path, text)).machine == "Foo"


def test_machine_defaults_to_hostname(tmp_path):
    import socket

    cfg = load_config(_write(tmp_path, VALID))
    assert cfg.machine == (socket.gethostname().split(".")[0] or "unknown")
    assert cfg.machine  # непустое


def test_machine_with_slash(tmp_path):
    text = '[drive]\ndest_folder="X"\nmachine="a/b"\n[[sources]]\npath="/a"\n'
    with pytest.raises(ConfigError, match="machine"):
        load_config(_write(tmp_path, text))


def test_name_with_slash(tmp_path):
    text = '[drive]\ndest_folder="X"\n[[sources]]\npath="/a"\nname="a/b"\n'
    with pytest.raises(ConfigError, match="'/'"):
        load_config(_write(tmp_path, text))
