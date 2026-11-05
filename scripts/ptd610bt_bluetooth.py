#!/usr/bin/env python3
"""PT-D610BT / Fedora Bluetooth diagnostic, limited to 12 mm laminated tape.

No arguments or ``preview`` ever print. ``print`` sends one bounded label and
never retries a job. No pairing, system configuration, SDK binaries or root are
needed. Run with ``python3 -I -B`` to avoid writing Python bytecode caches.

Commands (all paths are explicit; previews never overwrite existing files):
  status  --mac XX:XX:XX:XX:XX:XX --channel 2
  preview --text 'BT TEST' --output /tmp/new-label.png
  print   --mac XX:XX:XX:XX:XX:XX --text 'BT TEST' --trace

The default 30 mm is IMAGE length, not the final cut length. The current
small-margin job adds about 2 mm at each end and can cut off an additional
approximately 23 mm blank leader, as documented by Brother for PT-D610BT:
https://support.brother.com/g/b/faqend.aspx?c=us_ot&faqid=faqp00001293_017&lang=en&prod=d610bteus
Only 12 mm laminated TZe, normal resolution and a single page are supported.
Inspect the physical label as well as the completion response. On uncertain
outcomes, do not resend until you have checked what came out of the printer.

Protocol facts were checked against Brother China's iPrint&Label 5.3.17 APK
(SHA256 b803aed6adb54067325bb774013e9a032ce6d084655487e281f6b95006e753d0):
PT-D610BT profile in libcreatedata.so, PS_PT paper tables, BluetoothConnection,
PrintHelperBase and PrintStatusManager, with wire bit/column order refined
against this printer's physical samples. This is an independent diagnostic,
not an official Brother application or a complete Android port.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import io
import json
import math
import os
from pathlib import Path
import re
import socket
import struct
import sys
import time

from PIL import Image, ImageDraw, ImageFont, ImageOps


MODEL = 0x7C
HEAD_DOTS = 128
PRINT_DOTS = 64
DPI = 180
MAX_COLUMNS = math.ceil(80 * DPI / 25.4)
STATUS_REQUEST = b"\x1biS"
Image.MAX_IMAGE_PIXELS = 5_000_000


class DiagnosticError(Exception):
    """Invalid input, unready printer or communication failure before a job."""


class PrintOutcomeError(DiagnosticError):
    """A job may have been accepted; never automatically resend it."""


@dataclass(frozen=True)
class Status:
    model: int
    width: int
    media: int
    error1: int
    error2: int
    kind: int
    phase: int
    background: int
    foreground: int


def parse_status(data: bytes) -> Status:
    if len(data) != 32 or data[:4] != b"\x80\x20\x42\x30":
        raise DiagnosticError("Invalid Brother PT status frame; refusing to proceed")
    if data[4] != MODEL:
        raise DiagnosticError(f"Device model 0x{data[4]:02x} is not PT-D610BT (0x7c)")
    if data[18] not in (0, 1, 2, 4, 5, 6):
        raise DiagnosticError(f"Unrecognized status type 0x{data[18]:02x}")
    return Status(data[4], data[10], data[11], data[8], data[9],
                  data[18], data[19], data[24], data[25])


def require_ready(status: Status) -> None:
    if status.error1 or status.error2:
        raise DiagnosticError(f"Printer error bytes: {status.error1:02x} {status.error2:02x}")
    if status.kind != 0 or status.phase != 0:
        raise DiagnosticError("Printer is not reporting an idle status reply")
    if status.width != 12 or status.media != 1:
        raise DiagnosticError("This diagnostic requires 12 mm laminated TZe tape")


def packbits(data: bytes) -> bytes:
    """Encode TIFF PackBits (runs of 3 or more, literals of at most 128)."""
    result = bytearray()
    pos = 0
    while pos < len(data):
        end = pos + 1
        while end < min(pos + 128, len(data)) and data[end] == data[pos]:
            end += 1
        if end - pos >= 3:
            result.extend((257 - (end - pos), data[pos]))
            pos = end
            continue
        start = pos
        pos = end
        while pos < len(data) and pos - start < 128:
            if pos + 2 < len(data) and data[pos] == data[pos + 1] == data[pos + 2]:
                break
            pos += 1
        result.append(pos - start - 1)
        result.extend(data[start:pos])
    return bytes(result)


def raster_rows(image: Image.Image):
    if image.mode != "1" or image.height != PRINT_DOTS:
        raise DiagnosticError("Raster must be monochrome and exactly 64 dots high")
    if not 32 <= image.width <= MAX_COLUMNS:
        raise DiagnosticError("Label length must be between 32 dots and 80 mm")
    pixels = image.load()
    offset = (HEAD_DOTS - PRINT_DOTS) // 2
    # Tape feed reverses image-column order on this model. Send the right
    # edge first so the physical label agrees with the upright preview.
    for x in range(image.width - 1, -1, -1):
        row = bytearray(HEAD_DOTS // 8)
        for y in range(PRINT_DOTS):
            if not pixels[x, y]:
                # Reverse byte groups along the head, but keep the first
                # image pixel in bit 0. Reversing the entire dot number
                # instead also reverses each 8-dot block and corrupts text.
                byte = HEAD_DOTS // 8 - 1 - (offset + y) // 8
                row[byte] |= 1 << ((offset + y) % 8)
        yield bytes(row)


def build_job(image: Image.Image) -> bytes:
    rows = list(raster_rows(image))  # Validate the complete image before I/O.
    # 0x86: print information valid, media kind/width specified. Last page=2.
    information = b"\x1biz\x86\x01\x0c\x00" + struct.pack("<I", len(rows)) + b"\x02\x00"
    job = bytearray(bytes(200) + b"\x1b@\x1bia\x01")
    job += b"\x1bi!\x00"  # Enable asynchronous status notification.
    job += information
    job += b"\x1biM\x40"  # Automatic full cut.
    job += b"\x1biA\x01"  # Cut every page (only one page is sent).
    job += b"\x1biK\x08"  # Final feed and cut, normal resolution.
    job += b"\x1bid\x0e\x00"  # Approx. 2 mm requested margin at 180 dpi.
    job += b"M\x02"  # TIFF / PackBits compression.
    for row in rows:
        if not any(row):
            job += b"Z"
        else:
            compressed = packbits(row)
            job += b"G" + struct.pack("<H", len(compressed)) + compressed
    job += b"\x1a"  # Print final page with feeding.
    return bytes(job)


def connect(mac: str, channel: int, timeout: float):
    if not re.fullmatch(r"(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}", mac or ""):
        raise DiagnosticError("Specify a complete Bluetooth MAC address")
    if not 1 <= channel <= 30 or not math.isfinite(timeout) or not 0 < timeout <= 30:
        raise DiagnosticError("Channel must be 1..30; timeout must be finite and 0..30 seconds")
    sock = None
    try:
        sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
        sock.settimeout(timeout)
        sock.connect((mac, channel))
        return sock
    except (OSError, AttributeError) as exc:
        if sock is not None:
            sock.close()
        raise DiagnosticError(f"Cannot open RFCOMM channel {channel}: {exc}") from exc


class Session:
    def __init__(self, sock, timeout: float = 10, completion_timeout: float = 45,
                 on_status=None):
        self.sock = sock
        self.timeout = timeout
        self.completion_timeout = completion_timeout
        self.on_status = on_status

    def _frame(self, deadline: float) -> Status:
        data = bytearray()
        while len(data) < 32:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise DiagnosticError("Timed out waiting for a complete status frame")
            self.sock.settimeout(remaining)
            try:
                part = self.sock.recv(32 - len(data))
            except OSError as exc:
                raise DiagnosticError(f"Status receive failed: {exc}") from exc
            if not part:
                raise DiagnosticError("Bluetooth connection closed before a complete status frame")
            data.extend(part)
        status = parse_status(bytes(data))
        if self.on_status is not None:
            self.on_status(status)
        return status

    def query(self) -> Status:
        self.sock.settimeout(self.timeout)
        try:
            self.sock.sendall(STATUS_REQUEST)
        except OSError as exc:
            raise DiagnosticError(f"Cannot request printer status: {exc}") from exc
        deadline = time.monotonic() + self.timeout
        while True:
            status = self._frame(deadline)
            if status.error1 or status.error2 or status.kind == 2:
                raise DiagnosticError(f"Printer error bytes: {status.error1:02x} {status.error2:02x}")
            if status.kind == 0:
                return status
            if status.kind == 4:
                raise DiagnosticError("Printer is switching off")

    def print_image(self, image: Image.Image) -> Status:
        packet = build_job(image)
        require_ready(self.query())
        try:
            self.sock.settimeout(self.timeout)
            self.sock.sendall(packet)
            deadline = time.monotonic() + self.completion_timeout
            completed = False
            while True:
                status = self._frame(deadline)
                if status.error1 or status.error2 or status.kind in (2, 4):
                    raise DiagnosticError(f"Printer reported status {status.kind}, errors "
                                          f"{status.error1:02x} {status.error2:02x}")
                if status.width != 12 or status.media != 1:
                    raise DiagnosticError("Tape changed while the job was in progress")
                # PT-D610BT sends completion (01/01) before idle (06/00).
                # Both are required; idle alone is not proof a page printed.
                completed = completed or status.kind == 1
                if completed and status.phase == 0:
                    return status
        except (OSError, DiagnosticError, KeyboardInterrupt) as exc:
            raise PrintOutcomeError(
                f"Job transmission began; completion is not confirmed: {exc}. "
                "Check the physical label before retrying. No automatic retry was made."
            ) from exc


def render_label(text: str | None, image_path: str | None,
                 font_path: str | None = None, length_mm: float = 30) -> Image.Image:
    if not math.isfinite(length_mm) or not 8 <= length_mm <= 80:
        raise DiagnosticError("Image length must be between 8 and 80 mm, excluding feed margins")
    width = math.ceil(length_mm * DPI / 25.4)
    canvas = Image.new("L", (width, PRINT_DOTS), 255)
    if image_path:
        with Image.open(image_path) as source:
            if source.width * source.height > 5_000_000:
                raise DiagnosticError("Input image exceeds the diagnostic pixel limit")
            rgba = source.convert("RGBA")
        white = Image.new("RGBA", rgba.size, "white")
        white.alpha_composite(rgba)
        fitted = ImageOps.contain(white.convert("L"), (width - 8, PRINT_DOTS - 8))
        canvas.paste(fitted, ((width - fitted.width) // 2, (PRINT_DOTS - fitted.height) // 2))
    else:
        if not text or not text.strip() or len(text) > 1024:
            raise DiagnosticError("Supply 1..1024 nonblank text characters")
        if not font_path and not text.isascii():
            raise DiagnosticError("For Chinese/non-ASCII text, explicitly supply --font /path/to/font.ttf")
        draw = ImageDraw.Draw(canvas)
        for size in range(44, 5, -1):
            font = ImageFont.truetype(font_path, size) if font_path else ImageFont.load_default(size=size)
            box = draw.multiline_textbbox((0, 0), text, font=font, spacing=2, align="center")
            w, h = box[2] - box[0], box[3] - box[1]
            if w <= width - 8 and h <= PRINT_DOTS - 8:
                draw.multiline_text(((width - w) // 2 - box[0], (PRINT_DOTS - h) // 2 - box[1]),
                                    text, font=font, spacing=2, align="center", fill=0)
                break
        else:
            raise DiagnosticError("Text will not fit; shorten it or increase --length-mm")
    if not ImageOps.invert(canvas).getbbox():
        raise DiagnosticError("Refusing an entirely blank label")
    return canvas.convert("1", dither=Image.Dither.FLOYDSTEINBERG)


def save_preview(image: Image.Image, path: Path) -> None:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", dpi=(DPI, DPI))
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags, 0o600)
        with os.fdopen(fd, "wb") as output:
            output.write(buffer.getvalue())
    except OSError as exc:
        raise DiagnosticError(f"Cannot create new preview {path}; existing files are never overwritten: {exc}") from exc


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("status", "preview", "print"):
        sub = commands.add_parser(command)
        if command in ("status", "print"):
            sub.add_argument("--mac", required=True, help="Explicit address of your paired PT-D610BT")
            sub.add_argument("--channel", type=int, default=2, help="SPP channel from sdptool (this device: 2)")
            sub.add_argument("--timeout", type=float, default=10)
            sub.add_argument("--trace", action="store_true", help="Print decoded status frames to stderr")
        if command in ("preview", "print"):
            content = sub.add_mutually_exclusive_group(required=True)
            content.add_argument("--text")
            content.add_argument("--image", help="Read only the explicitly specified image")
            sub.add_argument("--font", help="Explicit font file for non-ASCII text")
            sub.add_argument("--length-mm", type=float, default=30,
                             help="Image length in mm, excluding printer feed margins (default: 30)")
        if command == "preview":
            sub.add_argument("--output", type=Path, required=True, help="New PNG path; existing files refused")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = make_parser().parse_args(argv)
    sock = None
    try:
        image = None
        if args.command in ("preview", "print"):
            image = render_label(args.text, args.image, args.font, args.length_mm)
        if args.command == "preview":
            save_preview(image, args.output)
            print(json.dumps({"preview": str(args.output.resolve()), "pixels": image.size,
                              "tape_mm": 12, "printer_contacted": False}))
            return 0
        sock = connect(args.mac, args.channel, args.timeout)
        def show_status(status):
            print("STATUS " + json.dumps(asdict(status)), file=sys.stderr, flush=True)

        session = Session(sock, args.timeout, on_status=show_status if args.trace else None)
        if args.command == "status":
            print(json.dumps({"model_name": "PT-D610BT", **asdict(session.query())}))
        else:
            print("Sending ONE label; automatic cutting enabled. Waiting for printer confirmation...", flush=True)
            status = session.print_image(image)
            print(json.dumps({"printer_confirmed_completion": True, "labels": 1, **asdict(status)}))
        return 0
    except PrintOutcomeError as exc:
        print(f"PRINT OUTCOME UNCERTAIN: {exc}", file=sys.stderr)
        return 3
    except (DiagnosticError, OSError, ValueError, Image.DecompressionBombError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Interrupted before job transmission", file=sys.stderr)
        return 130
    finally:
        if sock is not None:
            sock.close()


if __name__ == "__main__":
    raise SystemExit(main())
