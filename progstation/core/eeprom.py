"""Configurable EEPROM manufacturing-block builder (SRS section 8).

A project's layout is a JSON document describing a contiguous block and the
fields inside it::

    {
      "base_address": 0,
      "size": 32,
      "fill": 255,
      "fields": [
        {"name": "serial_number", "offset": 0,  "length": 4, "type": "uint32",
         "source": "serial"},
        {"name": "hw_revision",   "offset": 4,  "length": 2, "type": "ascii",
         "source": "hw_revision"},
        {"name": "variant",       "offset": 6,  "length": 6, "type": "ascii",
         "source": "product_variant"},
        {"name": "mfg_date",      "offset": 12, "length": 4, "type": "date_bcd",
         "source": "mfg_date"},
        {"name": "checksum",      "offset": 30, "length": 2, "type": "crc16",
         "source": "crc", "crc_from": 0, "crc_to": 30}
      ]
    }

Legacy products that only ever stored a serial number at ``SNAddress`` and a
manufacturing date at ``MFGAddress`` do not need a map at all --
:func:`legacy_map` synthesises one from those two columns, which is what SRS
Appendix A describes.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Sequence

from ..errors import ConfigurationError

#: Field types the map engine understands.
FIELD_TYPES = (
    "uint8", "uint16", "uint32", "ascii", "bytes", "bcd", "date_bcd",
    "date_ymd", "crc8", "crc16", "checksum8",
)

#: Named values the station can feed into a field.
SOURCES = (
    "serial", "serial_text", "hw_revision", "product_variant", "firmware_version",
    "mfg_date", "operator", "station_id", "project_name", "const", "crc",
)

_CRC_SOURCES = {"crc8", "crc16", "checksum8"}


def crc8(data: bytes, *, poly: int = 0x07, init: int = 0x00) -> int:
    """CRC-8/ATM (poly 0x07), the variant most AVR bootloaders ship with."""
    crc = init
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ poly) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


def crc16_ccitt(data: bytes, *, poly: int = 0x1021, init: int = 0xFFFF) -> int:
    """CRC-16/CCITT-FALSE."""
    crc = init
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ poly) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


@dataclass
class EepromField:
    name: str
    offset: int
    length: int
    type: str
    source: str = "const"
    value: Any = None                 # literal for source == "const"
    endian: str = "big"
    pad: str = "\x00"                 # ascii padding character
    truncate: bool = False            # allow silently cutting long ascii values
    crc_from: Optional[int] = None
    crc_to: Optional[int] = None

    def validate(self, block_size: int) -> None:
        if not self.name:
            raise ConfigurationError("EEPROM field is missing a name")
        where = f"field '{self.name}'"
        if self.type not in FIELD_TYPES:
            raise ConfigurationError(f"{where}: unknown type '{self.type}'")
        if self.source not in SOURCES:
            raise ConfigurationError(f"{where}: unknown source '{self.source}'")
        if self.offset < 0 or self.length <= 0:
            raise ConfigurationError(f"{where}: offset/length must be positive")
        if self.offset + self.length > block_size:
            raise ConfigurationError(
                f"{where}: extends past the {block_size}-byte block"
            )
        if self.endian not in ("big", "little"):
            raise ConfigurationError(f"{where}: endian must be 'big' or 'little'")
        fixed = {"uint8": 1, "uint16": 2, "uint32": 4, "crc8": 1, "checksum8": 1, "crc16": 2}
        if self.type in fixed and self.length != fixed[self.type]:
            raise ConfigurationError(
                f"{where}: type '{self.type}' requires length {fixed[self.type]}"
            )
        if self.type in _CRC_SOURCES:
            if self.crc_from is None or self.crc_to is None:
                raise ConfigurationError(f"{where}: crc_from/crc_to are required")
            if not 0 <= self.crc_from < self.crc_to <= block_size:
                raise ConfigurationError(f"{where}: crc range is outside the block")
            if self.crc_from <= self.offset < self.crc_to:
                raise ConfigurationError(f"{where}: crc field sits inside its own range")


@dataclass
class EepromMap:
    base_address: int = 0
    size: int = 32
    fill: int = 0xFF
    fields: List[EepromField] = field(default_factory=list)

    # ------------------------------------------------------------ (de)serial
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "EepromMap":
        if not isinstance(data, dict):
            raise ConfigurationError("EEPROM map must be a JSON object")
        try:
            fields = [EepromField(**f) for f in data.get("fields", [])]
        except TypeError as exc:
            raise ConfigurationError(f"invalid EEPROM field definition: {exc}") from exc
        obj = cls(
            base_address=int(data.get("base_address", 0)),
            size=int(data.get("size", 32)),
            fill=int(data.get("fill", 0xFF)),
            fields=fields,
        )
        obj.validate()
        return obj

    @classmethod
    def from_json(cls, text: str) -> "EepromMap":
        try:
            return cls.from_dict(json.loads(text))
        except json.JSONDecodeError as exc:
            raise ConfigurationError(f"EEPROM map is not valid JSON: {exc}") from exc

    def to_dict(self) -> Dict[str, Any]:
        return {
            "base_address": self.base_address,
            "size": self.size,
            "fill": self.fill,
            "fields": [
                {k: v for k, v in vars(f).items() if v is not None} for f in self.fields
            ],
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    # ---------------------------------------------------------------- checks
    def validate(self) -> None:
        if self.size <= 0:
            raise ConfigurationError("EEPROM block size must be positive")
        if self.base_address < 0:
            raise ConfigurationError("EEPROM base address must not be negative")
        if not 0 <= self.fill <= 0xFF:
            raise ConfigurationError("EEPROM fill byte must be 0..255")
        seen: List[EepromField] = []
        for f in self.fields:
            f.validate(self.size)
            for other in seen:
                if f.offset < other.offset + other.length and other.offset < f.offset + f.length:
                    raise ConfigurationError(
                        f"fields '{f.name}' and '{other.name}' overlap"
                    )
            seen.append(f)

    # ----------------------------------------------------------------- build
    def build(self, context: Dict[str, Any]) -> bytes:
        """Render the manufacturing block for one unit.

        ``context`` supplies the runtime values: ``serial`` (int),
        ``serial_text`` (str), ``hw_revision``, ``product_variant``,
        ``firmware_version``, ``mfg_date`` (``date``/``datetime``/ISO string),
        ``operator``, ``station_id`` and ``project_name``.
        """
        self.validate()
        block = bytearray([self.fill]) * self.size
        # CRC fields must come last: they hash bytes the other fields wrote.
        plain = [f for f in self.fields if f.type not in _CRC_SOURCES]
        crcs = [f for f in self.fields if f.type in _CRC_SOURCES]
        for f in plain:
            block[f.offset : f.offset + f.length] = self._encode(f, context)
        for f in crcs:
            window = bytes(block[f.crc_from : f.crc_to])  # type: ignore[index]
            if f.type == "crc8":
                value = crc8(window)
            elif f.type == "checksum8":
                value = sum(window) & 0xFF
            else:
                value = crc16_ccitt(window)
            block[f.offset : f.offset + f.length] = value.to_bytes(f.length, f.endian)
        return bytes(block)

    def describe(self, context: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Per-field breakdown for the admin preview screen."""
        block = self.build(context)
        out = []
        for f in sorted(self.fields, key=lambda x: x.offset):
            raw = block[f.offset : f.offset + f.length]
            out.append(
                {
                    "name": f.name,
                    "offset": self.base_address + f.offset,
                    "length": f.length,
                    "type": f.type,
                    "source": f.source,
                    "hex": raw.hex().upper(),
                    "text": _readable(f, raw),
                }
            )
        return out

    # -------------------------------------------------------------- encoding
    def _encode(self, f: EepromField, context: Dict[str, Any]) -> bytes:
        value = context.get(f.source) if f.source != "const" else f.value
        where = f"field '{f.name}'"

        if f.type in ("uint8", "uint16", "uint32"):
            if value is None:
                raise ConfigurationError(f"{where}: no value for source '{f.source}'")
            try:
                number = int(value)
            except (TypeError, ValueError) as exc:
                raise ConfigurationError(f"{where}: '{value}' is not an integer") from exc
            limit = 1 << (8 * f.length)
            if not 0 <= number < limit:
                raise ConfigurationError(
                    f"{where}: value {number} does not fit in {f.length} byte(s)"
                )
            return number.to_bytes(f.length, f.endian)

        if f.type == "ascii":
            text = "" if value is None else str(value)
            encoded = text.encode("ascii", errors="replace")
            if len(encoded) > f.length:
                if not f.truncate:
                    raise ConfigurationError(
                        f"{where}: '{text}' needs {len(encoded)} bytes, field is {f.length}"
                    )
                encoded = encoded[: f.length]
            return encoded.ljust(f.length, (f.pad or "\x00").encode("ascii")[:1])

        if f.type == "bytes":
            if isinstance(value, (bytes, bytearray)):
                raw = bytes(value)
            else:
                text = re.sub(r"[^0-9a-fA-F]", "", str(value or ""))
                if len(text) % 2:
                    raise ConfigurationError(f"{where}: hex value has an odd digit count")
                raw = bytes.fromhex(text)
            if len(raw) > f.length:
                raise ConfigurationError(f"{where}: value is longer than {f.length} bytes")
            return raw.ljust(f.length, b"\x00")

        if f.type == "bcd":
            digits = re.sub(r"\D", "", str(value if value is not None else ""))
            if len(digits) > f.length * 2:
                raise ConfigurationError(f"{where}: too many digits for {f.length} bytes")
            digits = digits.rjust(f.length * 2, "0")
            return bytes(
                int(digits[i], 10) << 4 | int(digits[i + 1], 10)
                for i in range(0, len(digits), 2)
            )

        if f.type in ("date_bcd", "date_ymd"):
            when = _coerce_date(value, where)
            if f.type == "date_bcd":
                # YYYYMMDD packed BCD, 4 bytes.
                if f.length != 4:
                    raise ConfigurationError(f"{where}: date_bcd requires length 4")
                digits = f"{when.year:04d}{when.month:02d}{when.day:02d}"
                return bytes(
                    int(digits[i], 10) << 4 | int(digits[i + 1], 10)
                    for i in range(0, 8, 2)
                )
            # date_ymd: 3 bytes, year offset from 2000.
            if f.length != 3:
                raise ConfigurationError(f"{where}: date_ymd requires length 3")
            year = when.year - 2000
            if not 0 <= year <= 255:
                raise ConfigurationError(f"{where}: year {when.year} is out of range")
            return bytes([year, when.month, when.day])

        raise ConfigurationError(f"{where}: unsupported type '{f.type}'")  # pragma: no cover


