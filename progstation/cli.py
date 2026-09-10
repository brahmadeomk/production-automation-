"""Command line interface -- the headless face of the station.

Everything the touchscreen can do is reachable here, which is what makes the
station scriptable for line automation, FAT/SAT scripts and maintenance:

    progstation status
    progstation project add --name TempSensor --mcu atmega328p --hex fw.hex
    progstation program --project TempSensor --operator op1
    progstation report --period daily --export day.xlsx
    progstation backup run
"""

from __future__ import annotations

import argparse
import getpass
import json
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, List, Optional

from . import APP_NAME, __version__
from .app import StationApp, configure_logging
from .config import StationConfig, load_config
from .core.eeprom import EepromMap, legacy_map, map_for_project, recommended_map
from .core.programmer import CycleResult
from .db.database import rows_to_dicts
from .errors import ConfigurationError, StationError
from .reports.engine import PERIODS
from .security.auth import ROLES

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_USAGE = 2


# --------------------------------------------------------------------- output
def _print_table(rows: List[dict], columns: List[str]) -> None:
    if not rows:
        print("(no records)")
        return
    widths = {
        c: max(len(c), max(len(str(r.get(c, ""))) for r in rows)) for c in columns
    }
    print("  ".join(c.ljust(widths[c]) for c in columns))
    print("  ".join("-" * widths[c] for c in columns))
    for row in rows:
        print("  ".join(str(row.get(c, "")).ljust(widths[c]) for c in columns))


def _emit(data: Any, as_json: bool) -> None:
    if as_json:
        print(json.dumps(data, indent=2, default=str))


# ------------------------------------------------------------------- commands
def cmd_status(app: StationApp, args) -> int:
    health = app.health()
    if args.json:
        _emit(health, True)
        return EXIT_OK
    print(f"{APP_NAME} {__version__}")
    for key, value in health.items():
        print(f"  {key:<18}: {value}")
    return EXIT_OK


def cmd_identity(app: StationApp, args) -> int:
    """Which station this is and how it is on the network (SRS section 4)."""
    from .hw.identity import gather

    identity = gather(app.config.station_id)
    if args.json:
        _emit(
            {
                "station_id": identity.station_id,
                "hostname": identity.hostname,
                "model": identity.model,
                "board_serial": identity.board_serial,
                "ssid": identity.ssid,
                "primary_mac": identity.primary_mac,
                "interfaces": [vars(i) for i in identity.interfaces],
            },
            True,
        )
        return EXIT_OK

    print(f"{APP_NAME} {__version__}")
    for caption, value in (
        ("device id", identity.station_id),
        ("connected to", identity.ssid),
        ("mac address", identity.primary_mac),
        ("hostname", identity.hostname),
        ("board", identity.model),
        ("board serial", identity.board_serial),
    ):
        print(f"  {caption:<14}: {value}")
    if identity.interfaces:
        print("  interfaces    :")
        for interface in identity.interfaces:
            print(f"      {interface.describe()}  {interface.ipv4}")
    return EXIT_OK


def cmd_wifi(app: StationApp, args) -> int:
    """Scan for and join a Wi-Fi network."""
    import getpass

    from .hw import wifi

    if not wifi.available():
        print("NetworkManager (nmcli) is not installed on this station.")
        return EXIT_FAIL

    if args.wifi_action == "list":
        networks = wifi.scan()
        if args.json:
            _emit([vars(n) for n in networks], True)
            return EXIT_OK
        if not networks:
            print("No networks found.")
            return EXIT_OK
        for network in networks:
            marker = "*" if network.in_use else " "
            print(f" {marker} {network.ssid:<32} {network.signal:>3}%  "
                  f"{network.security or 'open'}")
        return EXIT_OK

    ssid = args.ssid
    if not ssid:
        print("Give the network with --ssid.")
        return EXIT_FAIL
    # Prompted, never taken from the command line: arguments are readable by
    # any user on the box while the command runs, and end up in shell history.
    password = getpass.getpass(f"Password for '{ssid}' (blank if open): ")
    result = wifi.connect(ssid, password)
    print(result.detail)
    if result.ok:
        app.db.audit(args.actor or "cli", "network.wifi_connect", ssid, "")
    return EXIT_OK if result.ok else EXIT_FAIL


