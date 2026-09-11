"""Station identity: who this box is and how it is on the network.

SRS section 4 gives every station an id that ends up in the traceability
record, so an engineer holding a production log needs a way to tell which
physical Pi wrote it.  This gathers that: the configured station id, the
board's own serial, and the MAC and SSID of each interface.

Everything here is read-only and needs no privileges.  The MAC, link state
and interface list come from sysfs, which is always present; the SSID and
address need a tool that may not be installed, so each lookup degrades to a
plain "unavailable" string rather than raising.  A maintenance page that
cannot show the SSID is still worth opening for the MAC.
"""
from __future__ import annotations

import fcntl
import re
import shutil
import socket
import struct
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Sequence

#: ioctl for "give me this interface's IPv4 address" (linux/sockios.h).
SIOCGIFADDR = 0x8915

#: Interfaces that say nothing about how the station is connected.
_IGNORED_PREFIXES = ("lo", "ifb", "docker", "veth", "br-")

UNKNOWN = "unavailable"


def _run(command: Sequence[str], *, timeout: float = 4.0) -> str:
    """Run a lookup command, returning "" if it cannot be run at all.

    A missing tool is normal (a wired-only station has no wireless tools), and
    a hung one must not take the maintenance page with it, hence the timeout.
    """
    if shutil.which(command[0]) is None:
        return ""
    try:
        result = subprocess.run(
            list(command), capture_output=True, text=True, timeout=timeout, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


@dataclass
class Interface:
    name: str
    mac: str = UNKNOWN
    state: str = UNKNOWN
    ipv4: str = UNKNOWN
    wireless: bool = False
    ssid: str = ""

    @property
    def connected(self) -> bool:
        return self.state == "up"

    def describe(self) -> str:
        kind = "Wi-Fi" if self.wireless else "wired"
        where = f" on {self.ssid}" if self.wireless and self.ssid not in ("", UNKNOWN) else ""
        return f"{self.name} ({kind}) {self.mac} {self.state}{where}"


@dataclass
class StationIdentity:
    station_id: str
    hostname: str = UNKNOWN
    model: str = UNKNOWN
    board_serial: str = UNKNOWN
    interfaces: List[Interface] = field(default_factory=list)

    @property
    def wireless(self) -> Optional[Interface]:
        for interface in self.interfaces:
            if interface.wireless:
                return interface
        return None

    @property
    def ssid(self) -> str:
        """The network the station is on, or a reason there is none."""
        wireless = self.wireless
        if wireless is None:
            return "no wireless interface"
        if not wireless.connected:
            return "not connected"
        return wireless.ssid or UNKNOWN

    @property
    def primary_mac(self) -> str:
        """The MAC an engineer would quote for this station.

        The connected interface, preferring the one carrying the traffic; a
        station with the cable pulled still has a MAC worth showing.
        """
        for candidate in (
            [i for i in self.interfaces if i.connected],
            self.interfaces,
        ):
            if candidate:
                return candidate[0].mac
        return UNKNOWN


def _read(path: Path) -> str:
    try:
        return path.read_text(errors="replace").strip("\x00\n \t")
    except OSError:
        return ""


def _ipv4(name: str) -> str:
    """Ask the kernel directly, so no `ip`/`ifconfig` need be installed."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            packed = struct.pack("256s", name.encode()[:15])
            return socket.inet_ntoa(
                fcntl.ioctl(sock.fileno(), SIOCGIFADDR, packed)[20:24]
            )
    except (OSError, ValueError):
        return UNKNOWN


def _ssid(name: str, run: Callable[..., str]) -> str:
    """The SSID, from whichever tool this image happens to carry.

    Raspberry Pi OS moved to NetworkManager, but a station imaged earlier, or
    one cut down for production, may only have the wireless-tools pair -- so
    try each and take the first that answers.
    """
    for command in (
        ["iwgetid", "--raw", name],
        ["nmcli", "-t", "-f", "GENERAL.CONNECTION", "device", "show", name],
        ["iw", "dev", name, "link"],
    ):
        output = run(command)
        if not output:
            continue
        if command[0] == "iwgetid":
            return output
        if command[0] == "nmcli":
            value = output.split(":", 1)[-1].strip()
            # nmcli prints this for an interface that is up but unassociated.
            if value and value != "--":
                return value
            continue
        match = re.search(r"^\s*SSID:\s*(.+)$", output, re.MULTILINE)
        if match:
            return match.group(1).strip()
    return UNKNOWN


def _board_serial(proc: Path) -> str:
    """The Pi's own serial, which no two boards share."""
    for line in _read(proc / "cpuinfo").splitlines():
        if line.lower().startswith("serial"):
            value = line.split(":", 1)[-1].strip()
            if value:
                return value
    return UNKNOWN


def interfaces(
    sysfs: Path = Path("/sys/class/net"),
    *,
    run: Callable[..., str] = _run,
    address: Callable[[str], str] = _ipv4,
) -> List[Interface]:
    try:
        names = sorted(entry.name for entry in sysfs.iterdir())
    except OSError:
        return []

    found = []
    for name in names:
        if name.startswith(_IGNORED_PREFIXES):
            continue
        base = sysfs / name
        wireless = (base / "wireless").is_dir() or (base / "phy80211").exists()
        interface = Interface(
            name=name,
            mac=_read(base / "address") or UNKNOWN,
            state=_read(base / "operstate") or UNKNOWN,
            ipv4=address(name),
            wireless=wireless,
        )
        if wireless:
            interface.ssid = _ssid(name, run) if interface.connected else ""
        found.append(interface)
    # Wireless last: the wired link is the one a station normally runs on, and
    # the page reads better with the primary interface at the top.
    found.sort(key=lambda i: (i.wireless, i.name))
    return found


def gather(
    station_id: str,
    *,
    sysfs: Path = Path("/sys/class/net"),
    proc: Path = Path("/proc"),
    device_tree: Path = Path("/proc/device-tree"),
    run: Callable[..., str] = _run,
    address: Callable[[str], str] = _ipv4,
) -> StationIdentity:
    """Collect everything the identity page shows, in one pass."""
    return StationIdentity(
        station_id=station_id or UNKNOWN,
        hostname=socket.gethostname() or UNKNOWN,
        model=_read(device_tree / "model") or UNKNOWN,
        board_serial=_board_serial(proc),
        interfaces=interfaces(sysfs, run=run, address=address),
    )
