"""Offline Qt worker tests; no Bluetooth, settings or physical printing."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import sys
from pathlib import Path
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    from PySide6.QtWidgets import QApplication
    from PIL import Image
    from editor.printing import PrinterController, PrinterService
    from editor.backend import core
except ImportError as exc:
    if exc.name and (exc.name.startswith("PySide6") or exc.name.startswith("PIL")):
        raise unittest.SkipTest(f"GUI dependencies unavailable: {exc}")
    raise

APP = QApplication.instance() or QApplication([])
MAC = "00:00:00:00:00:00"


def drain(controller, timeout=3):
    deadline = time.monotonic() + timeout
    while controller.busy and time.monotonic() < deadline:
        APP.processEvents()
        time.sleep(0.005)
    APP.processEvents()
    if controller.busy:
        raise AssertionError("Worker did not finish")


class WorkerTests(unittest.TestCase):
    def test_invalid_address_and_geometry_never_contact_service(self):
        service = MagicMock()
        controller = PrinterController(service=service)
        self.assertFalse(controller.query("invalid"))
        self.assertFalse(controller.query(MAC, 0))
        self.assertFalse(controller.print_label(Image.new("1", (100, 63)), MAC))
        self.assertFalse(controller.busy)
        service.query.assert_not_called()
        service.print_label.assert_not_called()

    def test_one_job_only_and_image_is_snapshot(self):
        entered, release = threading.Event(), threading.Event()
        class Service:
            calls = 0
            pixel = None
            def print_label(self, image, mac, channel):
                self.calls += 1
                entered.set()
                release.wait(2)
                self.pixel = image.getpixel((0, 0))
        service = Service()
        controller = PrinterController(service=service)
        finished = []
        controller.finished.connect(lambda ok, msg: finished.append((ok, msg)))
        image = Image.new("1", (100, 64), 255)
        self.assertTrue(controller.print_label(image, MAC))
        self.assertTrue(controller.busy)
        self.assertFalse(controller.print_label(image, MAC))
        self.assertFalse(controller.query(MAC))
        image.putpixel((0, 0), 0)
        release.set()
        drain(controller)
        self.assertEqual(service.calls, 1)
        self.assertEqual(service.pixel, 255)
        self.assertTrue(finished[-1][0])

    def test_uncertain_print_is_visible_and_never_retried(self):
        service = MagicMock()
        service.print_label.side_effect = core.PrintOutcomeError("link lost")
        controller = PrinterController(service=service)
        results = []
        controller.finished.connect(lambda ok, msg: results.append((ok, msg)))
        self.assertTrue(controller.print_label(Image.new("1", (100, 64), 255), MAC))
        drain(controller)
        self.assertFalse(results[-1][0])
        self.assertIn("不确定", results[-1][1])
        self.assertEqual(service.print_label.call_count, 1)

    def test_status_failure_propagates_and_busy_resets(self):
        service = MagicMock()
        service.query.side_effect = OSError("connection refused")
        controller = PrinterController(service=service)
        results = []
        controller.finished.connect(lambda ok, msg: results.append((ok, msg)))
        self.assertTrue(controller.query(MAC))
        drain(controller)
        self.assertFalse(results[-1][0])
        self.assertIn("connection refused", results[-1][1])

    def test_service_closes_socket_and_preserves_verified_raster(self):
        sock, session = MagicMock(), MagicMock()
        service = PrinterService()
        image = Image.new("1", (100, 64), 255)
        with patch.object(core, "connect", return_value=sock), patch.object(core, "Session", return_value=session):
            service.print_label(image, MAC, 2)
        session.print_image.assert_called_once_with(image)
        sock.close.assert_called_once()

    def test_service_query_closes_socket_on_exception(self):
        sock, session = MagicMock(), MagicMock()
        session.query.side_effect = OSError("lost")
        with patch.object(core, "connect", return_value=sock), patch.object(core, "Session", return_value=session):
            with self.assertRaises(OSError):
                PrinterService().query(MAC, 2)
        sock.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
