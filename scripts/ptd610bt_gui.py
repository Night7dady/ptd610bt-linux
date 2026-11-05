#!/usr/bin/env python3
"""Open the native LBX editor. No arguments or file opening ever print."""
from __future__ import annotations
import argparse
from pathlib import Path
import sys


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("lbx", nargs="?", type=Path, help="Optional LBX to open read-only")
    parser.add_argument("--mac", default="", help="Paired printer address, kept only for this session")
    parser.add_argument("--channel", type=int, default=2)
    args = parser.parse_args(argv)
    if not 1 <= args.channel <= 30:
        parser.error("--channel must be between 1 and 30")
    # Only our fixed checkout root is added; the caller's working directory is ignored.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    try:
        from PySide6.QtWidgets import QApplication
        from editor.window import MainWindow
    except ImportError as exc:
        print(f"GUI dependency unavailable: {exc}\nFedora: sudo dnf install python3-pyside6", file=sys.stderr)
        return 2
    app = QApplication(sys.argv[:1])
    app.setApplicationName("LBX Label Studio")
    app.setOrganizationName("ptd610bt-linux")
    window = MainWindow()
    # Window exposes controls; these values are never saved to a user config file.
    window.mac_input.setText(args.mac)
    window.channel_input.setValue(args.channel)
    if args.lbx:
        window.open_path(args.lbx)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
