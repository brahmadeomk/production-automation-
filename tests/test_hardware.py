"""Panel IO and the avrdude command builder."""

import os

import pytest

from progstation.config import AvrdudeConfig, GpioConfig
from progstation.core.avrdude import (
    AvrdudeBackend,
    AvrdudeResult,
    signature_matches,
    signature_name,
)
from progstation.hw.gpio import HIGH, LOW, SimulatedBackend, create_backend
from progstation.hw.station_io import StationIO


@pytest.fixture
def io():
    station = StationIO(GpioConfig(backend="simulated"))
    yield station
    station.close()


def test_auto_backend_falls_back_to_simulation():
    assert isinstance(create_backend("auto"), SimulatedBackend)


def test_unknown_backend_rejected():
    with pytest.raises(ValueError, match="unknown GPIO backend"):
        create_backend("nonsense")


def test_pins_are_claimed_on_the_srs_allocation(io):
    assert set(io.backend.outputs) == {17, 27, 22}
    assert set(io.backend.inputs) == {23, 24}


def test_pass_and_fail_indicators(io):
    io.pass_result()
    assert io.backend.outputs[17] == HIGH and io.backend.outputs[27] == LOW
    io.fail_result()
    assert io.backend.outputs[17] == LOW and io.backend.outputs[27] == HIGH


def test_buzzer_sounds_three_times_on_fail(io):
    io.fail_result()
    assert sum(1 for _, pin, value in io.backend.history if pin == 22 and value == HIGH) == 3


def test_fixture_detect_is_active_low(io):
    io.simulate_fixture(True)
    assert io.fixture_present()
    io.simulate_fixture(False)
    assert not io.fixture_present()


def test_active_high_fixture_wiring():
    station = StationIO(GpioConfig(backend="simulated", fixture_active_low=False))
    station.backend.set_input(24, HIGH)
    assert station.fixture_present()
    station.close()


def test_all_off_clears_every_output(io):
    io.pass_result()
    io.all_off()
    assert all(value == LOW for value in io.backend.outputs.values())


def test_start_button_edge_is_debounced(io):
    presses = []
    io.on_start_button(lambda: presses.append(1))
    io.simulate_start_press()
    import time

    time.sleep(0.2)
    assert presses == [1]


# ------------------------------------------------------------------ avrdude
def test_command_matches_the_linuxspi_isp_setup():
    args = AvrdudeBackend(AvrdudeConfig()).base_args("atmega328p")
    assert args == [
        "avrdude", "-p", "atmega328p", "-c", "linuxspi", "-P", "/dev/spidev0.0",
        "-b", "200000",
    ]


def test_config_fragment_and_extra_args():
    config = AvrdudeConfig(config_file="+/etc/progstation/avrdude.conf", extra_args=["-q"])
    args = AvrdudeBackend(config).base_args("atmega328p")
    assert args[:3] == ["avrdude", "-C", "+/etc/progstation/avrdude.conf"]
    assert args[-1] == "-q"


def test_eeprom_write_disables_auto_erase():
    """Erasing here would wipe the flash that was just verified."""
    captured = {}

    def runner(command, timeout):
        captured["command"] = command
        return AvrdudeResult(True, 0)

    backend = AvrdudeBackend(AvrdudeConfig(), runner=runner)
    backend.program_eeprom("atmega328p", "/tmp/e.hex")
    assert "-D" in captured["command"]
    assert "eeprom:w:/tmp/e.hex:i" in captured["command"]


def test_signature_parsed_from_output():
    def runner(command, timeout):
        return AvrdudeResult(True, 0, stdout="0x1e\n0x95\n0x0f\n")

    _, signature = AvrdudeBackend(AvrdudeConfig(), runner=runner).read_signature("atmega328p")
    assert signature == "0x1e950f"


def test_floating_bus_reads_as_no_device():
    for text in ("0x00\n0x00\n0x00\n", "0xff\n0xff\n0xff\n"):
        def runner(command, timeout, text=text):
            return AvrdudeResult(True, 0, stdout=text)

        _, signature = AvrdudeBackend(AvrdudeConfig(), runner=runner).read_signature("x")
        assert signature == ""


def test_signature_comparison_is_tolerant():
    assert signature_matches("0x1E950F", "0x1e950f")
    assert signature_matches("1E 95 0F", "0x1e950f")
    assert not signature_matches("0x1e9403", "0x1e950f")
    assert not signature_matches("", "0x1e950f")


def test_known_signature_names():
    assert signature_name("0x1e950f") == "atmega328p"
    assert signature_name("0x1e9514") == "atmega328"
    assert signature_name("0xdeadbe") == ""


def test_fuse_values_are_normalised():
    captured = {}

    def runner(command, timeout):
        captured["command"] = command
        return AvrdudeResult(True, 0)

    backend = AvrdudeBackend(AvrdudeConfig(), runner=runner)
    backend.write_fuses("atmega328p", low="FF", high="0xD9")
    assert "lfuse:w:0xff:m" in captured["command"]
    assert "hfuse:w:0xd9:m" in captured["command"]


