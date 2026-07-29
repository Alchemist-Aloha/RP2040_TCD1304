"""Qt 6 control and visualization application for the TCD1304 spectrometer."""

from __future__ import annotations

import csv
import struct
import sys
import threading
import time
import zlib
from collections import deque
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pyqtgraph as pg
import serial
from PySide6.QtCore import QSettings, QThread, QTimer, Signal
from PySide6.QtGui import QAction, QCloseEvent, QColor
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QStatusBar,
    QToolBar,
    QVBoxLayout,
    QWidget,
)
from serial.tools import list_ports

MAGIC = b"TCD4"
HEADER = struct.Struct("<4sHHIHHII")
CRC = struct.Struct("<I")
PROTOCOL_VERSION = 2
MAX_SAMPLES = 10_000


@dataclass(frozen=True, slots=True)
class Frame:
    number: int
    samples: np.ndarray
    averages: int
    exposure_us: int
    received_at: float


class SerialReader(QThread):
    frame_received = Signal(object)
    status_changed = Signal(str)
    stream_error = Signal(str)
    counters_changed = Signal(int, int)

    def __init__(self, port: str, baud: int, parent: QWidget | None = None):
        super().__init__(parent)
        self.port_name = port
        self.baud = baud
        self._stop = threading.Event()
        self._valid_frames = 0
        self._rejected_frames = 0

    def stop(self) -> None:
        self._stop.set()

    def _read_exact(self, port: serial.Serial, size: int) -> bytes:
        data = bytearray()
        deadline = time.monotonic() + 2.0
        while len(data) < size and not self._stop.is_set():
            chunk = port.read(size - len(data))
            if chunk:
                data.extend(chunk)
                deadline = time.monotonic() + 2.0
            elif time.monotonic() >= deadline:
                raise TimeoutError(f"timed out after {len(data)}/{size} bytes")
        if self._stop.is_set():
            raise InterruptedError
        return bytes(data)

    def _seek_magic(self, port: serial.Serial) -> None:
        matched = 0
        while matched < len(MAGIC):
            byte = self._read_exact(port, 1)[0]
            if byte == MAGIC[matched]:
                matched += 1
            else:
                matched = 1 if byte == MAGIC[0] else 0

    def _read_frame(self, port: serial.Serial) -> Frame | None:
        self._seek_magic(port)
        raw_header = MAGIC + self._read_exact(port, HEADER.size - len(MAGIC))
        (
            _,
            version,
            header_bytes,
            number,
            sample_count,
            averages,
            exposure_us,
            payload_bytes,
        ) = HEADER.unpack(raw_header)

        if not (
            version == PROTOCOL_VERSION
            and header_bytes == HEADER.size
            and 0 < sample_count <= MAX_SAMPLES
            and payload_bytes == sample_count * 2
        ):
            self._rejected_frames += 1
            return None

        payload = self._read_exact(port, payload_bytes)
        received_crc = CRC.unpack(self._read_exact(port, CRC.size))[0]
        expected_crc = zlib.crc32(raw_header + payload) & 0xFFFFFFFF
        if received_crc != expected_crc:
            self._rejected_frames += 1
            return None

        samples = np.frombuffer(payload, dtype="<u2").astype(np.float64)
        self._valid_frames += 1
        return Frame(
            number, samples, averages, exposure_us, time.monotonic()
        )

    def run(self) -> None:
        try:
            with serial.Serial(
                self.port_name, self.baud, timeout=0.20, write_timeout=1.0
            ) as port:
                port.reset_input_buffer()
                self.status_changed.emit(f"Connected to {self.port_name}")
                while not self._stop.is_set():
                    try:
                        frame = self._read_frame(port)
                        if frame is not None:
                            self.frame_received.emit(frame)
                        self.counters_changed.emit(
                            self._valid_frames, self._rejected_frames
                        )
                    except TimeoutError:
                        if not self._stop.is_set():
                            self.status_changed.emit("Waiting for device frames…")
        except InterruptedError:
            pass
        except (serial.SerialException, OSError) as exc:
            if not self._stop.is_set():
                self.stream_error.emit(str(exc))
        finally:
            self.status_changed.emit("Disconnected")


def spin(
    minimum: int, maximum: int, value: int, suffix: str = ""
) -> QSpinBox:
    control = QSpinBox()
    control.setRange(minimum, maximum)
    control.setValue(value)
    control.setSuffix(suffix)
    return control