def cmd_init(app: StationApp, args) -> int:
    password = app.bootstrap_admin()
    if password:
        print("Created the default administrator account:")
        print("  username: admin")
        print(f"  password: {password}")
        print("Change it at first login (the account is flagged for a reset).")
    else:
        print("An administrator already exists; nothing to do.")
    if args.write_config:
        target = Path(args.write_config)
        app.config.save(target)
        print(f"Wrote configuration template to {target}")
    return EXIT_OK


# -- projects ---------------------------------------------------------------
def cmd_project_list(app: StationApp, args) -> int:
    rows = rows_to_dicts(app.db.list_projects(include_inactive=True))
    for row in rows:
        project = app.db.get_project(row["ProjectId"])
        row["Issues"] = "; ".join(app.engine.validate_project(project)) or "ok"
        row["NextSerial"] = app.db.next_serial_value(row["ProjectId"])
    if args.json:
        _emit(rows, True)
        return EXIT_OK
    _print_table(
        rows,
        ["ProjectId", "ProjectName", "MCU", "FirmwareVersion", "NextSerial", "Active", "Issues"],
    )
    return EXIT_OK


def _eeprom_map_from_args(args) -> Optional[dict]:
    if args.eeprom_map:
        path = Path(args.eeprom_map)
        text = path.read_text(encoding="utf-8") if path.is_file() else args.eeprom_map
        return EepromMap.from_json(text).to_dict()
    if args.recommended_map:
        return recommended_map().to_dict()
    return None


#: Values applied when a project is first created.  On update, a field the
#: user did not name is left exactly as it was -- silently blanking a HW
#: revision or firmware version would stamp empty manufacturing data into
#: every unit programmed afterwards.
_PROJECT_CREATE_DEFAULTS = {
    "SNAddress": 0,
    "MFGAddress": 8,
    "HwRevision": "",
    "ProductVariant": "",
    "FirmwareVersion": "",
    "SerialPrefix": "",
    "SerialStart": 1,
    "SerialEnd": 999999,
    "SerialDigits": 6,
    "Signature": "",
}

#: Command line option -> database column.
_PROJECT_ARG_COLUMNS = (
    ("mcu", "MCU"),
    ("signature", "Signature"),
    ("sn_address", "SNAddress"),
    ("mfg_address", "MFGAddress"),
    ("hw_revision", "HwRevision"),
    ("variant", "ProductVariant"),
    ("firmware_version", "FirmwareVersion"),
    ("serial_prefix", "SerialPrefix"),
    ("serial_start", "SerialStart"),
    ("serial_end", "SerialEnd"),
    ("serial_digits", "SerialDigits"),
    ("fuse_low", "FuseLow"),
    ("fuse_high", "FuseHigh"),
    ("fuse_extended", "FuseExtended"),
    ("lock_byte", "LockByte"),
)


def cmd_project_add(app: StationApp, args) -> int:
    existing = app.db.get_project_by_name(args.name)
    if existing and not args.update:
        print(f"project '{args.name}' already exists (use --update)", file=sys.stderr)
        return EXIT_FAIL

    # Only the options actually given are written.  Unset options carry None,
    # so an update touches nothing the operator did not ask to change.
    values = {"ProjectName": args.name}
    for option, column in _PROJECT_ARG_COLUMNS:
        supplied = getattr(args, option, None)
        if supplied is not None:
            values[column] = supplied
    if args.hex is not None:
        values["HexPath"] = str(Path(args.hex).expanduser())

    eeprom = _eeprom_map_from_args(args)
    if eeprom:
        values["EepromMap"] = eeprom

    if not existing:
        missing_required = [
            name for name, value in (("--mcu", args.mcu), ("--hex", args.hex))
            if value is None
        ]
        if missing_required:
            print(
                f"a new project needs {' and '.join(missing_required)}",
                file=sys.stderr,
            )
            return EXIT_FAIL
        for column, default in _PROJECT_CREATE_DEFAULTS.items():
            values.setdefault(column, default)

    project_id = app.db.upsert_project(
        values, project_id=int(existing["ProjectId"]) if existing else None
    )
    changed = ", ".join(sorted(k for k in values if k != "ProjectName"))
    app.db.audit(
        args.actor,
        "project.update" if existing else "project.create",
        args.name,
        changed,
    )

    project = app.db.get_project(project_id)
    problems = app.engine.validate_project(project)
    print(f"{'Updated' if existing else 'Created'} project '{args.name}' (id {project_id})")
    if existing:
        print(f"  changed: {changed or '(nothing)'}")
    for problem in problems:
        print(f"  warning: {problem}", file=sys.stderr)
    return EXIT_OK


