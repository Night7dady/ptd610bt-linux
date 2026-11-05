#!/usr/bin/env python3
"""Generate, without installing, KDE direct-print configuration for one printer."""

import argparse
import json
from pathlib import Path
import re
import sys


MIME = "application/x-brother-lbx"
APP_ID = "ptd610bt-lbx-print.desktop"
MIME_XML = '''<?xml version="1.0" encoding="UTF-8"?>
<mime-info xmlns="http://www.freedesktop.org/standards/shared-mime-info">
  <mime-type type="application/x-brother-lbx">
    <comment>Brother P-touch LBX label</comment>
    <comment xml:lang="zh_CN">Brother P-touch LBX 标签</comment>
    <sub-class-of type="application/zip"/>
    <glob pattern="*.lbx" weight="80"/>
    <icon name="printer"/>
  </mime-type>
</mime-info>
'''


def desktop_argument(value):
    """Quote one literal Exec argument, including Desktop Entry string escaping."""
    value = str(value)
    if not value or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError("Desktop paths must be nonempty and contain no control characters")
    # Field codes are expanded once after quoting; literal percent needs %%.
    value = value.replace("%", "%%")
    value = "".join("\\" + char if char in '\\"`$' else char for char in value)
    return '"' + value.replace("\\", "\\\\") + '"'


def desktop_entry(script, mac, channel=2, python=Path("/usr/bin/python3")):
    if not re.fullmatch(r"(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}", mac):
        raise ValueError("--mac must contain six hexadecimal pairs separated by colons")
    if not 1 <= channel <= 30:
        raise ValueError("RFCOMM channel must be between 1 and 30")
    script, python = Path(script), Path(python)
    if not script.is_absolute() or not python.is_absolute():
        raise ValueError("The script and Python paths must be absolute")
    command = ("/usr/bin/konsole --hold --separate -e " + desktop_argument(python)
               + " -I -B " + desktop_argument(script)
               + " print %f --mac " + mac.upper() + " --channel " + str(channel))
    return f'''[Desktop Entry]
Type=Application
Name=Print Brother LBX Label
Name[zh_CN]=打印 Brother LBX 标签（直接出纸）
Comment=Print one supported 12 mm LBX label directly over Bluetooth
Comment[zh_CN]=直接通过蓝牙打印一张支持的 12 mm LBX 标签
Exec={command}
TryExec=/usr/bin/konsole
Icon=printer
Terminal=false
StartupNotify=false
MimeType={MIME};
Categories=Utility;
'''


def generate(output, script, mac, channel=2, dry_run=False):
    """Create a new private output directory; never merge or replace files."""
    output = Path(output)
    contents = {"ptd610bt-lbx.xml": MIME_XML,
                APP_ID: desktop_entry(script, mac, channel)}
    if output.exists() or output.is_symlink():
        raise ValueError("--output-dir must not already exist")
    if not output.parent.is_dir():
        raise ValueError("The output directory's parent must already exist")
    if not dry_run:
        output.mkdir(mode=0o700)
        for name, content in contents.items():
            with (output / name).open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(content)
    return {"dry_run": dry_run, "installed": False, "printer_contacted": False,
            "files": [str(output / name) for name in contents]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mac", required=True, help="Your paired printer's Bluetooth address")
    parser.add_argument("--channel", type=int, default=2)
    parser.add_argument("--output-dir", type=Path, required=True, help="New directory; parent must exist")
    parser.add_argument("--dry-run", action="store_true", help="Validate and report; write nothing")
    args = parser.parse_args(argv)
    script = Path(__file__).resolve().parents[1] / "scripts" / "ptd610bt_lbx.py"
    try:
        if not script.is_file():
            raise ValueError("Keep this tool inside the complete project checkout")
        print(json.dumps(generate(args.output_dir, script, args.mac, args.channel, args.dry_run),
                         ensure_ascii=False))
        return 0
    except (ValueError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
