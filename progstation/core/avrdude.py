"""AVRDUDE backend for SPI ISP programming through the Pi's GPIO header.

The station drives ``avrdude -c linuxspi -P /dev/spidev0.0``, which bit-bangs
nothing: SCLK/MOSI/MISO come from the hardware SPI0 block (GPIO 11/10/9) and
only RESET (GPIO 25) is toggled as a plain output.  That matches the GPIO
allocation in SRS section 5.

Every public method returns a :class:`AvrdudeResult` rather than raising on a
non-zero exit, so the workflow layer decides which error code applies.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from ..config import AvrdudeConfig
from ..errors import ProgrammerTimeoutError

#: Factory device signatures for the AVRs this station supports.  Used to give
#: a readable name when avrdude reports a raw signature.
KNOWN_SIGNATURES: Dict[str, str] = {
    "0x1e9007": "attiny13",
    "0x1e9108": "attiny25",
    "0x1e910a": "attiny2313",
    "0x1e9206": "attiny45",
    "0x1e920d": "attiny4313",
    "0x1e930b": "attiny85",
    "0x1e9306": "atmega8515",
    "0x1e9307": "atmega8",
    "0x1e9308": "atmega8535",
    "0x1e930a": "atmega88",
    "0x1e930f": "atmega88p",
    "0x1e9403": "atmega16",
    "0x1e9406": "atmega168",
    "0x1e940b": "atmega168p",
    "0x1e9502": "atmega32",
    "0x1e950f": "atmega328p",
    "0x1e9514": "atmega328",
    "0x1e9516": "atmega328pb",
    "0x1e9587": "atmega32u4",
    "0x1e9602": "atmega64",
    "0x1e9608": "atmega640",
    "0x1e9609": "atmega644",
    "0x1e960a": "atmega644p",
    "0x1e9703": "atmega1280",
    "0x1e9704": "atmega1281",
    "0x1e9705": "atmega1284p",
    "0x1e9706": "atmega1284",
    "0x1e9801": "atmega2560",
    "0x1e9802": "atmega2561",
}

_SIGNATURE_RE = re.compile(r"[Dd]evice signature\s*=\s*(0x[0-9a-fA-F]+)")
_INVALID_SIGNATURE = ("0x000000", "0xffffff")


@dataclass
class AvrdudeResult:
    ok: bool
    returncode: int
    stdout: str = ""
    stderr: str = ""
    command: List[str] = field(default_factory=list)
    duration_ms: int = 0

    @property
    def output(self) -> str:
        return (self.stdout + "\n" + self.stderr).strip()

    def tail(self, lines: int = 6) -> str:
        """Last few output lines -- what goes into the operator-facing error."""
        text = [ln.strip() for ln in self.output.splitlines() if ln.strip()]
        return " | ".join(text[-lines:])


class AvrdudeBackend:
    """Thin, testable wrapper around the ``avrdude`` executable."""

    def __init__(self, config: Optional[AvrdudeConfig] = None, *, runner=None):
        self.config = config or AvrdudeConfig()
        # Injection point for tests and for the simulator.
        self._runner = runner or self._run_subprocess

    # ---------------------------------------------------------------- checks
    def is_available(self) -> bool:
        return shutil.which(self.config.binary) is not None or Path(self.config.binary).exists()

    def version(self) -> str:
        result = self._invoke(["-?"], timeout=10)
        match = re.search(r"[Vv]ersion\s+([0-9][0-9.\-a-z]*)", result.output)
        return match.group(1) if match else "unknown"

    # ------------------------------------------------------------ invocation
    def base_args(self, mcu: str) -> List[str]:
        cfg = self.config
        args = [cfg.binary]
        if cfg.config_file:
            # A leading '+' tells avrdude to *append* the file to its built-in
            # configuration, which is what a station-specific fragment (the
            # linuxspi reset line) needs.  Callers wanting a full replacement
            # pass the path with no prefix.
            args += ["-C", cfg.config_file]
        args += ["-p", mcu, "-c", cfg.programmer, "-P", cfg.port]
        if cfg.programmer == "linuxspi" and cfg.baudrate:
            args += ["-b", str(cfg.baudrate)]
        if cfg.bitclock:
            args += ["-B", str(cfg.bitclock)]
        if cfg.disable_auto_erase:
            args.append("-D")
        args += list(cfg.extra_args)
        return args

    def _invoke(self, extra: Sequence[str], *, mcu: str = "", timeout: Optional[int] = None) -> AvrdudeResult:
        command = (self.base_args(mcu) if mcu else [self.config.binary]) + list(extra)
        return self._runner(command, timeout or self.config.timeout_s)

    @staticmethod
    def _run_subprocess(command: Sequence[str], timeout: int) -> AvrdudeResult:
        import time

        started = time.monotonic()
        try:
            proc = subprocess.run(
                list(command),
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except FileNotFoundError as exc:
            return AvrdudeResult(
                False, 127, "", f"avrdude not found: {exc}", list(command), 0
            )
        except subprocess.TimeoutExpired as exc:
            raise ProgrammerTimeoutError(
                f"avrdude did not finish within {timeout}s", detail=str(exc)
            ) from exc
        elapsed = int((time.monotonic() - started) * 1000)
        return AvrdudeResult(
            proc.returncode == 0,
            proc.returncode,
            proc.stdout or "",
            proc.stderr or "",
            list(command),
            elapsed,
        )

    # ------------------------------------------------------------ operations
    def read_signature(self, mcu: str) -> tuple[AvrdudeResult, str]:
        """Read the device signature.  Returns ``(result, normalised_signature)``.

        An all-zero or all-ones signature means the ISP bus is floating -- no
        board in the fixture, or a dead target -- so it is reported as empty.
        """
        result = self._invoke(["-U", "signature:r:-:h"], mcu=mcu)
        signature = ""
        # avrdude 7 prints the hex bytes on stdout for `:-:h`; older versions
        # only announce it in the banner, so accept either.
        hex_bytes = re.findall(r"0x([0-9a-fA-F]{2})", result.stdout)
        if len(hex_bytes) >= 3:
            signature = "0x" + "".join(b.lower() for b in hex_bytes[:3])
        else:
            match = _SIGNATURE_RE.search(result.output)
            if match:
                signature = "0x" + match.group(1)[2:].lower().rjust(6, "0")
        if signature in _INVALID_SIGNATURE:
            signature = ""
        return result, signature

    def program_flash(self, mcu: str, hex_path: str | Path) -> AvrdudeResult:
        return self._invoke(["-U", f"flash:w:{Path(hex_path)}:i"], mcu=mcu)

    def verify_flash(self, mcu: str, hex_path: str | Path) -> AvrdudeResult:
        return self._invoke(["-U", f"flash:v:{Path(hex_path)}:i"], mcu=mcu)

    def program_eeprom(self, mcu: str, hex_path: str | Path) -> AvrdudeResult:
        # -D keeps avrdude from issuing a chip erase, which would wipe the flash
        # we just verified.
        return self._invoke(["-D", "-U", f"eeprom:w:{Path(hex_path)}:i"], mcu=mcu)

    def verify_eeprom(self, mcu: str, hex_path: str | Path) -> AvrdudeResult:
        return self._invoke(["-U", f"eeprom:v:{Path(hex_path)}:i"], mcu=mcu)

    def read_eeprom(self, mcu: str, out_path: str | Path) -> AvrdudeResult:
        return self._invoke(["-U", f"eeprom:r:{Path(out_path)}:i"], mcu=mcu)

    def write_fuses(
        self,
        mcu: str,
        *,
        low: Optional[str] = None,
        high: Optional[str] = None,
        extended: Optional[str] = None,
        lock: Optional[str] = None,
    ) -> Optional[AvrdudeResult]:
        """Write any fuse bytes the project defines.  ``None`` when there are none."""
        memories = [("lfuse", low), ("hfuse", high), ("efuse", extended), ("lock", lock)]
        args: List[str] = []
        for name, value in memories:
            if value:
                args += ["-U", f"{name}:w:{_normalise_fuse(value)}:m"]
        if not args:
            return None
        return self._invoke(args, mcu=mcu)


def _normalise_fuse(value: str) -> str:
    text = str(value).strip().lower()
    if not text.startswith("0x"):
        text = "0x" + text.lstrip("#$")
    return text


def signature_name(signature: str) -> str:
    return KNOWN_SIGNATURES.get((signature or "").lower(), "")


def signature_matches(expected: str, found: str) -> bool:
    """Compare signatures tolerantly (case, ``0x`` prefix and separators)."""
    def norm(value: str) -> str:
        text = (value or "").strip().lower()
        # Drop a leading 0x first: stripping separators blindly would leave its
        # '0' behind and make "0x1e950f" differ from "1E 95 0F".
        if text.startswith("0x"):
            text = text[2:]
        return re.sub(r"[^0-9a-f]", "", text)

    a, b = norm(expected), norm(found)
    return bool(a) and bool(b) and a == b


class SimulatedAvrdude(AvrdudeBackend):
    """Offline backend used by ``--simulate``, the FAT rig and the test-suite.

    It answers as a healthy target of the requested MCU type and keeps the last
    EEPROM image it was asked to write, so the full workflow -- including verify
    -- can be exercised on a bench without hardware.
    """

    def __init__(self, config: Optional[AvrdudeConfig] = None, *, signature: str = "0x1e950f"):
        super().__init__(config)
        self.signature = signature
        self.device_present = True
        self.fail_on: set[str] = set()      # {"flash", "flash_verify", "eeprom", ...}
        self.calls: List[str] = []
        self.last_eeprom: bytes = b""
        self.last_flash: Optional[str] = None

    def _fake(self, step: str, message: str = "") -> AvrdudeResult:
        self.calls.append(step)
        if not self.device_present or step in self.fail_on:
            reason = message or f"simulated failure in {step}"
            return AvrdudeResult(False, 1, "", reason, [f"<simulated:{step}>"], 5)
        return AvrdudeResult(True, 0, f"simulated {step} ok", "", [f"<simulated:{step}>"], 5)

    def is_available(self) -> bool:
        return True

    def version(self) -> str:
        return "simulated"

    def read_signature(self, mcu: str):
        result = self._fake("signature")
        return result, (self.signature if result.ok else "")

    def program_flash(self, mcu: str, hex_path):
        self.last_flash = str(hex_path)
        return self._fake("flash")

    def verify_flash(self, mcu: str, hex_path):
        return self._fake("flash_verify")

    def program_eeprom(self, mcu: str, hex_path):
        from .ihex import parse_hex

        result = self._fake("eeprom")
        if result.ok:
            memory = parse_hex(Path(hex_path).read_text(encoding="utf-8"))
            self.last_eeprom = bytes(memory[a] for a in sorted(memory))
        return result

    def verify_eeprom(self, mcu: str, hex_path):
        return self._fake("eeprom_verify")

    def read_eeprom(self, mcu: str, out_path):
        from .ihex import write_hex

        result = self._fake("eeprom_read")
        if result.ok:
            Path(out_path).write_text(write_hex([(0, self.last_eeprom)]), encoding="utf-8")
        return result

    def write_fuses(self, mcu: str, **kwargs):
        if not any(kwargs.values()):
            return None
        return self._fake("fuses")