def dspin(
    minimum: float,
    maximum: float,
    value: float,
    decimals: int = 4,
    suffix: str = "",
) -> QDoubleSpinBox:
    control = QDoubleSpinBox()
    control.setRange(minimum, maximum)
    control.setDecimals(decimals)
    control.setValue(value)
    control.setSuffix(suffix)
    control.setKeyboardTracking(False)
    return control


class SpectrumWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("TCD1304 Spectrum Studio")
        self.resize(1440, 880)
        self.settings = QSettings("TCD1304", "SpectrumStudio")
        self.reader: SerialReader | None = None
        self.latest_frame: Frame | None = None
        self.latest_x = np.array([], dtype=np.float64)
        self.latest_y = np.array([], dtype=np.float64)
        self.dark_reference: np.ndarray | None = None
        self.host_frames: deque[np.ndarray] = deque(maxlen=1)
        self.frame_times: deque[float] = deque(maxlen=30)
        self.line_color = QColor("#39d98a")
        self.paused = False

        pg.setConfigOptions(antialias=True, background="#101419", foreground="#d8dee9")
        self._build_ui()
        self._load_settings()
        self.refresh_ports()

        self.render_timer = QTimer(self)
        self.render_timer.timeout.connect(self.render_latest)
        self.render_timer.start(33)

    def _build_ui(self) -> None:
        self.plot = pg.PlotWidget()
        self.plot.setLabel("bottom", "CCD element")
        self.plot.setLabel("left", "ADC counts")
        self.plot.showGrid(x=True, y=True, alpha=0.22)
        self.plot.addLegend()
        self.curve = self.plot.plot(
            [], [], name="Spectrum", pen=pg.mkPen(self.line_color, width=1.5)
        )
        self.peak_marker = pg.ScatterPlotItem(
            size=9, brush=pg.mkBrush("#ffcc66"), pen=None
        )
        self.plot.addItem(self.peak_marker)

        controls = QWidget()
        controls_layout = QVBoxLayout(controls)
        controls_layout.setContentsMargins(8, 8, 8, 8)
        controls_layout.addWidget(self._connection_group())
        controls_layout.addWidget(self._device_group())
        controls_layout.addWidget(self._processing_group())
        controls_layout.addWidget(self._calibration_group())
        controls_layout.addWidget(self._display_group())
        controls_layout.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMinimumWidth(335)
        scroll.setMaximumWidth(420)
        scroll.setWidget(controls)

        splitter = QSplitter()
        splitter.addWidget(scroll)
        splitter.addWidget(self.plot)
        splitter.setStretchFactor(1, 1)
        self.setCentralWidget(splitter)

        toolbar = QToolBar("Spectrum")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)
        self.pause_action = QAction("Pause", self)
        self.pause_action.setCheckable(True)
        self.pause_action.toggled.connect(self.set_paused)
        toolbar.addAction(self.pause_action)
        toolbar.addAction("Reset view", self.plot.enableAutoRange)
        toolbar.addSeparator()
        toolbar.addAction("Save CSV", self.save_csv)
        toolbar.addAction("Save image", self.save_image)

        self.setStatusBar(QStatusBar())
        self.connection_status = QLabel("Disconnected")
        self.statusBar().addWidget(self.connection_status, 1)
        self.rate_status = QLabel("0.0 frame/s")
        self.statusBar().addPermanentWidget(self.rate_status)

    def _connection_group(self) -> QGroupBox:
        box = QGroupBox("Connection")
        form = QFormLayout(box)
        row = QHBoxLayout()
        self.port_combo = QComboBox()
        self.port_combo.setEditable(True)
        refresh = QPushButton("↻")
        refresh.setToolTip("Refresh serial ports")
        refresh.clicked.connect(self.refresh_ports)
        row.addWidget(self.port_combo, 1)
        row.addWidget(refresh)
        form.addRow("Port", row)
        self.baud_combo = QComboBox()
        self.baud_combo.addItems(["115200", "230400", "460800", "921600"])
        form.addRow("Baud", self.baud_combo)
        self.connect_button = QPushButton("Connect")
        self.connect_button.clicked.connect(self.toggle_connection)
        form.addRow(self.connect_button)
        return box

    def _device_group(self) -> QGroupBox:
        box = QGroupBox("Device acquisition")
        form = QFormLayout(box)
        self.frame_label = QLabel("—")
        self.samples_label = QLabel("—")
        self.exposure_label = QLabel("—")
        self.device_average_label = QLabel("—")
        self.valid_label = QLabel("0")
        self.rejected_label = QLabel("0")
        form.addRow("Frame", self.frame_label)
        form.addRow("Samples", self.samples_label)
        form.addRow("Exposure", self.exposure_label)
        form.addRow("Device averages", self.device_average_label)
        form.addRow("Valid frames", self.valid_label)
        form.addRow("Rejected/CRC", self.rejected_label)
        note = QLabel(
            "Exposure, preflush, PIO timing and device averaging are firmware "
            "settings in protocol v2."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #8f9baa;")
        form.addRow(note)
        return box

    def _processing_group(self) -> QGroupBox:
        box = QGroupBox("Processing")
        form = QFormLayout(box)
        self.first_pixel = spin(0, MAX_SAMPLES - 1, 32)
        self.last_pixel = spin(1, MAX_SAMPLES, 3680)
        self.host_average = spin(1, 100, 1, " frames")
        self.host_average.valueChanged.connect(self._resize_host_average)
        self.baseline_count = spin(0, MAX_SAMPLES, 0, " px")
        self.smoothing = spin(1, 501, 1, " px")
        self.smoothing.setSingleStep(2)
        self.invert = QCheckBox("Invert optical response")
        self.invert.setChecked(True)
        self.subtract_dark = QCheckBox("Subtract dark reference")
        self.normalize = QCheckBox("Normalize 0–1")
        form.addRow("First pixel", self.first_pixel)
        form.addRow("Last pixel", self.last_pixel)
        form.addRow("Host averaging", self.host_average)
        form.addRow("Baseline median", self.baseline_count)
        form.addRow("Smoothing window", self.smoothing)
        form.addRow(self.invert)
        form.addRow(self.subtract_dark)
        form.addRow(self.normalize)
        row = QHBoxLayout()
        capture = QPushButton("Capture dark")
        capture.clicked.connect(self.capture_dark)
        clear = QPushButton("Clear dark")
        clear.clicked.connect(self.clear_dark)
        row.addWidget(capture)
        row.addWidget(clear)
        form.addRow(row)
        return box

    def _calibration_group(self) -> QGroupBox:
        box = QGroupBox("X-axis calibration")
        form = QFormLayout(box)
        self.axis_mode = QComboBox()
        self.axis_mode.addItems(["Pixel", "Wavelength (nm)"])
        self.offset_nm = dspin(-100_000, 100_000, 0.0, 6, " nm")
        self.linear_nm = dspin(-1000, 1000, 1.0, 8, " nm/px")
        self.quadratic_nm = dspin(-1, 1, 0.0, 12, " nm/px²")
        form.addRow("Axis", self.axis_mode)
        form.addRow("Offset", self.offset_nm)
        form.addRow("Linear", self.linear_nm)
        form.addRow("Quadratic", self.quadratic_nm)
        return box

    def _display_group(self) -> QGroupBox:
        box = QGroupBox("Display")
        form = QFormLayout(box)
        self.autoscale_y = QCheckBox("Autoscale Y")
        self.autoscale_y.setChecked(True)
        self.y_min = dspin(-65535, 65535, 0, 2)
        self.y_max = dspin(-65535, 65535, 4095, 2)
        self.line_width = dspin(0.2, 8, 1.5, 1, " px")
        self.line_width.valueChanged.connect(self.update_pen)
        color_button = QPushButton("Choose line color")
        color_button.clicked.connect(self.choose_color)
        self.show_peak = QCheckBox("Mark strongest peak")
        self.show_peak.setChecked(True)
        form.addRow(self.autoscale_y)
        form.addRow("Y minimum", self.y_min)
        form.addRow("Y maximum", self.y_max)
        form.addRow("Line width", self.line_width)
        form.addRow(color_button)
        form.addRow(self.show_peak)
        return box

    def refresh_ports(self) -> None:
        current = self.port_combo.currentText()
        devices = [port.device for port in list_ports.comports()]
        self.port_combo.clear()
        self.port_combo.addItems(devices)
        preferred = current or str(self.settings.value("port", "COM14"))
        self.port_combo.setCurrentText(preferred)

    def toggle_connection(self) -> None:
        if self.reader and self.reader.isRunning():
            self.disconnect_reader()
            return
        port = self.port_combo.currentText().strip()
        if not port:
            QMessageBox.warning(self, "No port", "Select a serial port first.")
            return
        self.reader = SerialReader(port, int(self.baud_combo.currentText()), self)
        self.reader.frame_received.connect(self.accept_frame)
        self.reader.status_changed.connect(self.connection_status.setText)
        self.reader.stream_error.connect(self.handle_stream_error)
        self.reader.counters_changed.connect(self.update_counters)
        self.reader.finished.connect(self.reader_finished)
        self.connect_button.setText("Disconnect")
        self.reader.start()

    def disconnect_reader(self) -> None:
        if self.reader:
            self.reader.stop()
            self.reader.wait(3000)

    def reader_finished(self) -> None:
        self.connect_button.setText("Connect")

    def handle_stream_error(self, message: str) -> None:
        self.connection_status.setText(f"Serial error: {message}")
        QMessageBox.critical(self, "Serial connection", message)

    def update_counters(self, valid: int, rejected: int) -> None:
        self.valid_label.setText(str(valid))
        self.rejected_label.setText(str(rejected))

    def accept_frame(self, frame: Frame) -> None:
        self.latest_frame = frame
        self.host_frames.append(frame.samples)
        self.frame_times.append(frame.received_at)
        self.frame_label.setText(str(frame.number))
        self.samples_label.setText(str(len(frame.samples)))
        self.exposure_label.setText(f"{frame.exposure_us} µs")
        self.device_average_label.setText(str(frame.averages))
        if len(self.frame_times) > 1:
            elapsed = self.frame_times[-1] - self.frame_times[0]
            rate = (len(self.frame_times) - 1) / elapsed if elapsed else 0
            self.rate_status.setText(f"{rate:.2f} frame/s")

    def _resize_host_average(self, count: int) -> None:
        self.host_frames = deque(
            list(self.host_frames)[-count:], maxlen=count
        )

    def processed_data(self) -> tuple[np.ndarray, np.ndarray]:
        if not self.host_frames:
            return np.array([]), np.array([])
        raw = np.mean(np.stack(self.host_frames), axis=0)
        first = min(self.first_pixel.value(), len(raw) - 1)
        last = min(max(self.last_pixel.value(), first + 1), len(raw))
        pixels = np.arange(first, last, dtype=np.float64)
        y = raw[first:last].copy()

        if (
            self.subtract_dark.isChecked()
            and self.dark_reference is not None
            and len(self.dark_reference) == len(raw)
        ):
            y -= self.dark_reference[first:last]
        baseline = min(self.baseline_count.value(), len(y))
        if baseline:
            y -= np.median(y[:baseline])
        if self.invert.isChecked():
            y = -y

        window = min(self.smoothing.value(), len(y))
        if window > 1:
            if window % 2 == 0:
                window -= 1
            kernel = np.full(window, 1.0 / window)
            y = np.convolve(y, kernel, mode="same")
        if self.normalize.isChecked() and len(y):
            y -= np.nanmin(y)
            span = np.nanmax(y)
            if span:
                y /= span

        if self.axis_mode.currentIndex() == 1:
            x = (
                self.offset_nm.value()
                + self.linear_nm.value() * pixels
                + self.quadratic_nm.value() * pixels**2
            )
        else:
            x = pixels
        return x, y

    def render_latest(self) -> None:
        if self.paused or self.latest_frame is None:
            return
        x, y = self.processed_data()
        if not len(y):
            return
        self.latest_x, self.latest_y = x, y
        self.curve.setData(x, y)
        wavelength = self.axis_mode.currentIndex() == 1
        self.plot.setLabel("bottom", "Wavelength", units="nm" if wavelength else None)
        if self.autoscale_y.isChecked():
            self.plot.enableAutoRange(axis=pg.ViewBox.YAxis)
        else:
            self.plot.disableAutoRange(axis=pg.ViewBox.YAxis)
            self.plot.setYRange(self.y_min.value(), self.y_max.value(), padding=0)
        if self.show_peak.isChecked() and np.isfinite(y).any():
            peak = int(np.nanargmax(y))
            self.peak_marker.setData([x[peak]], [y[peak]])
            self.connection_status.setText(
                f"Frame {self.latest_frame.number} · peak {x[peak]:.4g}, "
                f"{y[peak]:.4g}"
            )
        else:
            self.peak_marker.setData([], [])

    def capture_dark(self) -> None:
        if not self.host_frames:
            QMessageBox.information(self, "Dark reference", "No frame available.")
            return
        self.dark_reference = np.mean(np.stack(self.host_frames), axis=0)
        self.subtract_dark.setChecked(True)
        self.statusBar().showMessage("Dark reference captured", 3000)

    def clear_dark(self) -> None:
        self.dark_reference = None
        self.subtract_dark.setChecked(False)
        self.statusBar().showMessage("Dark reference cleared", 3000)

    def set_paused(self, paused: bool) -> None:
        self.paused = paused
        self.pause_action.setText("Resume" if paused else "Pause")

    def choose_color(self) -> None:
        color = QColorDialog.getColor(self.line_color, self, "Spectrum color")
        if color.isValid():
            self.line_color = color
            self.update_pen()

    def update_pen(self) -> None:
        self.curve.setPen(pg.mkPen(self.line_color, width=self.line_width.value()))

    def save_csv(self) -> None:
        if not len(self.latest_y):
            QMessageBox.information(self, "Export", "No displayed spectrum.")
            return
        filename, _ = QFileDialog.getSaveFileName(
            self, "Save spectrum", "spectrum.csv", "CSV files (*.csv)"
        )
        if not filename:
            return
        with Path(filename).open("w", newline="", encoding="utf-8") as output:
            writer = csv.writer(output)
            if self.latest_frame:
                writer.writerow(["frame", self.latest_frame.number])
                writer.writerow(["exposure_us", self.latest_frame.exposure_us])
                writer.writerow(["device_averages", self.latest_frame.averages])
            axis_name = (
                "wavelength_nm" if self.axis_mode.currentIndex() else "pixel"
            )
            writer.writerow([axis_name, "signal"])
            writer.writerows(zip(self.latest_x, self.latest_y, strict=True))
        self.statusBar().showMessage(f"Saved {filename}", 4000)

    def save_image(self) -> None:
        filename, _ = QFileDialog.getSaveFileName(
            self, "Save plot", "spectrum.png", "PNG images (*.png)"
        )
        if filename:
            self.plot.grab().save(filename)
            self.statusBar().showMessage(f"Saved {filename}", 4000)

    def _load_settings(self) -> None:
        self.first_pixel.setValue(int(self.settings.value("first_pixel", 32)))
        self.last_pixel.setValue(int(self.settings.value("last_pixel", 3680)))
        self.host_average.setValue(int(self.settings.value("host_average", 1)))
        self.baseline_count.setValue(int(self.settings.value("baseline", 0)))
        self.smoothing.setValue(int(self.settings.value("smoothing", 1)))
        self.invert.setChecked(self.settings.value("invert", True, bool))
        self.offset_nm.setValue(float(self.settings.value("offset_nm", 0.0)))
        self.linear_nm.setValue(float(self.settings.value("linear_nm", 1.0)))
        self.quadratic_nm.setValue(
            float(self.settings.value("quadratic_nm", 0.0))
        )

    def _save_settings(self) -> None:
        self.settings.setValue("port", self.port_combo.currentText())
        self.settings.setValue("first_pixel", self.first_pixel.value())
        self.settings.setValue("last_pixel", self.last_pixel.value())
        self.settings.setValue("host_average", self.host_average.value())
        self.settings.setValue("baseline", self.baseline_count.value())
        self.settings.setValue("smoothing", self.smoothing.value())
        self.settings.setValue("invert", self.invert.isChecked())
        self.settings.setValue("offset_nm", self.offset_nm.value())
        self.settings.setValue("linear_nm", self.linear_nm.value())
        self.settings.setValue("quadratic_nm", self.quadratic_nm.value())

    def closeEvent(self, event: QCloseEvent) -> None:
        self._save_settings()
        self.disconnect_reader()
        event.accept()


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("TCD1304 Spectrum Studio")
    app.setOrganizationName("TCD1304")
    app.setStyle("Fusion")
    window = SpectrumWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