def test_no_fuses_means_no_call():
    assert AvrdudeBackend(AvrdudeConfig()).write_fuses("atmega328p") is None


def test_missing_binary_is_reported_not_raised():
    backend = AvrdudeBackend(AvrdudeConfig(binary="/nonexistent/avrdude"))
    result = backend.program_flash("atmega328p", "/tmp/x.hex")
    assert not result.ok and result.returncode == 127


# -------------------------------------------------- backend selection safety
def test_simulate_flag_also_simulates_gpio(tmp_path):
    """--simulate must need no hardware at all, panel included."""
    from progstation.app import StationApp
    from progstation.config import StationConfig

    cfg = StationConfig(data_dir=str(tmp_path), log_dir=str(tmp_path / "log"))
    cfg.database.path = str(tmp_path / "p.db")
    cfg.reports.export_dir = str(tmp_path / "exports")
    cfg.gpio.backend = "lgpio"          # would need real hardware
    app = StationApp(cfg, simulate=True)
    try:
        assert app.io is not None and app.io.simulated
        assert app.health()["gpio_backend"] == "simulated"
    finally:
        app.close()


def test_backend_is_probed_not_just_constructed(monkeypatch):
    """A backend that imports fine but cannot claim a pin must be rejected.

    This is the gpiozero failure mode: the constructor succeeds and the pin
    factory only collapses later, when a device is created.
    """
    from progstation.hw import gpio as gpio_module

    class ImportsButCannotClaim(gpio_module.GpioBackend):
        name = "broken"

        def setup_input(self, pin, pull_up=True):
            raise FileNotFoundError("/sys/class/gpio/gpio23/value")

        def setup_output(self, pin, initial=gpio_module.LOW):
            raise FileNotFoundError("/sys/class/gpio/gpio23/value")

        def read(self, pin):
            return gpio_module.LOW

        def write(self, pin, value):
            pass

    monkeypatch.setattr(gpio_module, "LgpioBackend", lambda chip=0: ImportsButCannotClaim())
    monkeypatch.setattr(gpio_module, "GpiozeroBackend", ImportsButCannotClaim)
    monkeypatch.setattr(gpio_module, "gpio_hardware_present", lambda: False)

    # No hardware present, so falling back to simulation is the right answer --
    # and crucially the FileNotFoundError does not escape.
    assert isinstance(gpio_module.create_backend("auto"), gpio_module.SimulatedBackend)


def test_real_hardware_that_cannot_be_claimed_raises(monkeypatch):
    """Never simulate silently on a station that has GPIO.

    A simulated station reports PASS while programming nothing, so refusing to
    start is the safer failure.
    """
    from progstation.hw import gpio as gpio_module

    def unavailable(*args, **kwargs):
        raise PermissionError("Permission denied")

    monkeypatch.setattr(gpio_module, "LgpioBackend", unavailable)
    monkeypatch.setattr(gpio_module, "GpiozeroBackend", unavailable)
    monkeypatch.setattr(gpio_module, "gpio_hardware_present", lambda: True)

    with pytest.raises(gpio_module.GpioUnavailableError) as excinfo:
        gpio_module.create_backend("auto")
    message = str(excinfo.value)
    assert "gpio' group" in message          # names the usual cause
    assert "--simulate" in message           # and the deliberate escape hatch


def test_lgpio_work_dir_is_writable():
    """lgpio writes a FIFO into LG_WD; it must never depend on the cwd."""
    from progstation.hw.gpio import _lgpio_work_dir

    work_dir = _lgpio_work_dir()
    assert os.path.isdir(work_dir) and os.access(work_dir, os.W_OK)


def test_status_names_the_backend_it_would_use(tmp_path):
    """`status` does not open the panel, but must not report a bare "none".

    Reporting "none" reads like a fault when it only means this command did not
    touch the GPIO.
    """
    from progstation.app import StationApp
    from progstation.config import StationConfig

    cfg = StationConfig(data_dir=str(tmp_path), log_dir=str(tmp_path / "log"))
    cfg.database.path = str(tmp_path / "p.db")
    cfg.reports.export_dir = str(tmp_path / "exports")

    app = StationApp(cfg, with_io=False)          # what `status` builds
    try:
        health = app.health()
        assert health["gpio_backend"] != "none"
        assert "not opened" in health["gpio_backend"]
        assert health["gpio_backend"].split()[0] in ("lgpio", "gpiozero", "simulated", "unavailable")
    finally:
        app.close()


def test_available_backend_claims_no_pins(monkeypatch):
    """Probing for status must never claim a pin -- GPIO25 is RESET."""
    from progstation.hw import gpio as gpio_module

    def must_not_be_called(*args, **kwargs):
        raise AssertionError("status probing must not construct a GPIO backend")

    monkeypatch.setattr(gpio_module, "LgpioBackend", must_not_be_called)
    monkeypatch.setattr(gpio_module, "GpiozeroBackend", must_not_be_called)
    assert gpio_module.available_backend() in ("lgpio", "gpiozero", "simulated", "unavailable")
