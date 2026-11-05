"""Offline checks for the PT-D610BT diagnostic; never contact a printer."""

import contextlib
import importlib.util
import io
from pathlib import Path
import random
import socket
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts/ptd610bt_bluetooth.py"
spec = importlib.util.spec_from_file_location("ptd610_diag", MODULE_PATH)
diag = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = diag
spec.loader.exec_module(diag)


def frame(kind=0, phase=0, error=0, width=12, model=0x7C):
    data = bytearray.fromhex(
        "802042307c32000000000c010000000000000000000000000108000000000000"
    )
    data[4], data[8], data[10] = model, error, width
    data[18], data[19] = kind, phase
    return bytes(data)


def unpackbits(data):
    result = bytearray()
    pos = 0
    while pos < len(data):
        count = data[pos]
        pos += 1
        if count < 128:
            result.extend(data[pos:pos + count + 1])
            pos += count + 1
        elif count > 128:
            result.extend(data[pos:pos + 1] * (257 - count))
            pos += 1
    return bytes(result)


class FakeSocket:
    def __init__(self, incoming):
        self.incoming = list(incoming)
        self.sent = []
        self.closed = False

    def settimeout(self, value):
        self.timeout = value

    def sendall(self, data):
        self.sent.append(data)

    def recv(self, count):
        if not self.incoming:
            raise socket.timeout("test timeout")
        part = self.incoming.pop(0)
        if len(part) > count:
            self.incoming.insert(0, part[count:])
        return part[:count]

    def close(self):
        self.closed = True


