#!/usr/bin/env python3
"""Read-only LBX adapter for simple 12 mm landscape image/text labels.

Uses the separately verified Bluetooth diagnostic without changing that file.
Supports one auto-length sheet, embedded images, and unrotated Arial text runs.
Arial is rendered with the installed Liberation Sans fonts. This is a limited
renderer, not a full P-touch Editor implementation. Preview before printing.
The original LBX is never extracted, saved, rewritten, or uploaded.
"""

import argparse
import hashlib
import importlib.util
import io
import json
import math
from pathlib import Path, PurePosixPath
import re
import sys
import xml.etree.ElementTree as ET
import zipfile

from PIL import Image, ImageDraw, ImageFont, ImageOps


_core_spec = importlib.util.spec_from_file_location(
    "ptd610_lbx_core", Path(__file__).resolve().with_name("ptd610bt_bluetooth.py")
)
core = importlib.util.module_from_spec(_core_spec)
sys.modules[_core_spec.name] = core
_core_spec.loader.exec_module(core)

NS = {name: f"http://schemas.brother.info/ptouch/2007/lbx/{suffix}" for name, suffix in
      (("pt", "main"), ("style", "style"), ("text", "text"), ("image", "image"))}
SCALE = 10  # 180 dpi * 4x supersampling / 72 points per inch.
FONTS = Path("/usr/share/fonts/liberation-sans-fonts")


def pt(value):
    if not re.fullmatch(r"-?\d+(?:\.\d+)?pt", value or ""):
        raise core.DiagnosticError(f"Unsupported LBX point dimension: {value!r}")
    return float(value[:-2])


def needed(element, path):
    child = element.find(path, NS)
    if child is None:
        raise core.DiagnosticError(f"Missing LBX element: {path}")
    return child


def font_spec(info, fallback=(400, 8.0)):
    if info is None:
        return fallback
    log = needed(info, "text:logFont")
    ext = needed(info, "text:fontExt")
    if log.get("name", "Arial") not in ("Arial", "Liberation Sans"):
        raise core.DiagnosticError("Only Arial/Liberation Sans font metrics are supported")
    if log.get("italic", "false") != "false" or ext.get("effect", "NOEFFECT") != "NOEFFECT":
        raise core.DiagnosticError("Italic and effect text are not supported")
    if ext.get("underline", "0") != "0" or ext.get("strikeout", "0") != "0":
        raise core.DiagnosticError("Underlined and struck text are not supported")
    weight = int(log.get("weight", str(fallback[0])))
    size = pt(ext.get("size", f"{fallback[1]}pt"))
    if weight not in (400, 700) or not 4 <= size <= 24:
        raise core.DiagnosticError("Unsupported font weight or point size")
    return weight, size


