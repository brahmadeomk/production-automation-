import pytest

from progstation.core.ihex import HexFormatError, hex_span, parse_hex, write_hex


def test_round_trip():
    data = bytes(range(64))
    memory = parse_hex(write_hex([(0x100, data)]))
    assert [memory[0x100 + i] for i in range(64)] == list(data)


def test_extended_linear_address_above_64k():
    memory = parse_hex(write_hex([(0x12000, b"\xAA\xBB")]))
    assert memory[0x12000] == 0xAA and memory[0x12001] == 0xBB


def test_span():
    assert hex_span(write_hex([(0x20, b"\x01\x02\x03")])) == (0x20, 3)


def test_rejects_bad_checksum():
    text = write_hex([(0, b"\x01\x02")])
    corrupted = text.replace(text.splitlines()[1][-2:], "00", 1)
    with pytest.raises(HexFormatError):
        parse_hex(corrupted)


def test_requires_eof_record():
    with pytest.raises(HexFormatError, match="end-of-file"):
        parse_hex(":020000040000FA\n")


def test_rejects_non_hex_line():
    with pytest.raises(HexFormatError):
        parse_hex("not a hex file\n")
