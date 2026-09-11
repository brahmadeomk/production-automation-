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

## 2. Getting in over SSH

Kiosk mode removes the desktop and blocks the console virtual terminals
(`DontVTSwitch`), so **SSH is the maintenance route into a station**. The
station's own Exit button leaves the kiosk, but that needs an administrator
standing at the panel.

> **Enable and test SSH before you enable kiosk mode.** Afterwards there is no
> desktop to enable it from and no console to switch to; recovery means taking
> the SD card to another machine.

### 2.1 Enabling SSH

On the station, before it is locked down:

```bash
sudo systemctl enable --now ssh
```

Or on a card imaged elsewhere, put an empty file named `ssh` in the boot
partition — the Pi enables the server on first boot and deletes the file.

### 2.2 Which account to log in as

Log in as the Pi's own administrator account — the one created when the card
was imaged (often `pi`, but whatever your site uses). **Not `progstation`:**
that is a system account created with `/usr/sbin/nologin` precisely so that
nobody can log in as the account that owns the production database.

```bash
ssh pi@<station address>
```

Find the address from the panel: **Settings → Identity** shows the IPv4
address and MAC of every interface. If the station is already unreachable,
its MAC is on that page too — your network administrator can find its lease
from that.

### 2.3 Running station commands

Once in, station commands run as the service account, so they read and write
the database as the station itself does. Running them as your own user, or
under plain `sudo`, can leave root-owned files that lock the station out of
its own database:

```bash
sudo -u progstation /opt/progstation/venv/bin/progstation status
sudo -u progstation /opt/progstation/venv/bin/progstation identity
sudo -u progstation /opt/progstation/venv/bin/progstation doctor
```

A shell alias saves the repetition:

```bash
alias pstation='sudo -u progstation /opt/progstation/venv/bin/progstation'
```

Service control and logs use your own account with `sudo`:

```bash
systemctl status progstation-kiosk        # kiosk mode
systemctl status progstation              # desktop autostart
journalctl -u progstation-kiosk -f        # follow the log
sudo systemctl restart progstation-kiosk
```

To stop the kiosk and leave the panel blank while you work, then bring it
back:

```bash
sudo systemctl stop progstation-kiosk
sudo systemctl start progstation-kiosk
```

### 2.4 Hardening the maintenance account

A station sits on a factory network with a database of production records, so
treat the SSH account as the way in that it is:

- **Change the imaged default password.** A Pi still on `raspberry` is open to
  anyone on the network.
- **Prefer keys to passwords.** Copy your key with
  `ssh-copy-id pi@<station>`, confirm it works, then set
  `PasswordAuthentication no` in `/etc/ssh/sshd_config` and
  `sudo systemctl restart ssh`.
- **Keep at least two people able to get in.** With the desktop gone and the
  kiosk exit behind a password, a single lost credential means an SD card
  rebuild.
- Station logins are separate from SSH: the operator and administrator
  accounts in section 8 are the application's own and grant no shell access.

### 2.5 Wi-Fi from the panel

The Settings page can join a Wi-Fi network, which needs one permission the
station does not otherwise have. `install.sh` puts a polkit rule in
`/etc/polkit-1/rules.d/50-progstation-wifi.rules` granting the service account
exactly two things: bring a wireless interface up or down, and save a
network's credentials. It cannot change the hostname, the wired connection the
station backs up over, or anything else.

Without that rule the page scans but every attempt to connect fails with
*"the station is not permitted to change networks"*.

**To forbid Wi-Fi changes from the panel** — a site where IT fixes the network
and an operator must not move it:

```bash
sudo rm /etc/polkit-1/rules.d/50-progstation-wifi.rules
```

The page then says why it cannot connect rather than failing silently.
Configure the network over SSH instead:

```bash
sudo nmcli device wifi list
sudo nmcli --ask device wifi connect <ssid>
```

### 2.6 The clock

Every production record is timestamped, so a wrong clock corrupts the
traceability evidence the station exists to produce. A Pi 4 has **no
battery-backed clock**: left alone it boots believing it is whenever it was
last shut down, which is why a station that has been powered off over a
weekend comes up days behind.

The station chases the time in this order, at start-up and then daily:

| Order | Source | Why |
|---|---|---|
| 1 | A time server on the plant network | Closest, works with no route to the internet, and agrees with the other equipment the records are compared against |
| 2 | An internet time server | If the station has a route out |
| 3 | The hardware RTC, if fitted | Not a sync but a reading — what carries the time across a power cut |

