"""SerialWorker — QObject facade owning a worker thread and a SerialSender.

Receives mask/tip frames from the GUI thread via Qt queued signals, transmits
on the worker thread (where SerialSender's blocking ACK wait is harmless),
and emits status back to the GUI thread.

Threading model: Qt's QObject + moveToThread pattern. The internal _Core lives
in self._thread; the public SerialWorker proxy lives wherever it was
constructed (the GUI thread). Public methods just emit private signals that
land in the core's slots via QueuedConnection.

Backpressure contract (IMPORTANT for GUI authors):
    The internal queue on _send_mask_req is UNBOUNDED. SerialSender blocks
    each TX up to ~200 ms (ack_timeout × retries). If the GUI emits frames
    faster than the worker drains them (e.g. 30 fps camera + 60 ms ACK), the
    queue grows without bound.

    The GUI MUST gate its emissions on send_complete_signal:
        - emit send_mask once
        - wait for send_complete_signal before emitting again
    See PixelMirrorGUI.update_screens for the canonical pattern.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import numpy as np
from PyQt6.QtCore import QMetaObject, QObject, QThread, QTimer, Qt, pyqtSignal, pyqtSlot

# Kyle/vision/dashboard/serial_worker.py -> Kyle/vision/
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import serial  # noqa: E402  (after sys.path manipulation)
import serial.tools.list_ports  # noqa: E402

from serial_protocol import (  # noqa: E402
    Fingertip,
    PANEL_SIZE,
    SerialSender,
)

logger = logging.getLogger("me135.serial_worker")

POLL_INTERVAL_MS = 50      # how often to poll for ESP32 mode-change notifies
STATS_INTERVAL_MS = 500    # how often to emit aggregated link stats


def list_serial_ports() -> list[tuple[str, str]]:
    """Return [(device, description), ...] for all discoverable serial ports."""
    ports = []
    for p in serial.tools.list_ports.comports():
        desc = p.description if p.description and p.description != "n/a" else ""
        ports.append((p.device, desc))
    return ports


class _Core(QObject):
    """Lives on the worker thread. Owns the SerialSender."""

    connected_signal = pyqtSignal(str)
    disconnected_signal = pyqtSignal()
    mode_changed_signal = pyqtSignal(int)
    link_stats_signal = pyqtSignal(int, int, int)  # sent, ack, nak
    error_signal = pyqtSignal(str)
    send_complete_signal = pyqtSignal(bool)        # True if ACK received

    def __init__(self) -> None:
        super().__init__()
        self._sender: SerialSender | None = None
        self._poll_timer: QTimer | None = None
        self._stats_timer: QTimer | None = None
        self._last_mode: int = -1

    @pyqtSlot()
    def setup(self) -> None:
        """Called once after moveToThread, runs on the worker thread."""
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(POLL_INTERVAL_MS)
        self._poll_timer.timeout.connect(self._poll_mode)

        self._stats_timer = QTimer(self)
        self._stats_timer.setInterval(STATS_INTERVAL_MS)
        self._stats_timer.timeout.connect(self._emit_stats)

    @pyqtSlot(str)
    def connect_to_port(self, port: str) -> None:
        if self._sender is not None:
            self._do_disconnect()
        try:
            self._sender = SerialSender(port=port)
        except (serial.SerialException, OSError) as exc:
            self._sender = None
            self.error_signal.emit(f"Could not open {port}: {exc}")
            return
        self._last_mode = -1
        if self._poll_timer is not None:
            self._poll_timer.start()
        if self._stats_timer is not None:
            self._stats_timer.start()
        self.connected_signal.emit(port)

    @pyqtSlot()
    def disconnect_port(self) -> None:
        self._do_disconnect()

    def _do_disconnect(self) -> None:
        if self._poll_timer is not None and self._poll_timer.isActive():
            self._poll_timer.stop()
        if self._stats_timer is not None and self._stats_timer.isActive():
            self._stats_timer.stop()
        if self._sender is not None:
            try:
                self._sender.close()
            except Exception as exc:  # close should never raise; guard anyway
                logger.warning("close() raised: %s", exc)
            self._sender = None
            self.disconnected_signal.emit()

    @pyqtSlot(np.ndarray)
    def send_mask(self, mask: np.ndarray) -> None:
        if self._sender is None:
            self.send_complete_signal.emit(False)
            return
        try:
            ok = self._sender.send_mask(mask)
        except (serial.SerialException, OSError) as exc:
            self.error_signal.emit(f"Serial write failed: {exc}")
            self._do_disconnect()
            self.send_complete_signal.emit(False)
            return
        except Exception as exc:
            # ValueError from a malformed mask, or anything else unexpected.
            # Don't disconnect, but always emit send_complete so the GUI's
            # in-flight gate doesn't stall.
            self.error_signal.emit(f"send_mask error: {exc}")
            self.send_complete_signal.emit(False)
            return
        self.send_complete_signal.emit(bool(ok))

    @pyqtSlot(list)
    def send_tips(self, tips: list) -> None:
        if self._sender is None:
            self.send_complete_signal.emit(False)
            return
        try:
            ok = self._sender.send_fingertips(list(tips))
        except (serial.SerialException, OSError) as exc:
            self.error_signal.emit(f"Serial write failed: {exc}")
            self._do_disconnect()
            self.send_complete_signal.emit(False)
            return
        except Exception as exc:
            self.error_signal.emit(f"send_tips error: {exc}")
            self.send_complete_signal.emit(False)
            return
        self.send_complete_signal.emit(bool(ok))

    @pyqtSlot()
    def send_blank(self) -> None:
        if self._sender is None:
            return
        blank = np.zeros((PANEL_SIZE, PANEL_SIZE), dtype=np.uint8)
        try:
            self._sender.send_mask(blank)
        except (serial.SerialException, OSError) as exc:
            self.error_signal.emit(f"Serial write failed: {exc}")
            self._do_disconnect()

    @pyqtSlot()
    def _poll_mode(self) -> None:
        if self._sender is None:
            return
        try:
            new_mode = self._sender.read_mode_change()
        except (serial.SerialException, OSError) as exc:
            self.error_signal.emit(f"Serial read failed: {exc}")
            self._do_disconnect()
            return
        if new_mode is not None and new_mode != self._last_mode:
            self._last_mode = new_mode
            self.mode_changed_signal.emit(new_mode)

    @pyqtSlot()
    def _emit_stats(self) -> None:
        if self._sender is None:
            return
        self.link_stats_signal.emit(
            self._sender.frames_sent,
            self._sender.frames_acked,
            self._sender.frames_naked,
        )


class SerialWorker(QObject):
    """Public-facing serial worker. Lives on the GUI thread; owns _Core in a QThread."""

    connected_signal = pyqtSignal(str)
    disconnected_signal = pyqtSignal()
    mode_changed_signal = pyqtSignal(int)
    link_stats_signal = pyqtSignal(int, int, int)
    error_signal = pyqtSignal(str)
    send_complete_signal = pyqtSignal(bool)

    # Internal request signals (GUI thread -> _Core slots, queued connection)
    _connect_req = pyqtSignal(str)
    _disconnect_req = pyqtSignal()
    _send_mask_req = pyqtSignal(np.ndarray)
    _send_tips_req = pyqtSignal(list)
    _send_blank_req = pyqtSignal()
    _setup_req = pyqtSignal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._thread = QThread()
        self._core = _Core()
        self._core.moveToThread(self._thread)

        # GUI -> core (queued, since core is on a different thread)
        self._connect_req.connect(self._core.connect_to_port, Qt.ConnectionType.QueuedConnection)
        self._disconnect_req.connect(self._core.disconnect_port, Qt.ConnectionType.QueuedConnection)
        self._send_mask_req.connect(self._core.send_mask, Qt.ConnectionType.QueuedConnection)
        self._send_tips_req.connect(self._core.send_tips, Qt.ConnectionType.QueuedConnection)
        self._send_blank_req.connect(self._core.send_blank, Qt.ConnectionType.QueuedConnection)
        self._setup_req.connect(self._core.setup, Qt.ConnectionType.QueuedConnection)

        # core -> GUI (forward, queued)
        self._core.connected_signal.connect(self.connected_signal)
        self._core.disconnected_signal.connect(self.disconnected_signal)
        self._core.mode_changed_signal.connect(self.mode_changed_signal)
        self._core.link_stats_signal.connect(self.link_stats_signal)
        self._core.error_signal.connect(self.error_signal)
        self._core.send_complete_signal.connect(self.send_complete_signal)

        self._thread.start()
        self._setup_req.emit()  # creates timers in worker-thread context

    # --- Public API (called from GUI thread) ---

    def connect_to_port(self, port: str) -> None:
        self._connect_req.emit(port)

    def disconnect_port(self) -> None:
        self._disconnect_req.emit()

    def send_mask(self, mask: np.ndarray) -> None:
        self._send_mask_req.emit(mask)

    def send_tips(self, tips: list[Fingertip]) -> None:
        self._send_tips_req.emit(list(tips))

    def send_blank(self) -> None:
        self._send_blank_req.emit()

    def stop(self) -> None:
        # Synchronous disconnect via BlockingQueuedConnection — the call returns
        # only after _Core.disconnect_port has finished on the worker thread.
        # This guarantees the serial port is closed before we ask the thread
        # to exit, avoiding a zombie thread holding a file descriptor.
        try:
            QMetaObject.invokeMethod(
                self._core,
                "disconnect_port",
                Qt.ConnectionType.BlockingQueuedConnection,
            )
        except RuntimeError as exc:
            logger.warning("disconnect_port invokeMethod failed: %s", exc)
        self._thread.quit()
        if not self._thread.wait(2000):
            logger.warning(
                "SerialWorker thread did not exit cleanly within 2s; terminating"
            )
            self._thread.terminate()
            self._thread.wait(500)