def _readable(f: EepromField, raw: bytes) -> str:
    if f.type == "ascii":
        return raw.decode("ascii", errors="replace").rstrip("\x00 ")
    if f.type in ("uint8", "uint16", "uint32"):
        return str(int.from_bytes(raw, f.endian))
    if f.type == "date_bcd":
        d = raw.hex()
        return f"{d[0:4]}-{d[4:6]}-{d[6:8]}"
    if f.type == "date_ymd":
        return f"{2000 + raw[0]:04d}-{raw[1]:02d}-{raw[2]:02d}"
    return raw.hex().upper()


def _coerce_date(value: Any, where: str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value is None:
        return date.today()
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
    except ValueError as exc:
        raise ConfigurationError(f"{where}: '{value}' is not a date") from exc


def legacy_map(
    sn_address: int,
    mfg_address: int,
    *,
    serial_bytes: int = 4,
    serial_as_ascii: bool = False,
    serial_digits: int = 6,
) -> EepromMap:
    """Build the two-field layout described in SRS Appendix A.

    The block spans from the lower of the two addresses to the end of the
    manufacturing date, so nothing outside the legacy fields is touched.
    """
    if sn_address < 0 or mfg_address < 0:
        raise ConfigurationError("EEPROM addresses must not be negative")
    sn_len = serial_digits if serial_as_ascii else serial_bytes
    mfg_len = 4
    if sn_address < mfg_address and sn_address + sn_len > mfg_address:
        raise ConfigurationError("serial number field overlaps the manufacturing date")
    if mfg_address < sn_address and mfg_address + mfg_len > sn_address:
        raise ConfigurationError("manufacturing date field overlaps the serial number")

    base = min(sn_address, mfg_address)
    size = max(sn_address + sn_len, mfg_address + mfg_len) - base
    return EepromMap(
        base_address=base,
        size=size,
        fill=0xFF,
        fields=[
            EepromField(
                name="serial_number",
                offset=sn_address - base,
                length=sn_len,
                type="ascii" if serial_as_ascii else "uint32",
                source="serial_text" if serial_as_ascii else "serial",
            ),
            EepromField(
                name="mfg_date",
                offset=mfg_address - base,
                length=mfg_len,
                type="date_bcd",
                source="mfg_date",
            ),
        ],
    )


def recommended_map() -> EepromMap:
    """The 0..31 manufacturing block recommended by SRS section 8."""
    return EepromMap(
        base_address=0,
        size=32,
        fill=0xFF,
        fields=[
            EepromField("serial_number", 0, 4, "uint32", source="serial"),
            EepromField("hw_revision", 4, 4, "ascii", source="hw_revision", truncate=True),
            EepromField("product_variant", 8, 8, "ascii", source="product_variant", truncate=True),
            EepromField("mfg_date", 16, 4, "date_bcd", source="mfg_date"),
            EepromField("firmware_version", 20, 8, "ascii", source="firmware_version", truncate=True),
            # bytes 28..29 reserved for future use, left at the fill value
            EepromField("crc", 30, 2, "crc16", source="crc", crc_from=0, crc_to=30),
        ],
    )


def map_for_project(project: Any) -> EepromMap:
    """Return the map a project row should use, legacy or configured."""
    raw = project["EepromMap"] if "EepromMap" in project.keys() else None
    if raw:
        return EepromMap.from_json(raw) if isinstance(raw, str) else EepromMap.from_dict(raw)
    return legacy_map(int(project["SNAddress"]), int(project["MFGAddress"]))
