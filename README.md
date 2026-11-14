# ScopeMeter

Host application for the **TOOLTOP ET120MC2** oscilloscope.
A native port of the Windows ScopeMeter.exe (WinForms, .NET) to Python + GTK 3 + pyserial.
Runs on Linux and Windows (MSYS2).

The USB CDC protocol (115200 8N1, sync `0xA5`) was reverse-engineered from the
binary — see [`docs/protocol.md`](docs/protocol.md).

[Українська версія README](README.ua.md)

## The device

<img src="images/et120mc2.jpg" width="420" alt="TOOLTOP ET120MC2">

**TOOLTOP ET120MC2** — handheld 2-channel oscilloscope + signal generator:
120 MHz (40 MHz with both channels), 500 MSa/s (equivalent), 320×240 screen,
up to 2500 saved waveforms, Li-ion battery.

Purchased here:
**[AliExpress — TOOLTOP ET120MC2 Dual Channel Digital Oscilloscope](https://s.click.aliexpress.com/e/_c3wwxn7F)**

## Features

- **DSO** — real-time and Analyse modes, two channels (CH1/CH2), measurements
  Vpp / Vrms / Vavg / Vmax / Vmin / Freq / Period / Duty+, time/div and V/div,
  probe attenuation (1X/10X), t/V cursors (Δt, ΔV) and XY mode (Lissajous).
- **FFT** — Rectangle, Hanning, Hamming, Blackman, FlatTop, Bartlett windows;
  CH1/CH2 channel chips (both spectra at once, same as in DSO),
  labeled grid (frequency / level), on-graph peak marker,
  f1/f2 cursors with Δf and levels, auto placement near the peak.
- **Waveform memory** — load waveform slots from device memory (DSO only),
  view, FFT analysis, Exit memory → back to RealTime.
- **CSV / PNG** — save and open waveforms, save PNG images.
- **Device info** — production info request (model, dates, HW/SW versions).
- Automatic device detection by VID `28E9` PID `018B` (GD32-CDC) and `2E88:4603`.
- **MOCK** mode with a virtual device — development and demos without hardware.

> ET120MC2 is a handheld oscilloscope + signal generator. The protocol is
> shared with the ET521A family, but the multimeter (DMM) function is not
> present in hardware and is disabled in this host. Parameter control
> (timebase, V/div, trigger, generator) stays on the device buttons —
> the host only reads data and states.

## Screenshots

| DSO | FFT |
|-----|-----|
| ![DSO](screenshot_dso.png) | ![FFT](screenshot_fft.png) |

| Cursors | XY |
|---------|----|
| ![Cursors](screenshot_cursors.png) | ![XY](screenshot_xy.png) |

| Analyse | Active tab |
|---------|------------|
| ![Analyse](screenshot_analyse.png) | ![Tabs](screenshot_tabs.png) |

## Running

Linux:

```sh
./run.sh                 # auto-detect the device
./run.sh --port /dev/ttyACM0
./run.sh --mock          # virtual device
./run.sh --selftest      # protocol check without GUI
./run.sh --list-ports    # list serial ports
```

Windows (see below):

```bat
run.bat                  # auto-detect the device
run.bat --port COM5
run.bat --mock           # virtual device
run.bat --selftest       # protocol check without GUI
run.bat --list-ports     # list serial ports
```

Dependencies: Python 3.10+, `pyserial`, `PyGObject` (GTK 3), `numpy`.

```sh
pip install -r requirements.txt
# or from system packages: python3-gi gir1.2-gtk-3.0 python3-serial python3-numpy
```

## Windows

GTK is not installable with pip on Windows — the easiest way is **[MSYS2](https://www.msys2.org)**:

1. Install MSYS2 from <https://www.msys2.org> (default path `C:\msys64`).
2. Open **MSYS2 MINGW64** shell and install the packages:

   ```sh
   pacman -S mingw-w64-x86_64-python \
             mingw-w64-x86_64-python-gobject \
             mingw-w64-x86_64-python-pyserial \
             mingw-w64-x86_64-python-numpy
   ```

3. Run from the MSYS2 shell:

   ```sh
   python -m scopemeter             # auto-detect the device
   python -m scopemeter --port COM5
   python -m scopemeter --mock      # virtual device
   python -m scopemeter --selftest  # protocol check without GUI
   python -m scopemeter --list-ports
   ```

   Or add `C:\msys64\mingw64\bin` to the Windows `PATH` and use `run.bat`
   from CMD / PowerShell.

Settings are stored in `%APPDATA%\scopemeter\scope.ini`.

### Port access on Windows

No udev rules are needed: Windows loads the built-in `usbser.sys` driver for
the USB CDC device and creates a COM port automatically.

Verify:

```bat
run.bat --list-ports     # should list the device with VID 28e9 / PID 018b
```

or check **Device Manager → Ports (COM & LPT)** for the device.

If no COM port appears:

- unplug/replug the device, press the refresh button in the header bar;
- in Device Manager select the unknown device → *Update driver* and let
  Windows search (the device is a standard USB CDC/ACM gadget);
- try another USB cable/port (charge-only cables have no data lines).

## Port access (Linux)

The device shows up as `/dev/ttyACM0`. udev rules:

```sh
sudo cp 99-scopemeter.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules && sudo udevadm trigger
```

Verify:

```sh
./run.sh --list-ports     # should list the device with VID 28e9 / PID 018b
dmesg | tail              # appearance of /dev/ttyACM*
```

## Tests

```sh
python3 -m unittest discover -s tests
./run.sh --selftest
```

## Layout

```
scopemeter/
  protocol.py   0xA5 framing, command codes, time/voltage scales
  models.py     ChannelInfo, DsoFrame
  device.py     reader thread, poller, frame parser, auto-detect
  mock.py       virtual device (DSO + memory + production)
  config.py     settings persistence
  selftest.py   GUI-less protocol check
  ui/           GTK: app.py, dso_page.py, fft_page.py
tests/          unittest for protocol, device, fft/csv
docs/protocol.md  protocol description
images/         device photo
```
