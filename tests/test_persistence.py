from pathlib import Path

from alfen_driver.persistence import PersistenceManager


def test_load_state_nonexistent_file(tmp_path: Path) -> None:
    cfg = tmp_path / "state.json"
    pm = PersistenceManager(str(cfg))
    # No file -> empty state
    assert pm.get("x") is None


def test_save_and_load_roundtrip(tmp_path: Path) -> None:
    cfg = tmp_path / "state.json"
    pm = PersistenceManager(str(cfg))
    pm.set("a", 1)
    pm.set("b", {"c": 2})
    assert pm.save_state() is True

    # New instance loads existing file
    pm2 = PersistenceManager(str(cfg))
    assert pm2.get("a") == 1
    assert pm2.get_section("b") == {"c": 2}


def test_get_set_section_and_clear(tmp_path: Path) -> None:
    cfg = tmp_path / "state.json"
    pm = PersistenceManager(str(cfg))
    pm.set_section("cfg", {"z": 9})
    assert pm.get_section("cfg") == {"z": 9}
    pm.clear()
    assert pm.get("cfg") is None


def test_properties_mode_start_stop_set_current(tmp_path: Path) -> None:
    cfg = tmp_path / "state.json"
    pm = PersistenceManager(str(cfg))
    # Defaults
    assert pm.mode == 0
    assert pm.start_stop == 1
    assert pm.set_current == 6.0

    # Set and read back with correct types
    pm.mode = 2
    pm.start_stop = 0
    pm.set_current = 12.5
    assert pm.mode == 2
    assert pm.start_stop == 0
    assert pm.set_current == 12.5


def test_save_failure_is_handled(monkeypatch) -> None:
    # Force mkdir to fail
    pm = PersistenceManager("/nonexistent-dir/does/not/exist/state.json")

    def fail_mkdir(self: Path, parents: bool = True, exist_ok: bool = True) -> None:
        raise OSError("nope")

    monkeypatch.setattr(Path, "mkdir", fail_mkdir, raising=True)
    assert pm.save_state() is False
