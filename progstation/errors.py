"""Error taxonomy for the programming station (SRS section 16).

Every failure that can abort a programming cycle -- or that must be recorded in
the audit trail -- is represented by a :class:`StationError` subclass carrying a
stable ``code``.  The code is what gets written into ``ProductionLog.Error`` so
that reports and Excel exports can group failures without parsing free text.
"""

from __future__ import annotations


class StationError(Exception):
    """Base class for all recoverable station faults."""

    code = "E_UNKNOWN"
    #: Short operator-facing text shown on the touchscreen.
    operator_hint = "Unexpected error. Call maintenance."

    def __init__(self, message: str = "", *, detail: str = ""):
        super().__init__(message or self.operator_hint)
        self.detail = detail

    def as_log_entry(self) -> str:
        text = str(self)
        return f"{self.code}: {text}" if text else self.code


class NoDeviceError(StationError):
    code = "E_NO_DEVICE"
    operator_hint = "No sensor detected. Seat the board in the fixture."


class FixtureOpenError(StationError):
    code = "E_FIXTURE_OPEN"
    operator_hint = "Fixture is open. Close the fixture and press Start."


class SignatureMismatchError(StationError):
    code = "E_SIGNATURE_MISMATCH"
    operator_hint = "Wrong MCU detected. Check the project selection."

    def __init__(self, expected: str = "", found: str = "", **kw):
        super().__init__(
            f"expected signature {expected or '?'}, found {found or '?'}", **kw
        )
        self.expected = expected
        self.found = found


class FlashProgramError(StationError):
    code = "E_FLASH_PROGRAM"
    operator_hint = "Firmware programming failed."


class FlashVerifyError(StationError):
    code = "E_FLASH_VERIFY"
    operator_hint = "Firmware verification failed."


class EepromProgramError(StationError):
    code = "E_EEPROM_PROGRAM"
    operator_hint = "EEPROM write failed."


class EepromVerifyError(StationError):
    code = "E_EEPROM_VERIFY"
    operator_hint = "EEPROM verification failed."


class FirmwareMissingError(StationError):
    code = "E_FIRMWARE_MISSING"
    operator_hint = "Firmware file not found. Contact administrator."


class ConfigurationError(StationError):
    code = "E_CONFIG"
    operator_hint = "Project configuration is invalid. Contact administrator."


class SerialRangeExhaustedError(StationError):
    code = "E_SERIAL_RANGE"
    operator_hint = "Serial number range exhausted. Contact administrator."


class DatabaseError(StationError):
    code = "E_DATABASE"
    operator_hint = "Database error. Production halted."


class BackupError(StationError):
    code = "E_BACKUP"
    operator_hint = "Network backup failed."


class AuthError(StationError):
    code = "E_AUTH"
    operator_hint = "Login failed."


class PermissionDeniedError(StationError):
    code = "E_PERMISSION"
    operator_hint = "Administrator rights required."


class ProgrammerTimeoutError(StationError):
    code = "E_TIMEOUT"
    operator_hint = "Programmer timed out."
