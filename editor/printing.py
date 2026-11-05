"""Asynchronous one-job Bluetooth operations; the verified protocol is unchanged."""
from __future__ import annotations

from collections.abc import Callable
import re

from PIL import Image
from PySide6.QtCore import QObject, QRunnable, QThreadPool, Qt, Signal, Slot

from .backend import core


class PrinterService:
    """Own each connection for exactly one explicitly requested operation."""

    def query(self, mac: str, channel: int = 2):
        sock = core.connect(mac, channel, 10)
        try:
            status = core.Session(sock).query()
            core.require_ready(status)
            return status
        finally:
            sock.close()

    def print_label(self, image: Image.Image, mac: str, channel: int = 2):
        sock = core.connect(mac, channel, 10)
        try:
            return core.Session(sock).print_image(image)
        finally:
            sock.close()


class _Signals(QObject):
    done = Signal(bool, str)


class _Job(QRunnable):
    def __init__(self, action: Callable[[], str]):
        super().__init__()
        self.action = action
        self.signals = _Signals()

    @Slot()
    def run(self):
        try:
            message = self.action()
        except core.PrintOutcomeError as exc:
            self.signals.done.emit(
                False, f"打印结果不确定：{exc}\n请先检查已经输出的标签，再决定是否重试；程序不会自动重发。")
        except Exception as exc:
            # Worker exceptions must reach the UI, including unexpected backend errors.
            self.signals.done.emit(False, f"操作失败：{type(exc).__name__}: {exc}")
        else:
            self.signals.done.emit(True, message)


class PrinterController(QObject):
    """Queue no more than one operation; emit completion only on the GUI thread."""

    busyChanged = Signal(bool)
    message = Signal(str)
    finished = Signal(bool, str)

    def __init__(self, parent=None, service=None):
        super().__init__(parent)
        self.service = service if service is not None else PrinterService()
        self._busy = False
        self._job = None
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)

    @property
    def busy(self) -> bool:
        return self._busy

    def _device(self, mac: str, channel: int) -> tuple[str, int]:
        mac = mac.strip()
        if not re.fullmatch(r"(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}", mac):
            raise ValueError("请输入六组十六进制组成的蓝牙地址，例如设备信息中的地址。")
        if not isinstance(channel, int) or not 1 <= channel <= 30:
            raise ValueError("RFCOMM 通道必须在 1 到 30 之间。")
        return mac.upper(), channel

    def _reject(self, exc: Exception) -> bool:
        self.message.emit(str(exc))
        self.finished.emit(False, str(exc))
        return False

    def _start(self, action: Callable[[], str], progress: str) -> bool:
        if self._busy:
            self.message.emit("当前任务尚未完成，请等待；没有排队或重复发送。")
            return False
        self._job = _Job(action)
        self._job.signals.done.connect(self._complete, Qt.ConnectionType.QueuedConnection)
        self._busy = True
        self.busyChanged.emit(True)
        self.message.emit(progress)
        self._pool.start(self._job)
        return True

    def query(self, mac: str, channel: int = 2) -> bool:
        if self._busy:
            self.message.emit("当前任务尚未完成，请等待。")
            return False
        try:
            mac, channel = self._device(mac, channel)
        except ValueError as exc:
            return self._reject(exc)

        def query_once():
            status = self.service.query(mac, channel)
            return f"PT-D610BT 已就绪 · 标签带 {status.width} mm · RFCOMM 通道 {channel}"
        return self._start(query_once, "正在查询打印机状态…")

    def print_label(self, image: Image.Image, mac: str, channel: int = 2) -> bool:
        if self._busy:
            self.message.emit("当前任务尚未完成，请等待；没有重复发送。")
            return False
        try:
            mac, channel = self._device(mac, channel)
            if image.mode != "1" or image.height != core.PRINT_DOTS or not 1 <= image.width <= core.MAX_COLUMNS:
                raise ValueError("预览必须是宽度不超过 80 mm、高度 64 点的黑白标签。")
            if image.histogram()[0] == 0:
                raise ValueError("标签为空，请添加或恢复可见内容后再打印。")
            snapshot = image.copy()
        except (ValueError, AttributeError) as exc:
            return self._reject(exc)

        def print_once():
            self.service.print_label(snapshot, mac, channel)
            return "打印机已报告完成并回到空闲状态，已发送 1 张。请检查实体标签。"
        return self._start(print_once, "正在发送 1 张标签并等待设备确认；请勿关闭打印机…")

    @Slot(bool, str)
    def _complete(self, success: bool, message: str):
        self._busy = False
        self._job = None
        self.busyChanged.emit(False)
        self.message.emit(message)
        self.finished.emit(success, message)