def cmd_project_show(app: StationApp, args) -> int:
    project = app.db.get_project_by_name(args.name)
    if not project:
        print(f"unknown project '{args.name}'", file=sys.stderr)
        return EXIT_FAIL
    data = dict(project)
    data["NextSerial"] = app.db.next_serial_value(int(project["ProjectId"]))
    data["Issues"] = app.engine.validate_project(project)

    eeprom = map_for_project(project)
    context = {
        "serial": data["NextSerial"] or 0,
        "serial_text": str(data["NextSerial"] or 0).zfill(int(project["SerialDigits"] or 6)),
        "hw_revision": project["HwRevision"],
        "product_variant": project["ProductVariant"],
        "firmware_version": project["FirmwareVersion"],
        "mfg_date": date.today(),
        "operator": "preview",
        "station_id": app.config.station_id,
        "project_name": project["ProjectName"],
    }
    data["EepromPreview"] = eeprom.describe(context)
    data["EepromBlockHex"] = eeprom.build(context).hex().upper()

    if args.json:
        _emit(data, True)
        return EXIT_OK
    for key in (
        "ProjectId", "ProjectName", "MCU", "HexPath", "Signature", "HwRevision",
        "ProductVariant", "FirmwareVersion", "SerialPrefix", "SerialStart",
        "SerialEnd", "SerialDigits", "NextSerial", "Active",
    ):
        print(f"  {key:<16}: {data[key]}")
    print(f"  {'EEPROM base':<16}: 0x{eeprom.base_address:04X} ({eeprom.size} bytes)")
    print(f"  {'EEPROM preview':<16}: {data['EepromBlockHex']}")
    for field in data["EepromPreview"]:
        print(
            f"      0x{field['offset']:04X} +{field['length']:<2} {field['name']:<18}"
            f" {field['hex']:<20} {field['text']}"
        )
    for problem in data["Issues"]:
        print(f"  issue           : {problem}")
    return EXIT_OK


def cmd_project_serial(app: StationApp, args) -> int:
    project = app.db.get_project_by_name(args.name)
    if not project:
        print(f"unknown project '{args.name}'", file=sys.stderr)
        return EXIT_FAIL
    if args.set is None:
        print(app.db.next_serial_value(int(project["ProjectId"])))
        return EXIT_OK
    app.serials.set_next(int(project["ProjectId"]), args.set, actor=args.actor)
    print(f"next serial for '{args.name}' is now {args.set}")
    return EXIT_OK


# -- users ------------------------------------------------------------------
def cmd_user_list(app: StationApp, args) -> int:
    rows = [
        {k: v for k, v in dict(r).items() if k != "PasswordHash"}
        for r in app.db.list_users()
    ]
    if args.json:
        _emit(rows, True)
        return EXIT_OK
    _print_table(rows, ["UserId", "Username", "FullName", "Role", "Active", "LastLoginAt"])
    return EXIT_OK


def _read_password(args) -> str:
    if args.password:
        return args.password
    first = getpass.getpass("Password: ")
    if first != getpass.getpass("Repeat: "):
        raise StationError("passwords do not match")
    return first


def cmd_user_add(app: StationApp, args) -> int:
    app.auth.create_user(
        args.username,
        _read_password(args),
        args.role,
        full_name=args.full_name,
        actor=args.actor,
    )
    print(f"created {args.role} '{args.username}'")
    return EXIT_OK


def cmd_user_passwd(app: StationApp, args) -> int:
    app.auth.set_password(args.username, _read_password(args), actor=args.actor)
    print(f"password updated for '{args.username}'")
    return EXIT_OK


def cmd_user_set(app: StationApp, args) -> int:
    if args.role:
        app.auth.set_role(args.username, args.role, actor=args.actor)
        print(f"'{args.username}' is now {args.role}")
    if args.active is not None:
        app.auth.set_active(args.username, args.active, actor=args.actor)
        print(f"'{args.username}' is now {'active' if args.active else 'inactive'}")
    return EXIT_OK


