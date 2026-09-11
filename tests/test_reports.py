from datetime import date, timedelta

import pytest

from progstation.db.database import utc_now
from progstation.reports.engine import ReportEngine, period_bounds


def _log(db, project_id, *, result="PASS", operator="op1", when=None, fw="1.0.0", error=""):
    db.record_cycle(
        {
            "Timestamp": when or utc_now(),
            "ProjectId": project_id,
            "ProjectName": "TestSensor",
            "SerialNo": "000001" if result == "PASS" else "",
            "Result": result,
            "Operator": operator,
            "FirmwareVersion": fw,
            "DurationMs": 4000,
            "ErrorCode": error,
        }
    )


@pytest.fixture
def populated(db, project):
    project_id = int(project["ProjectId"])
    today = date.today().isoformat()
    for _ in range(8):
        _log(db, project_id, when=f"{today}T08:00:00.000Z")
    for _ in range(2):
        _log(db, project_id, result="FAIL", error="E_FLASH_VERIFY", when=f"{today}T08:30:00.000Z")
    _log(db, project_id, operator="op2", when=f"{today}T09:00:00.000Z")
    return db, project_id


def test_period_bounds_daily_weekly_monthly():
    reference = date(2026, 8, 24)             # a Monday
    assert period_bounds("daily", reference)[0].startswith("2026-08-24")
    assert period_bounds("weekly", reference)[0].startswith("2026-08-24")
    start, end, _ = period_bounds("monthly", reference)
    assert start.startswith("2026-08-01") and end.startswith("2026-09-01")


def test_period_bounds_rejects_unknown_period():
    with pytest.raises(ValueError, match="unknown period"):
        period_bounds("hourly")


def test_daily_summary_counts_and_yield(populated):
    db, _ = populated
    summary = ReportEngine(db).daily()
    assert (summary.total, summary.passed, summary.failed) == (11, 9, 2)
    assert summary.yield_pct == pytest.approx(81.82, abs=0.01)
    assert summary.avg_cycle_ms == 4000


def test_operator_statistics(populated):
    db, _ = populated
    by_operator = {r["operator"]: r for r in ReportEngine(db).daily().by_operator}
    assert by_operator["op1"]["total"] == 10
    assert by_operator["op1"]["failed"] == 2
    assert by_operator["op2"]["yield_pct"] == 100.0


def test_failure_breakdown(populated):
    db, _ = populated
    errors = {r["error_code"]: r["count"] for r in ReportEngine(db).daily().by_error}
    assert errors == {"E_FLASH_VERIFY": 2}


def test_project_filter(populated, db, firmware):
    other = db.upsert_project(
        {"ProjectName": "Other", "MCU": "atmega8", "HexPath": str(firmware)}
    )
    _log(db, other, operator="op3")
    engine = ReportEngine(db)
    assert engine.daily().total == 12
    assert engine.daily(project_id=other).total == 1


def test_monthly_rolls_up_days(populated):
    db, _ = populated
    summary = ReportEngine(db).monthly()
    assert summary.total == 11
    assert len(summary.by_day) == 1
    assert summary.by_day[0]["passed"] == 9


def test_empty_period_is_safe(db):
    summary = ReportEngine(db).daily(date(2001, 1, 1))
    assert summary.total == 0 and summary.yield_pct == 0.0


def test_text_rendering(populated):
    db, _ = populated
    text = ReportEngine(db).as_text(ReportEngine(db).daily())
    assert "PASS / FAIL  : 9 / 2" in text
    assert "E_FLASH_VERIFY" in text
