# User Manual
### RPi Sensor Programming & Manufacturing Traceability Station

---

## 1. Who does what

| Role | Can do |
|---|---|
| **Operator** | Program boards, view history, view and export reports |
| **Administrator** | Everything an operator can, plus projects, EEPROM maps, firmware locations, users, serial counters, backup and the audit log |

The station is locked to a login. Every board programmed is recorded against the
operator who was signed in.

---

## 2. Daily operation

### 2.1 Signing in

1. The station boots straight into the login screen.
2. Tap the username field; an on-screen keyboard appears (there is no need for a
   physical keyboard).
3. Enter your password and tap **Sign in**.

If this is your first login, you are asked to set a new password before you can
continue.

### 2.2 Programming a board

1. On the **Production** screen, select the product from the drop-down.
   Check the line beneath it: MCU, firmware version, HW revision and variant.
2. Confirm the **Next serial number** shown in large digits is what you expect.
3. Seat the board in the ISP fixture and close it.
4. Press **START** (on screen, or the physical green start button if your
   station is configured for it).
5. Watch the banner:

| Banner | LED | Buzzer | Meaning |
|---|---|---|---|
| **PROGRAMMING** (orange) | green blinking | — | Cycle in progress — **do not remove the board** |
| **PASS** (green) | green steady | one short beep | Programmed and verified; serial number consumed |
| **FAIL** (red) | red steady | three beeps | Nothing was consumed; see the message |

6. On PASS, remove the board and load the next one. The counter has already
   advanced.
7. On FAIL, read the message, act on it (section 2.4), and retry. **The failed
   board does not use up a serial number** — the next attempt reuses it.

### 2.3 Understanding the shift counters

The bottom-left of the Production screen shows today's totals for the selected
product: PASS, FAIL and yield. They update after every cycle.

### 2.4 What the failure messages mean

| Message | Code | What to do |
|---|---|---|
| No sensor detected | `E_NO_DEVICE` | Reseat the board. Check fixture pins and that the board is powered. |
| Fixture is open | `E_FIXTURE_OPEN` | Close the fixture fully until the detect switch clicks. |
| Wrong MCU detected | `E_SIGNATURE_MISMATCH` | You have the wrong product selected, or the wrong board. Check both. |
| Firmware programming failed | `E_FLASH_PROGRAM` | Reseat and retry. If it repeats, call maintenance — usually a fixture contact. |
| Firmware verification failed | `E_FLASH_VERIFY` | Retry once. Repeats mean a marginal ISP connection or a bad target. |
| EEPROM write / verify failed | `E_EEPROM_PROGRAM` / `E_EEPROM_VERIFY` | Retry once, then quarantine the board and call maintenance. |
| Firmware file not found | `E_FIRMWARE_MISSING` | Call an administrator — the firmware path is wrong or the file is gone. |
| Serial number range exhausted | `E_SERIAL_RANGE` | Call an administrator to extend the range. |
| Database error | `E_DATABASE` | **Stop production** and call maintenance. Traceability is not being recorded. |

> A board that shows FAIL has **not** been given a serial number and has **no**
> production record claiming it passed. Handle it per your line's scrap or rework
> procedure.

---

## 3. History screen

Search everything the station has ever programmed.

**Filters:** serial number (full or partial), date range, operator, firmware
version, and PASS/FAIL. Tap **Search** to apply, **Clear** to reset.

- Double-tap any row for the complete record, including the exact EEPROM bytes
  written and the device signature read.
- **Export to Excel** writes the current filtered set to an `.xlsx` file in the
  export folder, with a second sheet recording which filters produced it.

---

## 4. Reports screen

Choose **Daily**, **Weekly** or **Monthly**, a date inside the period, and
optionally one product.

The five tiles show programmed count, PASS, FAIL, yield and average cycle time.
Below them:

- **By project** — per-product totals and yield
- **By operator** — operator statistics
- **Failures** — failure counts grouped by cause, worst first
- **By day** — the day-by-day trend inside the period

**Export to Excel** writes the whole report — every tab above plus the
underlying records — to one workbook.

---

## 5. Administrator: Settings

Only administrators see this tab.

### 5.1 Adding a product

**Settings → Projects → New project**

**General tab**

| Field | Notes |
|---|---|
| Project name | Shown to the operator; must be unique |
| MCU | avrdude part id, e.g. `atmega328p` |
| Expected signature | Optional but **strongly recommended** — this is what catches the wrong board in the fixture, e.g. `0x1e950f` |
| Firmware .hex | Use **Browse**. Keep firmware on the Pi, not a USB stick |
| HW revision, Product variant, Firmware version | Written into the EEPROM and stamped on every production record |
| Fuses / lock byte | Optional. Written before the flash, only if set |

> **Fuses are permanent.** A wrong clock-source fuse can brick the target so
> it can no longer be programmed over ISP. Verify against the device datasheet
> and prove it on a sacrificial board before releasing to production.

**Serial numbers tab** — prefix, range start, range end (0 = unlimited) and
digit count. `TS` + 6 digits from 1000 gives `TS001000`.

**EEPROM map tab** — either:

- Tick **Legacy layout** and give the serial number address and manufacturing
  date address. Only those bytes are touched.
- Or leave it unticked and edit the JSON map. **Load recommended 32-byte block**
  fills in the standard layout as a starting point.

Always press **Preview EEPROM block** before saving. It shows the exact bytes
and decodes each field. The **Status** column on the Projects list must read
`ready` before operators can run the product.

### 5.2 Changing a serial counter

**Settings → Projects → Set serial counter.** You are asked to confirm, and the
change is written to the audit log with the old and new values.

### 5.3 Users

**Settings → Users** — add users, reset passwords, change roles, enable and
disable accounts.

- Passwords are stored as salted PBKDF2-SHA256 hashes; nobody can read them back.
- The station refuses to delete or demote the last administrator.
- Five wrong passwords locks an account for ten minutes.

### 5.4 System

Shows station health and offers three buttons:

- **Run backup now** — immediate backup to the network share
- **Run self-test** — the acceptance checks from section 19 of the SRS
- **Test LEDs and buzzer** — exercises the panel and reports the fixture switch

### 5.5 Audit log

Every login, project change, user change and serial-counter override, with who
and when. Read-only by design.

---

## 6. End of shift

Nothing is required — every cycle is committed as it happens. If your site runs
scheduled backups, they happen automatically; check the backup status in the
status bar at the bottom of the screen.

To hand the station over, tap **Log out**. The station returns to the login
screen without restarting.

---

## 7. Quick reference

| Symptom | First check |
|---|---|
| START is greyed out | A project is selected and its status is `ready`? Fixture closed? |
| Every board fails with `E_NO_DEVICE` | Fixture seating, target power, ISP cable |
| Serial number went backwards | Someone overrode the counter — check the audit log |
| Status bar says `SIMULATION` | The station is not programming real hardware; call maintenance |
| Status bar shows a backup failure | Network or share problem; production is unaffected |