class ProtocolTests(unittest.TestCase):
    def test_observed_status_matches_official_model(self):
        status = diag.parse_status(frame())
        self.assertEqual((status.model, status.width, status.media), (0x7C, 12, 1))
        self.assertEqual((status.background, status.foreground), (1, 8))
        diag.require_ready(status)

    def test_invalid_frames_and_wrong_model_fail_closed(self):
        for bad in (b"", frame()[:-1], b"bad" + frame()[3:], frame(model=0x78)):
            with self.subTest(bad=bad.hex()), self.assertRaises(diag.DiagnosticError):
                diag.parse_status(bad)

    def test_unready_printer_never_passes_preflight(self):
        for packet in (frame(error=1), frame(width=0), frame(width=24),
                       frame(phase=1), frame(kind=2), frame(kind=1)):
            with self.subTest(packet=packet.hex()), self.assertRaises(diag.DiagnosticError):
                diag.require_ready(diag.parse_status(packet))

    def test_packbits_roundtrip_boundaries_and_mixed_runs(self):
        rng = random.Random(610)
        samples = [b"", bytes(16), b"\xff" * 128, bytes(range(256)),
                   b"aaabccdddd" * 40]
        samples.extend(rng.randbytes(n) for n in (1, 2, 16, 127, 128, 129, 512))
        for raw in samples:
            with self.subTest(length=len(raw)):
                self.assertEqual(unpackbits(diag.packbits(raw)), raw)

    def test_raster_centers_64_dots_with_correct_bit_order(self):
        image = Image.new("1", (32, 64), 1)
        image.putpixel((0, 0), 0)
        rows = diag.raster_rows(image)
        expected = bytearray(16)
        # The first physical label reproduced an 8-dot block reversal when
        # this was 0x80. PT-D610BT requires the top pixel in bit 0 here.
        expected[11] = 0x01
        self.assertEqual(list(rows), [bytes(16)] * 31 + [bytes(expected)])

    def test_each_of_the_64_pixels_has_correct_wire_byte_and_bit(self):
        image = Image.new("1", (64, 64), 1)
        for y in range(64):
            image.putpixel((y, y), 0)
        for wire_column, row in enumerate(diag.raster_rows(image)):
            y = 63 - wire_column
            expected = bytearray(16)
            expected[11 - y // 8] = 1 << (y % 8)
            self.assertEqual(row, bytes(expected), f"wire position of pixel {y}")

    def test_feed_direction_does_not_mirror_an_asymmetric_label(self):
        # The second physical sample was complete but mirrored left/right.
        # The rightmost image column has to reach this printer first.
        image = Image.new("1", (32, 64), 1)
        image.putpixel((0, 0), 0)
        image.putpixel((31, 16), 0)
        rows = list(diag.raster_rows(image))
        self.assertEqual(rows[0][9], 1)
        self.assertEqual(rows[-1][11], 1)

    def test_job_has_compressed_rows_and_explicit_final_print_command(self):
        image = Image.new("1", (60, 64), 1)
        image.putpixel((10, 30), 0)
        packet = diag.build_job(image)
        self.assertTrue(packet.startswith(bytes(200) + b"\x1b@\x1bia\x01"))
        self.assertTrue(packet.endswith(b"\x1a"))
        self.assertIn(b"\x1biM\x40", packet)
        self.assertIn(b"\x1biK\x08", packet)
        info = b"\x1biz\x86\x01\x0c\x00" + struct.pack("<I", 60) + b"\x02\x00"
        self.assertIn(info, packet)
        self.assertIn(b"\x1bid\x0e\x00M\x02", packet)
        raster = packet.split(b"M\x02", 1)[1][:-1]
        rows = []
        while raster:
            if raster[0] == ord("Z"):
                rows.append(bytes(16))
                raster = raster[1:]
            else:
                self.assertEqual(raster[0], ord("G"))
                length = int.from_bytes(raster[1:3], "little")
                rows.append(unpackbits(raster[3:3 + length]))
                raster = raster[3 + length:]
        self.assertEqual(rows, list(diag.raster_rows(image)))

    def test_bad_image_geometry_cannot_be_sent(self):
        for image in (Image.new("1", (20, 70)), Image.new("1", (0, 64)),
                      Image.new("1", (1000, 64)), Image.new("RGB", (20, 64))):
            with self.subTest(size=image.size), self.assertRaises(diag.DiagnosticError):
                diag.build_job(image)

    def test_fragmented_status_and_notifications(self):
        raw = frame(kind=6, phase=1) + frame()
        sock = FakeSocket([raw[:3], raw[3:17], raw[17:]])
        session = diag.Session(sock, 0.2)
        self.assertEqual(session.query().kind, 0)
        self.assertEqual(sock.sent, [b"\x1biS"])

    def test_preflight_error_prevents_any_initialization_or_print(self):
        sock = FakeSocket([frame(error=1)])
        with self.assertRaises(diag.DiagnosticError):
            diag.Session(sock, 0.2).print_image(Image.new("1", (60, 64), 1))
        self.assertEqual(sock.sent, [b"\x1biS"])

    def test_only_explicit_completed_status_counts_as_print_success(self):
        sock = FakeSocket([frame(), frame(kind=6, phase=1), frame(kind=0), frame(kind=1)])
        result = diag.Session(sock, 0.2).print_image(Image.new("1", (60, 64), 1))
        self.assertEqual(result.kind, 1)
        self.assertEqual(len(sock.sent), 2)

    def test_real_device_completion_precedes_idle_phase_notification(self):
        # Actual PT-D610BT: 06/01 -> 01/01 -> 06/00.
        sock = FakeSocket([frame(), frame(kind=6, phase=1),
                           frame(kind=1, phase=1), frame(kind=6, phase=0)])
        result = diag.Session(sock, 0.05).print_image(Image.new("1", (60, 64), 1))
        self.assertEqual(result.phase, 0)
        self.assertEqual(len(sock.sent), 2)

    def test_idle_phase_without_completion_does_not_claim_success(self):
        sock = FakeSocket([frame(), frame(kind=6, phase=1), frame(kind=6, phase=0)])
        with self.assertRaises(diag.PrintOutcomeError):
            diag.Session(sock, 0.05).print_image(Image.new("1", (60, 64), 1))

    def test_unknown_print_outcome_never_retries(self):
        for incoming in ([frame(), frame(kind=0)], [frame(), b""],
                         [frame(), frame(kind=2, error=4)]):
            sock = FakeSocket(incoming)
            with self.subTest(incoming=incoming), self.assertRaises(diag.PrintOutcomeError):
                diag.Session(sock, 0.05).print_image(Image.new("1", (60, 64), 1))
            self.assertEqual(len(sock.sent), 2)

    def test_partial_send_failure_is_reported_as_unknown_not_retried(self):
        sock = FakeSocket([frame()])
        send = sock.sendall

        def fail_job(data):
            if data == b"\x1biS":
                send(data)
            else:
                sock.sent.append(data)
                raise BrokenPipeError("test partial write")

        sock.sendall = fail_job
        with self.assertRaises(diag.PrintOutcomeError):
            diag.Session(sock, 0.1).print_image(Image.new("1", (60, 64), 1))
        self.assertEqual(len(sock.sent), 2)

    def test_preview_cannot_overwrite_existing_file(self):
        with tempfile.TemporaryDirectory(prefix="ptd610-test-") as tmp:
            path = Path(tmp) / "preview.png"
            path.write_bytes(b"original")
            with self.assertRaises(diag.DiagnosticError):
                diag.save_preview(Image.new("1", (60, 64), 1), path)
            self.assertEqual(path.read_bytes(), b"original")

    def test_preview_cli_works_without_bluetooth(self):
        with tempfile.TemporaryDirectory(prefix="ptd610-test-") as tmp:
            path = Path(tmp) / "preview.png"
            with patch.object(diag, "connect", side_effect=AssertionError("no radio")):
                with contextlib.redirect_stdout(io.StringIO()):
                    code = diag.main(["preview", "--text", "BT TEST", "--output", str(path)])
            self.assertEqual(code, 0)
            with Image.open(path) as image:
                self.assertEqual(image.height, 64)

    def test_invalid_address_does_not_open_a_socket(self):
        with patch.object(socket, "socket", side_effect=AssertionError("no socket")):
            with self.assertRaises(diag.DiagnosticError):
                diag.connect("invalid", 2, 1)


if __name__ == "__main__":
    unittest.main()
