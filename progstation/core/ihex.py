"""Minimal Intel HEX reader/writer.

AVRDUDE cannot write a raw binary at an arbitrary EEPROM offset, so the station
emits the manufacturing block as an Intel HEX file whose records carry the real
target addresses.  That also makes ``-U eeprom:v:<file>:i`` verify exactly the
bytes we intended to write and nothing else.
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Tuple

_REC_DATA = 0x00
_REC_EOF = 0x01
_REC_EXT_SEG = 0x02
_REC_EXT_LIN = 0x04


class HexFormatError(ValueError):
    """Raised when a file is not valid Intel HEX."""


def _checksum(payload: bytes) -> int:
    return (-sum(payload)) & 0xFF


def write_hex(chunks: Iterable[Tuple[int, bytes]], *, bytes_per_line: int = 16) -> str:
    """Render ``(address, data)`` chunks as Intel HEX text.

    Addresses above 64 KiB emit extended-linear-address records, which keeps the
    output valid for the (rare) AVR with more than 64 KiB of addressable EEPROM
    space and harmless for everything else.
    """
    lines: List[str] = []
    upper = None
    for base, data in chunks:
        for offset in range(0, len(data), bytes_per_line):
            piece = data[offset : offset + bytes_per_line]
            address = base + offset
            high = (address >> 16) & 0xFFFF
            if high != upper:
                ext = bytes([2, 0, 0, _REC_EXT_LIN, (high >> 8) & 0xFF, high & 0xFF])
                lines.append(":" + (ext + bytes([_checksum(ext)])).hex().upper())
                upper = high
            low = address & 0xFFFF
            record = bytes(
                [len(piece), (low >> 8) & 0xFF, low & 0xFF, _REC_DATA]
            ) + piece
            lines.append(":" + (record + bytes([_checksum(record)])).hex().upper())
    eof = bytes([0, 0, 0, _REC_EOF])
    lines.append(":" + (eof + bytes([_checksum(eof)])).hex().upper())
    return "\n".join(lines) + "\n"


def parse_hex(text: str) -> Dict[int, int]:
    """Parse Intel HEX into an ``{address: byte}`` map."""
    memory: Dict[int, int] = {}
    base = 0
    seen_eof = False
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        if not line.startswith(":"):
            raise HexFormatError(f"line {lineno}: record does not start with ':'")
        try:
            payload = bytes.fromhex(line[1:])
        except ValueError as exc:
            raise HexFormatError(f"line {lineno}: {exc}") from exc
        if len(payload) < 5:
            raise HexFormatError(f"line {lineno}: record too short")
        count, addr_hi, addr_lo, rectype = payload[0], payload[1], payload[2], payload[3]
        data = payload[4:-1]
        if len(data) != count:
            raise HexFormatError(f"line {lineno}: byte count mismatch")
        if _checksum(payload[:-1]) != payload[-1]:
            raise HexFormatError(f"line {lineno}: bad checksum")
        address = (addr_hi << 8) | addr_lo
        if rectype == _REC_DATA:
            for i, value in enumerate(data):
                memory[base + address + i] = value
        elif rectype == _REC_EOF:
            seen_eof = True
            break
        elif rectype == _REC_EXT_LIN:
            base = ((data[0] << 8) | data[1]) << 16
        elif rectype == _REC_EXT_SEG:
            base = ((data[0] << 8) | data[1]) << 4
        else:
            raise HexFormatError(f"line {lineno}: unsupported record type {rectype:#04x}")
    if not seen_eof:
        raise HexFormatError("missing end-of-file record")
    return memory


def hex_span(text: str) -> Tuple[int, int]:
    """Return ``(lowest_address, byte_count)`` of a HEX file's data records."""
    memory = parse_hex(text)
    if not memory:
        return (0, 0)
    return (min(memory), len(memory))
