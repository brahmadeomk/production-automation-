"""Join a Wi-Fi network from the panel.

The station is a sealed appliance: no desktop, no console, and a service
account that cannot be logged into.  That leaves no way to move it to a
different network without an engineer and an SSH session, which is why this
exists -- but it is also why it is deliberately narrow.  It scans, and it
joins.  It does not edit connections, manage saved profiles or touch the
wired interface, so the station cannot be knocked off the network it backs up
over by a mis-tap.

Everything goes through NetworkManager's ``nmcli``, which is what Raspberry Pi
OS uses.  Where it is absent the page says so rather than failing: a station
on a cable does not need any of this.

**The password is never passed as a command-line argument.**  Arguments are
world-readable in ``/proc`` while the command runs, and a factory Wi-Fi key is
worth protecting from that; ``nmcli --ask`` reads it from stdin instead.  It
is likewise never logged, never returned in a result, and never written to the
audit trail -- only the fact that the network changed, and who changed it.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence

NMCLI = "nmcli"

#: nmcli's terse output separates fields with ':' and escapes any that appear
#: inside a value.  An SSID may legitimately contain one.
_UNESCAPED_COLON = re.compile(r"(?<!\\):")


@dataclass
class Network:
    ssid: str
    signal: int = 0
    security: str = ""
    in_use: bool = False

    @property
    def open(self) -> bool:
        """An unsecured network -- worth saying out loud on a factory floor."""
        return self.security in ("", "--", "none")

    @property
    def bars(self) -> str:
        """A four-step gauge; even a weak network shows one bar, not none."""
        for threshold, filled in ((75, 4), (50, 3), (25, 2)):
            if self.signal >= threshold:
                return "▂▄▆█"[:filled]
        return "▂"


@dataclass
class Result:
    ok: bool
    detail: str


def _run(command: Sequence[str], *, stdin: str = "", timeout: float = 45.0):
    """Run an nmcli command. ``stdin`` carries the password, never argv."""
    return subprocess.run(
        list(command),
        input=stdin,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def available(which: Callable[[str], Optional[str]] = shutil.which) -> bool:
    return which(NMCLI) is not None


def _fields(line: str) -> List[str]:
    return [part.replace("\\:", ":") for part in _UNESCAPED_COLON.split(line)]


def scan(*, run: Callable[..., object] = _run, rescan: bool = True) -> List[Network]:
    """List the networks in range, strongest first.

    Returns [] when nmcli is missing or the scan fails; the caller shows that
    as "no networks found", which is the truth from the operator's side.
    """
    command = [
        NMCLI, "-t", "-f", "SSID,SIGNAL,SECURITY,IN-USE",
        "device", "wifi", "list",
    ]
    if rescan:
        command += ["--rescan", "yes"]
    try:
        completed = run(command)
    except (OSError, subprocess.SubprocessError):
        return []
    if getattr(completed, "returncode", 1) != 0:
        return []

    found: dict[str, Network] = {}
    for line in (completed.stdout or "").splitlines():
        if not line.strip():
            continue
        parts = _fields(line)
        if len(parts) < 4:
            continue
        ssid, signal, security, in_use = parts[0], parts[1], parts[2], parts[3]
        if not ssid:
            continue                      # a hidden network has no name to show
        try:
            strength = int(signal)
        except ValueError:
            strength = 0
        network = Network(
            ssid=ssid,
            signal=strength,
            security=security.strip(),
            in_use=in_use.strip() == "*",
        )
        # The same SSID appears once per access point; keep the strongest.
        if ssid not in found or strength > found[ssid].signal:
            found[ssid] = network
    return sorted(found.values(), key=lambda n: (-n.signal, n.ssid.lower()))


def connect(
    ssid: str,
    password: str = "",
    *,
    interface: str = "",
    run: Callable[..., object] = _run,
) -> Result:
    """Join ``ssid``.  The password goes in on stdin, never on the command line."""
    if not ssid:
        return Result(False, "no network selected")

    command = [NMCLI]
    if password:
        # --ask makes nmcli prompt for the secret, which we answer on stdin.
        command.append("--ask")
    command += ["device", "wifi", "connect", ssid]
    if interface:
        command += ["ifname", interface]

    try:
        completed = run(command, stdin=f"{password}\n" if password else "")
    except subprocess.TimeoutExpired:
        return Result(False, "timed out while connecting")
    except OSError as exc:
        return Result(False, f"could not run nmcli ({exc.strerror})")

    if getattr(completed, "returncode", 1) == 0:
        return Result(True, f"connected to {ssid}")
    return Result(False, _explain(completed, ssid))


def _explain(completed, ssid: str) -> str:
    """Turn nmcli's output into something an operator can act on."""
    text = ((getattr(completed, "stderr", "") or "")
            + " "
            + (getattr(completed, "stdout", "") or "")).strip()
    lowered = text.lower()
    if "secrets were required" in lowered or "no secrets" in lowered:
        return "wrong password"
    if "not authorized" in lowered or "access denied" in lowered:
        return (
            "the station is not permitted to change networks - the polkit rule "
            "from deploy/ is not installed (see MAINTENANCE section 2)"
        )
    if "no network with ssid" in lowered or "not found" in lowered:
        return f"'{ssid}' is no longer in range"
    if "timeout" in lowered:
        return "timed out while connecting"
    return text.splitlines()[0] if text else "could not connect"
