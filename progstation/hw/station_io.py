"""Station panel: LEDs, buzzer, start button and fixture-detect switch.

Wraps the raw pins from SRS section 5 in the meanings the workflow cares about
(``pass_result()``, ``fixture_present()``) so no other module has to know a pin
number or an active level.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Optional

from ..config import GpioConfig
from .gpio import HIGH, LOW, EdgeWatcher, GpioBackend, SimulatedBackend, create_backend

log = logging.getLogger(__name__)


class StationIO:
    def __init__(self, config: Optional[GpioConfig] = None, *, backend: Optional[GpioBackend] = None):
        self.config = config or GpioConfig()
        self.backend = backend or create_backend(self.config.backend, self.config.chip)
        self._button_watcher: Optional[EdgeWatcher] = None
        self._fixture_watcher: Optional[EdgeWatcher] = None
        self._blink_stop = threading.Event()
        self._blink_thread: Optional[threading.Thread] = None
        self._setup()

    @property
    def simulated(self) -> bool:
        return isinstance(self.backend, SimulatedBackend)

    def _setup(self) -> None:
        cfg = self.config
        # RESET is driven by avrdude's linuxspi driver during a cycle; we claim
        # it only to hold the target out of reset while idle.
        for pin in (cfg.green_led, cfg.red_led, cfg.buzzer):
            self.backend.setup_output(pin, LOW)
        for pin in (cfg.start_button, cfg.fixture_detect):
            self.backend.setup_input(pin, pull_up=True)

    # ------------------------------------------------------------- indicators
    def green(self, on: bool) -> None:
        self.backend.write(self.config.green_led, HIGH if on else LOW)

    def red(self, on: bool) -> None:
        self.backend.write(self.config.red_led, HIGH if on else LOW)

    def buzzer(self, on: bool) -> None:
        self.backend.write(self.config.buzzer, HIGH if on else LOW)

    def all_off(self) -> None:
        self.stop_blink()
        self.green(False)
        self.red(False)
        self.buzzer(False)

    def beep(self, duration: float = 0.15, count: int = 1, gap: float = 0.1) -> None:
        for i in range(count):
            self.buzzer(True)
            time.sleep(duration)
            self.buzzer(False)
            if i + 1 < count:
                time.sleep(gap)

    # --------------------------------------------------------- cycle signals
    def busy(self) -> None:
        """Programming in progress: green blinking, red off."""
        self.red(False)
        self.start_blink(self.config.green_led, period=0.4)

    def ready(self) -> None:
        """Idle and armed: both LEDs off, waiting for a board."""
        self.all_off()

    def pass_result(self) -> None:
        """PASS: steady green plus one short beep."""
        self.stop_blink()
        self.red(False)
        self.green(True)
        self.beep(0.15, count=1)

    def fail_result(self) -> None:
        """FAIL: steady red plus three beeps -- audible over machine noise."""
        self.stop_blink()
        self.green(False)
        self.red(True)
        self.beep(0.25, count=3, gap=0.15)

    # ------------------------------------------------------------- blinking
    def start_blink(self, pin: int, period: float = 0.5) -> None:
        self.stop_blink()
        self._blink_stop = threading.Event()
        stop = self._blink_stop

        def loop() -> None:
            state = False
            while not stop.is_set():
                state = not state
                self.backend.write(pin, HIGH if state else LOW)
                stop.wait(period / 2)
            self.backend.write(pin, LOW)

        self._blink_thread = threading.Thread(target=loop, daemon=True, name="blink")
        self._blink_thread.start()

    def stop_blink(self) -> None:
        if self._blink_thread:
            self._blink_stop.set()
            self._blink_thread.join(timeout=1.0)
            self._blink_thread = None

    # --------------------------------------------------------------- inputs
    def fixture_present(self) -> bool:
        """True when the fixture-detect switch reports a seated board."""
        value = self.backend.read(self.config.fixture_detect)
        return value == (LOW if self.config.fixture_active_low else HIGH)

    def start_pressed(self) -> bool:
        value = self.backend.read(self.config.start_button)
        return value == (LOW if self.config.button_active_low else HIGH)

    def on_start_button(self, callback: Callable[[], None]) -> None:
        self._button_watcher = EdgeWatcher(
            self.backend,
            self.config.start_button,
            callback,
            active_low=self.config.button_active_low,
            debounce_ms=self.config.debounce_ms,
        )
        self._button_watcher.start()

    def on_fixture_closed(self, callback: Callable[[], None]) -> None:
        self._fixture_watcher = EdgeWatcher(
            self.backend,
            self.config.fixture_detect,
            callback,
            active_low=self.config.fixture_active_low,
            debounce_ms=self.config.debounce_ms,
        )
        self._fixture_watcher.start()

    # -------------------------------------------------------------- teardown
    def close(self) -> None:
        self.stop_blink()
        for watcher in (self._button_watcher, self._fixture_watcher):
            if watcher:
                watcher.stop()
        try:
            self.all_off()
        finally:
            self.backend.close()

    def __enter__(self) -> "StationIO":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ------------------------------------------------------- simulation help
    def simulate_fixture(self, present: bool) -> None:
        """Set the fixture switch in simulation (no-op on real hardware)."""
        if isinstance(self.backend, SimulatedBackend):
            active_value = LOW if self.config.fixture_active_low else HIGH
            idle_value = HIGH if self.config.fixture_active_low else LOW
            self.backend.set_input(
                self.config.fixture_detect, active_value if present else idle_value
            )

    def simulate_start_press(self) -> None:
        if isinstance(self.backend, SimulatedBackend):
            active_value = LOW if self.config.button_active_low else HIGH
            idle_value = HIGH if self.config.button_active_low else LOW
            self.backend.set_input(self.config.start_button, active_value)
            time.sleep((self.config.debounce_ms + 60) / 1000.0)
            self.backend.set_input(self.config.start_button, idle_value)
