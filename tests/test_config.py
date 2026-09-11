import pytest
import yaml

from progstation.config import StationConfig, from_dict, load_config


def test_defaults_match_the_srs_gpio_allocation():
    gpio = StationConfig().gpio
    assert (gpio.mosi, gpio.miso, gpio.sclk, gpio.reset) == (10, 9, 11, 25)
    assert (gpio.green_led, gpio.red_led, gpio.buzzer) == (17, 27, 22)
    assert (gpio.start_button, gpio.fixture_detect) == (23, 24)


def test_partial_override_keeps_other_defaults():
    cfg = from_dict({"station_id": "LINE2", "gpio": {"green_led": 5}})
    assert cfg.station_id == "LINE2"
    assert cfg.gpio.green_led == 5
    assert cfg.gpio.red_led == 27


def test_nested_station_key_accepted():
    cfg = from_dict({"station": {"station_id": "LINE3"}, "gpio": {"buzzer": 6}})
    assert cfg.station_id == "LINE3" and cfg.gpio.buzzer == 6


def test_unknown_option_is_rejected():
    with pytest.raises(ValueError, match="unknown"):
        from_dict({"gpio": {"purple_led": 1}})
    with pytest.raises(ValueError, match="unknown station"):
        from_dict({"nonsense": 1})


def test_save_and_reload_round_trip(tmp_path):
    original = StationConfig(station_id="ROUND-TRIP")
    original.avrdude.baudrate = 125000
    path = tmp_path / "station.yaml"
    original.save(path)
    reloaded = load_config(path)
    assert reloaded.station_id == "ROUND-TRIP"
    assert reloaded.avrdude.baudrate == 125000
    assert reloaded.source_path == str(path)


def test_missing_explicit_config_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "nope.yaml")


def test_ensure_directories(tmp_path):
    cfg = StationConfig(data_dir=str(tmp_path / "d"), log_dir=str(tmp_path / "l"))
    cfg.database.path = str(tmp_path / "db" / "p.db")
    cfg.reports.export_dir = str(tmp_path / "e")
    cfg.ensure_directories()
    for path in ("d", "l", "db", "e"):
        assert (tmp_path / path).is_dir()
