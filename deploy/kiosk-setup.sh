#!/usr/bin/env bash
#
# Turn this Pi into a dedicated programming station: it boots straight into
# the application with no desktop behind it.
#
#   sudo ./kiosk-setup.sh          enable kiosk mode
#   sudo ./kiosk-setup.sh --revert restore the normal desktop
#
# Rather than trying to lock down a full desktop -- taskbar, right-click menu,
# file manager, Alt-Tab, all of which have to be disabled individually and any
# one of which is a way out -- this replaces the session. X starts with the
# station as its only client. There is nothing to switch to because nothing
# else is running.
#
# Leaving the application still needs an administrator password; that is
# enforced by the application itself, not by this script.

set -euo pipefail

PREFIX=/opt/progstation
KIOSK_USER=progstation
SERVICE=progstation-kiosk.service

log()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m==>\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m==>\033[0m %s\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "run this as root (sudo ./kiosk-setup.sh)"

# --------------------------------------------------------------- revert
if [[ "${1:-}" == "--revert" ]]; then
    log "Restoring the normal desktop"
    systemctl disable --now "$SERVICE" 2>/dev/null || true
    rm -f "/etc/systemd/system/$SERVICE"
    rm -f /etc/X11/xorg.conf.d/50-progstation-kiosk.conf
    systemctl daemon-reload
    systemctl set-default graphical.target
    log "Done. Reboot to return to the desktop."
    exit 0
fi

# ------------------------------------------------------------- packages
log "Installing the minimal X stack"
apt-get update -qq
# matchbox-window-manager keeps the window fullscreen and undecorated, and
# offers no menus, no taskbar and no way to raise another window.
apt-get install -y --no-install-recommends \
    xserver-xorg xinit x11-xserver-utils matchbox-window-manager unclutter

id -u "$KIOSK_USER" >/dev/null 2>&1 || die "user '$KIOSK_USER' not found; run install.sh first"
# Starting X needs console access.
for group in tty video input render; do
    getent group "$group" >/dev/null 2>&1 && usermod -aG "$group" "$KIOSK_USER" || true
done

# ---------------------------------------------------------- X session
log "Writing the kiosk session"
install -d -o "$KIOSK_USER" -g "$KIOSK_USER" -m 0755 "/home/$KIOSK_USER" 2>/dev/null || true
usermod -d "/home/$KIOSK_USER" "$KIOSK_USER" 2>/dev/null || true

cat > "/home/$KIOSK_USER/.xinitrc" <<'SESSION'
#!/bin/sh
# The station's entire X session. No desktop, no panel, no file manager.
xset s off          # no screen blanking
xset -dpms          # no power management
xset s noblank
unclutter -idle 3 & # hide the pointer on a touch panel
matchbox-window-manager -use_titlebar no -use_cursor no &
exec /opt/progstation/venv/bin/progstation gui --kiosk
SESSION
chmod 0755 "/home/$KIOSK_USER/.xinitrc"
chown "$KIOSK_USER:$KIOSK_USER" "/home/$KIOSK_USER/.xinitrc"

# Block Ctrl+Alt+F1..F6. Without this an operator can reach a login console.
install -d -m 0755 /etc/X11/xorg.conf.d
cat > /etc/X11/xorg.conf.d/50-progstation-kiosk.conf <<'XORG'
Section "ServerFlags"
    Option "DontVTSwitch" "true"
    Option "DontZap"      "true"
EndSection
XORG

# --------------------------------------------------------------- service
log "Installing $SERVICE"
cat > "/etc/systemd/system/$SERVICE" <<UNIT
[Unit]
Description=RPi Sensor Programming Station (kiosk session)
After=systemd-user-sessions.service plymouth-quit-wait.service
Conflicts=getty@tty1.service

[Service]
User=$KIOSK_USER
Group=$KIOSK_USER
PAMName=login
WorkingDirectory=/home/$KIOSK_USER
Environment=PROGSTATION_CONFIG=/etc/progstation/station.yaml
# startx owns tty1; the application is the session's only client.
ExecStart=/usr/bin/startx -- vt1 -keeptty -nolisten tcp
Restart=always
RestartSec=3
TTYPath=/dev/tty1
TTYReset=yes
TTYVHangup=yes
StandardInput=tty
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
# The station is the graphical session, so the desktop target must not also run.
systemctl set-default multi-user.target
systemctl disable --now lightdm 2>/dev/null || true
systemctl enable "$SERVICE"

cat <<DONE

Kiosk mode configured.

  Session      : /home/$KIOSK_USER/.xinitrc
  Service      : $SERVICE
  Boot target  : multi-user (no desktop)

Reboot to start it. After the reboot:

  * the station fills the screen with nothing behind it
  * Ctrl+Alt+F1..F6 are blocked
  * leaving needs an administrator password, in the application

To get a shell for maintenance, use SSH -- or run:

    sudo $PREFIX/deploy/kiosk-setup.sh --revert && sudo reboot

DONE