def draw_text(canvas, obj, x, y, height, *, allow_empty=False):
    data = needed(obj, "pt:data").text or ""
    if (not data and not allow_empty) or len(data) > 4096 or any(ord(c) > 0xFFFF for c in data):
        raise core.DiagnosticError("Empty, excessive or non-BMP text is not supported")
    base = font_spec(needed(obj, "text:ptFontInfo"))
    styles = [base] * len(data)
    offset = 0
    for span in obj.findall("text:stringItem", NS):
        count = int(span.get("charLen", "0"))
        if count < 0 or offset + count > len(data):
            raise core.DiagnosticError("Invalid LBX text style span length")
        styles[offset:offset + count] = [font_spec(span.find("text:ptFontInfo", NS), base)] * count
        offset += count
    style = needed(obj, "text:textStyle")
    align = needed(obj, "text:textAlign")
    control = needed(obj, "text:textControl")
    if style.get("vertical", "false") != "false" or style.get("charSpace", "0") != "0":
        raise core.DiagnosticError("Vertical or custom character-spacing text is unsupported")
    if control.get("control") != "FREE" or control.get("autoLF", "false") != "false":
        raise core.DiagnosticError("Only free text without automatic wrapping is supported")
    if align.get("horizontalAlignment") not in ("LEFT", "JUSTIFY") or align.get("verticalAlignment") != "CENTER":
        raise core.DiagnosticError("Unsupported text alignment")
    line_factor = 1 + float(style.get("lineSpace", "0")) / 100
    if not 0.7 <= line_factor <= 2:
        raise core.DiagnosticError("Unsupported line spacing")
    if allow_empty and not data.strip():
        return
    fonts = {}
    for weight, size in set(styles):
        filename = "LiberationSans-Bold.ttf" if weight == 700 else "LiberationSans-Regular.ttf"
        fonts[(weight, size)] = ImageFont.truetype(str(FONTS / filename), round(size * SCALE))
    lines = [[]]
    for char, key in zip(data, styles):
        if char == "\n":
            lines.append([])
        elif lines[-1] and lines[-1][-1][1] == key:
            lines[-1][-1] = (lines[-1][-1][0] + char, key)
        else:
            lines[-1].append((char, key))
    line_height = max(sum(font.getmetrics()) for font in fonts.values()) * line_factor
    top, bottom = math.inf, -math.inf
    for index, runs in enumerate(lines):
        cursor = x
        for text, key in runs:
            box = fonts[key].getbbox(text, anchor="ls")
            if cursor + box[2] > canvas.width + 1:
                raise core.DiagnosticError("Text extends past the recorded auto-length label band")
            top = min(top, index * line_height + box[1])
            bottom = max(bottom, index * line_height + box[3])
            cursor += fonts[key].getlength(text)
    if not math.isfinite(top) or bottom - top > height + 1:
        raise core.DiagnosticError("Text does not fit vertically without clipping")
    baseline = y + (height - (bottom - top)) / 2 - top
    draw = ImageDraw.Draw(canvas)
    for index, runs in enumerate(lines):
        cursor = x
        for text, key in runs:
            draw.text((cursor, baseline + index * line_height), text, font=fonts[key], fill=0, anchor="ls")
            cursor += fonts[key].getlength(text)


