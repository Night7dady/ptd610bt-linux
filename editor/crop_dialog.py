"""Pixel-coordinate image crop selection; input pixels are never modified."""
from __future__ import annotations

import math

from PIL import Image
from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QImage, QMouseEvent, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFormLayout, QLabel,
    QPushButton, QSizePolicy, QSpinBox, QVBoxLayout, QWidget,
)


class CropCanvas(QWidget):
    """Draw a fitted bitmap while retaining ROI coordinates in source pixels."""

    selectionChanged = Signal(object)

    def __init__(self, image: Image.Image, parent=None):
        super().__init__(parent)
        rgba = image.convert("RGBA")
        data = rgba.tobytes()
        self._image = QImage(data, rgba.width, rgba.height,
                             rgba.width * 4, QImage.Format.Format_RGBA8888).copy()
        self._width, self._height = image.size
        self._box = (0, 0, self._width, self._height)
        self._drag_start: tuple[float, float] | None = None
        self.setMinimumSize(160, 140)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setAccessibleName("图片裁剪框选区域")
        self.setToolTip("拖动选择保留区域；也可使用下方像素边界输入框。")
        self.setCursor(Qt.CursorShape.CrossCursor)

    def sizeHint(self):
        return QSize(640, 360)

    def image_rect(self) -> QRectF:
        available = QRectF(self.rect()).adjusted(12, 12, -12, -12)
        scale = min(max(1, available.width()) / self._width,
                    max(1, available.height()) / self._height)
        width, height = self._width * scale, self._height * scale
        return QRectF(available.center().x() - width / 2,
                      available.center().y() - height / 2, width, height)

    def image_to_widget(self, x: float, y: float) -> QPointF:
        rect = self.image_rect()
        return QPointF(rect.left() + x * rect.width() / self._width,
                       rect.top() + y * rect.height() / self._height)

    def _widget_to_image(self, point: QPointF) -> tuple[float, float]:
        rect = self.image_rect()
        x = (point.x() - rect.left()) * self._width / rect.width()
        y = (point.y() - rect.top()) * self._height / rect.height()
        return max(0, min(self._width, x)), max(0, min(self._height, y))

    def set_box(self, box: tuple[int, int, int, int], *, emit=True):
        self._box = box
        self.update()
        if emit:
            self.selectionChanged.emit(box)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), self.palette().base())
        rect = self.image_rect()
        painter.drawImage(rect, self._image)
        left, top, right, bottom = self._box
        path = QPainterPath()
        path.addRect(rect)
        if right > left and bottom > top:
            start, end = self.image_to_widget(left, top), self.image_to_widget(right, bottom)
            selected = QRectF(start, end)
            path.addRect(selected)
            painter.fillPath(path, QColor(0, 0, 0, 110))
            painter.setPen(QPen(QColor("#1672bc"), 2))
            painter.drawRect(selected)
            painter.setBrush(QColor("white"))
            for point in [selected.topLeft(), selected.topRight(),
                          selected.bottomLeft(), selected.bottomRight()]:
                painter.drawRect(QRectF(point.x() - 3, point.y() - 3, 6, 6))
        else:
            painter.fillPath(path, QColor(0, 0, 0, 110))

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton and self.image_rect().contains(event.position()):
            self._drag_start = self._widget_to_image(event.position())
            event.accept()
        else:
            super().mousePressEvent(event)

    def _drag_to(self, point: QPointF):
        if self._drag_start is None:
            return
        x, y = self._widget_to_image(point)
        start_x, start_y = self._drag_start
        if abs(start_x - x) < 0.00001 or abs(start_y - y) < 0.00001:
            return
        box = (max(0, math.floor(min(start_x, x))),
               max(0, math.floor(min(start_y, y))),
               min(self._width, math.ceil(max(start_x, x))),
               min(self._height, math.ceil(max(start_y, y))))
        self.set_box(box)

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._drag_start is not None:
            self._drag_to(event.position())
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton and self._drag_start is not None:
            self._drag_to(event.position())
            self._drag_start = None
            event.accept()
        else:
            super().mouseReleaseEvent(event)


