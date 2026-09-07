# Maintenance Guide
### RPi Sensor Programming & Manufacturing Traceability Station

Audience: maintenance and manufacturing engineering. Assumes shell access to the
station.

---

## 1. Layout

| Path | Contents |
|---|---|
| `/opt/progstation` | Application and its virtual environment |
| `/etc/progstation/station.yaml` | Station configuration |
| `/etc/progstation/smb-credentials` | Backup share credentials (root, `chmod 600`) |
| `/var/lib/progstation/progstation.db` | **Production database — the traceability record** |
| `/var/lib/progstation/exports` | Generated XLSX files |
| `/var/log/progstation/progstation.log` | Rotating application log |

Service units: `progstation.service` (the touchscreen),
`progstation-backup.timer` / `.service` (scheduled backup).

```bash
systemctl status progstation
journalctl -u progstation -f
systemctl restart progstation
```

---

## 2. First checks on any fault

```bash
sudo -u progstation /opt/progstation/venv/bin/progstation status
sudo -u progstation /opt/progstation/venv/bin/progstation selftest --outputs
```

`status` reports the GPIO backend, avrdude version, database path and last
backup. `selftest --outputs` additionally blinks the LEDs and sounds the buzzer.

> **If `status` reports `gpio_backend: simulated` on a real station, it is not
> programming anything.** A station with GPIO now refuses to start rather than
> simulating, so seeing this means it was started with `--simulate` or has
> `gpio.backend: simulated` in its configuration — see section 4.

---

## 3. Programming faults

### 3.1 Every board reports `E_NO_DEVICE`

The signature read came back all-zeros or all-ones, meaning the ISP bus is
floating. In likelihood order:

1. **Fixture contacts** — the usual cause. Inspect and clean the pogo pins.
2. **Target power** — the AVR must be powered by the fixture; ISP does not power it.
3. **SPI not enabled** — `ls /dev/spidev0.*` must list a device. If it does not:
   ```bash
   sudo raspi-config nonint do_spi 0 && sudo reboot
   ```
4. **Level shifter** — check its enable pin and that it is powered on both rails.
5. **SPI clock too fast** — a factory-fused AVR runs at 1 MHz, so ISP must stay
   below 250 kHz. Lower `avrdude.baudrate` to `125000` and retry.

**Start with `doctor`.** It prints the exact avrdude command the station runs,
the configured part id, and the raw result — which separates a configuration
fault from a genuine wiring problem in one step:

```bash
sudo -u progstation /opt/progstation/venv/bin/progstation doctor
```

```
Project : Impact Detection
  MCU configured : 'atmega328p'
  avrdude command: avrdude -p atmega328p -c linuxspi -P /dev/spidev0.0:/dev/gpiochip0:25 -b 200000
  expected sig   : 0x1e950f
  signature read : 0x1e950f (atmega328p)  OK
```

The MCU is printed quoted so a stray capital or trailing space is visible —
`'ATmega328P'` is not a part id avrdude accepts, and a wrong one reports as a
configuration fault rather than a missing board.

Reproduce outside the application to split hardware from software:

```bash
avrdude -p atmega328p -c linuxspi \
        -P /dev/spidev0.0:/dev/gpiochip0:25 -b 125000 -v
```

> **Port format.** avrdude 7.x takes the reset line *in the port*:
> `/dev/spidevX.Y:/dev/gpiochipN[:resetno]`. A bare `/dev/spidev0.0` is the
> avrdude 6.x form and 7.x rejects it with
> `linuxspi_open() error: unknown port specification`. The station builds the
> full string from `gpio.reset` and `gpio.chip`, so you only set the pin once.
> Check what it actually runs with `progstation --verbose program ...`.

### 3.2 `E_FIRMWARE_MISSING` — "cannot be read" / "not readable by this account"

The station runs as `progstation`, which cannot read another user's home
directory. A `.hex` on the desktop (`/home/pi/Desktop/...`) will never program.
Keep firmware in `/var/lib/progstation/firmware`, created by the installer:

```bash
sudo install -o progstation -g progstation -m 0644 \
     /home/pi/Desktop/product.hex /var/lib/progstation/firmware/

sudo -u progstation /opt/progstation/venv/bin/progstation \
     project add --name "<project>" --update \
     --hex /var/lib/progstation/firmware/product.hex --actor maintenance
```

