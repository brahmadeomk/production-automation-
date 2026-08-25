"""GPIO abstraction with an automatic simulation fallback.

Three backends are supported, probed in this order when ``backend: auto``:

``lgpio``
    The driver Raspberry Pi OS Trixie ships with; talks to ``/dev/gpiochipN``
    directly and works headless and without root beyond the ``gpio`` group.
``gpiozero``
    Convenience layer, used when it is installed but ``lgpio`` is not.
``simulated``
    Pure Python, no hardware.  Chosen automatically off-Pi, which is what lets
    the workflow, GUI and tests run on a developer machine.
"""

from __future__ import annotations

import glob
import logging
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional

log = logging.getLogger(__name__)

HIGH = 1
LOW = 0


class GpioUnavailableError(RuntimeError):
    """Real GPIO hardware exists but no backend could drive it."""


def gpio_hardware_present() -> bool:
    """True when the kernel exposes GPIO character devices.

    This separates "no hardware, so simulation is correct" from "real hardware
    we failed to claim", which must never be silently simulated.
    """
    return bool(glob.glob("/dev/gpiochip*"))


def _lgpio_work_dir() -> str:
    """A directory lgpio can always write its notification FIFO into."""
    for candidate in ("/var/lib/progstation", "/run/progstation"):
        path = Path(candidate)
        if path.is_dir() and os.access(path, os.W_OK):
            return str(path)
    return tempfile.gettempdir()


class GpioBackend:
    """Interface every backend implements."""

    name = "base"

    def setup_output(self, pin: int, initial: int = LOW) -> None:
        raise NotImplementedError

    def setup_input(self, pin: int, pull_up: bool = True) -> None:
        raise NotImplementedError

    def write(self, pin: int, value: int) -> None:
        raise NotImplementedError

    def read(self, pin: int) -> int:
        raise NotImplementedError

    def close(self) -> None:
        pass


class SimulatedBackend(GpioBackend):
    """In-memory pins.  Inputs idle high (pull-up), matching the real wiring."""

    name = "simulated"

    def __init__(self):
        self.outputs: Dict[int, int] = {}
        self.inputs: Dict[int, int] = {}
        #: Every output transition, for assertions in tests and the FAT rig.
        self.history: List[tuple[float, int, int]] = []

    def setup_output(self, pin: int, initial: int = LOW) -> None:
        self.outputs[pin] = initial

    def setup_input(self, pin: int, pull_up: bool = True) -> None:
        self.inputs.setdefault(pin, HIGH if pull_up else LOW)

    def write(self, pin: int, value: int) -> None:
        value = HIGH if value else LOW
        self.outputs[pin] = value
        self.history.append((time.monotonic(), pin, value))

    def read(self, pin: int) -> int:
        if pin in self.inputs:
            return self.inputs[pin]
        return self.outputs.get(pin, LOW)

    # ------------------------------------------------- test/simulation hooks
    def set_input(self, pin: int, value: int) -> None:
        self.inputs[pin] = HIGH if value else LOW


class LgpioBackend(GpioBackend):  # pragma: no cover - requires hardware
    name = "lgpio"

    def __init__(self, chip: int = 0, work_dir: Optional[str] = None):
        # lgpio drops a notification FIFO (.lgd-nfy*) into its working
        # directory, which defaults to the *current* directory.  Starting the
        # station from a directory its service account cannot write -- a
        # developer checkout, say -- makes gpiochip_open fail with a permission
        # error that looks nothing like a GPIO problem.  Pin the working
        # directory somewhere always writable instead.
        os.environ.setdefault("LG_WD", work_dir or _lgpio_work_dir())

        import lgpio

        self._lgpio = lgpio
        self._handle = lgpio.gpiochip_open(chip)
        self._claimed: Dict[int, str] = {}

    def setup_output(self, pin: int, initial: int = LOW) -> None:
        self._lgpio.gpio_claim_output(self._handle, pin, initial)
        self._claimed[pin] = "out"

    def setup_input(self, pin: int, pull_up: bool = True) -> None:
        flags = self._lgpio.SET_PULL_UP if pull_up else self._lgpio.SET_PULL_DOWN
        self._lgpio.gpio_claim_input(self._handle, pin, flags)
        self._claimed[pin] = "in"

    def write(self, pin: int, value: int) -> None:
        self._lgpio.gpio_write(self._handle, pin, HIGH if value else LOW)

    def read(self, pin: int) -> int:
        return int(self._lgpio.gpio_read(self._handle, pin))

    def close(self) -> None:
        for pin in list(self._claimed):
            try:
                self._lgpio.gpio_free(self._handle, pin)
            except Exception:
                pass
        try:
            self._lgpio.gpiochip_close(self._handle)
        except Exception:
            pass


