import pytest

from progstation.backup.smb import BackupManager
from progstation.config import BackupConfig


@pytest.fixture
def share(tmp_path):
    path = tmp_path / "share"
    path.mkdir()
    return path


def _manager(db, share, tmp_path, **overrides):
    options = dict(
        enabled=True, mount_point=str(share), manage_mount=False, keep_days=0
    )
    options.update(overrides)
    exports = tmp_path / "exports"
    exports.mkdir(exist_ok=True)
    (exports / "report.xlsx").write_bytes(b"x" * 64)
    return BackupManager(
        BackupConfig(**options), db, extra_dirs=[str(exports)], station_id="STN-1"
    )


def test_backup_copies_database_and_exports(db, project, share, tmp_path):
    result = _manager(db, share, tmp_path).run_backup()
    assert result.ok, result.detail
    copied = sorted(p.name for p in share.rglob("*") if p.is_file())
    assert "report.xlsx" in copied
    assert any(name.endswith(".db") for name in copied)


def test_snapshot_is_a_usable_database(db, project, share, tmp_path):
    from progstation.db.database import Database

    _manager(db, share, tmp_path).run_backup()
    snapshot = next(p for p in share.rglob("*.db"))
    with Database(str(snapshot)) as copy:
        assert copy.get_project_by_name("TestSensor") is not None


def test_success_is_recorded(db, project, share, tmp_path):
    _manager(db, share, tmp_path).run_backup()
    assert db.last_backup()["Result"] == "PASS"


def test_failure_is_recorded_not_raised(db, tmp_path):
    manager = BackupManager(
        BackupConfig(enabled=True, mount_point=str(tmp_path / "gone"), manage_mount=False),
        db,
        station_id="STN-1",
    )
    result = manager.run_backup()
    assert not result.ok
    assert db.last_backup()["Result"] == "FAIL"
    assert "not mounted" in db.last_backup()["Detail"]


def test_prune_removes_old_folders_only(db, project, share, tmp_path):
    manager = _manager(db, share, tmp_path, keep_days=7)
    root = share / "progstation" / "STN-1"
    root.mkdir(parents=True)
    (root / "2001-01-01_000000").mkdir()          # ancient, ours
    (root / "keep-me").mkdir()                    # not ours, must survive
    assert manager.prune() == 1
    assert not (root / "2001-01-01_000000").exists()
    assert (root / "keep-me").exists()


def test_status_strings(db, share, tmp_path):
    disabled = BackupManager(BackupConfig(enabled=False), db)
    assert "disabled" in disabled.status()
    manager = _manager(db, share, tmp_path)
    assert "never run" in manager.status()
    manager.run_backup()
    assert "PASS" in manager.status()


def test_backup_does_not_leave_root_owned_database_sidecars(db, project, share, tmp_path, monkeypatch):
    """A backup run as root must not lock the station out of its own database.

    In WAL mode root would create -wal/-shm owned by root; the station account
    could then no longer write the production log.
    """
    chowned = []
    monkeypatch.setattr("progstation.backup.smb.os.geteuid", lambda: 0)
    monkeypatch.setattr("progstation.backup.smb.os.chown",
                        lambda path, uid, gid: chowned.append(str(path)))

    manager = _manager(db, share, tmp_path)
    manager.db.path = str(tmp_path / "prod.db")
    (tmp_path / "prod.db").write_bytes(b"")
    result = manager.run_backup()

    assert result.ok, result.detail
    assert any(name.endswith("prod.db") for name in chowned)


def test_ownership_guard_is_a_no_op_for_a_normal_user(db, project, share, tmp_path, monkeypatch):
    """Nothing is chowned when the backup runs unprivileged, as the timer does."""
    monkeypatch.setattr("progstation.backup.smb.os.geteuid", lambda: 1000)
    monkeypatch.setattr("progstation.backup.smb.os.chown",
                        lambda *a: (_ for _ in ()).throw(AssertionError("must not chown")))
    assert _manager(db, share, tmp_path).run_backup().ok
