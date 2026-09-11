from datetime import date

import pytest

from progstation.core.eeprom import (
    EepromField,
    EepromMap,
    crc16_ccitt,
    crc8,
    legacy_map,
    recommended_map,
)
from progstation.errors import ConfigurationError

CONTEXT = {
    "serial": 1234,
    "serial_text": "001234",
    "hw_revision": "RevC",
    "product_variant": "TS-100",
    "firmware_version": "1.4.0",
    "mfg_date": date(2026, 8, 24),
    "operator": "op1",
    "station_id": "STN-1",
    "project_name": "TempSensor",
}


def test_recommended_block_layout():
    block = recommended_map().build(CONTEXT)
    assert len(block) == 32
    assert block[0:4] == (1234).to_bytes(4, "big")
    assert block[4:8] == b"RevC"
    assert block[8:16] == b"TS-100\x00\x00"
    assert block[16:20].hex() == "20260824"          # packed BCD date
    assert block[28:30] == b"\xFF\xFF"               # reserved, untouched
    assert block[30:32] == crc16_ccitt(block[0:30]).to_bytes(2, "big")


def test_json_round_trip_is_byte_identical():
    original = recommended_map()
    restored = EepromMap.from_json(original.to_json())
    assert restored.build(CONTEXT) == original.build(CONTEXT)


def test_legacy_map_spans_only_the_two_fields():
    eeprom = legacy_map(sn_address=0, mfg_address=8)
    assert eeprom.base_address == 0 and eeprom.size == 12
    block = eeprom.build(CONTEXT)
    assert block[0:4] == (1234).to_bytes(4, "big")
    assert block[4:8] == b"\xFF\xFF\xFF\xFF"         # gap keeps the fill byte
    assert block[8:12].hex() == "20260824"


def test_legacy_map_with_offset_base():
    eeprom = legacy_map(sn_address=16, mfg_address=24)
    assert eeprom.base_address == 16 and eeprom.size == 12


def test_legacy_map_rejects_overlap():
    with pytest.raises(ConfigurationError, match="overlap"):
        legacy_map(sn_address=0, mfg_address=2)


def test_overlapping_fields_rejected():
    eeprom = EepromMap(
        size=8,
        fields=[
            EepromField("a", 0, 4, "uint32", source="serial"),
            EepromField("b", 2, 4, "uint32", source="serial"),
        ],
    )
    with pytest.raises(ConfigurationError, match="overlap"):
        eeprom.validate()


def test_field_past_end_rejected():
    eeprom = EepromMap(size=4, fields=[EepromField("a", 2, 4, "uint32", source="serial")])
    with pytest.raises(ConfigurationError, match="past the"):
        eeprom.validate()


def test_value_too_large_for_field():
    eeprom = EepromMap(size=2, fields=[EepromField("a", 0, 2, "uint16", source="serial")])
    with pytest.raises(ConfigurationError, match="does not fit"):
        eeprom.build({**CONTEXT, "serial": 70000})


def test_ascii_overflow_rejected_unless_truncating():
    field = EepromField("v", 0, 4, "ascii", source="product_variant")
    eeprom = EepromMap(size=4, fields=[field])
    with pytest.raises(ConfigurationError, match="needs 6 bytes"):
        eeprom.build(CONTEXT)
    field.truncate = True
    assert eeprom.build(CONTEXT) == b"TS-1"


def test_little_endian_serial():
    eeprom = EepromMap(
        size=4, fields=[EepromField("s", 0, 4, "uint32", source="serial", endian="little")]
    )
    assert eeprom.build(CONTEXT) == (1234).to_bytes(4, "little")


def test_crc_field_inside_its_own_range_rejected():
    eeprom = EepromMap(
        size=4,
        fields=[EepromField("c", 0, 2, "crc16", source="crc", crc_from=0, crc_to=4)],
    )
    with pytest.raises(ConfigurationError, match="inside its own range"):
        eeprom.validate()


def test_crc8_and_checksum8():
    eeprom = EepromMap(
        size=4,
        fields=[
            EepromField("s", 0, 2, "uint16", source="serial"),
            EepromField("c", 2, 1, "crc8", source="crc", crc_from=0, crc_to=2),
            EepromField("k", 3, 1, "checksum8", source="crc", crc_from=0, crc_to=2),
        ],
    )
    block = eeprom.build(CONTEXT)
    assert block[2] == crc8(block[0:2])
    assert block[3] == sum(block[0:2]) & 0xFF


def test_date_ymd_and_const_and_bytes():
    eeprom = EepromMap(
        size=8,
        fields=[
            EepromField("d", 0, 3, "date_ymd", source="mfg_date"),
            EepromField("c", 3, 2, "bytes", source="const", value="DEAD"),
            EepromField("n", 5, 1, "uint8", source="const", value=7),
            EepromField("b", 6, 2, "bcd", source="const", value="1234"),
        ],
    )
    block = eeprom.build(CONTEXT)
    assert block[0:3] == bytes([26, 8, 24])
    assert block[3:5] == b"\xDE\xAD"
    assert block[5] == 7
    assert block[6:8] == b"\x12\x34"


def test_describe_is_human_readable():
    fields = {f["name"]: f for f in recommended_map().describe(CONTEXT)}
    assert fields["serial_number"]["text"] == "1234"
    assert fields["mfg_date"]["text"] == "2026-08-24"
    assert fields["hw_revision"]["offset"] == 4