Verify the account can actually read it:

```bash
sudo -u progstation head -c 64 /var/lib/progstation/firmware/product.hex
```

Storing firmware under the station also keeps it inside the scheduled backup,
so the exact binary shipped with each serial number stays recoverable.

### 3.3 `E_SIGNATURE_MISMATCH`

The station read a valid signature that is not the one the project expects.
Almost always the wrong product selected or the wrong board loaded. The error
detail names both the expected and the found signature.

### 3.4 Intermittent `E_FLASH_VERIFY` or `E_EEPROM_VERIFY`

Programming started, so the bus works — this is a marginal connection.

- Clean the pogo pins and check fixture pressure.
- Shorten the ISP harness; SPI at 200 kHz tolerates little capacitance.
- Lower `avrdude.baudrate`.
- Check the target's decoupling if a whole batch behaves this way.

### 3.5 A board bricked after a fuse change

Setting the clock-source fuse to an external crystal the board does not have
disables ISP. Prevent this by proving fuse settings on a sacrificial board
before releasing a project — see section 7 of the user manual for worked
ATmega328P values and the recovery table.

A missing or faulty crystal is recoverable: feed a clock into XTAL1 from a
signal generator or another MCU and ISP responds again. A programmed
`RSTDISBL` or an unprogrammed `SPIEN` is not — those need a high-voltage
programmer.

---

## 4. GPIO backend problems

The station probes `lgpio`, then `gpiozero`. Each candidate is **proved** by
claiming and releasing a pin, not merely imported — `gpiozero` imports cleanly
and only collapses later when its pin factory turns out to be unusable.

What happens when none works depends on the machine:

| Machine | Behaviour |
|---|---|
| No `/dev/gpiochip*` (a developer laptop) | Falls back to the simulated backend with a warning |
| GPIO present but unclaimable (a real station) | **Refuses to start**, raising `GpioUnavailableError` |

That second row is deliberate. A station running simulated would show PASS
while programming nothing, and stamp serial numbers onto boards that never
received firmware. Refusing to start is the safer failure.

### 4.1 `GpioUnavailableError` at startup

The message lists what each backend reported. The usual causes:

**Permission denied / not in the `gpio` group**

```bash
id progstation                 # must list the gpio group
sudo usermod -aG gpio progstation
sudo systemctl restart progstation
```

**`lguGetWorkDir: can't set working directory` or `xCreatePipe: Can't set permissions ... .lgd-nfy0`**

`lgpio` writes a notification FIFO into its working directory. The station pins
that to `LG_WD` (defaulting to `/var/lib/progstation`), but if you launch it by
hand from a directory the service account cannot write, an inherited `LG_WD`
or an old build may still land there. Force it:

```bash
sudo -u progstation LG_WD=/var/lib/progstation \
     /opt/progstation/venv/bin/progstation status
```

**`FileNotFoundError: /sys/class/gpio/gpio23/value`**

`gpiozero` fell through to its NativeFactory, which uses the legacy sysfs GPIO
interface removed from current kernels. Install a working pin factory:

```bash
sudo /opt/progstation/venv/bin/pip install lgpio
```

**Device busy** — another process holds the pins:

```bash
sudo fuser -v /dev/gpiochip0
```

### 4.2 Checking which backend is live

`status` does **not** open the panel — only `program`, `gui` and `selftest` do.
It is safe to run during production and reports which backend *would* be used:

```bash
sudo -u progstation /opt/progstation/venv/bin/progstation status
```

```
gpio_backend      : lgpio (not opened by this command)
simulated         : False
```

The suffix is normal. `simulated: False` with a named backend is a healthy
station. `simulated: True` on a real station means it is not programming
anything — treat that as a stop-the-line fault.

To actually exercise the GPIO, LEDs and buzzer, run the self-test **without**
`--simulate`:

```bash
sudo -u progstation /opt/progstation/venv/bin/progstation selftest --outputs
```

That opens the real panel, blinks both LEDs, sounds the buzzer, and reports the
fixture-detect state. It is the only command that proves the wiring.

> `status` deliberately avoids claiming pins: GPIO25 is the target's RESET line,
> and grabbing it mid-cycle could disturb a board being programmed.

To run without hardware on purpose (training, bench work), pass `--simulate`,
which simulates the programmer *and* the panel, or set `gpio.backend: simulated`
in `station.yaml`.