class ImageCropDialog(QDialog):
    """Return a selected source-pixel box only after explicit valid acceptance."""

    def __init__(self, image: Image.Image, parent=None):
        if not isinstance(image, Image.Image) or min(image.size) < 1:
            raise ValueError("裁剪需要一张非空图片。")
        if image.width * image.height > 5_000_000:
            raise ValueError("图片超过 500 万像素的裁剪限制。")
        super().__init__(parent)
        self._size = image.size
        self._invalid_edits: set[QSpinBox] = set()
        self.setWindowTitle("裁剪图片内容")
        self.resize(760, 630)
        self.setMinimumSize(400, 430)
        layout = QVBoxLayout(self)
        instructions = QLabel("拖动框选要保留的图片内容，或输入原图像素边界。")
        instructions.setWordWrap(True)
        layout.addWidget(instructions)
        self.canvas = CropCanvas(image.copy(), self)
        layout.addWidget(self.canvas, 1)
        self.dimensions_label = QLabel()
        self.dimensions_label.setWordWrap(True)
        self.dimensions_label.setAccessibleName("裁剪尺寸与边界有效性")
        layout.addWidget(self.dimensions_label)
        form = QFormLayout()
        self.left_spin = self._spin("裁剪左边界（像素）", image.width)
        self.top_spin = self._spin("裁剪上边界（像素）", image.height)
        self.right_spin = self._spin("裁剪右边界（像素，不包含）", image.width)
        self.bottom_spin = self._spin("裁剪下边界（像素，不包含）", image.height)
        for text, spin in [("左边界", self.left_spin), ("上边界", self.top_spin),
                           ("右边界", self.right_spin), ("下边界", self.bottom_spin)]:
            form.addRow(text, spin)
        layout.addLayout(form)
        note = QLabel("裁剪会保留原打印比例，标签内的图片尺寸随保留区域相应缩小。")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.buttons = QDialogButtonBox()
        self.reset_button = self.buttons.addButton("重置为整张图片", QDialogButtonBox.ButtonRole.ResetRole)
        self.apply_button = self.buttons.addButton("应用裁剪", QDialogButtonBox.ButtonRole.AcceptRole)
        self.cancel_button = self.buttons.addButton("取消", QDialogButtonBox.ButtonRole.RejectRole)
        self.apply_button.setDefault(True)
        self.reset_button.setAutoDefault(False)
        self.cancel_button.setAutoDefault(False)
        self.reset_button.clicked.connect(self.reset_selection)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.canvas.selectionChanged.connect(self._from_canvas)
        self.reset_selection()

    def _spin(self, name: str, maximum: int) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(0, maximum)
        spin.setSuffix(" px")
        spin.setAccessibleName(name)
        spin.valueChanged.connect(lambda _value, field=spin: self._value_changed(field))
        spin.lineEdit().textEdited.connect(lambda _text, field=spin: self._raw_edited(field))
        return spin

    def _valid(self, box) -> bool:
        left, top, right, bottom = box
        return 0 <= left < right <= self._size[0] and 0 <= top < bottom <= self._size[1]

    def selected_box(self) -> tuple[int, int, int, int]:
        return (self.left_spin.value(), self.top_spin.value(),
                self.right_spin.value(), self.bottom_spin.value())

    def _fields(self):
        return [self.left_spin, self.top_spin, self.right_spin, self.bottom_spin]

    def _value_changed(self, spin):
        # A real step/value change repairs input; focus-loss restoration to the
        # same stored value does not emit valueChanged and must stay blocked.
        if spin.hasAcceptableInput():
            self._invalid_edits.discard(spin)
        self._from_numbers()

    def _raw_edited(self, spin):
        if spin.hasAcceptableInput():
            self._invalid_edits.discard(spin)
        else:
            self._invalid_edits.add(spin)
        self._from_numbers()

    def _input_valid(self):
        return not self._invalid_edits and all(spin.hasAcceptableInput() for spin in self._fields())

    def _from_canvas(self, box):
        self._invalid_edits.clear()
        for spin, value in zip(
                [self.left_spin, self.top_spin, self.right_spin, self.bottom_spin], box):
            old = spin.blockSignals(True)
            spin.setValue(value)
            spin.blockSignals(old)
        self._update_validation()

    def _from_numbers(self, *_):
        if not hasattr(self, "bottom_spin"):
            return
        self.canvas.set_box(self.selected_box(), emit=False)
        self._update_validation()

    def _update_validation(self):
        box = self.selected_box()
        valid = self._input_valid() and self._valid(box)
        self.apply_button.setEnabled(valid)
        if valid:
            self.dimensions_label.setText(
                f"保留区域：{box[2] - box[0]} × {box[3] - box[1]} 像素"
                f"  ·  原图：{self._size[0]} × {self._size[1]} 像素")
        elif not self._input_valid():
            self.dimensions_label.setText("边界输入不完整：请输入有效像素值，或重置为整张图片。")
        else:
            self.dimensions_label.setText("边界无效：右边界必须大于左边界，下边界必须大于上边界。")

    def reset_selection(self):
        self.canvas.set_box((0, 0, *self._size))

    def accept(self):
        if self._input_valid():
            for spin in self._fields():
                spin.interpretText()
        if self._input_valid() and self._valid(self.selected_box()):
            super().accept()
        else:
            self._update_validation()
