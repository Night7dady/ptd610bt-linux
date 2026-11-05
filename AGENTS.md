# ptd610bt-linux

Version 0.1.0 · Python 3.10+ / Pillow 10.1+ · No TCP service/port.
Bluetooth Classic SPP uses RFCOMM channel 2 by default.

## Purpose

Print one bounded label on Brother PT-D610BT over an already paired Linux Bluetooth
connection. Current support is 12 mm laminated TZe, 64-dot printable height, 180 dpi.
LBX support is intentionally limited; this is not an editor or official Brother SDK.

## Commands

```bash
./setup.sh --check
./setup.sh --desktop
python3 -I -B -m unittest discover -s tests -v
python3 -I -B scripts/ptd610bt_bluetooth.py --help
python3 -I -B scripts/ptd610bt_lbx.py --help
python3 -I -B tools/configure_desktop.py --help
```

`setup.sh` checks existing dependencies only. No package installation, downloads,
pairing, service changes, configuration installation or printing is automatic.
Tests are offline and use mocked sockets and synthetic in-memory labels.

## Architecture

```text
scripts/ptd610bt_bluetooth.py   status, raster conversion, PackBits, RFCOMM session
scripts/ptd610bt_lbx.py         bounded read-only LBX parser and renderer; imports core
tools/configure_desktop.py    new-file-only KDE configuration generator
tests/                       protocol, LBX and desktop generator tests
docs/USAGE.md                actual printing commands and result interpretation
docs/DEPLOYMENT.md           Fedora packages, desktop association and uninstall
```

The LBX renderer returns a monochrome raster to the verified core session.
The optional desktop configuration runs one explicit LBX print in Konsole.
Generated desktop files refer to a stable checkout path; they contain local MAC
configuration and must not be committed.

## Invariants

- Never print, pair, install packages or alter host associations as a test.
  Physical printing requires an explicit user instruction for that job.
- Preserve head-byte/bit order, reversed raster columns and completion-then-idle
  tracking. Existing tests encode physically verified behavior.
- Never retry a partly submitted job automatically. Return 3 on uncertain outcomes.
- User labels/images are read-only; preview output must be a new file.
- Reject unsupported LBX objects instead of dropping them silently.
- Do not commit user LBX files, photos, actual printer addresses, APKs, native SDK
  binaries, credentials, local configuration, caches or raw diagnostic traces.
- Keep MIME registration specific to application/x-brother-lbx; ZIP is only a parent.
  Never set the printing application as the default for application/zip.
- Use current native Codex model/tools. Do not rely on another Agent's private
  configuration, shared skills, external model gateway or shell startup files.

See CONTRIBUTING.md. No extra service, environment secret or .env file is required.