## 5. Serial numbers

Read and set the counter:

```bash
progstation project serial TempSensor
progstation project serial TempSensor --set 5000 --actor "your.name"
```

Both the GUI and the CLI write the change to `AuditLog` with the old and new
values. `--actor` is what gets recorded — use a real name. It may be written
either before the subcommand (`progstation --actor jane user passwd bob`) or
after it (`progstation user passwd bob --actor jane`); the trailing form wins if
you give both.

**The counter only advances inside the same transaction that commits a PASS
record.** A power loss mid-cycle therefore leaves the counter untouched: the
board on the fixture is unprogrammed or partly programmed and its number is
still available. Reprogram it normally.

If a counter is ever suspected wrong, the log is authoritative:

```bash
progstation history --result PASS --limit 5
```

---

## 6. Database

### 6.1 Backup

```bash
progstation backup run          # immediate
progstation backup status       # result of the last attempt
systemctl list-timers progstation-backup.timer
```

Backups use SQLite `VACUUM INTO`, so the snapshot is consistent even if a cycle
commits during the copy. Each run writes to
`<share>/progstation/<station-id>/<timestamp>/` and prunes folders older than
`backup.keep_days`.

Backup failures are logged to `BackupLog` and shown in the status bar. **They
never stop production** — a station that cannot reach the file server keeps
programming and recording locally.

### 6.2 Enabling backup

The share must be mounted **outside** the station: the backup timer runs as the
`progstation` account, not root, so a backup job can never leave root-owned
SQLite sidecar files that lock the station out of its own database.

1. Credentials, root-owned and unreadable by anyone else:

   ```bash
   sudo install -m 600 /dev/null /etc/progstation/smb-credentials
   sudo tee /etc/progstation/smb-credentials >/dev/null <<'EOF'
   username=svc_progstation
   password=<the password>
   domain=WORKGROUP
   EOF
   ```

2. Mount from `/etc/fstab` so it survives a reboot:

   ```
   //fileserver/production  /mnt/progstation-backup  cifs  credentials=/etc/progstation/smb-credentials,vers=3.0,uid=progstation,gid=progstation,file_mode=0640,dir_mode=0750,_netdev,nofail  0  0
   ```

   `nofail` matters: without it a file server that is down at boot stops the Pi
   from booting, taking the line with it.

   ```bash
   sudo mkdir -p /mnt/progstation-backup
   sudo systemctl daemon-reload && sudo mount -a
   ```

3. In `station.yaml`:

   ```yaml
   backup:
     enabled: true
     manage_mount: false          # fstab owns the mount
     mount_point: /mnt/progstation-backup
     interval_minutes: 1440
     keep_days: 90
   ```

4. Prove it, then enable the timer:

   ```bash
   sudo -u progstation /opt/progstation/venv/bin/progstation backup run
   sudo systemctl enable --now progstation-backup.timer
   systemctl list-timers progstation-backup.timer
   ```

Run it as `progstation`, not with `sudo` — that is exactly how the timer runs it,
so a success proves the scheduled job will work too.

### 6.3 Backup share will not mount

```bash
sudo mount -t cifs //fileserver/production /mnt/progstation-backup \
     -o credentials=/etc/progstation/smb-credentials,vers=3.0
```

- `Permission denied` → credentials file. It must be root-owned, `chmod 600`,
  and contain `username=` / `password=` lines with no quotes.
- `Host is down` with a working ping → SMB version. Try `vers=2.1` or `vers=3.1.1`
  in `backup.mount_options`.
- Mounted by `/etc/fstab` instead? Set `backup.manage_mount: false`.

### 6.4 Restore

```bash
sudo systemctl stop progstation
sudo cp /var/lib/progstation/progstation.db /var/lib/progstation/progstation.db.bad
sudo cp /mnt/progstation-backup/progstation/STATION-01/<timestamp>/progstation.db \
        /var/lib/progstation/progstation.db
sudo chown progstation:progstation /var/lib/progstation/progstation.db
sudo -u progstation /opt/progstation/venv/bin/progstation status
sudo systemctl start progstation
```

Keep the `.bad` copy: records created after the backup live only there, and can
be recovered by an engineer with SQLite.

### 6.5 Integrity check

```bash
sudo -u progstation sqlite3 /var/lib/progstation/progstation.db "PRAGMA integrity_check;"
```