# -- programming ------------------------------------------------------------
def _print_cycle(result: CycleResult) -> None:
    banner = "PASS" if result.ok else "FAIL"
    print(f"\n  ===== {banner} =====")
    if result.serial_no:
        print(f"  serial   : {result.serial_no}")
    print(f"  project  : {result.project_name}")
    print(f"  operator : {result.operator}")
    print(f"  duration : {result.duration_ms / 1000.0:.2f} s")
    if result.signature:
        print(f"  signature: {result.signature}")
    if result.eeprom_hex:
        print(f"  eeprom   : {result.eeprom_hex}")
    if not result.ok:
        print(f"  error    : {result.error_code} - {result.error_message}")
        print(f"  action   : {result.operator_hint}")


def cmd_program(app: StationApp, args) -> int:
    project = app.db.get_project_by_name(args.project)
    if not project:
        print(f"unknown project '{args.project}'", file=sys.stderr)
        return EXIT_FAIL
    problems = app.engine.validate_project(project)
    if problems:
        for problem in problems:
            print(f"project is not ready: {problem}", file=sys.stderr)
        return EXIT_FAIL

    mfg_date = datetime.strptime(args.mfg_date, "%Y-%m-%d").date() if args.mfg_date else None

    def progress(step: str, message: str, ok: Optional[bool]) -> None:
        mark = " " if ok is None else ("+" if ok else "!")
        print(f"  [{mark}] {message}")

    failures = 0
    for index in range(max(1, args.count)):
        if args.count > 1:
            print(f"\n--- unit {index + 1} of {args.count} ---")
        result = app.engine.run_cycle(
            project,
            args.operator,
            progress=progress if not args.json else None,
            mfg_date=mfg_date,
            skip_fixture_check=args.no_fixture_check,
        )
        if args.json:
            _emit(
                {
                    "result": result.result_text,
                    "serial_no": result.serial_no,
                    "project": result.project_name,
                    "operator": result.operator,
                    "duration_ms": result.duration_ms,
                    "error_code": result.error_code,
                    "error": result.error_message,
                    "eeprom_hex": result.eeprom_hex,
                },
                True,
            )
        else:
            _print_cycle(result)
        if not result.ok:
            failures += 1
            if args.stop_on_fail:
                break
        # Re-read the project so the next unit sees the advanced counter.
        project = app.db.get_project(int(project["ProjectId"]))
    return EXIT_OK if failures == 0 else EXIT_FAIL


# -- history / reports ------------------------------------------------------
def cmd_history(app: StationApp, args) -> int:
    rows = rows_to_dicts(
        app.db.search_production(
            serial=args.serial,
            date_from=args.since,
            date_to=args.until,
            operator=args.operator,
            firmware_version=args.firmware,
            result=args.result,
            limit=args.limit,
        )
    )
    if args.export:
        from .reports.excel import export_records

        path = export_records(
            rows,
            args.export,
            title="Filtered production records",
            filters={
                "serial": args.serial, "since": args.since, "until": args.until,
                "operator": args.operator, "firmware": args.firmware, "result": args.result,
            },
        )
        print(f"exported {len(rows)} record(s) to {path}")
        return EXIT_OK
    if args.json:
        _emit(rows, True)
        return EXIT_OK
    _print_table(
        rows,
        ["Timestamp", "SerialNo", "Result", "ProjectName", "Operator", "FirmwareVersion", "ErrorCode"],
    )
    print(f"\n{len(rows)} record(s)")
    return EXIT_OK


def cmd_report(app: StationApp, args) -> int:
    reference = datetime.strptime(args.date, "%Y-%m-%d").date() if args.date else None
    project_id = None
    if args.project:
        project = app.db.get_project_by_name(args.project)
        if not project:
            print(f"unknown project '{args.project}'", file=sys.stderr)
            return EXIT_FAIL
        project_id = int(project["ProjectId"])

    summary = app.reports.summary(
        args.period, reference, project_id=project_id, operator=args.operator
    )
    if args.export:
        from .reports.excel import export_period

        path = export_period(
            app.db, summary, args.export, company_name=app.config.reports.company_name
        )
        print(f"exported {args.period} report to {path}")
        return EXIT_OK
    if args.json:
        _emit(summary.to_dict(), True)
        return EXIT_OK
    print(app.reports.as_text(summary))
    return EXIT_OK


