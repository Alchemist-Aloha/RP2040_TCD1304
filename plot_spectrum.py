"""Live TCD1304 spectrum viewer for the binary UART/USB protocol."""

from __future__ import annotations

import argparse
import csv
import struct
import time
import zlib
from dataclasses import dataclass
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import serial
from serial.tools import list_ports

MAGIC = b"TCD4"
HEADER = struct.Struct("<4sHHIHHII")
CRC = struct.Struct("<I")
MAX_SAMPLES = 10000


@dataclass(frozen=True)
class Frame:
    number: int
    samples: np.ndarray
    averages: int
    integration_us: int


def read_exact(port: serial.Serial, size: int) -> bytes:
    data = bytearray()
    deadline = time.monotonic() + max(2.0, port.timeout or 0.0)
    while len(data) < size:
        chunk = port.read(size - len(data))
        if chunk:
            data.extend(chunk)
            deadline = time.monotonic() + max(2.0, port.timeout or 0.0)
        elif time.monotonic() >= deadline:
            raise TimeoutError(f"timed out after {len(data)}/{size} bytes")
    return bytes(data)


def seek_magic(port: serial.Serial) -> None:
    matched = 0
    while matched < len(MAGIC):
        byte = read_exact(port, 1)[0]
        if byte == MAGIC[matched]:
            matched += 1
        else:
            matched = 1 if byte == MAGIC[0] else 0


def read_frame(port: serial.Serial) -> Frame:
    while True:
        seek_magic(port)
        remainder = read_exact(port, HEADER.size - len(MAGIC))
        raw_header = MAGIC + remainder
        (
            _,
            version,
            header_bytes,
            frame_number,
            sample_count,
            averages,
            integration_us,
            payload_bytes,
        ) = HEADER.unpack(raw_header)

        valid = (
            version == 2
            and header_bytes == HEADER.size
            and 0 < sample_count <= MAX_SAMPLES
            and payload_bytes == sample_count * 2
        )
        if not valid:
            continue

        payload = read_exact(port, payload_bytes)
        received_crc = CRC.unpack(read_exact(port, CRC.size))[0]
        expected_crc = zlib.crc32(raw_header + payload) & 0xFFFFFFFF
        if received_crc != expected_crc:
            print(
                f"CRC mismatch on frame {frame_number}: "
                f"{received_crc:08x} != {expected_crc:08x}"
            )
            continue

        samples = np.frombuffer(payload, dtype="<u2").astype(np.float64)
        return Frame(frame_number, samples, averages, integration_us)


def save_csv(path: Path, frame: Frame, x: np.ndarray, y: np.ndarray) -> None:
    with path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.writer(output)
        writer.writerow(
            ["frame", frame.number, "averages", frame.averages,
             "integration_us", frame.integration_us]
        )
        writer.writerow(["pixel", "signal"])
        writer.writerows(zip(x.astype(int), y))


def parse_args() -> argparse.Namespace:
    ports = ", ".join(port.device for port in list_ports.comports()) or "none"
    parser = argparse.ArgumentParser(
        description=f"Plot TCD1304 frames (detected serial ports: {ports})"
    )
    parser.add_argument("--port", required=True, help="serial port, e.g. COM14")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--first-pixel", type=int, default=0)
    parser.add_argument("--last-pixel", type=int, default=None)
    parser.add_argument("--invert", action="store_true",
                        help="show higher optical intensity upward")
    parser.add_argument("--baseline", type=int, default=0,
                        help="subtract median of the first N displayed pixels")
    parser.add_argument("--save", type=Path,
                        help="write the latest displayed frame to CSV on exit")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    latest: tuple[Frame, np.ndarray, np.ndarray] | None = None

    with serial.Serial(args.port, args.baud, timeout=1.0) as port:
        port.reset_input_buffer()
        plt.ion()
        fig, ax = plt.subplots(figsize=(11, 6))
        (line,) = ax.plot([], [], lw=1.0)
        ax.set(xlabel="CCD element", ylabel="ADC counts",
               title="TCD1304 live spectrum")
        ax.grid(True, alpha=0.25)

        try:
            while plt.fignum_exists(fig.number):
                frame = read_frame(port)
                stop = args.last_pixel or len(frame.samples)
                y = frame.samples[args.first_pixel:stop].copy()
                x = np.arange(args.first_pixel, args.first_pixel + len(y))
                if args.baseline:
                    count = min(args.baseline, len(y))
                    y -= np.median(y[:count])
                if args.invert:
                    y = -y

                line.set_data(x, y)
                ax.relim()
                ax.autoscale_view()
                ax.set_title(
                    f"TCD1304 frame {frame.number} | {frame.averages} averages | "
                    f"{frame.integration_us / 1000:.3f} ms integration"
                )
                fig.canvas.draw_idle()
                fig.canvas.flush_events()
                latest = (frame, x, y)
        except (KeyboardInterrupt, serial.SerialException, TimeoutError) as exc:
            print(f"\nStopped: {exc}")

    if args.save and latest is not None:
        save_csv(args.save, *latest)
        print(f"Saved {args.save}")


if __name__ == "__main__":
    main()
