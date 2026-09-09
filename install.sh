#!/usr/bin/env bash
#
# Installer for the RPi Sensor Programming Station.
# Target: Raspberry Pi OS Trixie (64-bit) on a Pi 4B.
#
#   sudo ./install.sh
#
# Safe to re-run: it upgrades an existing installation in place and never
# overwrites /etc/progstation/station.yaml or the production database.

set -euo pipefail

PREFIX=/opt/progstation
CONFIG_DIR=/etc/progstation
DATA_DIR=/var/lib/progstation
LOG_DIR=/var/log/progstation
SERVICE_USER=progstation
SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

log()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m==>\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m==>\033[0m %s\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "run this as root (sudo ./install.sh)"

# ---------------------------------------------------------------- packages
log "Installing system packages"
apt-get update -qq
apt-get install -y --no-install-recommends \
    python3 python3-venv python3-pip \
    python3-pyqt5 \
    avrdude \
    cifs-utils \
    rsync \
    fonts-dejavu-core

# ------------------------------------------------------------ SPI + groups
log "Enabling SPI"
if command -v raspi-config >/dev/null 2>&1; then
    raspi-config nonint do_spi 0 || warn "raspi-config could not enable SPI; do it manually"
else
    CONFIG_TXT=/boot/firmware/config.txt
    [[ -f $CONFIG_TXT ]] || CONFIG_TXT=/boot/config.txt
    if ! grep -q '^dtparam=spi=on' "$CONFIG_TXT" 2>/dev/null; then
        echo 'dtparam=spi=on' >> "$CONFIG_TXT"
        warn "SPI enabled in $CONFIG_TXT -- a reboot is required"
    fi
fi

log "Creating the service account"
if ! id -u "$SERVICE_USER" >/dev/null 2>&1; then
    useradd --system --home-dir "$PREFIX" --shell /usr/sbin/nologin "$SERVICE_USER"
fi
# Group membership is what lets the station touch SPI, GPIO and the panel
# without running as root.
for group in gpio spi video render input; do
    getent group "$group" >/dev/null 2>&1 && usermod -aG "$group" "$SERVICE_USER" || true
done

# ------------------------------------------------------------ directories
log "Creating directories"
install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0750 "$DATA_DIR" "$LOG_DIR" "$DATA_DIR/exports"
# Firmware belongs somewhere the station account can read.  A .hex left in an
# operator's home directory is unreadable by the service user and the project
# will never program.
install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0755 "$DATA_DIR/firmware"
install -d -m 0755 "$CONFIG_DIR" "$PREFIX"

# ------------------------------------------------------------ application
log "Installing the application into $PREFIX"
rsync -a --delete \
    --exclude '.git' --exclude '__pycache__' --exclude '*.pyc' \
    --exclude '.pytest_cache' --exclude 'venv' \
    "$SOURCE_DIR"/ "$PREFIX"/

log "Building the virtual environment"
# --system-site-packages picks up the apt-installed PyQt5, which is far better
# tested on the Pi than the wheel.
python3 -m venv --system-site-packages "$PREFIX/venv"
"$PREFIX/venv/bin/pip" install --quiet --upgrade pip
"$PREFIX/venv/bin/pip" install --quiet "$PREFIX"
"$PREFIX/venv/bin/pip" install --quiet openpyxl
# lgpio is the GPIO driver on Trixie; the station falls back to gpiozero or
# simulation if it will not build.
"$PREFIX/venv/bin/pip" install --quiet lgpio || warn "lgpio unavailable; will fall back to gpiozero"

chown -R "$SERVICE_USER:$SERVICE_USER" "$PREFIX"

# ---------------------------------------------------------- configuration
log "Installing configuration"
# avrdude 7.x takes the reset line in the port string, so the old config
# fragment is obsolete -- and redefining the programmer makes avrdude warn.
rm -f "$CONFIG_DIR/avrdude-linuxspi.conf"

if [[ -f "$CONFIG_DIR/station.yaml" ]]; then
    log "Keeping the existing $CONFIG_DIR/station.yaml"
    install -m 0644 "$SOURCE_DIR/config/station.yaml" "$CONFIG_DIR/station.yaml.new"
    log "New template written to station.yaml.new -- diff it for new options"
else
    install -m 0644 "$SOURCE_DIR/config/station.yaml" "$CONFIG_DIR/station.yaml"
fi

# ------------------------------------------------------------- first boot
log "Preparing the database"
sudo -u "$SERVICE_USER" \
    PROGSTATION_CONFIG="$CONFIG_DIR/station.yaml" \
    "$PREFIX/venv/bin/progstation" init

# --------------------------------------------------------------- services
log "Installing systemd units"
if [[ -f "$CONFIG_DIR/display.env" ]]; then
    log "Keeping the existing $CONFIG_DIR/display.env"
else
    install -m 0644 "$SOURCE_DIR/deploy/progstation.env" "$CONFIG_DIR/display.env"
    # Default to whatever this machine actually is: a Pi running the desktop
    # needs xcb, because linuxfb would fight the X server for the screen.
    if ! systemctl is-active --quiet graphical.target 2>/dev/null \
       && [[ ! -e /tmp/.X11-unix/X0 ]]; then
        sed -i 's/^QT_QPA_PLATFORM=xcb/QT_QPA_PLATFORM=linuxfb/' "$CONFIG_DIR/display.env"
        log "No desktop session detected - display.env set to linuxfb"
    else
        log "Desktop session detected - display.env set to xcb"
    fi
fi
install -d -m 0755 "$PREFIX/deploy"
install -m 0644 "$SOURCE_DIR/deploy/progstation-kiosk.desktop" "$PREFIX/deploy/" 2>/dev/null || true
install -m 0755 "$SOURCE_DIR/deploy/kiosk-setup.sh" "$PREFIX/deploy/" 2>/dev/null || true
install -m 0644 "$SOURCE_DIR/deploy/progstation.service"        /etc/systemd/system/
install -m 0644 "$SOURCE_DIR/deploy/progstation-backup.service" /etc/systemd/system/
install -m 0644 "$SOURCE_DIR/deploy/progstation-backup.timer"   /etc/systemd/system/
systemctl daemon-reload
systemctl enable progstation.service
log "Backup timer left disabled; enable it once the share is configured:"
log "    systemctl enable --now progstation-backup.timer"

# ------------------------------------------------------------ self-check
log "Running the self-test"
sudo -u "$SERVICE_USER" \
    PROGSTATION_CONFIG="$CONFIG_DIR/station.yaml" \
    "$PREFIX/venv/bin/progstation" --simulate selftest || warn "self-test reported problems"

cat <<'DONE'

Installation complete.

  Configuration : /etc/progstation/station.yaml
  Database      : /var/lib/progstation/progstation.db
  Logs          : /var/log/progstation/progstation.log

Next steps:
  1. Edit /etc/progstation/station.yaml (station id, backup share).
  2. Start the station:      systemctl start progstation
  3. Log in with the admin account printed above and change the password.
  4. Add your products under Settings > Projects.

If SPI was just enabled, reboot before programming real hardware.
DONE
