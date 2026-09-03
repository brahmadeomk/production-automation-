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
> Section 7 has worked values for the common ATmega328P cases.

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

> **Updating a project changes only the fields you supply.** From the CLI,
> `project add --update` leaves every option you omit exactly as it was, and
> prints a `changed:` line naming what it touched. Check that line: it is your
> confirmation that nothing else moved.

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

## 7. Fuse reference — ATmega328P

Fuses configure the chip itself: clock source, brown-out detection, whether ISP
stays enabled. They are written once per unit, before the flash, and only when
the project defines them.

### 7.1 Do you need to set fuses at all?

A new controller has **no bootloader, and does not need one** — ISP writes flash
directly, which is why this station replaced Arduino IDE programming. A virgin
chip programs as-is.

Fuses matter for a different reason. A factory ATmega328P runs on its internal
8 MHz oscillator with CKDIV8 programmed, so it clocks at **1 MHz**. If your
board has a 16 MHz crystal and the firmware was built for `F_CPU=16000000`,
everything time-related — delays, baud rates, timers — runs 16× slow until the
clock fuses are set.

| Your board | Action |
|---|---|
| No crystal, firmware built for 8 MHz internal | Leave the fuse fields empty |
| 16 MHz crystal, firmware built for 16 MHz | Set the fuses in 8.2 |

### 7.2 16 MHz external crystal, no bootloader

| Fuse | Value | Meaning |
|---|---|---|
| Low | `0xFF` | 16 MHz crystal, no clock division |
| High | `0xD1` | No bootloader, ISP enabled, **EEPROM preserved on erase** |
| Extended | `0xFD` | Brown-out detect at 2.7 V |

**Low fuse `0xFF` = `1111 1111`**

| Bit | Name | Value | Effect |
|---|---|---|---|
| 7 | CKDIV8 | 1 | Not divided by 8 → full 16 MHz |
| 6 | CKOUT | 1 | No clock output on PB0 |
| 5–4 | SUT[1:0] | 11 | Slow rising power, 16K CK + 65 ms startup |
| 3–0 | CKSEL[3:0] | 1111 | Low-power crystal oscillator, 8–16 MHz |

CKDIV8 is the bit that matters most: the factory default has it programmed,
which is what holds a new chip at 1 MHz.

For a noisy environment or long crystal traces, `0xF7` selects the **full-swing**
oscillator instead — stronger drive, slightly more current.

**High fuse — choose deliberately**

| Value | Bits | EESAVE | Consequence |
|---|---|---|---|
| `0xD9` | `1101 1001` | Unprogrammed | A chip erase **wipes the EEPROM** |
| `0xD1` | `1101 0001` | Programmed | A chip erase **preserves the EEPROM** |

Both give: RESET enabled, ISP enabled, watchdog not forced on, and BOOTRST
unprogrammed so execution starts at address 0x0000 with no bootloader.

**Prefer `0xD1`.** The serial number and manufacturing block live in EEPROM.
With `0xD9`, anyone reflashing firmware later — in the field or during rework —
silently destroys the traceability record for that unit. Either value works for
a fresh unit on this station, because the cycle writes flash first (which
erases) and the EEPROM afterwards.

**Extended fuse `0xFD` = `1111 1101`**

Only bits 2–0 exist; the rest read back as 1. `101` sets brown-out detection at
2.7 V. Enable it: a sagging supply during an EEPROM write is the classic cause
of corrupted EEPROM, which is exactly the data that must not be lost. avrdude
may report this byte back as `0x05` — the same setting.

### 7.3 Proving fuses before production

Fuses are the one irreversible operation on this station. Test on a sacrificial
board before saving them into a project.

```bash
sudo -u progstation avrdude -p atmega328p -c linuxspi \
  -P /dev/spidev0.0:/dev/gpiochip0:25 -b 125000 \
  -U lfuse:w:0xFF:m -U hfuse:w:0xD1:m -U efuse:w:0xFD:m

sudo -u progstation avrdude -p atmega328p -c linuxspi \
  -P /dev/spidev0.0:/dev/gpiochip0:25 -b 200000 \
  -U lfuse:r:-:h -U hfuse:r:-:h -U efuse:r:-:h
```

Then confirm the board still answers ISP and that the firmware's timing is
correct. Only after that, enter the values in **Settings → Projects → Edit**.

The fuse write happens while the chip is still running at 1 MHz, so the ISP
clock must stay below 250 kHz. The station's default 200 kHz is inside that.

### 7.4 Recovering a chip that stopped responding

| What was set | Effect | Recovery |
|---|---|---|
| External crystal, none fitted or faulty | No clock, ISP dead | Feed a clock into XTAL1 from a signal generator or another MCU, then ISP responds again |
| `RSTDISBL` programmed | RESET pin repurposed as I/O | High-voltage programmer only |
| `SPIEN` unprogrammed | ISP disabled | High-voltage programmer only |

Never program `RSTDISBL` or clear `SPIEN` on a production board. Neither is
recoverable with this station.

---

## 8. Quick reference

| Symptom | First check |
|---|---|
| START is greyed out | A project is selected and its status is `ready`? Fixture closed? |
| Every board fails with `E_NO_DEVICE` | Fixture seating, target power, ISP cable |
| Serial number went backwards | Someone overrode the counter — check the audit log |
| Status bar says `SIMULATION` | The station is not programming real hardware; call maintenance |
| Status bar shows a backup failure | Network or share problem; production is unaffected |
