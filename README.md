# Work in Progress!!

## [TCD1304](https://toshiba.semicon-storage.com/us/semiconductor/product/linear-image-sensors/detail.TCD1304DG.html) with Raspberry Pi Pico

This project implements a simple TCD1304 CCD driver using the Raspberry Pi Pico microcontroller. The TCD1304 is a 3648-pixel linear CCD sensor, and the Raspberry Pi Pico is a dual-core Cortex M0 microcontroller with a built-in 500 ksps ADC.

To view the captured spectrum, flash `build/TCD1304.uf2`, install the desktop
dependencies, and run the live viewer:

```powershell
python -m pip install matplotlib numpy pyserial
python plot_spectrum.py --port COM14 --invert --first-pixel 32 --last-pixel 3680
```

Use `python plot_spectrum.py --help` for baseline subtraction and CSV export.
The viewer validates CRC-32 on every binary frame and automatically
resynchronizes after dropped or partial serial data.

### Qt spectrum application

For a reproducible environment, use
[uv](https://docs.astral.sh/uv/) to install the locked dependencies and launch
Spectrum Studio:

```powershell
uv sync
uv run python spectrum_gui.py
```

The lightweight Matplotlib viewer can be launched in the same environment:

```powershell
uv run python plot_spectrum.py --port COM14 --invert --first-pixel 32 --last-pixel 3680
```

To include development tools such as Ruff:

```powershell
uv sync --group dev
uv run ruff check .
```

Alternatively, install the GUI dependencies with pip:

```powershell
python -m pip install -r requirements-gui.txt
python spectrum_gui.py
```

The application provides serial-port discovery, CRC/error counters, live frame
rate and device metadata, pixel cropping, host averaging, baseline and dark
subtraction, smoothing, inversion, normalization, polynomial wavelength
calibration, peak tracking, manual or automatic axes, persistent settings, CSV
export, and PNG screenshots. Protocol v2 does not accept runtime acquisition
commands, so exposure, preflush count, PIO timing, and firmware averaging are
displayed as device metadata rather than editable controls.

The image below shows the spectrum captured from the TCD1304 with a 100 µs integration time and averaging over 10 frames. The spectrum is inverted on the y-axis, meaning low photon count corresponds to high ADC values. The peak in the middle of the spectrum is due to a shadow on the CCD detector.

![image](doc/captured_100us_10avg.png)

## Hardware Setup

This project utilizes the typical drive circuit from the TCD1304 datasheet, excluding the use of the 74HC04 Hex inverter.

![image](doc/circuit.png)

## Timing

The RP2040's ADC operates at 500 ksps, capturing data via DMA, which synchronizes with the 2 MHz Master Clock (MC) of the TCD1304. The integration time (Shift Gate cycle) is currently set to 100 µs, while the full 3648-pixel readout time is approximately 80 ms.

PIO state machine 0 generates the 2 MHz master clock. A second PIO state
machine aligns ICG and SH to that clock with datasheet-compliant timing
(`t2 = 500 ns`, `t3 = 1 us`, `t1 = 5 us`, and phase-aligned `t4 = 0 ns`).
It also maintains a 10 us
electronic-shutter cadence during the complete line readout. The RP2040 ADC
still limits readout to 12-bit samples at 500 ksps.

ADC startup is hardware-synchronized: the gate PIO pushes a trigger token at a
fixed master-clock phase, its RX DREQ starts a one-transfer DMA channel, and
that DMA sets the ADC `START_MANY` bit. A second DMA channel collects ADC FIFO
results. CPU scheduling therefore cannot shift the spectrum between frames.
Before each captured line, PIO generates 16 preflush SH pulses at the same
10 us cadence to clear charge accumulated while frame data was being sent.

The 31-instruction gate program runs on PIO0 and the 2-instruction master-clock
program runs on PIO1. This is required because a single RP2040 PIO block has
only 32 instruction slots.

![image](doc/timing.png)

## Configuration

The main acquisition settings are at the top of `TCD1304.c`:

- `MC_FREQUENCY_HZ`: master clock frequency (2 MHz by default)
- `EXPOSURE_US`: electronic-shutter exposure (10 us, the datasheet minimum)
- `PREFLUSH_PULSES`: clearing pulses before each captured line (16 by default)
- `FRAME_AVERAGES`: number of frames in each true arithmetic mean

The current PIO delay constants implement 10 us directly. If `EXPOSURE_US` is
changed, update the PIO shutter delays and ADC lead-in compensation together.
