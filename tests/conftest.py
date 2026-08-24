from __future__ import annotations

import pytest

from progstation.config import StationConfig
from progstation.core.avrdude import SimulatedAvrdude
from progstation.core.eeprom import recommended_map
from progstation.core.programmer import ProgrammingEngine
from progstation.db.database import Database

SAMPLE_HEX = ":100000000C9434000C943E000C943E000C943E0082\n:00000001FF\n"


@pytest.fixture
def firmware(tmp_path):
    path = tmp_path / "firmware.hex"
    path.write_text(SAMPLE_HEX, encoding="utf-8")
    return path


@pytest.fixture
def db():
    database = Database(":memory:")
    yield database
    database.close()


@pytest.fixture
def config(tmp_path):
    cfg = StationConfig(
        station_id="TEST-STN",
        data_dir=str(tmp_path),
        log_dir=str(tmp_path / "log"),
        require_fixture_detect=False,
        result_dwell_s=0,
    )
    cfg.database.path = str(tmp_path / "test.db")
    cfg.reports.export_dir = str(tmp_path / "exports")
    return cfg


@pytest.fixture
def backend():
    return SimulatedAvrdude()


@pytest.fixture
def project(db, firmware):
    project_id = db.upsert_project(
        {
            "ProjectName": "TestSensor",
            "MCU": "atmega328p",
            "HexPath": str(firmware),
            "EepromMap": recommended_map().to_dict(),
            "HwRevision": "RevA",
            "ProductVariant": "TS-1",
            "FirmwareVersion": "1.0.0",
            "SerialStart": 100,
            "SerialEnd": 199,
            "SerialDigits": 6,
            "Signature": "0x1e950f",
        }
    )
    return db.get_project(project_id)


@pytest.fixture
def engine(db, config, backend):
    return ProgrammingEngine(db, config, backend, io=None)