Anything other than `ok` means restore from backup. Do not run production on a
database that fails this check — traceability is no longer trustworthy.

---

## 7. Users

```bash
progstation user list
progstation user add jsmith --role operator --actor "your.name"
progstation user passwd jsmith --actor "your.name"
progstation user set jsmith --active false --actor "your.name"
```

**Locked out of every administrator account?** Create one directly:

```bash
sudo systemctl stop progstation
sudo -u progstation /opt/progstation/venv/bin/progstation \
     user add recovery --role admin --actor "maintenance"
sudo systemctl start progstation
```

This is itself audited. Passwords cannot be recovered, only reset — they are
stored as salted PBKDF2-SHA256 hashes.

---

## 8. Upgrading

```bash
cd /path/to/checkout && git pull
sudo ./install.sh
```

Re-running the installer is safe: it never overwrites `station.yaml` (a new
template lands at `station.yaml.new` for diffing) and never touches the
database. Schema migrations run automatically at startup, guarded by
`PRAGMA user_version`.

**Back up before upgrading:**

```bash
progstation backup run
```

---

## 9. Display and touchscreen

### 9.1 Choosing the Qt platform

`/etc/progstation/display.env` decides how the station reaches the screen. The
installer picks a default by looking for a desktop session; change it if the
machine changes.

| Setting | Use when | Note |
|---|---|---|
| `QT_QPA_PLATFORM=xcb` | The Raspberry Pi desktop is running (you can VNC in) | Also set `DISPLAY` and `XAUTHORITY` |
| `QT_QPA_PLATFORM=linuxfb` | Console only, no desktop | Lightest; draws straight to the framebuffer |
| `QT_QPA_PLATFORM=eglfs` | Console only, linuxfb shows nothing on HDMI | GPU accelerated |

> **Do not use `linuxfb` while the desktop is running.** Both want the display,
> and the result is a black screen or a flickering console. This is the usual
> cause of "the service is active but nothing appears".

```bash
sudo nano /etc/progstation/display.env
sudo systemctl restart progstation
journalctl -u progstation -n 40 --no-pager
```

### 9.2 Autostarting inside the desktop

On a Pi that boots to the desktop, running the station as a desktop
application is simpler than driving X from a system service:

```bash
sudo usermod -aG progstation pi          # so the desktop user can read the data
mkdir -p ~/.config/autostart
cp /opt/progstation/deploy/progstation-kiosk.desktop ~/.config/autostart/
sudo systemctl disable --now progstation # avoid two copies fighting for the screen
```

Log out and back in. Use either this **or** the systemd service, never both.

### 9.3 Interface size

The interface is written for the smallest supported panel (800x480) and scales
up with the screen, to a limit of 1.6x. A 10-inch 1280x800 panel therefore gets
noticeably larger text and buttons than a 7-inch one. The chosen factor is
logged at startup:

```bash
journalctl -u progstation | grep "UI scale"
```

### 9.4 Common symptoms



| Symptom | Fix |
|---|---|
| Black screen, service running | Wrong Qt platform. Try `QT_QPA_PLATFORM=eglfs` in `progstation.service` |
| Display rotated | Set `QT_QPA_EGLFS_ROTATION=180` in the unit, or fix the display overlay in `config.txt` |
| Touch offset from the cursor | Calibrate the panel; check the vendor overlay in `/boot/firmware/config.txt` |
| Falls back to a window | Expected under X/Wayland; `showFullScreen` still applies |
| Window smaller than the panel | Start with `--kiosk` (frameless, fullscreen, always on top). The systemd unit already does |
| Right-hand tabs cut off | The layout is sized for 800x480; a narrower panel will clip. Report the resolution |

Test the GUI without the panel:

```bash
QT_QPA_PLATFORM=offscreen progstation --simulate gui --windowed
```

---

## 10. Preventive maintenance

| Interval | Task |
|---|---|
| Daily | Check the backup status in the status bar |
| Weekly | Review the Failures tab; a rising `E_FLASH_VERIFY` rate means fixture wear |
| Monthly | Clean the fixture pogo pins; run `selftest --outputs` |
| Monthly | Verify a restore actually works from the newest backup |
| Quarterly | `PRAGMA integrity_check`; review the audit log for unexpected counter overrides |
| On firmware change | Update the project, re-preview the EEPROM block, program one board and verify it |