Whenever 1 or 2 answers, the RTC is written back, so the next cold start has
something better than the last shutdown to go on.

Set your own servers in `station.yaml` — this is the part worth changing,
since the defaults are guesses:

```yaml
time:
  enabled: true
  lan_servers: [ntp.plant.local, 192.168.1.1]   # yours: often the DC or gateway
  internet_servers: [pool.ntp.org, time.google.com]
  interval_hours: 24
```

Check it from the panel at **Settings → Identity** (the Clock row, and
**Sync clock now**), or over SSH:

```bash
sudo -u progstation /opt/progstation/venv/bin/progstation time
sudo -u progstation /opt/progstation/venv/bin/progstation time sync
```

Setting the clock needs privilege the station does not otherwise have.
`install.sh` adds `/etc/sudoers.d/progstation-time` granting exactly two
commands — `date` and `hwclock` — and nothing else. Without it the station can
read the time but not apply it, and says so rather than failing quietly.
Remove that file on a site where the clock is managed centrally.

> **Timestamps are stored in UTC and displayed in local time.** If the dates
> on screen look like yesterday, check the station's timezone
> (`timedatectl set-timezone Asia/Kolkata`) before suspecting the clock.
> Exports carry the UTC offset on every row and name the zone in Export Info,
> so a file read in another office cannot be misread.

### 2.7 When SSH will not connect

| Symptom | Check |
|---|---|
| `Connection refused` | The server is not running: SSH was never enabled before lock-down |
| `No route to host` | Wrong address, or the station moved networks — check **Settings → Identity** on the panel |
| `Permission denied` | Wrong account. `progstation` cannot be logged into at all; use the Pi's admin account |
| Nothing on the network at all | Read the MAC from **Settings → Identity** and ask for its DHCP lease |

If none of that gets you in, the remaining routes are the panel itself —
**Exit** with an administrator password, which drops you to a desktop — or
powering down and reading the SD card on another machine.

---

## 3. First checks on any fault

```bash
sudo -u progstation /opt/progstation/venv/bin/progstation status
sudo -u progstation /opt/progstation/venv/bin/progstation selftest --outputs
```

`status` reports the GPIO backend, avrdude version, database path and last
backup. `selftest --outputs` additionally blinks the LEDs and sounds the buzzer.

> **If `status` reports `gpio_backend: simulated` on a real station, it is not
> programming anything.** A station with GPIO now refuses to start rather than
> simulating, so seeing this means it was started with `--simulate` or has
> `gpio.backend: simulated` in its configuration — see section 5.

---

## 4. Programming faults

### 4.1 Every board reports `E_NO_DEVICE`

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

### 4.2 `E_FIRMWARE_MISSING` — "cannot be read" / "not readable by this account"

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

### 4.3 `E_SIGNATURE_MISMATCH`

The station read a valid signature that is not the one the project expects.
Almost always the wrong product selected or the wrong board loaded. The error
detail names both the expected and the found signature.

### 4.4 Intermittent `E_FLASH_VERIFY` or `E_EEPROM_VERIFY`

Programming started, so the bus works — this is a marginal connection.

- Clean the pogo pins and check fixture pressure.
- Shorten the ISP harness; SPI at 200 kHz tolerates little capacitance.
- Lower `avrdude.baudrate`.
- Check the target's decoupling if a whole batch behaves this way.

### 4.5 A board bricked after a fuse change

Setting the clock-source fuse to an external crystal the board does not have
disables ISP. Prevent this by proving fuse settings on a sacrificial board
before releasing a project — see section 7 of the user manual for worked
ATmega328P values and the recovery table.

A missing or faulty crystal is recoverable: feed a clock into XTAL1 from a
signal generator or another MCU and ISP responds again. A programmed
`RSTDISBL` or an unprogrammed `SPIEN` is not — those need a high-voltage
programmer.

---

## 5. GPIO backend problems

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

### 5.1 `GpioUnavailableError` at startup

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

### 5.2 Checking which backend is live

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

## 6. Serial numbers

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

## 7. Database

### 7.1 Backup

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

### 7.2 Enabling backup

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

### 7.3 Backup share will not mount

```bash
sudo mount -t cifs //fileserver/production /mnt/progstation-backup \
     -o credentials=/etc/progstation/smb-credentials,vers=3.0
```

- `Permission denied` → credentials file. It must be root-owned, `chmod 600`,
  and contain `username=` / `password=` lines with no quotes.
