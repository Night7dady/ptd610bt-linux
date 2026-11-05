"""Native Qt workspace for the deliberately bounded LBX document model."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import re

from PIL import Image
from PySide6.QtCore import Qt, QSignalBlocker, QTimer
from PySide6.QtGui import QAction, QFont, QImage, QKeySequence, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QDoubleSpinBox, QFileDialog,
    QFormLayout, QFrame, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMainWindow, QMessageBox, QProgressBar, QPushButton,
    QScrollArea, QSlider, QSpinBox, QSplitter, QTextEdit, QToolBar,
    QVBoxLayout, QWidget,
)

from .document import LabelDocument


class MainWindow(QMainWindow):
    """Edit an in-memory label; opening and previewing never contact a printer."""

    def __init__(self, document=None, printer=None):
        super().__init__()
        if printer is None:
            from .printing import PrinterController
            printer = PrinterController(self)
        self.printer = printer
        self.document = document if document is not None else LabelDocument.new()
        self._selected_id = None
        self._loading = False
        self._text_pending = False
        self._geometry_pending = False
        self._length_pending = False
        self._raster = None
        self._shown_once = False
        self.resize(1230, 800)
        self.setMinimumSize(1024, 680)
        self._build_ui()
        printer.busyChanged.connect(self._busy_changed)
        printer.message.connect(self.set_status)
        printer.finished.connect(self._print_finished)
        self.refresh_document()
        self.set_status("就绪 · 打开 LBX 或新建标签；预览与编辑不会连接打印机。")

    @property
    def busy(self):
        return bool(self.printer.busy)

    def _action(self, text, shortcut, callback):
        action = QAction(text, self)
        if shortcut:
            action.setShortcut(QKeySequence(shortcut))
        action.triggered.connect(callback)
        return action

    def _build_ui(self):
        self.setStyleSheet("""
            QWidget { color: #192b3b; }
            QMainWindow { background: #f3f5f7; }
            QToolBar { background: #e9eef5; border: 0; spacing: 6px; padding: 6px; }
            QToolButton { background: #edf1f6; color: #192b3b; min-height: 28px; padding: 3px 10px; }
            QToolButton:hover { background: #dce7f3; }
            QLineEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QListWidget { background: #ffffff; color: #192b3b; }
            QPushButton { background: #edf1f6; color: #192b3b; }
            QPushButton:disabled { color: #64748b; }
            QGroupBox { font-weight: 600; border: 1px solid #cbd3dd;
                border-radius: 6px; margin-top: 12px; padding: 12px; }
            QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 4px; }
            QPushButton { min-height: 28px; padding: 3px 10px; }
            QLineEdit, QSpinBox, QDoubleSpinBox { min-height: 26px; }
            QTextEdit, QListWidget { border: 1px solid #b7c4d3; border-radius: 4px; }
            QLineEdit:focus, QTextEdit:focus, QListWidget:focus {
                border: 2px solid #2367a8; }
            QPushButton:focus { border: 2px solid #2367a8; }
        """)
        bar = QToolBar("文件与编辑", self)
        bar.setMovable(False)
        bar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.addToolBar(bar)
        self.new_action = self._action("新建", "Ctrl+N", self.new_document)
        self.open_action = self._action("打开 LBX…", "Ctrl+O", self.open_dialog)
        self.save_action = self._action("另存为…", "Ctrl+Shift+S", self.save_as_dialog)
        self.save_action.setShortcuts([QKeySequence("Ctrl+S"), QKeySequence("Ctrl+Shift+S")])
        self.undo_action = self._action("撤销", "Ctrl+Z", self.undo_document)
        self.redo_action = self._action("重做", "Ctrl+Shift+Z", self.redo_document)
        self.redo_action.setShortcuts([QKeySequence("Ctrl+Shift+Z"), QKeySequence("Ctrl+Y")])
        for action in [self.new_action, self.open_action, self.save_action]:
            bar.addAction(action)
        bar.addSeparator()
        bar.addAction(self.undo_action)
        bar.addAction(self.redo_action)
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(20, 14, 20, 12)
        layout.setSpacing(12)
        heading = QLabel("LBX 标签工作室")
        font = QFont(); font.setPointSize(20); font.setBold(True)
        heading.setFont(font)
        layout.addWidget(heading)
        subtitle = QLabel("12 mm 覆膜 TZe · 简单图文布局 · 原文件只读，编辑结果另存为新 LBX")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.objects_panel = self._build_objects()
        self.preview_panel = self._build_preview()
        self.properties_panel = self._build_properties()
        for panel in [self.objects_panel, self.preview_panel, self.properties_panel]:
            self.splitter.addWidget(panel)
        self.splitter.setSizes([185, 640, 300])
        self.splitter.setStretchFactor(1, 1)
        layout.addWidget(self.splitter, 1)
        self.printer_panel = self._build_printer()
        layout.addWidget(self.printer_panel)
        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        self.status_label.setTextFormat(Qt.TextFormat.PlainText)
        self.status_label.setAccessibleName("操作与打印状态")
        self.status_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.status_label)
        self.setCentralWidget(root)
        self.statusBar().showMessage("所有编辑均在内存中进行")

    def _build_objects(self):
        panel = QGroupBox("标签对象")
        panel.setMinimumWidth(160)
        column = QVBoxLayout(panel)
        self.object_list = QListWidget()
        self.object_list.setAccessibleName("标签中的文字和图片对象")
        self.object_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.object_list.currentItemChanged.connect(self._selection_changed)
        column.addWidget(self.object_list, 1)
        self.add_text_button = QPushButton("＋ 添加文字")
        self.add_image_button = QPushButton("＋ 添加图片…")
        self.remove_button = QPushButton("移除所选对象")
        self.add_text_button.clicked.connect(self.add_text)
        self.add_image_button.clicked.connect(self.add_image_dialog)
        self.remove_button.clicked.connect(self.remove_selected)
        for button in [self.add_text_button, self.add_image_button, self.remove_button]:
            column.addWidget(button)
        return panel

    def _build_preview(self):
        panel = QGroupBox("打印预览")
        column = QVBoxLayout(panel)
        controls = QHBoxLayout()
        self.length_spin = QDoubleSpinBox()
        self.length_spin.setRange(5, 80)
        self.length_spin.setDecimals(2)
        self.length_spin.setSuffix(" mm")
        self.length_spin.setAccessibleName("标签内容长度（毫米）")
        self.length_spin.setKeyboardTracking(False)
        length_label = QLabel("内容长度")
        length_label.setBuddy(self.length_spin)
        controls.addWidget(length_label)
        controls.addWidget(self.length_spin)
        controls.addStretch()
        self.length_spin.valueChanged.connect(self._length_changed)
        self.length_spin.lineEdit().textEdited.connect(self._length_edited)
        column.addLayout(controls)
        self.preview_info = QLabel()
        self.preview_info.setWordWrap(True)
        column.addWidget(self.preview_info)
        self.preview_scroll = QScrollArea()
        self.preview_scroll.setWidgetResizable(True)
        self.preview_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.preview_scroll.setStyleSheet("QScrollArea { background: #e4eaf0; border-radius: 6px; }")
        self.preview_label = QLabel()
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setMargin(24)
        self.preview_label.setAccessibleName("当前标签的黑白打印光栅预览")
        self.preview_label.setStyleSheet("background: #e4eaf0;")
        self.preview_scroll.setWidget(self.preview_label)
        column.addWidget(self.preview_scroll, 1)
        zoom_row = QHBoxLayout()
        zoom_label = QLabel("缩放")
        self.zoom_slider = QSlider(Qt.Orientation.Horizontal)
        self.zoom_slider.setRange(100, 1000)
        self.zoom_slider.setValue(300)
        self.zoom_slider.setSingleStep(25)
        self.zoom_slider.setAccessibleName("预览缩放百分比")
        zoom_label.setBuddy(self.zoom_slider)
        self.zoom_value = QLabel("300%")
        self.zoom_value.setMinimumWidth(48)
        self.zoom_slider.valueChanged.connect(self._update_preview_pixmap)
        zoom_row.addWidget(zoom_label)
        zoom_row.addWidget(self.zoom_slider, 1)
        zoom_row.addWidget(self.zoom_value)
        self.fit_button = QPushButton("适合宽度")
        self.fit_button.clicked.connect(self.fit_preview)
        zoom_row.addWidget(self.fit_button)
        column.addLayout(zoom_row)
        hint = QLabel("预览含两端约 2 mm 留白；打印时还可能切下约 23 mm 空白前导带。")
        hint.setWordWrap(True)
        column.addWidget(hint)
        return panel

    def _spin(self, name, minimum=0, maximum=80):
        spin = QDoubleSpinBox()
        spin.setRange(minimum, maximum)
        spin.setDecimals(2)
        spin.setSuffix(" mm")
        spin.setAccessibleName(name)
        spin.valueChanged.connect(self._geometry_edited)
        spin.lineEdit().textEdited.connect(self._geometry_edited)
        return spin

    def _build_properties(self):
        panel = QGroupBox("对象属性")
        panel.setMinimumWidth(270)
        column = QVBoxLayout(panel)
        self.object_heading = QLabel("选择一个对象")
        column.addWidget(self.object_heading)
        text_label = QLabel("文字内容")
        self.text_edit = QTextEdit()
        self.text_edit.setAcceptRichText(False)
        self.text_edit.setAccessibleName("所选文字对象的内容")
        self.text_edit.setMinimumHeight(105)
        self.text_edit.setMaximumHeight(150)
        text_label.setBuddy(self.text_edit)
        self.text_edit.textChanged.connect(self._text_edited)
        column.addWidget(text_label)
        column.addWidget(self.text_edit)
        font_row = QHBoxLayout()
        font_label = QLabel("字号")
        self.font_size = QDoubleSpinBox()
        self.font_size.setRange(4, 24)
        self.font_size.setDecimals(1)
        self.font_size.setSuffix(" pt")
        self.font_size.setAccessibleName("所选文字字号")
        self.font_size.valueChanged.connect(self._text_edited)
        self.font_size.lineEdit().textEdited.connect(self._text_edited)
        font_label.setBuddy(self.font_size)
        self.bold_check = QCheckBox("粗体")
        self.bold_check.toggled.connect(self._text_edited)
        font_row.addWidget(font_label)
        font_row.addWidget(self.font_size)
        font_row.addWidget(self.bold_check)
        column.addLayout(font_row)
        self.style_note = QLabel("Arial 使用 Liberation Sans 替代。")
        self.style_note.setWordWrap(True)
        column.addWidget(self.style_note)
        self.apply_button = QPushButton("应用修改并更新预览")
        self.apply_button.clicked.connect(self.apply_selected)
        column.addWidget(self.apply_button)
        form = QFormLayout()
        self.x_spin = self._spin("对象相对可打印区域的 X 位置")
        self.y_spin = self._spin("对象相对可打印区域的 Y 位置", 0, 9.04)
        self.width_spin = self._spin("图片宽度", 0.1, 100)
        self.height_spin = self._spin("对象高度", 0.1, 9.04)
        form.addRow("X 位置", self.x_spin)
        form.addRow("Y 位置", self.y_spin)
        form.addRow("宽度", self.width_spin)
        form.addRow("高度", self.height_spin)
        column.addLayout(form)
        self.geometry_button = QPushButton("应用位置与尺寸")
        self.geometry_button.clicked.connect(self.apply_geometry)
        column.addWidget(self.geometry_button)
        note = QLabel("位置相对于可打印区域。调整后点击应用；保存和打印会先应用待处理修改。")
        note.setWordWrap(True)
        column.addWidget(note)
        column.addStretch()
        return panel

    def _build_printer(self):
        panel = QGroupBox("蓝牙打印 · 单张")
        row = QHBoxLayout(panel)
        label = QLabel("打印机地址")
        self.mac_edit = QLineEdit()
        self.mac_input = self.mac_edit
        self.mac_edit.setPlaceholderText("已配对 PT-D610BT 的蓝牙地址")
        self.mac_edit.setAccessibleName("打印机蓝牙 MAC 地址")
        self.mac_edit.setMinimumWidth(220)
        label.setBuddy(self.mac_edit)
        self.mac_edit.textChanged.connect(self._refresh_enabled)
        channel_label = QLabel("通道")
        self.channel_spin = QSpinBox()
        self.channel_input = self.channel_spin
        self.channel_spin.setRange(1, 30)
        self.channel_spin.setValue(2)
        self.channel_spin.setAccessibleName("蓝牙 RFCOMM 通道")
        channel_label.setBuddy(self.channel_spin)
        self.query_button = QPushButton("查询状态")
        self.query_button.clicked.connect(self.query_printer)
        self.print_button = QPushButton("打印 1 张")
        self.print_button.setToolTip("直接打印当前内存中的标签；不需要先保存")
        self.print_button.setStyleSheet("QPushButton:enabled { background: #1f629f; color: white; border-radius: 4px; padding: 5px 18px; }")
        self.print_button.clicked.connect(self.print_current)
        self.print_action = self._action("打印 1 张", "Ctrl+P", self.print_current)
        self.addAction(self.print_action)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setMaximumWidth(100)
        self.progress.setAccessibleName("蓝牙任务进行中")
        self.progress.hide()
        for widget in [label, self.mac_edit, channel_label, self.channel_spin, self.query_button,
                       self.print_button, self.progress]:
            row.addWidget(widget)
        row.setStretch(1, 1)
        return panel

    def _length_edited(self, *_):
        if not self._loading:
            self._length_pending = True
            self._update_title()
            self._refresh_enabled()

    def _has_pending(self):
        return self._text_pending or self._geometry_pending or self._length_pending

    def _text_edited(self, *_):
        if not self._loading:
            self._text_pending = True
            self._update_title()
            self._refresh_enabled()

    def _geometry_edited(self, *_):
        if not self._loading:
            self._geometry_pending = True
            self._update_title()
            self._refresh_enabled()

    def _current_object(self):
        return next((item for item in self.document.objects() if item.id == self._selected_id), None)

    def _update_title(self):
        path = self.document.source_path
        name = Path(path).name if path else "新标签"
        dirty = self.document.dirty or self._has_pending()
        self.setWindowTitle(f"{'● ' if dirty else ''}{name} — LBX 标签工作室")

    def refresh_document(self, selected_id=None):
        selected_id = selected_id or self._selected_id
        self._loading = True
        try:
            with QSignalBlocker(self.object_list):
                self.object_list.clear()
                selected_row = 0
                for index, item in enumerate(self.document.objects()):
                    summary = item.text.replace("\n", " / ")[:24] if item.kind == "text" else "内嵌图片"
                    row = QListWidgetItem(f"{'文字' if item.kind == 'text' else '图片'}  {index + 1}\n{summary}")
                    row.setData(Qt.ItemDataRole.UserRole, item.id)
                    self.object_list.addItem(row)
                    if item.id == selected_id:
                        selected_row = index
                if self.object_list.count():
                    self.object_list.setCurrentRow(selected_row)
            current = self.object_list.currentItem()
            self._selected_id = current.data(Qt.ItemDataRole.UserRole) if current else None
            with QSignalBlocker(self.length_spin):
                self.length_spin.setValue(self.document.length_mm)
            self._text_pending = self._geometry_pending = self._length_pending = False
            self._load_properties()
            self._raster = self.document.render().copy()
            self.preview_info.setText(f"标签带 12 mm  ·  内容 {self.document.length_mm:.2f} mm  ·  "
                                      f"64 点高 / 180 dpi  ·  预计成品约 {self.document.length_mm + 4:.1f} mm")
            self._update_preview_pixmap()
            self._update_title()
            self._refresh_enabled()
        finally:
            self._loading = False

    def _load_properties(self):
        item = self._current_object()
        text = item is not None and item.kind == "text"
        self.text_edit.setEnabled(text)
        self.font_size.setEnabled(text)
        self.bold_check.setEnabled(text)
        self.apply_button.setEnabled(text)
        self.object_heading.setText("文字对象" if text else "图片对象" if item else "没有对象")
        self.text_edit.setPlainText(item.text if text else "")
        self.font_size.setValue(item.font_size if text else 8)
        self.bold_check.setChecked(item.bold if text else False)
        self.style_note.setText("混合文字样式：应用文字会统一该对象的字号和粗细。" if text and item.mixed_style
                                else "Arial 使用 Liberation Sans 替代。应用文字会统一该对象样式。")
        for field, value in [(self.x_spin, item.x_mm if item else 0),
                             (self.y_spin, item.y_mm if item else 0),
                             (self.width_spin, item.width_mm if item else 1),
                             (self.height_spin, item.height_mm if item else 1)]:
            field.setValue(value)
        self.width_spin.setEnabled(item is not None and not text)
        self.width_spin.setToolTip("自由文字不按记录宽度换行或缩放。" if text else "内嵌图片的打印宽度")

    def _selection_changed(self, current, previous):
        if self._loading:
            return
        desired_id = current.data(Qt.ItemDataRole.UserRole) if current else None
        old_id = self._selected_id
        accepted = self._commit_pending()
        self._selected_id = desired_id if accepted else old_id
        self._loading = True
        try:
            with QSignalBlocker(self.object_list):
                for index in range(self.object_list.count()):
                    row = self.object_list.item(index)
                    if row.data(Qt.ItemDataRole.UserRole) == self._selected_id:
                        self.object_list.setCurrentItem(row)
                        break
            if accepted:
                self._load_properties()
                self._text_pending = self._geometry_pending = False
        finally:
            self._loading = False
        self._refresh_enabled()

    def _update_preview_pixmap(self, *_):
        if self._raster is None:
            return
        zoom = self.zoom_slider.value() / 100
        self.zoom_value.setText(f"{self.zoom_slider.value()}%")
        raster = self._raster.convert("RGB")
        tape = Image.new("RGB", (raster.width + 28, 85), "white")
        tape.paste(raster, (14, (85 - raster.height) // 2))
        data = tape.tobytes()
        image = QImage(data, tape.width, tape.height, tape.width * 3, QImage.Format.Format_RGB888).copy()
        pixmap = QPixmap.fromImage(image).scaled(round(tape.width * zoom), round(tape.height * zoom),
                                                Qt.AspectRatioMode.KeepAspectRatio,
                                                Qt.TransformationMode.FastTransformation)
        self.preview_label.setPixmap(pixmap)
        self.preview_label.setMinimumSize(pixmap.width() + 48, pixmap.height() + 48)

    def fit_preview(self):
        if self._raster is not None:
            available = max(1, self.preview_scroll.viewport().width() - 48)
            value = round(available / (self._raster.width + 28) * 100)
            self.zoom_slider.setValue(max(100, min(1000, value)))

    def showEvent(self, event):
        super().showEvent(event)
        if not self._shown_once:
            self._shown_once = True
            QTimer.singleShot(0, self.fit_preview)

    @staticmethod
    def _interpret_numeric(spin, label):
        # interpretText alone silently restores the old value for intermediate input.
        if not spin.hasAcceptableInput():
            raise ValueError(f"请为{label}输入有效范围内的完整数值。")
        with QSignalBlocker(spin):
            spin.interpretText()
        return spin.value()

    def _commit_pending(self):
        if self.busy:
            return False
        if not self._has_pending():
            return True
        try:
            length = self.document.length_mm
            if self._length_pending:
                length = self._interpret_numeric(self.length_spin, "标签长度")
            fields = {}
            if self._text_pending and self._selected_id is not None:
                fields.update(text=self.text_edit.toPlainText(),
                              font_size=self._interpret_numeric(self.font_size, "字号"),
                              bold=self.bold_check.isChecked())
            if self._geometry_pending and self._selected_id is not None:
                fields.update(x_mm=self._interpret_numeric(self.x_spin, "X 位置"),
                              y_mm=self._interpret_numeric(self.y_spin, "Y 位置"),
                              width_mm=self._interpret_numeric(self.width_spin, "宽度"),
                              height_mm=self._interpret_numeric(self.height_spin, "高度"))
            # Stage all changes so clipping or invalid input cannot partially alter
            # the displayed document, its saved fingerprint, or undo/redo history.
            candidate = deepcopy(self.document)
            if length > candidate.length_mm:
                candidate.set_length(length)
            if fields:
                candidate.update_object(self._selected_id, **fields)
            if length < candidate.length_mm:
                candidate.set_length(length)
            self.document.commit_candidate(candidate)
            self._text_pending = self._geometry_pending = self._length_pending = False
            self.refresh_document()
            self.set_status("修改已应用，打印预览已更新。")
            return True
        except Exception as exc:
            self.show_error("无法应用修改", exc)
            return False

    def apply_selected(self):
        return self._commit_pending()

    def apply_geometry(self):
        return self._commit_pending()

    def _length_changed(self, length):
        if self._loading or self.busy:
            return
        self._length_pending = True
        self._update_title()
        self._refresh_enabled()
        self._commit_pending()

    def add_text(self):
        if not self._commit_pending():
            return
        try:
            identity = self.document.add_text()
            self.refresh_document(identity)
        except Exception as exc:
            self.show_error("无法添加文字", exc)

    def add_image_dialog(self):
        if self.busy or not self._commit_pending():
            return
        name, _ = QFileDialog.getOpenFileName(self, "选择内嵌图片", "", "图片 (*.png *.jpg *.jpeg *.bmp *.gif *.webp)")
        if name:
            try:
                identity = self.document.add_image(Path(name))
                self.refresh_document(identity)
            except Exception as exc:
                self.show_error("无法添加图片", exc)

    def remove_selected(self):
        if self.busy or self._selected_id is None:
            return
        try:
            self.document.remove(self._selected_id)
            self.refresh_document()
        except Exception as exc:
            self.show_error("无法移除对象", exc)

    def undo_document(self):
        if self.busy:
            return
        if self._has_pending():
            self.refresh_document()
        elif self.document.can_undo:
            self.document.undo()
            self.refresh_document()

    def redo_document(self):
        if not self.busy and not self._has_pending() and self.document.can_redo:
            self.document.redo()
            self.refresh_document()

    def confirm_discard(self):
        if not (self.document.dirty or self._has_pending()):
            return True
        answer = QMessageBox.question(self, "标签有未保存的修改", "是否将修改另存为新的 LBX？",
                                      QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard |
                                      QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Cancel)
        if answer == QMessageBox.StandardButton.Save:
            return self.save_as_dialog()
        return answer == QMessageBox.StandardButton.Discard

    def new_document(self):
        if self.busy or not self.confirm_discard():
            return False
        try:
            self.document = LabelDocument.new()
            self._selected_id = None
            self.refresh_document()
            self.set_status("已新建 12 mm 标签。")
            return True
        except Exception as exc:
            self.show_error("无法新建标签", exc)
            return False

    def open_dialog(self):
        if self.busy:
            return False
        name, _ = QFileDialog.getOpenFileName(self, "打开 LBX 标签", "", "Brother 标签 (*.lbx *.LBX)")
        return self.open_path(Path(name)) if name else False

    def open_path(self, path):
        if self.busy or not self.confirm_discard():
            return False
        try:
            candidate = LabelDocument.load(Path(path))
            candidate.render()
            self.document = candidate
            self._selected_id = None
            self.refresh_document()
            self.set_status("已打开标签；原文件保持只读。")
            return True
        except Exception as exc:
            self.show_error("无法打开此 LBX；当前标签保持不变", exc)
            return False

    def save_as_dialog(self):
        if self.busy:
            return False
        source = self.document.source_path
        proposed = str(Path(source).with_name(Path(source).stem + "-edited.lbx")) if source else "label.lbx"
        name, _ = QFileDialog.getSaveFileName(self, "另存为新的 LBX", proposed, "Brother 标签 (*.lbx)",
                                             options=QFileDialog.Option.DontConfirmOverwrite)
        return self.save_as_path(Path(name)) if name else False

    def save_as_path(self, path):
        if self.busy or not self._commit_pending():
            return False
        try:
            path = Path(path)
            if not path.suffix:
                path = path.with_suffix(".lbx")
            self.document.save_as(path)
            self.refresh_document()
            self.set_status(f"已另存为 {path.name}；未覆盖任何已有文件。")
            return True
        except Exception as exc:
            self.show_error("无法另存为；请选择尚不存在的新文件名", exc)
            return False

    def _valid_mac(self):
        return bool(re.fullmatch(r"(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}", self.mac_edit.text().strip()))

    def query_printer(self):
        if self.busy or not self._valid_mac():
            self.set_status("请先输入已配对 PT-D610BT 的完整蓝牙地址。")
            return False
        try:
            channel = self._interpret_numeric(self.channel_spin, "蓝牙通道")
            return self.printer.query(self.mac_edit.text().strip(), channel)
        except Exception as exc:
            self.show_error("无法查询打印机", exc)
            return False

    def print_current(self):
        if self.busy or not self._valid_mac():
            self.set_status("请先输入已配对 PT-D610BT 的完整蓝牙地址，并等待当前任务结束。")
            return False
        if not self._commit_pending():
            return False
        try:
            image = self.document.render().copy()
            channel = self._interpret_numeric(self.channel_spin, "蓝牙通道")
            return self.printer.print_label(image, self.mac_edit.text().strip(), channel)
        except Exception as exc:
            self.show_error("无法准备打印任务", exc)
            return False

    def _refresh_enabled(self, *_):
        busy = self.busy
        for panel in [self.objects_panel, self.properties_panel]:
            panel.setEnabled(not busy)
        self.length_spin.setEnabled(not busy)
        self.mac_edit.setEnabled(not busy)
        self.channel_spin.setEnabled(not busy)
        for action in [self.new_action, self.open_action, self.save_action]:
            action.setEnabled(not busy)
        self.undo_action.setEnabled(not busy and bool(self.document.can_undo or self._has_pending()))
        self.redo_action.setEnabled(not busy and bool(self.document.can_redo) and not self._has_pending())
        self.query_button.setEnabled(not busy and self._valid_mac())
        self.print_button.setEnabled(not busy and self._valid_mac())
        self.print_action.setEnabled(not busy and self._valid_mac())
        self.progress.setVisible(busy)

    def _busy_changed(self, busy):
        self._refresh_enabled()
        if busy:
            self.set_status("蓝牙任务进行中，请等待设备完成；界面不会自动重发。")

    def _print_finished(self, success, message):
        self._refresh_enabled()
        self.set_status(message)

    def set_status(self, message):
        self.status_label.setText(str(message))
        self.statusBar().showMessage(str(message))

    def show_error(self, title, error):
        self.set_status(f"{title}：{error}")
        dialog = QMessageBox(QMessageBox.Icon.Critical, title, str(error), parent=self)
        dialog.setTextFormat(Qt.TextFormat.PlainText)
        dialog.exec()

    def closeEvent(self, event):
        if self.busy:
            self.set_status("蓝牙任务仍在进行，请等待结果后关闭窗口。")
            event.ignore()
        elif self.confirm_discard():
            event.accept()
        else:
            event.ignore()
