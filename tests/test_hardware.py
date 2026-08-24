"""Panel IO and the avrdude command builder."""

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
