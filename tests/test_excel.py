import pytest

openpyxl = pytest.importorskip("openpyxl")

from progstation.db.database import utc_now
from progstation.reports.engine import ReportEngine
from progstation.reports.excel import default_filename, export_period, export_records


@pytest.fixture
def logged(db, project):
    project_id = int(project["ProjectId"])
    for index in range(5):
        db.record_cycle(
            {
                "ProjectId": project_id,
                "ProjectName": "TestSensor",
                "SerialNo": f"{index:06d}",
                "Result": "PASS" if index % 2 == 0 else "FAIL",
                "Operator": "op1",
                "FirmwareVersion": "1.0.0",
                "DurationMs": 3200,
                "ErrorCode": "" if index % 2 == 0 else "E_FLASH_VERIFY",
            }
        )
    return db


def test_export_records_workbook(logged, tmp_path):
    path = export_records(
        logged.search_production(), tmp_path / "log.xlsx", filters={"result": "All"}
    )
    book = openpyxl.load_workbook(path)
    assert book.sheetnames == ["Production Log", "Export Info"]
    sheet = book["Production Log"]
    assert sheet.max_row == 6                      # header + 5 records
    assert sheet.cell(1, 1).value == "Timestamp"
    assert sheet.auto_filter.ref is not None


def test_export_summary_has_a_sheet_per_breakdown(logged, tmp_path):
    summary = ReportEngine(logged).daily()
    path = export_period(logged, summary, tmp_path / "report.xlsx", company_name="ACME")
    book = openpyxl.load_workbook(path)
    assert {"Summary", "By Operator", "By Project", "Failures", "By Day", "Records"} <= set(
        book.sheetnames
    )
    values = {row[0]: row[1] for row in book["Summary"].iter_rows(values_only=True) if row[0]}
    assert values["Total programmed"] == 5
    assert values["PASS"] == 3 and values["FAIL"] == 2
    assert values["Company"] == "ACME"


def test_export_of_an_empty_selection(db, tmp_path):
    path = export_records([], tmp_path / "empty.xlsx")
    assert openpyxl.load_workbook(path)["Production Log"].max_row == 1


def test_default_filename_shape():
    name = default_filename("report", "daily")
    assert name.startswith("report-daily-") and name.endswith(".xlsx")