class GpiozeroBackend(GpioBackend):  # pragma: no cover - requires hardware
    name = "gpiozero"

    def __init__(self):
        from gpiozero import DigitalInputDevice, DigitalOutputDevice

        self._out_cls = DigitalOutputDevice
        self._in_cls = DigitalInputDevice
        self._devices: Dict[int, object] = {}

    def setup_output(self, pin: int, initial: int = LOW) -> None:
        self._devices[pin] = self._out_cls(pin, initial_value=bool(initial))

    def setup_input(self, pin: int, pull_up: bool = True) -> None:
        self._devices[pin] = self._in_cls(pin, pull_up=pull_up)

    def write(self, pin: int, value: int) -> None:
        device = self._devices[pin]
        device.on() if value else device.off()  # type: ignore[attr-defined]

    def read(self, pin: int) -> int:
        return int(bool(self._devices[pin].value))  # type: ignore[attr-defined]

    def close(self) -> None:
        for device in self._devices.values():
            try:
                device.close()  # type: ignore[attr-defined]
            except Exception:
                pass
        self._devices.clear()


def _probe(backend: GpioBackend, pin: int) -> None:
    """Claim and release one pin to prove the backend actually works.

    Constructing a backend is not proof: ``gpiozero`` imports cleanly and only
    fails later, when a device is created and its pin factory turns out to be
    unusable.  Probing here keeps that failure inside the fallback logic
    instead of letting it escape into the first programming cycle.
    """
    backend.setup_input(pin, pull_up=True)
    backend.read(pin)


def create_backend(
    name: str = "auto", chip: int = 0, *, probe_pin: int = 25
) -> GpioBackend:
    """Instantiate a GPIO backend.

    With ``auto`` the backends are probed in order, and the simulated backend is
    used only when the machine has no GPIO hardware at all.  On a station that
    *does* have GPIO, a backend that cannot be claimed raises instead of quietly
    simulating -- a simulated station would report PASS without programming
    anything, which is far worse than refusing to start.
    """
    name = (name or "auto").lower()
    if name == "simulated":
        return SimulatedBackend()
    if name == "lgpio":
        return LgpioBackend(chip)
    if name == "gpiozero":
        return GpiozeroBackend()
    if name != "auto":
        raise ValueError(f"unknown GPIO backend '{name}'")

    failures: List[str] = []
    for factory, label in ((lambda: LgpioBackend(chip), "lgpio"), (GpiozeroBackend, "gpiozero")):
        backend = None
        try:
            backend = factory()
            _probe(backend, probe_pin)
            backend.close()
            backend = factory()  # fresh handle, with the probe pin released
            log.info("GPIO backend: %s", label)
            return backend
        except Exception as exc:  # ImportError off-Pi, OSError without permission
            failures.append(f"{label}: {type(exc).__name__}: {exc}")
            log.debug("GPIO backend %s unavailable: %s", label, exc)
            if backend is not None:
                try:
                    backend.close()
                except Exception:
                    pass

    if gpio_hardware_present():
        raise GpioUnavailableError(
            "GPIO hardware is present but no backend could claim it:\n  "
            + "\n  ".join(failures)
            + "\n\nRefusing to continue: a simulated backend would report PASS"
            " without programming anything."
            "\nCheck that this account is in the 'gpio' group, that LG_WD points"
            " at a writable directory, and that no other process holds the pins."
            "\nTo run deliberately without hardware, pass --simulate or set"
            " gpio.backend: simulated in station.yaml."
        )

    log.warning("No GPIO hardware on this machine - using the simulated backend")
    return SimulatedBackend()


class EdgeWatcher:
    """Polled, debounced edge detection.

    Polling at 10 ms is plenty for an operator pressing a button and avoids
    depending on backend-specific interrupt APIs, which differ across lgpio and
    gpiozero.
    """

    def __init__(
        self,
        backend: GpioBackend,
        pin: int,
        callback: Callable[[], None],
        *,
        active_low: bool = True,
        debounce_ms: int = 40,
        poll_interval: float = 0.01,
    ):
        self._backend = backend
        self._pin = pin
        self._callback = callback
        self._active = LOW if active_low else HIGH
        self._debounce = debounce_ms / 1000.0
        self._poll = poll_interval
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name=f"edge-{self._pin}")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.0)
            self._thread = None

    def _loop(self) -> None:
        previous = self._backend.read(self._pin)
        while not self._stop.is_set():
            current = self._backend.read(self._pin)
            if current != previous and current == self._active:
                # Re-read after the debounce window; a real press is still held.
                if self._stop.wait(self._debounce):
                    return
                if self._backend.read(self._pin) == self._active:
                    try:
                        self._callback()
                    except Exception:
                        log.exception("edge callback for GPIO%s failed", self._pin)
            previous = current
            self._stop.wait(self._poll)
