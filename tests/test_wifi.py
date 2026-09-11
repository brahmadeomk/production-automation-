"""Joining a Wi-Fi network from the panel.

Driven against recorded nmcli output, so the parsing and -- more importantly
-- the handling of the password are covered on a machine with no wireless.
"""
from __future__ import annotations

import json
import subprocess

import pytest

from progstation.hw import wifi
from progstation.hw.wifi import Network, connect, scan


class FakeRun:
    """Stands in for nmcli, recording exactly how it was called."""

    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout, self.stderr, self.returncode = stdout, stderr, returncode
        self.calls = []
        self.stdins = []

    def __call__(self, command, *, stdin="", timeout=45.0):
        self.calls.append(list(command))
        self.stdins.append(stdin)
        return subprocess.CompletedProcess(
            command, self.returncode, self.stdout, self.stderr
        )


SCAN_OUTPUT = "\n".join([
    "PlantFloor-2G:72:WPA2:*",
    "PlantFloor-5G:88:WPA2:",
    "Guest:45::",
    ":30:WPA2:",              # hidden network, no name to show
    "PlantFloor-2G:51:WPA2:", # the same SSID on a second access point
])


# ------------------------------------------------------------------- scanning
def test_scan_lists_networks_strongest_first():
    run = FakeRun(SCAN_OUTPUT)
    networks = scan(run=run)
    assert [n.ssid for n in networks] == ["PlantFloor-5G", "PlantFloor-2G", "Guest"]
    assert networks[0].signal == 88


def test_scan_keeps_the_strongest_of_a_repeated_ssid():
    """One network on three access points is one entry, not three."""
    networks = scan(run=FakeRun(SCAN_OUTPUT))
    matches = [n for n in networks if n.ssid == "PlantFloor-2G"]
    assert len(matches) == 1
    assert matches[0].signal == 72


def test_scan_marks_the_network_in_use():
    networks = scan(run=FakeRun(SCAN_OUTPUT))
    assert [n.ssid for n in networks if n.in_use] == ["PlantFloor-2G"]


def test_scan_reports_an_open_network():
    """Worth saying out loud before someone puts a station on it."""
    guest = [n for n in scan(run=FakeRun(SCAN_OUTPUT)) if n.ssid == "Guest"][0]
    assert guest.open
    secured = [n for n in scan(run=FakeRun(SCAN_OUTPUT)) if n.ssid == "PlantFloor-5G"][0]
    assert not secured.open


def test_scan_handles_a_colon_in_the_ssid():
    """nmcli escapes them; splitting naively loses the name."""
    networks = scan(run=FakeRun("Bay\\:3:64:WPA2:"))
    assert networks[0].ssid == "Bay:3"


def test_scan_returns_nothing_when_nmcli_fails():
    assert scan(run=FakeRun("", "command not found", returncode=127)) == []


def test_scan_survives_nmcli_being_absent():
    def explode(command, **kwargs):
        raise FileNotFoundError("nmcli")

    assert scan(run=explode) == []


# ------------------------------------------------------------------ connecting
def test_password_is_never_passed_on_the_command_line():
    """Arguments are world-readable in /proc while the command runs."""
    run = FakeRun()
    connect("PlantFloor-2G", "s3cret-factory-key", run=run)
    flat = " ".join(" ".join(call) for call in run.calls)
    assert "s3cret-factory-key" not in flat, "the Wi-Fi key leaked into argv"
    assert "s3cret-factory-key\n" in run.stdins, "the key never reached nmcli"
    assert "--ask" in run.calls[0]


def test_successful_connect_reports_the_network():
    result = connect("PlantFloor-2G", "key", run=FakeRun())
    assert result.ok
    assert "PlantFloor-2G" in result.detail


def test_password_is_not_echoed_in_a_failure_message():
    """A failure notice is shown on the panel and may be read out or logged."""
    run = FakeRun("", "Error: Connection activation failed.", returncode=4)
    result = connect("PlantFloor-2G", "s3cret-factory-key", run=run)
    assert not result.ok
    assert "s3cret-factory-key" not in result.detail


@pytest.mark.parametrize("stderr, expected", [
    ("Error: Secrets were required, but not provided.", "wrong password"),
    ("Error: Not authorized to control networking.", "not permitted"),
    ("Error: No network with SSID 'PlantFloor-2G' found.", "no longer in range"),
])
def test_failures_are_explained_in_terms_an_operator_can_act_on(stderr, expected):
    result = connect("PlantFloor-2G", "key", run=FakeRun("", stderr, returncode=4))
    assert not result.ok
    assert expected in result.detail


def test_missing_polkit_rule_is_named_as_the_cause():
    """The likeliest failure on a fresh station, and unguessable otherwise."""
    result = connect(
        "PlantFloor-2G", "key",
        run=FakeRun("", "Error: Not authorized to control networking.", returncode=4),
    )
    assert "polkit" in result.detail


def test_an_open_network_is_joined_without_a_prompt():
    run = FakeRun()
    assert connect("Guest", "", run=run).ok
    assert "--ask" not in run.calls[0], "asked for a secret on an open network"
    assert run.stdins == [""]