def cmd_trace(app: StationApp, args) -> int:
    rows = rows_to_dicts(app.db.serial_history(args.serial))
    if not rows:
        print(f"no records for serial '{args.serial}'", file=sys.stderr)
        return EXIT_FAIL
    if args.json:
        _emit(rows, True)
        return EXIT_OK
    for row in rows:
        print(f"  {row['Timestamp']}  {row['Result']}  {row['ProjectName']}")
        for key in ("Operator", "FirmwareVersion", "HwRevision", "ProductVariant",
                    "StationId", "Signature", "EepromHex", "ErrorCode", "Error"):
            if row.get(key):
                print(f"      {key:<16}: {row[key]}")
    return EXIT_OK


# -- backup / audit ---------------------------------------------------------
def cmd_backup(app: StationApp, args) -> int:
    if args.backup_action == "status":
        print(app.backup.status())
        return EXIT_OK
    result = app.backup.run_backup()
    if result.ok:
        print(f"backup ok: {result.files} file(s), {result.bytes_copied} bytes -> {result.destination}")
        return EXIT_OK
    print(f"backup FAILED: {result.detail}", file=sys.stderr)
    return EXIT_FAIL


def cmd_audit(app: StationApp, args) -> int:
    rows = rows_to_dicts(app.db.list_audit(args.limit))
    if args.json:
        _emit(rows, True)
        return EXIT_OK
    _print_table(rows, ["Timestamp", "Username", "Action", "Target", "Detail"])
    return EXIT_OK


def cmd_doctor(app: StationApp, args) -> int:
    """Show exactly what the station runs, and probe the ISP bus.

    The point is to make a failing station self-diagnosing: the operator sees
    the real avrdude command and its raw output instead of an error code.
    """
    projects = (
        [app.db.get_project_by_name(args.project)] if args.project
        else list(app.db.list_projects())
    )
    if args.project and not projects[0]:
        print(f"unknown project '{args.project}'", file=sys.stderr)
        return EXIT_FAIL
    if not projects:
        print("no projects configured", file=sys.stderr)
        return EXIT_FAIL

    from .core.avrdude import classify_failure

    # A station.yaml carried over from an older install can still point at an
    # avrdude fragment that no longer exists; avrdude then fails for a reason
    # that has nothing to do with the board in the fixture.
    fragment = (app.config.avrdude.config_file or "").lstrip("+")
    if fragment and not Path(fragment).is_file():
        print(
            f"WARNING: avrdude.config_file points at {fragment}, which does not"
            " exist.\n         avrdude 7.x does not need it - set"
            " `config_file: null` in station.yaml.",
            file=sys.stderr,
        )

    report = []
    for project in projects:
        mcu = project["MCU"]
        command = " ".join(app.backend.base_args(mcu))
        entry = {"project": project["ProjectName"], "mcu": mcu, "command": command}

        if not args.json:
            print(f"\nProject : {project['ProjectName']}")
            print(f"  MCU configured : {mcu!r}")
            print(f"  avrdude command: {command}")
            print(f"  expected sig   : {project['Signature'] or '(none set)'}")
            for problem in app.engine.validate_project(project):
                print(f"  issue          : {problem}")
            if mcu != mcu.lower():
                print(
                    f"  NOTE           : avrdude part ids are lower case;"
                    f" try '{mcu.lower()}'"
                )

        result, signature = app.backend.read_signature(mcu)
        entry["signature"] = signature
        entry["ok"] = bool(signature)
        cause = classify_failure(result)
        entry["cause"] = cause or ""
        if not args.json:
            if signature:
                from .core.avrdude import signature_name, signature_matches

                name = signature_name(signature)
                print(f"  signature read : {signature}{f' ({name})' if name else ''}  OK")
                expected = project["Signature"] or ""
                if expected and not signature_matches(expected, signature):
                    print(f"  MISMATCH       : project expects {expected}")
            elif cause:
                print(f"  FAILED         : {cause}")
                print(f"  avrdude said   : {result.tail(4)}")
            else:
                print("  FAILED         : no signature - ISP bus is floating")
                print("                   check target power, fixture contacts,")
                print("                   MISO/MOSI orientation and the level shifter")
                print(f"  avrdude said   : {result.tail(4)}")
        report.append(entry)

    if args.json:
        _emit(report, True)
    return EXIT_OK if all(e["ok"] for e in report) else EXIT_FAIL


