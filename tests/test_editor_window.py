import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tempfile
import unittest
from unittest.mock import patch
from PySide6.QtCore import QObject, Signal, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox
from editor.document import LabelDocument
from editor.window import MainWindow


class FakePrinter(QObject):
    busyChanged = Signal(bool)
    message = Signal(str)
    finished = Signal(bool, str)
    def __init__(self):
        super().__init__()
        self.busy = False
        self.calls = []
    def query(self, mac, channel=2):
        self.calls.append(("query", mac, channel))
        return True
    def print_label(self, image, mac, channel=2):
        self.calls.append(("print", image.copy(), mac, channel))
        self.busy = True
        self.busyChanged.emit(True)
        return True


class WindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
    def setUp(self):
        self.printer = FakePrinter()
        self.window = MainWindow(printer=self.printer)
        self.error_patch = patch.object(self.window, "show_error")
        self.errors = self.error_patch.start()
    def tearDown(self):
        self.printer.busy = False
        self.printer.busyChanged.emit(False)
        with patch.object(self.window, "confirm_discard", return_value=True):
            self.window.close()
        self.error_patch.stop()
    def test_startup_has_preview_without_printer_contact(self):
        self.assertEqual(self.printer.calls, [])
        self.assertFalse(self.window.preview_label.pixmap().isNull())
        self.assertEqual(self.window.mac_edit.text(), "")
        self.assertFalse(self.window.print_button.isEnabled())
    def test_text_apply_updates_preview_and_undo(self):
        old = self.window.document.to_bytes()
        self.window.text_edit.setPlainText("Changed")
        self.assertTrue(self.window.apply_selected())
        self.assertEqual(self.window.document.objects()[0].text, "Changed")
        self.assertTrue(self.window.document.dirty)
        self.window.undo_document()
        self.assertEqual(self.window.document.to_bytes(), old)
        self.window.redo_document()
        self.assertEqual(self.window.document.objects()[0].text, "Changed")
    def test_open_failure_keeps_current_document(self):
        old = self.window.document
        with tempfile.TemporaryDirectory() as folder:
            bad = Path(folder) / "invalid.lbx"
            bad.write_bytes(b"not a zip")
            with patch.object(self.window, "confirm_discard", return_value=True):
                self.assertFalse(self.window.open_path(bad))
        self.assertIs(self.window.document, old)
        self.errors.assert_called()
    def test_save_as_includes_pending_text_and_refuses_existing(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder) / "new.lbx"
            self.window.text_edit.setPlainText("Saved")
            self.assertTrue(self.window.save_as_path(out))
            self.assertEqual(LabelDocument.load(out).objects()[0].text, "Saved")
            before = out.read_bytes()
            self.assertFalse(self.window.save_as_path(out))
            self.assertEqual(out.read_bytes(), before)
    def test_print_uses_current_pending_edits_and_locks_busy_ui(self):
        self.window.mac_edit.setText("00:00:00:00:00:00")
        self.window.text_edit.setPlainText("Print")
        self.assertTrue(self.window.print_current())
        self.assertEqual(self.window.document.objects()[0].text, "Print")
        self.assertEqual(len(self.printer.calls), 1)
        self.assertFalse(self.window.print_button.isEnabled())
        self.assertFalse(self.window.open_action.isEnabled())
        self.assertFalse(self.window.print_current())
        self.assertEqual(len(self.printer.calls), 1)
    def test_invalid_mac_blocks_printer_query_and_job(self):
        self.window.mac_edit.setText("bad")
        self.assertFalse(self.window.query_printer())
        self.assertFalse(self.window.print_current())
        self.assertEqual(self.printer.calls, [])
    def test_busy_close_is_ignored(self):
        self.printer.busy = True
        self.printer.busyChanged.emit(True)
        self.window.show()
        self.assertFalse(self.window.close())
        self.assertTrue(self.window.isVisible())
    def test_cancelled_save_prevents_dirty_navigation(self):
        self.window.text_edit.setPlainText("Unsaved")
        old = self.window.document
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Save), \
             patch.object(self.window, "save_as_dialog", return_value=False):
            self.assertFalse(self.window.new_document())
        self.assertIs(self.window.document, old)
        self.assertEqual(self.window.text_edit.toPlainText(), "Unsaved")
    def test_failed_apply_does_not_print(self):
        self.window.mac_edit.setText("00:00:00:00:00:00")
        self.window.text_edit.setPlainText("X" * 4097)
        self.assertFalse(self.window.print_current())
        self.assertEqual(self.printer.calls, [])
    def test_combined_invalid_form_changes_are_atomic(self):
        before = self.window.document.to_bytes()
        self.window.text_edit.setPlainText("Atomic")
        self.window.x_spin.setValue(79)
        self.assertFalse(self.window.apply_selected())
        self.assertEqual(self.window.document.to_bytes(), before)
        self.assertEqual(self.window.text_edit.toPlainText(), "Atomic")
        self.assertEqual(self.window.x_spin.value(), 79)
    def test_selection_commits_pending_changes_without_losing_target(self):
        self.window.add_text()
        second = self.window.document.objects()[1].id
        self.window.text_edit.setPlainText("Second")
        self.window.object_list.setCurrentRow(0)
        self.assertEqual(self.window.document.objects()[1].text, "Second")
        self.assertEqual(self.window._selected_id, self.window.document.objects()[0].id)
        self.window.object_list.setCurrentRow(1)
        self.assertEqual(self.window._selected_id, second)
        self.assertEqual(self.window.text_edit.toPlainText(), "Second")

    def _type_spin(self, spin, text):
        self.window.show()
        self.window.activateWindow()
        self.app.processEvents()
        editor = spin.lineEdit()
        editor.setFocus()
        editor.selectAll()
        if text:
            QTest.keyClicks(editor, text)
        else:
            QTest.keyClick(editor, Qt.Key.Key_Backspace)
        self.app.processEvents()
        self.assertTrue(editor.hasFocus())
        return editor

    def test_focused_length_keyboard_entry_is_printed_by_shortcut(self):
        self.window.mac_edit.setText("00:00:00:00:00:00")
        editor = self._type_spin(self.window.length_spin, "40")
        self.assertEqual(self.window.length_spin.value(), 35)
        self.assertAlmostEqual(self.window.document.length_mm, 35)
        self.assertIn("●", self.window.windowTitle())
        QTest.keyClick(editor, Qt.Key.Key_P, Qt.KeyboardModifier.ControlModifier)
        self.app.processEvents()
        self.assertEqual(len(self.printer.calls), 1)
        self.assertAlmostEqual(self.window.document.length_mm, 40)
        self.assertEqual(self.printer.calls[0][1].width, round(40 * 180 / 25.4))

    def test_focused_length_keyboard_entry_is_saved_without_focus_loss(self):
        self._type_spin(self.window.length_spin, "40")
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "keyboard-length.lbx"
            self.assertTrue(self.window.save_as_path(path))
            self.assertAlmostEqual(LabelDocument.load(path).length_mm, 40)
        self.assertFalse(self.window.document.dirty)
        self.assertNotIn("●", self.window.windowTitle())

    def test_focused_length_edit_blocks_close_when_cancelled(self):
        self._type_spin(self.window.length_spin, "40")
        self.assertFalse(self.window.document.dirty)
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Cancel) as question:
            self.assertFalse(self.window.close())
        question.assert_called_once()
        self.assertTrue(self.window.isVisible())
        self.assertIn("●", self.window.windowTitle())

    def test_focused_length_edit_blocks_new_and_open_when_cancelled(self):
        self._type_spin(self.window.length_spin, "40")
        previous = self.window.document
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Cancel) as question:
            self.assertFalse(self.window.new_document())
            self.assertFalse(self.window.open_path(Path("does-not-exist.lbx")))
        self.assertEqual(question.call_count, 2)
        self.assertIs(self.window.document, previous)

    def test_typed_length_clipping_failure_keeps_document_and_blocks_job(self):
        self.window.mac_edit.setText("00:00:00:00:00:00")
        previous = self.window.document.to_bytes()
        self._type_spin(self.window.length_spin, "5")
        self.assertFalse(self.window.print_current())
        self.assertEqual(self.window.document.to_bytes(), previous)
        self.assertEqual(self.printer.calls, [])
        self.assertIn("●", self.window.windowTitle())
        self.errors.assert_called()

    def test_incomplete_numeric_edit_is_not_silently_reverted_for_print(self):
        self.window.mac_edit.setText("00:00:00:00:00:00")
        for name in ["length_spin", "font_size", "x_spin"]:
            with self.subTest(field=name):
                self.window.refresh_document()
                before = self.window.document.to_bytes()
                spin = getattr(self.window, name)
                self._type_spin(spin, "")
                self.assertFalse(spin.hasAcceptableInput())
                self.assertIn("●", self.window.windowTitle())
                self.assertFalse(self.window.print_current())
                self.assertEqual(self.window.document.to_bytes(), before)
                self.assertEqual(self.printer.calls, [])

    def test_incomplete_channel_blocks_query_and_print(self):
        self.window.mac_edit.setText("00:00:00:00:00:00")
        self._type_spin(self.window.channel_spin, "")
        self.assertFalse(self.window.query_printer())
        self.assertFalse(self.window.print_current())
        self.assertEqual(self.printer.calls, [])

    def test_length_and_invalid_text_commit_is_atomic(self):
        previous = self.window.document
        original = previous.to_bytes()
        self.window.text_edit.setPlainText("W" * 4097)
        self._type_spin(self.window.length_spin, "40")
        self.assertFalse(self.window.apply_selected())
        self.assertIs(self.window.document, previous)
        self.assertEqual(self.window.document.to_bytes(), original)
        self.assertFalse(self.window.document.can_undo)

    def test_compound_length_and_text_apply_is_one_undo_step(self):
        original = self.window.document
        self._type_spin(self.window.length_spin, "40")
        self.window.text_edit.setPlainText("Changed")
        self.assertTrue(self.window.apply_selected())
        self.assertIs(self.window.document, original)
        self.assertAlmostEqual(self.window.document.length_mm, 40)
        self.assertEqual(self.window.document.objects()[0].text, "Changed")
        self.window.undo_document()
        self.assertAlmostEqual(self.window.document.length_mm, 35)
        self.assertEqual(self.window.document.objects()[0].text, "Label")
        self.assertFalse(self.window.document.can_undo)
        self.window.redo_document()
        self.assertAlmostEqual(self.window.document.length_mm, 40)
        self.assertEqual(self.window.document.objects()[0].text, "Changed")

    def test_length_growth_and_longer_text_commit_together(self):
        self._type_spin(self.window.length_spin, "70")
        # setPlainText does not transfer focus from the pending length editor.
        self.window.text_edit.setPlainText("W" * 22)
        self.assertTrue(self.window.apply_selected())
        self.assertAlmostEqual(self.window.document.length_mm, 70)
        self.assertEqual(self.window.document.objects()[0].text, "W" * 22)

    def test_object_selection_has_accessible_labels(self):
        self.assertTrue(self.window.text_edit.accessibleName())
        self.assertTrue(self.window.object_list.accessibleName())
        self.assertTrue(self.window.mac_edit.accessibleName())


if __name__ == "__main__":
    unittest.main()