def render_lbx(data, *, allow_empty=False):
    """Render supported LBX; empty output is permitted only for editor callers."""
    if len(data) > 10_000_000:
        raise core.DiagnosticError("LBX exceeds the diagnostic size limit")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            names = [entry.filename for entry in entries]
            if len(entries) > 64 or len(set(names)) != len(names) or sum(e.file_size for e in entries) > 20_000_000:
                raise core.DiagnosticError("Excessive or ambiguous LBX archive")
            xml = archive.read("label.xml")
            if b"<!DOCTYPE" in xml.upper() or b"<!ENTITY" in xml.upper():
                raise core.DiagnosticError("XML entities and document types are refused")
            root = ET.fromstring(xml)
            sheets = root.findall("pt:body/style:sheet", NS)
            if len(sheets) != 1:
                raise core.DiagnosticError("Exactly one LBX sheet is required")
            sheet = sheets[0]
            paper = needed(sheet, "style:paper")
            band = needed(sheet, "style:backGround")
            if paper.get("orientation") != "landscape" or paper.get("autoLength") != "true":
                raise core.DiagnosticError("Only auto-length landscape LBX labels are supported")
            if abs(pt(paper.get("width")) * 25.4 / 72 - 12) > 0.5:
                raise core.DiagnosticError("LBX tape width does not match the supported 12 mm tape")
            bx, by, bw, bh = (pt(band.get(key)) for key in ("x", "y", "width", "height"))
            pixels = round(bw * core.DPI / 72)
            if abs(bh * core.DPI / 72 - core.PRINT_DOTS) > 0.5 or not 32 <= pixels <= core.MAX_COLUMNS:
                raise core.DiagnosticError("Recorded LBX printable band does not fit the diagnostic limits")
            canvas = Image.new("L", (round(bw * SCALE), round(bh * SCALE)), 255)
            objects = list(needed(sheet, "pt:objects"))
            if not objects and not allow_empty:
                raise core.DiagnosticError("LBX contains no printable objects")
            for obj in objects:
                if obj.tag not in (f"{{{NS['text']}}}text", f"{{{NS['image']}}}image"):
                    raise core.DiagnosticError("Unsupported LBX object; refusing to omit it silently")
                geo = needed(obj, "pt:objectStyle")
                if geo.get("angle", "0") != "0" or geo.get("flip", "NONE") != "NONE":
                    raise core.DiagnosticError("Rotated or flipped objects are unsupported")
                x, y = (pt(geo.get("x")) - bx) * SCALE, (pt(geo.get("y")) - by) * SCALE
                width, height = pt(geo.get("width")) * SCALE, pt(geo.get("height")) * SCALE
                if min(x, y) < -0.01 or height <= 0 or width <= 0 or y + height > canvas.height + 1:
                    raise core.DiagnosticError("Object extends outside the printable band")
                if obj.tag == f"{{{NS['text']}}}text":
                    draw_text(canvas, obj, x, y, height, allow_empty=allow_empty)
                else:
                    image_style = needed(obj, "image:imageStyle")
                    name = image_style.get("fileName", "")
                    if PurePosixPath(name).is_absolute() or ".." in PurePosixPath(name).parts or name not in names:
                        raise core.DiagnosticError("Image must be embedded inside this LBX")
                    trim = image_style.find("image:trimming", NS)
                    if trim is not None and trim.get("flag", "false") != "false":
                        raise core.DiagnosticError("Cropped LBX image objects are unsupported")
                    if x + width > canvas.width + 1:
                        raise core.DiagnosticError("Image extends past the label band")
                    with Image.open(io.BytesIO(archive.read(name))) as source:
                        if source.width * source.height > 5_000_000:
                            raise core.DiagnosticError("Embedded image exceeds the pixel limit")
                        rgba = source.convert("RGBA")
                    background = Image.new("RGBA", rgba.size, "white")
                    background.alpha_composite(rgba)
                    resized = background.convert("L").resize((round(width), round(height)), Image.Resampling.LANCZOS)
                    canvas.paste(resized, (round(x), round(y)))
            raster = canvas.resize((pixels, core.PRINT_DOTS), Image.Resampling.LANCZOS).point(
                lambda value: 255 if value >= 128 else 0, mode="1"
            )
            if not allow_empty and not ImageOps.invert(raster.convert("L")).getbbox():
                raise core.DiagnosticError("LBX rendered an empty label")
            return raster, {"tape_mm": 12, "content_length_mm": bw * 25.4 / 72,
                            "objects": len(objects), "font_substitution": "Arial -> Liberation Sans",
                            "source_sha256": hashlib.sha256(data).hexdigest()}
    except (zipfile.BadZipFile, KeyError, ET.ParseError, ValueError, OSError) as exc:
        raise core.DiagnosticError(f"Cannot render LBX: {exc}") from exc


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("preview", "print"))
    parser.add_argument("lbx", type=Path)
    parser.add_argument("--output", type=Path, help="New preview PNG path")
    parser.add_argument("--mac", help="Explicit paired PT-D610BT address")
    parser.add_argument("--channel", type=int, default=2)
    args = parser.parse_args(argv)
    if args.command == "preview" and args.output is None:
        parser.error("preview requires --output")
    if args.command == "print" and not args.mac:
        parser.error("print requires --mac")
    sock = None
    try:
        with args.lbx.open("rb") as source:
            data = source.read(10_000_001)
        raster, info = render_lbx(data)
        if args.command == "preview":
            core.save_preview(raster, args.output)
            print(json.dumps({"preview": str(args.output.resolve()), **info}))
        else:
            sock = core.connect(args.mac, args.channel, 10)
            core.Session(sock).print_image(raster)
            print(json.dumps({"printer_confirmed_completion": True, "labels": 1, **info}))
        return 0
    except core.PrintOutcomeError as exc:
        print(f"PRINT OUTCOME UNCERTAIN: {exc}", file=sys.stderr)
        return 3
    except (core.DiagnosticError, OSError, Image.DecompressionBombError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130
    finally:
        if sock is not None:
            sock.close()


if __name__ == "__main__":
    raise SystemExit(main())
