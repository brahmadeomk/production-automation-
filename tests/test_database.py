import pytest

from progstation.db.database import Database, utc_now
from progstation.errors import DatabaseError


def test_schema_creates_all_tables(db):
    names = {
        row["name"]
        for row in db.query("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    assert {"Users", "Projects", "SerialCounters", "ProductionLog", "AuditLog",
            "BackupLog"} <= names


def test_project_insert_creates_its_counter(db, firmware):
    project_id = db.upsert_project(
        {"ProjectName": "P", "MCU": "atmega328p", "HexPath": str(firmware), "SerialStart": 42}
    )
    assert db.next_serial_value(project_id) == 42


def test_counter_and_log_commit_together(db, project):
    project_id = int(project["ProjectId"])
    db.record_cycle(
        {"ProjectId": project_id, "ProjectName": "TestSensor", "SerialNo": "000100",
         "SerialValue": 100, "Result": "PASS", "Operator": "op1"},
        advance_serial_to=101,
    )
    assert db.next_serial_value(project_id) == 101
    assert db.search_production()[0]["SerialNo"] == "000100"


def test_result_check_constraint(db, project):
    with pytest.raises(DatabaseError):
        db.record_cycle({"ProjectId": int(project["ProjectId"]), "Result": "MAYBE"})


def test_search_filters_combine(db, project):
    project_id = int(project["ProjectId"])
    for serial, result, operator, fw in (
        ("000001", "PASS", "op1", "1.0"),
        ("000002", "FAIL", "op2", "1.0"),
        ("000003", "PASS", "op1", "2.0"),
    ):
        db.record_cycle(
            {"ProjectId": project_id, "SerialNo": serial, "Result": result,
             "Operator": operator, "FirmwareVersion": fw}
        )
    assert len(db.search_production(result="PASS")) == 2
    assert len(db.search_production(operator="op1", firmware_version="2.0")) == 1
    assert len(db.search_production(serial="0002")) == 1


def test_search_is_newest_first(db, project):
    project_id = int(project["ProjectId"])
    db.record_cycle({"ProjectId": project_id, "Timestamp": "2026-01-01T00:00:00.000Z",
                     "SerialNo": "old", "Result": "PASS"})
    db.record_cycle({"ProjectId": project_id, "Timestamp": "2026-06-01T00:00:00.000Z",
                     "SerialNo": "new", "Result": "PASS"})
    assert db.search_production()[0]["SerialNo"] == "new"


def test_serial_history_is_chronological(db, project):
    project_id = int(project["ProjectId"])
    for stamp in ("2026-03-01T00:00:00.000Z", "2026-01-01T00:00:00.000Z"):
        db.record_cycle({"ProjectId": project_id, "Timestamp": stamp,
                         "SerialNo": "000100", "Result": "PASS"})
    history = db.serial_history("000100")
    assert [row["Timestamp"] for row in history] == sorted(
        row["Timestamp"] for row in history
    )


def test_vacuum_into_makes_a_readable_snapshot(tmp_path, project, db):
    snapshot = tmp_path / "snap.db"
    db.vacuum_into(snapshot)
    with Database(str(snapshot)) as copy:
        assert copy.get_project_by_name("TestSensor") is not None


def test_project_upsert_updates_in_place(db, project):
    project_id = int(project["ProjectId"])
    db.upsert_project({"FirmwareVersion": "9.9.9"}, project_id=project_id)
    assert db.get_project(project_id)["FirmwareVersion"] == "9.9.9"
    assert len(db.list_projects(include_inactive=True)) == 1


def test_inactive_projects_hidden_by_default(db, project):
    db.set_project_active(int(project["ProjectId"]), False)
    assert db.list_projects() == []
    assert len(db.list_projects(include_inactive=True)) == 1
