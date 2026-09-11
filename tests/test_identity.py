"""Station identity page (device id, MAC, SSID).

Built against a synthetic /sys/class/net so the wireless paths can be tested
on a machine that has no wireless -- including the container this suite runs
in, which has no network tools at all.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from progstation.hw.identity import UNKNOWN, Interface, gather, interfaces


def make_sysfs(tmp_path: Path, spec: dict) -> Path:
    """spec: {name: {"address":..., "operstate":..., "wireless": bool}}"""
    # A unique root per call: some tests build two stations from one tmp_path.
    root = tmp_path / f"net{len(list(tmp_path.glob('net*')))}"
    root.mkdir()
    for name, values in spec.items():
        base = root / name
        base.mkdir()
        (base / "address").write_text(values.get("address", "") + "\n")
        (base / "operstate").write_text(values.get("operstate", "down") + "\n")
        if values.get("wireless"):
            (base / "wireless").mkdir()
    return root


def no_address(name):
    return UNKNOWN


def no_commands(command, **kwargs):
    return ""


def test_reads_mac_and_state_from_sysfs(tmp_path):
    sysfs = make_sysfs(tmp_path, {
        "eth0": {"address": "b8:27:eb:11:22:33", "operstate": "up"},
    })
    found = interfaces(sysfs, run=no_commands, address=no_address)
    assert [i.name for i in found] == ["eth0"]
    assert found[0].mac == "b8:27:eb:11:22:33"
    assert found[0].connected


def test_loopback_and_virtual_interfaces_are_left_out(tmp_path):
    """They tell an engineer nothing about how the station is connected."""
    sysfs = make_sysfs(tmp_path, {
        "lo": {"address": "00:00:00:00:00:00", "operstate": "unknown"},
        "ifb0": {"address": "52:a4:59:bf:9e:7b", "operstate": "down"},
        "docker0": {"address": "02:42:0a:00:00:01", "operstate": "down"},
        "eth0": {"address": "b8:27:eb:11:22:33", "operstate": "up"},
    })
    found = interfaces(sysfs, run=no_commands, address=no_address)
    assert [i.name for i in found] == ["eth0"]


def test_wired_interface_is_listed_before_wireless(tmp_path):
    sysfs = make_sysfs(tmp_path, {
        "wlan0": {"address": "b8:27:eb:aa:bb:cc", "operstate": "up", "wireless": True},
        "eth0": {"address": "b8:27:eb:11:22:33", "operstate": "up"},
    })
    found = interfaces(sysfs, run=no_commands, address=no_address)
    assert [i.name for i in found] == ["eth0", "wlan0"]


@pytest.mark.parametrize("tool, output, expected", [
    ("iwgetid", "PlantFloor-2G", "PlantFloor-2G"),
    ("nmcli", "GENERAL.CONNECTION:PlantFloor-2G", "PlantFloor-2G"),
    ("iw", "Connected to b8:27:eb\n\tSSID: PlantFloor-2G\n\tfreq: 2437", "PlantFloor-2G"),
])
def test_ssid_read_from_whichever_tool_is_installed(tmp_path, tool, output, expected):
    """Pi OS moved to NetworkManager; older or cut-down images have neither."""
    sysfs = make_sysfs(tmp_path, {
        "wlan0": {"address": "b8:27:eb:aa:bb:cc", "operstate": "up", "wireless": True},
    })

    def run(command, **kwargs):
        return output if command[0] == tool else ""

    found = interfaces(sysfs, run=run, address=no_address)
    assert found[0].ssid == expected


def test_ssid_unavailable_when_no_tool_answers(tmp_path):
    """A station with no wireless tools must still show its MAC."""
    sysfs = make_sysfs(tmp_path, {
        "wlan0": {"address": "b8:27:eb:aa:bb:cc", "operstate": "up", "wireless": True},
    })
    found = interfaces(sysfs, run=no_commands, address=no_address)
    assert found[0].ssid == UNKNOWN
    assert found[0].mac == "b8:27:eb:aa:bb:cc"


def test_nmcli_placeholder_is_not_reported_as_a_network(tmp_path):
    """nmcli prints '--' for an interface that is up but unassociated."""
    sysfs = make_sysfs(tmp_path, {
        "wlan0": {"address": "b8:27:eb:aa:bb:cc", "operstate": "up", "wireless": True},
    })

    def run(command, **kwargs):
        return "GENERAL.CONNECTION:--" if command[0] == "nmcli" else ""

    assert interfaces(sysfs, run=run, address=no_address)[0].ssid == UNKNOWN


def test_ssid_not_looked_up_for_a_down_interface(tmp_path):
    sysfs = make_sysfs(tmp_path, {
        "wlan0": {"address": "b8:27:eb:aa:bb:cc", "operstate": "down", "wireless": True},
    })
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return "Somewhere"

    found = interfaces(sysfs, run=run, address=no_address)
    assert not calls, "queried the wireless tools for an interface that is down"
    assert found[0].ssid == ""


# ------------------------------------------------------------------ summary
def _identity(tmp_path, spec, run=no_commands):
    return gather(
        "STATION-01",
        sysfs=make_sysfs(tmp_path, spec),
        proc=tmp_path / "missing",
        device_tree=tmp_path / "missing",
        run=run,
        address=no_address,
    )


def test_summary_reports_the_connected_network(tmp_path):
    identity = _identity(
        tmp_path,
        {"wlan0": {"address": "b8:27:eb:aa:bb:cc", "operstate": "up", "wireless": True}},
        run=lambda command, **kw: "PlantFloor-2G" if command[0] == "iwgetid" else "",
    )
    assert identity.ssid == "PlantFloor-2G"
    assert identity.primary_mac == "b8:27:eb:aa:bb:cc"
    assert identity.station_id == "STATION-01"


def test_summary_says_why_there_is_no_ssid(tmp_path):
    """'unavailable' next to SSID on a wired station reads like a fault."""
    wired = _identity(tmp_path, {"eth0": {"address": "b8:27:eb:11:22:33", "operstate": "up"}})
    assert wired.ssid == "no wireless interface"

    off = _identity(
        tmp_path,
        {"wlan0": {"address": "b8:27:eb:aa:bb:cc", "operstate": "down", "wireless": True}},
    )
    assert off.ssid == "not connected"


def test_primary_mac_prefers_a_connected_interface(tmp_path):
    identity = _identity(tmp_path, {
        "eth0": {"address": "b8:27:eb:11:22:33", "operstate": "down"},
        "wlan0": {"address": "b8:27:eb:aa:bb:cc", "operstate": "up", "wireless": True},
    })
    assert identity.primary_mac == "b8:27:eb:aa:bb:cc"


def test_missing_files_do_not_raise(tmp_path):
    """A cut-down or unusual image must not take the page down."""
    identity = gather(
        "STATION-01",
        sysfs=tmp_path / "no-such-dir",
        proc=tmp_path / "no-such-dir",
        device_tree=tmp_path / "no-such-dir",
        run=no_commands,
        address=no_address,
    )
    assert identity.interfaces == []
    assert identity.primary_mac == UNKNOWN
    assert identity.board_serial == UNKNOWN
    assert identity.station_id == "STATION-01"


def test_board_serial_is_read_from_cpuinfo(tmp_path):
    proc = tmp_path / "proc"
    proc.mkdir()
    (proc / "cpuinfo").write_text(
        "processor\t: 0\nHardware\t: BCM2711\nSerial\t\t: 100000004f1c2b3a\n"
    )
    identity = gather(
        "STATION-01",
        sysfs=make_sysfs(tmp_path, {}),
        proc=proc,
        device_tree=tmp_path / "missing",
        run=no_commands,
        address=no_address,
    )
    assert identity.board_serial == "100000004f1c2b3a"


def test_describe_names_the_network(tmp_path):
    wifi = Interface("wlan0", "b8:27:eb:aa:bb:cc", "up", "192.168.1.50", True, "PlantFloor-2G")
    assert "PlantFloor-2G" in wifi.describe()
    assert "Wi-Fi" in wifi.describe()
    wired = Interface("eth0", "b8:27:eb:11:22:33", "up", "192.168.1.9")
    assert "wired" in wired.describe()
    assert "on " not in wired.describe()


# ------------------------------------------------------- the admin-only page
def test_identity_command_prints_the_three_headline_facts(tmp_path, capsys, monkeypatch):
    """Reachable over SSH as well as on the panel."""
    from progstation.app import StationApp
    from progstation.cli import main
    from progstation.config import StationConfig
    from progstation.hw import identity as identity_module

    config = StationConfig(
        station_id="STATION-07", data_dir=str(tmp_path), log_dir=str(tmp_path / "log")
    )
    config.database.path = str(tmp_path / "s.db")
    config.reports.export_dir = str(tmp_path / "e")
    config.save(tmp_path / "station.yaml")

    monkeypatch.setattr(
        identity_module, "_ipv4", lambda name: "192.168.1.50", raising=False
    )
    monkeypatch.setattr(
        identity_module, "_run",
        lambda command, **kw: "PlantFloor-2G" if command[0] == "iwgetid" else "",
        raising=False,
    )
    monkeypatch.setattr(
        identity_module, "interfaces",
        lambda *a, **kw: [
            Interface("wlan0", "b8:27:eb:aa:bb:cc", "up", "192.168.1.50",
                      True, "PlantFloor-2G")
        ],
    )
    assert main(["--config", str(tmp_path / "station.yaml"), "identity"]) == 0
    out = capsys.readouterr().out
    assert "STATION-07" in out
    assert "b8:27:eb:aa:bb:cc" in out
    assert "PlantFloor-2G" in out


def test_identity_page_is_admin_only(qt_app_identity, tmp_path):
    """The page carries the station's network details; SRS section 15 keeps
    that behind an administrator."""
    from progstation.errors import PermissionDeniedError
    from progstation.gui.admin_screen import AdminScreen
    from progstation.security.auth import Session

    app = _station_app(tmp_path)
    try:
        operator = Session(2, "op1", "Operator One", "operator")
        with pytest.raises(PermissionDeniedError):
            AdminScreen(app, operator)
    finally:
        app.db.close()


def test_identity_page_shows_id_mac_and_ssid(qt_app_identity, tmp_path, monkeypatch):
    from progstation.gui import admin_screen as screen_module
    from progstation.hw.identity import StationIdentity
    from progstation.security.auth import Session

    app = _station_app(tmp_path)
    try:
        import progstation.hw.identity as identity_module

        monkeypatch.setattr(
            identity_module, "gather",
            lambda station_id, **kw: StationIdentity(
                station_id="STATION-07",
                hostname="progstation-07",
                model="Raspberry Pi 4 Model B Rev 1.4",
                board_serial="100000004f1c2b3a",
                interfaces=[
                    Interface("eth0", "b8:27:eb:11:22:33", "down", "unavailable"),
                    Interface("wlan0", "b8:27:eb:aa:bb:cc", "up", "192.168.1.50",
                              True, "PlantFloor-2G"),
                ],
            ),
        )
        page = screen_module.AdminScreen(app, Session(1, "admin", "A", "admin"))
        shown = {k: v.text() for k, v in page.identity_labels.items()}
        assert shown["station_id"] == "STATION-07"
        assert shown["primary_mac"] == "b8:27:eb:aa:bb:cc"
        assert shown["ssid"] == "PlantFloor-2G"
        assert shown["board_serial"] == "100000004f1c2b3a"
        # Both interfaces listed, with the SSID column blank on the wired row.
        assert page.interface_table.rowCount() == 2
    finally:
        app.db.close()


def _station_app(tmp_path):
    from progstation.app import StationApp
    from progstation.config import StationConfig

    config = StationConfig(
        station_id="STATION-07", data_dir=str(tmp_path), log_dir=str(tmp_path / "log")
    )
    config.database.path = str(tmp_path / "s.db")
    config.reports.export_dir = str(tmp_path / "e")
    app = StationApp(config, simulate=True, with_io=False)
    app.auth.ensure_default_admin()
    return app


@pytest.fixture(scope="module")
def qt_app_identity():
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PyQt5")
    from PyQt5 import QtWidgets

    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