- `Host is down` with a working ping → SMB version. Try `vers=2.1` or `vers=3.1.1`
  in `backup.mount_options`.
- Mounted by `/etc/fstab` instead? Set `backup.manage_mount: false`.

### 7.4 Restore

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

### 7.5 Integrity check

```bash
sudo -u progstation sqlite3 /var/lib/progstation/progstation.db "PRAGMA integrity_check;"
```

Anything other than `ok` means restore from backup. Do not run production on a
database that fails this check — traceability is no longer trustworthy.

---

## 8. Users

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

## 9. Upgrading

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

## 10. Display and touchscreen

### 10.1 Choosing the Qt platform

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

### 10.2 Dedicated kiosk mode

For a station that must boot straight into the application with nothing an
operator can reach behind it:

```bash
sudo /opt/progstation/deploy/kiosk-setup.sh
sudo reboot
```

This replaces the desktop session rather than locking one down. X starts with
the station as its only client, under a window manager that has no taskbar, no
menus and no way to raise another window — there is nothing to switch to
because nothing else is running. Locking down a full desktop means disabling
the panel, the right-click menu, the file manager and the window switcher
individually, and any one of them left enabled is a way out.

It also sets `DontVTSwitch`, so Ctrl+Alt+F1..F6 cannot reach a login console,
and boots to `multi-user.target` so the desktop never starts.

The session drives the panel at the mode `xrandr` marks native (`+`). A desktop
autostart entry would never run here, since there is no desktop — so the
resolution is set by `.xinitrc` instead.

The login screen carries its own keyboard rather than opening one in a separate
window: the kiosk window manager is a minimal one built for single-window
applications, and a keyboard of its own would be at its mercy. Tapping either
field points the keyboard at it. Where the panel is too short for every row,
the symbols row is dropped before the keys are shrunk past a usable size —
Shift on the digit row still reaches `!@#$%^&*()`.

**Leaving requires an administrator.** The Exit button on the login screen
prompts for administrator credentials; an operator account is refused, and both
the refusal and the successful exit are written to the audit log
(`kiosk.exit_denied`, `kiosk.exit`). Escape no longer closes the login screen.

**No window in the kiosk is frameless.** This one is easy to undo by accident.
Under matchbox a frameless parent window wedges every child window the station
opens -- dialogs, keyboards and message boxes alike -- and the application
stops responding. Measured on a 1024x600 panel, a frameless parent wedged the
child five times out of five; the same window without the hint worked every
time and still covered the panel exactly. `showFullScreen()` gives the full
screen on its own and the session starts matchbox with `-use_titlebar no`, so
the hint bought nothing and cost every dialog. A test enforces this.

Every dialog in the sign-in path also carries its own keyboard, for the same
reason as the login screen. That includes the forced password change: an account
flagged to change its password meets that dialog between Sign in and the
programming screen, and without keys on it the operator cannot get past --
the station looks like it ignored the sign-in.

The exit prompt carries its own keyboard too. When it relied on a separate keyboard window the kiosk window manager
gave it a 30 px sliver with no fields on it, so Exit looked like a button that
did nothing. Off the kiosk — a maintainer running `progstation gui` on the
desktop — the prompt keeps the ordinary keyboard button instead, since a full
window manager places a second window correctly.

For maintenance, use SSH — see section 2. To restore the normal desktop:

```bash
sudo /opt/progstation/deploy/kiosk-setup.sh --revert
sudo reboot
```

> Set an administrator password you can retrieve before enabling this, and
> confirm SSH works first (section 2). With the desktop gone and the exit
> behind a password, SSH is the remaining way in.

### 10.3 Autostarting inside the desktop

On a Pi that boots to the desktop, running the station as a desktop
application is simpler than driving X from a system service:

```bash
sudo usermod -aG progstation pi          # so the desktop user can read the data
mkdir -p ~/.config/autostart
cp /opt/progstation/deploy/progstation-kiosk.desktop ~/.config/autostart/
sudo systemctl disable --now progstation # avoid two copies fighting for the screen
```

Log out and back in. Use either this **or** the systemd service, never both.

### 10.4 Interface size

The interface is written for the smallest supported panel (800x480) and scales
up with the screen, to a limit of 1.6x.

| Panel | Scale | Notes |
|---|---|---|
| 800x480 (7-inch) | 1.00x | The size everything is designed at |
| **1024x600 (10-inch)** | **1.25x** | **The fitted HMI** |
| 1280x800 | 1.60x | Scaling caps here |

