import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tempfile
import unittest
from unittest.mock import patch
from PySide6.QtCore import QCoreApplication, QEvent, QObject, QTimer, Signal, Qt
import shiboken6
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox, QDialog, QFileDialog, QPushButton
from PySide6.QtGui import QColor, QPalette
from PIL import Image
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
        with patch.object(self.window, "ask_save_changes", return_value=QMessageBox.StandardButton.Save), \
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
        with patch.object(self.window, "ask_save_changes", return_value=QMessageBox.StandardButton.Cancel) as question:
            self.assertFalse(self.window.close())
        question.assert_called_once()
        self.assertTrue(self.window.isVisible())
        self.assertIn("●", self.window.windowTitle())

    def test_focused_length_edit_blocks_new_and_open_when_cancelled(self):
        self._type_spin(self.window.length_spin, "40")
        previous = self.window.document
        with patch.object(self.window, "ask_save_changes", return_value=QMessageBox.StandardButton.Cancel) as question:
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

    def test_clear_text_removes_preview_and_allows_adding_again(self):
        self.window.text_edit.clear()
        self.assertTrue(self.window.apply_selected())
        self.assertEqual(self.window.document.objects(), [])
        self.assertTrue(self.window.document.is_blank)
        self.assertIsNone(self.window._selected_id)
        self.assertFalse(self.window.geometry_button.isEnabled())
        self.assertFalse(self.window.remove_button.isEnabled())
        self.window.mac_edit.setText("00:00:00:00:00:00")
        self.assertFalse(self.window.print_current())
        self.assertEqual(self.printer.calls, [])
        self.window.add_text()
        self.assertEqual(len(self.window.document.objects()), 1)
        self.assertFalse(self.window.document.is_blank)

    def test_blank_document_save_and_undo(self):
        self.window.remove_selected()
        self.assertTrue(self.window.document.is_blank)
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder) / "blank.lbx"
            self.assertTrue(self.window.save_as_path(out))
            self.assertTrue(LabelDocument.load(out).is_blank)
        self.window.undo_document()
        self.assertEqual(self.window.document.objects()[0].text, "Label")

    def test_autofit_tracks_window_and_splitter_and_preserves_aspect(self):
        self.window.show()
        self.window.resize(1100, 760)
        self.app.processEvents()
        self.window.fit_preview()
        self.app.processEvents()
        initial = self.window.preview_label.pixmap().size()
        self.window.resize(800, 560)
        self.window.splitter.setSizes([180, 230, 310])
        for _ in range(3):
            self.app.processEvents()
        viewport = self.window.preview_scroll.viewport().size()
        actual = self.window.preview_label.pixmap().size()
        self.assertLess(actual.width(), initial.width())
        self.assertLessEqual(actual.width(), viewport.width())
        self.assertLessEqual(actual.height(), viewport.height())
        expected_ratio = (self.window._raster.width + 28) / 85
        self.assertAlmostEqual(actual.width() / actual.height(), expected_ratio, delta=.15)
        self.assertLessEqual(self.window.width(), 800)
        self.assertLessEqual(self.window.height(), 560)
        self.window.properties_scroll.ensureWidgetVisible(self.window.geometry_button)
        self.app.processEvents()
        mapped = self.window.geometry_button.mapTo(self.window.properties_scroll.viewport(), self.window.geometry_button.rect().center())
        self.assertTrue(self.window.properties_scroll.viewport().rect().contains(mapped))

    def test_small_window_sidebar_controls_have_usable_width(self):
        self.window.show()
        self.window.resize(800, 560)
        for _ in range(3):
            self.app.processEvents()
        for scroll, content in [(self.window.objects_scroll, self.window.objects_panel),
                                (self.window.properties_scroll, self.window.properties_panel)]:
            usable_width = max(content.minimumWidth(), content.minimumSizeHint().width())
            self.assertGreaterEqual(scroll.viewport().width(), usable_width)
            self.assertEqual(scroll.horizontalScrollBar().maximum(), 0)
        for button, scroll in [(self.window.add_image_button, self.window.objects_scroll),
                               (self.window.apply_button, self.window.properties_scroll),
                               (self.window.geometry_button, self.window.properties_scroll)]:
            scroll.ensureWidgetVisible(button)
            self.app.processEvents()
            origin = button.mapTo(scroll.viewport(), button.rect().topLeft())
            self.assertGreaterEqual(origin.x(), 0)
            self.assertLessEqual(origin.x() + button.width(), scroll.viewport().width())
        pixmap = self.window.preview_label.pixmap()
        viewport = self.window.preview_scroll.viewport()
        self.assertLessEqual(pixmap.width(), viewport.width())
        self.assertLessEqual(pixmap.height(), viewport.height())
        self.assertEqual(self.window.width(), 800)

    def test_long_label_fits_below_one_hundred_percent_and_height(self):
        self.window.document.set_length(80)
        self.window.refresh_document()
        self.window.show()
        self.window.resize(800, 560)
        self.window.splitter.setSizes([170, 230, 320])
        self.window.fit_preview()
        for _ in range(3):
            self.app.processEvents()
        self.assertLess(self.window.zoom_slider.value(), 100)
        self.window.preview_scroll.setFixedSize(700, 75)
        for _ in range(3):
            self.app.processEvents()
        pixmap = self.window.preview_label.pixmap()
        self.assertLessEqual(pixmap.width(), self.window.preview_scroll.viewport().width())
        self.assertLessEqual(pixmap.height(), self.window.preview_scroll.viewport().height())

    def test_manual_zoom_opts_out_until_fit_clicked(self):
        self.window.show()
        self.app.processEvents()
        self.window.zoom_slider.setValue(150)
        manual_size = self.window.preview_label.pixmap().size()
        self.window.resize(850, 600)
        for _ in range(3):
            self.app.processEvents()
        self.assertEqual(self.window.preview_label.pixmap().size(), manual_size)
        self.window.fit_preview()
        for _ in range(3):
            self.app.processEvents()
        viewport = self.window.preview_scroll.viewport().size()
        actual = self.window.preview_label.pixmap().size()
        self.assertLessEqual(actual.width(), viewport.width())
        self.assertLessEqual(actual.height(), viewport.height())

    def test_light_window_and_dialog_palette_over_dark_desktop(self):
        old_palette = self.app.palette()
        dark = QPalette()
        for group in [QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive, QPalette.ColorGroup.Disabled]:
            for role in [QPalette.ColorRole.Window, QPalette.ColorRole.Base, QPalette.ColorRole.Button]:
                dark.setColor(group, role, QColor("#151515"))
            for role in [QPalette.ColorRole.Text, QPalette.ColorRole.WindowText, QPalette.ColorRole.ButtonText]:
                dark.setColor(group, role, QColor("#eeeeee"))
        self.app.setPalette(dark)
        window = MainWindow(printer=FakePrinter())
        try:
            window.show()
            self.app.processEvents()
            for widget in [window, window.query_button, window.font_size, window.text_edit]:
                for group in [QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive, QPalette.ColorGroup.Disabled]:
                    palette = widget.palette()
                    self.assertGreater(palette.color(group, QPalette.ColorRole.Button).lightness(), 180)
                    self.assertLess(palette.color(group, QPalette.ColorRole.ButtonText).lightness(), 130)
            dialog = window.make_message_box("标题", "文字")
            for widget in [dialog, *dialog.findChildren(QPushButton)]:
                self.assertGreater(widget.palette().color(QPalette.ColorRole.Window).lightness(), 180)
                self.assertLess(widget.palette().color(QPalette.ColorRole.WindowText).lightness(), 130)
            dialog.deleteLater()
            context = window.make_context_menu(window.text_edit)
            self.assertEqual(window.text_edit.contextMenuPolicy(), Qt.ContextMenuPolicy.CustomContextMenu)
            self.assertGreater(context.palette().color(QPalette.ColorRole.Window).lightness(), 180)
            self.assertLess(context.palette().color(QPalette.ColorRole.WindowText).lightness(), 130)
            context.deleteLater()
            with patch.object(QFileDialog, "exec", return_value=QDialog.DialogCode.Rejected):
                self.assertEqual(window.choose_file("选择标签", "LBX (*.lbx)"), "")
            file_dialog = window.findChildren(QFileDialog)[-1]
            self.assertTrue(file_dialog.testOption(QFileDialog.Option.DontUseNativeDialog))
            self.assertGreater(file_dialog.palette().color(QPalette.ColorRole.Base).lightness(), 180)
            self.assertLess(file_dialog.palette().color(QPalette.ColorRole.Text).lightness(), 130)
        finally:
            with patch.object(window, "confirm_discard", return_value=True):
                window.close()
            self.app.setPalette(old_palette)

    def test_crop_cancel_and_apply_and_undo(self):
        with tempfile.TemporaryDirectory() as folder:
            image = Path(folder) / "synthetic.png"
            Image.new("RGB", (100, 50), "black").save(image)
            identity = self.window.document.add_image(image)
            self.window.refresh_document(identity)
            before = self.window.document.to_bytes()
            with patch.object(self.window, "make_crop_dialog") as factory:
                factory.return_value.exec.return_value = QDialog.DialogCode.Rejected
                self.assertFalse(self.window.crop_selected_image())
            self.assertEqual(self.window.document.to_bytes(), before)
            with patch.object(self.window, "make_crop_dialog") as factory:
                factory.return_value.exec.return_value = QDialog.DialogCode.Accepted
                factory.return_value.selected_box.return_value = (0, 0, 50, 50)
                self.assertTrue(self.window.crop_selected_image())
            self.assertEqual(self.window.document.get_image(identity).size, (50, 50))
            self.window.undo_document()
            self.assertEqual(self.window.document.to_bytes(), before)

    def test_queued_resize_fit_is_cancelled_when_window_is_destroyed(self):
        window = MainWindow(printer=FakePrinter())
        window.show()
        self.app.processEvents()
        viewport = window.preview_scroll.viewport()
        viewport.resize(viewport.width() - 1, viewport.height() - 1)
        timer = window._fit_timer
        self.assertTrue(timer.isActive())
        with patch("sys.excepthook") as exceptions:
            shiboken6.delete(window)
            self.app.processEvents()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            self.app.processEvents()
        exceptions.assert_not_called()
        self.assertFalse(shiboken6.isValid(timer))

    def test_rejected_crop_dialogs_are_destroyed_and_style_remains_usable(self):
        from editor.crop_dialog import ImageCropDialog
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "synthetic.png"
            Image.new("RGB", (200, 100), "black").save(source)
            identity = self.window.document.add_image(source)
            self.window.refresh_document(identity)
        original = self.window.document.to_bytes()
        factory = self.window.make_crop_dialog
        def rejecting_dialog(image):
            dialog = factory(image)
            QTimer.singleShot(0, dialog, dialog.reject)
            return dialog
        with patch.object(self.window, "make_crop_dialog", side_effect=rejecting_dialog):
            for _ in range(4):
                self.assertFalse(self.window.crop_selected_image())
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                self.app.processEvents()
                self.assertEqual(self.window.findChildren(ImageCropDialog), [])
                self.assertTrue(shiboken6.isValid(self.window._fusion_style))
                self.assertEqual(self.window.document.to_bytes(), original)
        another = self.window.make_crop_dialog(self.window.document.get_image(identity))
        another.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.assertTrue(shiboken6.isValid(self.window._fusion_style))

    def test_message_and_file_dialogs_are_destroyed_after_use(self):
        factory = self.window.make_message_box
        def auto_close(*args, **kwargs):
            dialog = factory(*args, **kwargs)
            QTimer.singleShot(0, dialog, lambda: dialog.done(int(QMessageBox.StandardButton.Cancel)))
            return dialog
        with patch.object(self.window, "make_message_box", side_effect=auto_close):
            for _ in range(3):
                self.assertEqual(self.window.ask_save_changes(), QMessageBox.StandardButton.Cancel)
                MainWindow.show_error(self.window, "合成错误", ValueError("测试"))
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                self.assertEqual(self.window.findChildren(QMessageBox), [])
        with patch.object(QFileDialog, "exec", return_value=QDialog.DialogCode.Rejected):
            for _ in range(3):
                self.assertEqual(self.window.choose_file("选择标签", "LBX (*.lbx)"), "")
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                self.assertEqual(self.window.findChildren(QFileDialog), [])
        self.assertTrue(shiboken6.isValid(self.window._fusion_style))

    def test_object_selection_has_accessible_labels(self):
        self.assertTrue(self.window.text_edit.accessibleName())
        self.assertTrue(self.window.object_list.accessibleName())
        self.assertTrue(self.window.mac_edit.accessibleName())


if __name__ == "__main__":
    unittest.main()
