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

The image below shows the spectrum captured from the TCD1304 with a 100 µs integration time and averaging over 10 frames. The spectrum is inverted on the y-axis, meaning low photon count corresponds to high ADC values. The peak in the middle of the spectrum is due to a shadow on the CCD detector.

![image](doc/captured_100us_10avg.png)

## Hardware Setup

This project utilizes the typical drive circuit from the TCD1304 datasheet, excluding the use of the 74HC04 Hex inverter.

![image](doc/circuit.png)

## Timing

The RP2040's ADC operates at 500 ksps, capturing data via DMA, which synchronizes with the 2 MHz Master Clock (MC) of the TCD1304. The integration time (Shift Gate cycle) is currently set to 100 µs, while the full 3648-pixel readout time is approximately 80 ms.

PIO state machine 0 generates the 2 MHz master clock. A second PIO state
machine aligns ICG and SH to that clock with datasheet-compliant timing
(`t2 = 500 ns`, `t3 = 1 us`, and `t1 = 5 us`). It also maintains a 40 us
electronic-shutter cadence during the complete line readout. The RP2040 ADC
still limits readout to 12-bit samples at 500 ksps.

![image](doc/timing.png)

## Configuration

The main acquisition settings are at the top of `TCD1304.c`:

- `MC_FREQUENCY_HZ`: master clock frequency (2 MHz by default)
- `EXPOSURE_US`: electronic-shutter exposure (40 us by default)
- `FRAME_AVERAGES`: number of frames in each true arithmetic mean

The current PIO delay constants implement 40 us directly. If `EXPOSURE_US` is
changed, update the PIO shutter delays and ADC lead-in compensation together.