The on-screen keyboard measures the assembled dialog against the screen and
takes any overflow out of the key rows, so OK and Cancel stay reachable on a
short panel. Keys never shrink below 38 px, and every row is the same height.

If the keys ever render as a thin strip while Shift and Space look normal, an
application style sheet is overriding their height -- a style sheet
`min-height` beats `setFixedHeight()`.

### Forcing the panel's native mode

A panel driven at the wrong mode is rescaled and looks soft. Compare the two
markers in `xrandr`: `+` is the mode the panel reports as native, `*` is the one
in use.

```
1024x600      59.82*+      <- correct: both on the native mode
1024x600      59.82 +      <- native, but something else is active
1280x720      60.00*
```

Set it for the current session:

```bash
xrandr --output HDMI-1 --mode 1024x600
```

To make it survive a reboot on Raspberry Pi OS Bookworm/Trixie, use the kernel
command line. The old `hdmi_group` / `hdmi_mode` / `hdmi_cvt` settings in
`config.txt` are **ignored** by the KMS driver (`vc4-kms-v3d`) these releases
use — if they are present and appear to be forcing a mode, remove them.

```bash
sudo nano /boot/firmware/cmdline.txt
```

Append to the single existing line (do not add a new line):

```
video=HDMI-A-1:1024x600@60
```

Reboot, then confirm the console and X agree:

```bash
xrandr | grep '\*'
journalctl -u progstation | grep "UI scale"
```

If the desktop session is the only thing that needs it, an autostart entry
works too and avoids touching the boot files:

```bash
mkdir -p ~/.config/autostart
cat > ~/.config/autostart/panel-mode.desktop <<'ENTRY'
[Desktop Entry]
Type=Application
Name=Panel native mode
Exec=xrandr --output HDMI-1 --mode 1024x600
ENTRY
```

### Confirming the resolution

```bash
DISPLAY=:0 xdpyinfo | grep dimensions
```

```
dimensions:    1024x600 pixels (270x158 millimeters)
```

`DISPLAY=:0` matters: run inside a RealVNC *virtual* desktop, `xdpyinfo`
reports the VNC session's geometry rather than the physical panel. Cross-check
with `xrandr | grep '\*'`, or `fbset -s | grep geometry` when X is not running.

The station logs the display it detected, in the same form, so the two can be
compared directly:

```bash
journalctl -u progstation | grep "UI scale"
```

```
display 1024x600, UI scale 1.25
```

If that disagrees with `xdpyinfo`, the station attached to a different display
— check `DISPLAY` in `/etc/progstation/display.env`.

### 10.5 Common symptoms



| Symptom | Fix |
|---|---|
| Black screen, service running | Wrong Qt platform. Try `QT_QPA_PLATFORM=eglfs` in `progstation.service` |
| Display rotated | Set `QT_QPA_EGLFS_ROTATION=180` in the unit, or fix the display overlay in `config.txt` |
| Touch offset from the cursor | Calibrate the panel; check the vendor overlay in `/boot/firmware/config.txt` |
| Falls back to a window | Expected under X/Wayland; `showFullScreen` still applies |
| Window smaller than the panel | Start with `--kiosk` (frameless, fullscreen, always on top). The systemd unit already does |
| Right-hand tabs cut off | The layout is sized for 800x480; a narrower panel will clip. Report the resolution |
| Exit does nothing in kiosk mode | Fixed: the prompt now embeds its keyboard. On an older build the window manager collapsed it to a sliver off the visible area |
| Station freezes when any dialog opens | A window was made frameless again. See above -- the kiosk window manager wedges children of a frameless parent |
| Sign in accepted, programming screen never appears | The account is flagged to change its password. The change dialog now carries a keyboard; on an older build it depended on a separate window the kiosk WM mismanaged, leaving the operator stuck on it |

Test the GUI without the panel:

```bash
QT_QPA_PLATFORM=offscreen progstation --simulate gui --windowed
```

---

## 11. Preventive maintenance

| Interval | Task |
|---|---|
| Daily | Check the backup status in the status bar |
| Weekly | Review the Failures tab; a rising `E_FLASH_VERIFY` rate means fixture wear |
| Monthly | Clean the fixture pogo pins; run `selftest --outputs` |
| Monthly | Verify a restore actually works from the newest backup |
| Quarterly | `PRAGMA integrity_check`; review the audit log for unexpected counter overrides |
| On firmware change | Update the project, re-preview the EEPROM block, program one board and verify it |