def cmd_selftest(app: StationApp, args) -> int:
    """FAT/SAT helper (SRS section 19): exercise every subsystem and report."""
    from .selftest import run_selftest

    report = run_selftest(app, exercise_outputs=args.outputs)
    if args.json:
        _emit(report, True)
    else:
        for check in report["checks"]:
            mark = "PASS" if check["ok"] else "FAIL"
            print(f"  [{mark}] {check['name']:<28} {check['detail']}")
        print(f"\n{report['passed']}/{report['total']} checks passed")
    return EXIT_OK if report["ok"] else EXIT_FAIL


def cmd_gui(app: StationApp, args) -> int:
    from .gui import run_gui

    return run_gui(app, fullscreen=not args.windowed, kiosk=args.kiosk)


# --------------------------------------------------------------------- parser
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="progstation", description=f"{APP_NAME} v{__version__}"
    )
    parser.add_argument("--config", help="path to station.yaml")
    parser.add_argument("--db", help="override the database path")
    parser.add_argument(
        "--simulate",
        action="store_true",
        help="use the simulated programmer (no hardware required)",
    )
    parser.add_argument("--json", action="store_true", help="machine readable output")
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--actor", default="cli", help="name recorded in the audit log")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    # --actor is global, but writing it after the subcommand is the natural
    # thing to type ("... user passwd admin --actor jane").  Offer it in both
    # positions: SUPPRESS means the subcommand copy only lands in the namespace
    # when actually given, so it overrides the global value without clobbering
    # it with a default.
    actor_opt = argparse.ArgumentParser(add_help=False)
    actor_opt.add_argument("--actor", default=argparse.SUPPRESS,
                           help="name recorded in the audit log")

    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="show station health").set_defaults(func=cmd_status)
    sub.add_parser(
        "identity", help="show the device id, MAC and connected SSID"
    ).set_defaults(func=cmd_identity)

    wifi_parser = sub.add_parser("wifi", help="scan for and join a Wi-Fi network")
    wifi_parser.add_argument(
        "wifi_action", choices=["list", "connect"], nargs="?", default="list"
    )
    wifi_parser.add_argument("--ssid", help="network to join")
    wifi_parser.set_defaults(func=cmd_wifi)

    init = sub.add_parser("init", help="prepare a fresh station")
    init.add_argument("--write-config", help="write a configuration template here")
    init.set_defaults(func=cmd_init)

    # projects
    project = sub.add_parser("project", help="manage projects").add_subparsers(
        dest="project_action", required=True
    )
    project.add_parser("list", help="list projects").set_defaults(func=cmd_project_list)

    add = project.add_parser("add", help="create or update a project",
                             parents=[actor_opt])
    add.add_argument("--name", required=True)
    add.add_argument("--mcu", help="avrdude part id, e.g. atmega328p")
    add.add_argument("--hex", help="path to the firmware .hex")
    add.add_argument("--signature", help="expected device signature, e.g. 0x1e950f")
    add.add_argument("--sn-address", type=int, help="legacy serial address")
    add.add_argument("--mfg-address", type=int, help="legacy mfg-date address")
    add.add_argument("--eeprom-map", help="JSON map, inline or a file path")
    add.add_argument(
        "--recommended-map",
        action="store_true",
        help="use the 32-byte manufacturing block from SRS section 8",
    )
    add.add_argument("--hw-revision")
    add.add_argument("--variant")
    add.add_argument("--firmware-version")
    add.add_argument("--serial-prefix")
    add.add_argument("--serial-start", type=int)
    add.add_argument("--serial-end", type=int)
    add.add_argument("--serial-digits", type=int)
    add.add_argument("--fuse-low", default=None)
    add.add_argument("--fuse-high", default=None)
    add.add_argument("--fuse-extended", default=None)
    add.add_argument("--lock-byte", default=None)
    add.add_argument("--update", action="store_true", help="update if it already exists")
    add.set_defaults(func=cmd_project_add)

    show = project.add_parser("show", help="show a project and preview its EEPROM block")
    show.add_argument("name")
    show.set_defaults(func=cmd_project_show)

    serial = project.add_parser("serial", help="read or set the serial counter",
                                parents=[actor_opt])
    serial.add_argument("name")
    serial.add_argument("--set", type=int, help="set the next serial number")
    serial.set_defaults(func=cmd_project_serial)

    # users
    user = sub.add_parser("user", help="manage users").add_subparsers(
        dest="user_action", required=True
    )
    user.add_parser("list", help="list users").set_defaults(func=cmd_user_list)

    user_add = user.add_parser("add", help="create a user", parents=[actor_opt])
    user_add.add_argument("username")
    user_add.add_argument("--role", choices=ROLES, default="operator")
    user_add.add_argument("--full-name", default="")
    user_add.add_argument("--password", help="prompted for when omitted")
    user_add.set_defaults(func=cmd_user_add)

    passwd = user.add_parser("passwd", help="change a password", parents=[actor_opt])
    passwd.add_argument("username")
    passwd.add_argument("--password")
    passwd.set_defaults(func=cmd_user_passwd)

    user_set = user.add_parser("set", help="change role or activation",
                               parents=[actor_opt])
    user_set.add_argument("username")
    user_set.add_argument("--role", choices=ROLES)
    user_set.add_argument(
        "--active", type=lambda v: v.lower() in ("1", "true", "yes", "on")
    )
    user_set.set_defaults(func=cmd_user_set)

    # programming
    program = sub.add_parser("program", help="run a programming cycle", parents=[actor_opt])
    program.add_argument("--project", required=True)
    program.add_argument("--operator", required=True)
    program.add_argument("--count", type=int, default=1, help="program N units in a row")
    program.add_argument("--mfg-date", help="override the manufacturing date (YYYY-MM-DD)")
    program.add_argument(
        "--no-fixture-check", action="store_true", help="ignore the fixture-detect switch"
    )
    program.add_argument("--stop-on-fail", action="store_true")
    program.set_defaults(func=cmd_program)

    # history / reports
    history = sub.add_parser("history", help="search the production log")
    history.add_argument("--serial")
    history.add_argument("--since", help="ISO timestamp or date")
    history.add_argument("--until")
    history.add_argument("--operator")
    history.add_argument("--firmware")
    history.add_argument("--result", choices=["PASS", "FAIL"])
    history.add_argument("--limit", type=int, default=100)
    history.add_argument("--export", help="write the filtered records to an .xlsx file")
    history.set_defaults(func=cmd_history)

    report = sub.add_parser("report", help="daily / weekly / monthly summary")
    report.add_argument("--period", choices=PERIODS, default="daily")
    report.add_argument("--date", help="reference date inside the period (YYYY-MM-DD)")
    report.add_argument("--project")
    report.add_argument("--operator")
    report.add_argument("--export", help="write the report to an .xlsx file")
    report.set_defaults(func=cmd_report)

    trace = sub.add_parser("trace", help="full history of one serial number")
    trace.add_argument("serial")
    trace.set_defaults(func=cmd_trace)

    backup = sub.add_parser("backup", help="network backup", parents=[actor_opt])
    backup.add_argument("backup_action", choices=["run", "status"], nargs="?", default="run")
    backup.set_defaults(func=cmd_backup)

    audit = sub.add_parser("audit", help="show the audit trail")
    audit.add_argument("--limit", type=int, default=50)
    audit.set_defaults(func=cmd_audit)

    selftest = sub.add_parser("selftest", help="FAT/SAT acceptance checks")
    selftest.add_argument(
        "--outputs", action="store_true", help="also blink the LEDs and sound the buzzer"
    )
    selftest.set_defaults(func=cmd_selftest)

    doctor = sub.add_parser(
        "doctor", help="show the real avrdude command and probe the ISP bus"
    )
    doctor.add_argument("--project", help="limit to one project")
    doctor.set_defaults(func=cmd_doctor)

    gui = sub.add_parser("gui", help="start the touchscreen application")
    gui.add_argument("--windowed", action="store_true", help="do not go fullscreen")
    gui.add_argument(
        "--kiosk", action="store_true",
        help="production mode: frameless, fullscreen, always on top",
    )
    gui.set_defaults(func=cmd_gui)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        config = load_config(args.config)
    except (OSError, ValueError) as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    if args.db:
        config.database.path = args.db
    configure_logging(config, args.verbose)

    # The GUI owns the panel hardware; other commands only need it when they
    # actually run a cycle or exercise the outputs.
    needs_io = args.command in ("program", "gui", "selftest")
    try:
        app = StationApp(config, simulate=args.simulate, with_io=needs_io)
    except StationError as exc:
        print(f"startup failed: {exc}", file=sys.stderr)
        return EXIT_FAIL

    try:
        return args.func(app, args)
    except (StationError, ConfigurationError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_FAIL
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return EXIT_FAIL
    finally:
        app.close()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
