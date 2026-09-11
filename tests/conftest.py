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


@pytest.fixture(autouse=True)
def _destroy_leftover_widgets():
    """Delete any dialog a GUI test left behind.

    The Qt application is module-scoped but the database fixtures are not, so
    a dialog that outlives its test goes on receiving events with a closed
    database behind it. The exception is raised inside a Qt event handler,
    where PyQt cannot propagate it, and the process aborts -- taking the whole
    run with it and pointing at whichever unlucky test came next.

    Closing is not enough: close() only hides. The widget has to be destroyed,
    and deleteLater() needs one turn of the loop to take effect.
    """
    yield
    try:
        from PyQt5 import QtWidgets, sip
    except ImportError:                     # a run without the GUI extras
        return
    app = QtWidgets.QApplication.instance()
    if app is None:
        return
    # Destroyed outright, rather than closed and queued for deletion. close()
    # would run MainWindow's own handler, which logs the operator out against
    # the database the test has already closed; deleteLater() would need a turn
    # of the event loop, and turning it lets the screens' refresh timers fire
    # against that same closed database. sip.delete takes the C++ object down
    # immediately, stopping its timers, without running either.
    #
    # The list is re-read after every delete: destroying one window destroys
    # the dialogs parented to it, so anything captured up front goes stale and
    # deleting through a dangling wrapper segfaults.
    for _ in range(500):
        alive = [w for w in app.topLevelWidgets() if not sip.isdeleted(w)]
        if not alive:
            break
        sip.delete(alive[0])