def test_connect_refuses_an_empty_ssid():
    run = FakeRun()
    result = connect("", "key", run=run)
    assert not result.ok
    assert not run.calls, "ran nmcli with no network selected"


def test_a_hung_nmcli_does_not_hang_the_panel():
    def hang(command, **kwargs):
        raise subprocess.TimeoutExpired(command, 45)

    result = connect("PlantFloor-2G", "key", run=hang)
    assert not result.ok
    assert "timed out" in result.detail


def test_signal_bars_track_strength():
    assert Network("A", 90).bars == "▂▄▆█"
    assert Network("A", 10).bars == "▂"


# ------------------------------------------------------------------ the page
@pytest.fixture(scope="module")
def qt_app_wifi():
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PyQt5")
    from PyQt5 import QtWidgets

    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


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


def _admin_screen(tmp_path):
    from progstation.gui.admin_screen import AdminScreen
    from progstation.security.auth import Session

    app = _station_app(tmp_path)
    return app, AdminScreen(app, Session(1, "admin", "A", "admin"))


def test_page_lists_the_networks_it_scanned(qt_app_wifi, tmp_path, monkeypatch):
    monkeypatch.setattr(wifi, "available", lambda *a, **k: True)
    monkeypatch.setattr(wifi, "scan", lambda **kw: [
        Network("PlantFloor-5G", 88, "WPA2"),
        Network("Guest", 45, ""),
    ])
    app, page = _admin_screen(tmp_path)
    try:
        page.scan_wifi()
        assert page.wifi_table.rowCount() == 2
        assert page.wifi_join_button.isEnabled()
    finally:
        app.db.close()


def test_page_says_so_when_the_station_cannot_change_networks(
    qt_app_wifi, tmp_path, monkeypatch
):
    """A wired-only image has no nmcli; that is a fact, not an error."""
    monkeypatch.setattr(wifi, "available", lambda *a, **k: False)
    app, page = _admin_screen(tmp_path)
    try:
        page.scan_wifi()
        assert not page.wifi_join_button.isEnabled()
        assert "nmcli" in page.wifi_message.text()
    finally:
        app.db.close()


def test_joining_audits_the_network_but_never_the_password(
    qt_app_wifi, tmp_path, monkeypatch
):
    """The audit log is exported and read by people who should not learn the
    factory Wi-Fi key from it."""
    from progstation.gui.admin_screen import WifiPasswordDialog

    app = _station_app(tmp_path)
    try:
        network = Network("PlantFloor-2G", 72, "WPA2")
        dialog = WifiPasswordDialog(None, app, network, "admin")
        dialog.password.setText("s3cret-factory-key")
        dialog._finished(True, "connected to PlantFloor-2G")

        entries = [dict(row) for row in app.db.list_audit(50)]
        connects = [e for e in entries if e["Action"] == "network.wifi_connect"]
        assert connects, "the network change was not audited"
        assert connects[0]["Target"] == "PlantFloor-2G"
        assert "s3cret-factory-key" not in json.dumps(entries), (
            "the Wi-Fi password reached the audit log"
        )
        assert dialog.joined
    finally:
        app.db.close()


def test_a_failed_join_is_audited_with_the_reason(qt_app_wifi, tmp_path):
    from progstation.gui.admin_screen import WifiPasswordDialog

    app = _station_app(tmp_path)
    try:
        dialog = WifiPasswordDialog(None, app, Network("PlantFloor-2G", 72, "WPA2"), "admin")
        dialog.password.setText("wrong-key")
        dialog._finished(False, "wrong password")

        entries = [dict(row) for row in app.db.list_audit(50)]
        failures = [e for e in entries if e["Action"] == "network.wifi_failed"]
        assert failures and failures[0]["Target"] == "PlantFloor-2G"
        assert "wrong-key" not in json.dumps(entries)
        assert not dialog.joined
        assert "wrong password" in dialog.message.text()
    finally:
        app.db.close()


def test_dialog_will_not_connect_with_an_empty_password(qt_app_wifi, tmp_path):
    from progstation.gui.admin_screen import WifiPasswordDialog

    app = _station_app(tmp_path)
    try:
        dialog = WifiPasswordDialog(None, app, Network("PlantFloor-2G", 72, "WPA2"), "admin")
        dialog._attempt()
        assert "password" in dialog.message.text().lower()
        assert dialog._worker is None, "started a join with no password"
    finally:
        app.db.close()


def test_open_network_warns_before_joining(qt_app_wifi, tmp_path):
    """A station's backups go over this link."""
    from PyQt5 import QtWidgets

    from progstation.gui.admin_screen import WifiPasswordDialog

    app = _station_app(tmp_path)
    try:
        dialog = WifiPasswordDialog(None, app, Network("Guest", 45, ""), "admin")
        text = " ".join(
            label.text() for label in dialog.findChildren(QtWidgets.QLabel)
        )
        assert "unsecured" in text.lower()
    finally:
        app.db.close()
