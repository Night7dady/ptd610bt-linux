"""Synthetic crop dialog interaction tests; no files, devices or host settings."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog
from editor.crop_dialog import ImageCropDialog


class CropDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.image = Image.new("RGBA", (120, 80), "white")
        for x in range(30, 90):
            for y in range(20, 60):
                self.image.putpixel((x, y), (x, y, 0, 255))
        self.before = self.image.tobytes()
        self.dialog = ImageCropDialog(self.image)
        self.dialog.show()
        self.app.processEvents()

    def tearDown(self):
        self.dialog.close()
        self.app.processEvents()
        self.assertEqual(self.image.tobytes(), self.before)

    def drag_image(self, start, end):
        canvas = self.dialog.canvas
        a = canvas.image_to_widget(*start).toPoint()
        b = canvas.image_to_widget(*end).toPoint()
        QTest.mousePress(canvas, Qt.MouseButton.LeftButton, pos=a)
        QTest.mouseMove(canvas, b)
        QTest.mouseRelease(canvas, Qt.MouseButton.LeftButton, pos=b)
        self.app.processEvents()

    def assert_box_near(self, expected):
        for actual, wanted in zip(self.dialog.selected_box(), expected):
            self.assertLessEqual(abs(actual - wanted), 1)

    def test_starts_full_image_and_reset_restores_it(self):
        self.assertEqual(self.dialog.selected_box(), (0, 0, 120, 80))
        self.drag_image((30, 20), (90, 60))
        self.assert_box_near((30, 20, 90, 60))
        QTest.mouseClick(self.dialog.reset_button, Qt.MouseButton.LeftButton)
        self.assertEqual(self.dialog.selected_box(), (0, 0, 120, 80))

    def test_drag_coordinates_map_through_letterboxed_view(self):
        self.dialog.resize(900, 500)
        self.app.processEvents()
        self.drag_image((30, 20), (90, 60))
        self.assert_box_near((30, 20, 90, 60))
        box = self.dialog.selected_box()
        crop = self.image.crop(box)
        self.assertEqual(crop.size, (box[2] - box[0], box[3] - box[1]))
        self.assertEqual(crop.getpixel((1, 1)), self.image.getpixel((box[0] + 1, box[1] + 1)))

    def test_reverse_drag_is_normalized_and_outside_clamps(self):
        self.drag_image((90, 60), (30, 20))
        self.assert_box_near((30, 20, 90, 60))
        canvas = self.dialog.canvas
        a = canvas.image_to_widget(40, 30).toPoint()
        outside = QPoint(canvas.width() + 80, canvas.height() + 80)
        QTest.mousePress(canvas, Qt.MouseButton.LeftButton, pos=a)
        QTest.mouseMove(canvas, outside)
        QTest.mouseRelease(canvas, Qt.MouseButton.LeftButton, pos=outside)
        self.assert_box_near((40, 30, 120, 80))

    def test_numeric_selection_acceptance_and_input_unchanged(self):
        self.dialog.left_spin.setValue(30)
        self.dialog.top_spin.setValue(20)
        self.dialog.right_spin.setValue(90)
        self.dialog.bottom_spin.setValue(60)
        self.assertEqual(self.dialog.selected_box(), (30, 20, 90, 60))
        self.assertTrue(self.dialog.apply_button.isEnabled())
        self.dialog.accept()
        self.assertEqual(self.dialog.result(), QDialog.DialogCode.Accepted)

    def test_invalid_numeric_box_blocks_accept(self):
        self.dialog.left_spin.setValue(100)
        self.dialog.right_spin.setValue(50)
        self.assertFalse(self.dialog.apply_button.isEnabled())
        self.dialog.accept()
        self.assertNotEqual(self.dialog.result(), QDialog.DialogCode.Accepted)
        self.assertIn("边界", self.dialog.dimensions_label.text())

    def test_resize_keeps_roi_and_image_aspect(self):
        self.dialog.left_spin.setValue(20)
        self.dialog.right_spin.setValue(70)
        original = self.dialog.selected_box()
        for size in [(440, 480), (950, 700)]:
            self.dialog.resize(*size)
            self.app.processEvents()
            self.assertEqual(self.dialog.selected_box(), original)
            rect = self.dialog.canvas.image_rect()
            self.assertAlmostEqual(rect.width() / rect.height(), 1.5)

    def test_incomplete_numeric_input_blocks_accept_until_corrected(self):
        spin = self.dialog.left_spin
        spin.setFocus()
        spin.lineEdit().selectAll()
        QTest.keyClick(spin, Qt.Key.Key_Backspace)
        self.app.processEvents()
        self.assertFalse(self.dialog.apply_button.isEnabled())
        self.dialog.accept()
        self.assertNotEqual(self.dialog.result(), QDialog.DialogCode.Accepted)
        self.dialog.right_spin.setFocus()
        self.app.processEvents()
        self.dialog.accept()
        self.assertNotEqual(self.dialog.result(), QDialog.DialogCode.Accepted)
        self.dialog.reset_selection()
        self.assertTrue(self.dialog.apply_button.isEnabled())
        self.dialog.accept()
        self.assertEqual(self.dialog.result(), QDialog.DialogCode.Accepted)

    def test_valid_up_steps_repair_incomplete_numeric_input(self):
        spin = self.dialog.left_spin
        spin.setFocus()
        spin.lineEdit().selectAll()
        QTest.keyClick(spin, Qt.Key.Key_Backspace)
        self.assertFalse(self.dialog.apply_button.isEnabled())
        for _ in range(3):
            QTest.keyClick(spin, Qt.Key.Key_Up)
        self.app.processEvents()
        self.assertTrue(spin.hasAcceptableInput())
        self.assertGreater(spin.value(), 0)
        self.assertEqual(self.dialog.selected_box()[0], spin.value())
        self.assertTrue(self.dialog.apply_button.isEnabled())
        self.dialog.accept()
        self.assertEqual(self.dialog.result(), QDialog.DialogCode.Accepted)

    def test_cancel_has_no_source_changes(self):
        self.drag_image((20, 10), (70, 60))
        self.dialog.reject()
        self.assertEqual(self.dialog.result(), QDialog.DialogCode.Rejected)

    def test_invalid_source_is_rejected(self):
        for source in [Image.new("RGB", (0, 10)), Image.new("1", (2500, 2001)), "not an image"]:
            with self.subTest(source=type(source).__name__), self.assertRaises(ValueError):
                ImageCropDialog(source)


if __name__ == "__main__":
    unittest.main()
