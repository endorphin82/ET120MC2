# TOOLTOP ET120MC2 (ScopeMeter host) — USB CDC serial protocol

Device: TOOLTOP ET120MC2 portable smart oscilloscope (+ built-in signal generator).
USB CDC ACM → `/dev/ttyACM*` (observed VID:PID `28e9:018b` on GD32-CDC; original
ET521A-family VID:PID `2E88:4603` also supported). Link: **115200 8N1**.

Reverse-engineered from `ScopeMeter.exe` v1.0 (`.NET` WinForms, class `示波器.Form1ShiBoQi`).
Protocol is shared with the ET521A/PC521 family; DMM frames (types 0x25–0x28) exist in
firmware but **ET120MC2 has no physical DMM inputs** — the host UI exposes only DSO/FFT/memory.

## Framing

All frames (both directions) start with sync byte `0xA5` and end with an 8-bit checksum:

```
checksum = (-(sum of all preceding frame bytes)) & 0xFF
```
i.e. `(sum of ALL frame bytes) & 0xFF == 0`.

### PC → device

Two shapes are used:

1. **Command** (`sendCommand`): 4 bytes
   `A5 | cmd | param | chk` — `param` is non-zero only for `cmd == 0x02` (uses `commandParamter`), otherwise `0x00`.
2. **Payload** (`sendBytes`): `A5 | payload... | chk`

Commands observed:

| cmd  | meaning                                            |
|------|----------------------------------------------------|
| 0x01 | keep-alive / poll (sent every 500 ms cycle)        |
| 0x02 | load waveform from device memory slot N → replies with type 0x22 |
| 0x03 | request DSO real-time frame (0x23)                 |
| 0x04 | request DSO analyse/FFT frame (0x24)               |
| 0x05 | request DMM auto-record batch (0x26/0x27)          |
| 0x06 | request DMM calibration table (0x28)               |
| 0x09 | request production/device-info message (0x29)      |

On connect the PC sends probe `A5 01 00 5A` (= cmd 0x01) and waits for a `0x21` status frame.
When the PC-side UI enters DSO mode it also sends payload frame `sendBytes([0x03])` = `A5 03 F8`.

The device itself reports mode changes (physical buttons) via `0x21` frames; the PC just follows.

### Device → PC

Parser (`getData`): skip bytes until `0xA5`, next byte = **type**, then:

* **type `0x21` (status)** — fixed: `A5 | 21 | mode | chk` (1 payload byte).
  `mode == 0` → device is in DSO machine mode; `mode != 0` → DMM machine mode.
* **type `0x22..0x29` and `0x3A` (long frames)**:
  `A5 | type | len_lo | len_hi | payload[len] | chk` — `len` is uint16 LE, ≤ 5000.

  Two checksum layouts are accepted:
  * **Windows / ET521A** — checksum is the last of `5 + len` bytes.
  * **GD32 clones** (e.g. VID:PID `28e9:018b`, extra `0xe6` prefix byte in the `0x23` payload) — checksum sits one byte beyond that region; the whole frame is `6 + len` bytes. In both cases `sum(all bytes incl. chk) ≡ 0 (mod 256)`.

  On GD32 clones the `0x23` payload is shifted by one byte: `[0xe6, mask, CH1×600, CH2×600, …]`. `DsoFrame.from_payload` detects the prefix automatically, so `mask` and measurement fields land on the same offsets for both layouts.

Frame type dispatch (`PackageHandleThread`):

| type  | handler                       | content                                |
|-------|-------------------------------|----------------------------------------|
| 0x21  | inline in getData             | machine mode status                    |
| 0x22  | forOldDraw                    | old drawing data (skip 4 bytes, then as 0x23) |
| 0x23  | forDSORealTimeData            | real-time waveform (600 pts/ch)        |
| 0x24  | forDSODataForAnalyse          | analyse waveform (2048 pts/ch, len=4254) |
| 0x25  | forDMM_realTimeData           | one DMM reading                        |
| 0x26  | forDMMAutoRecordHoldRecord    | DMM record batch                       |
| 0x27  | forDMMAutoRecordHoldRecord    | DMM record batch                       |
| 0x28  | forCalibrationData            | calibration table (package len 1301)   |
| 0x29  | forProductionMessage          | device info strings                    |

## DSO memory frames (type 0x22)

The device can store and recall up to 255 saved waveform "slots". To load one, the PC sends a
payload command: `A5 | 0x02 | slot | chk` (`build_load_wave`). The device replies with a long
frame of type **0x22** (`forOldDraw`), whose payload has a 4-byte header, then the body is
byte-for-byte the same as a 0x23 real-time frame (600 pts/ch, 300 unique points after
step-pair dedupe).

Parsed as: `DsoFrame.from_payload(payload, kind="memory", skip=4)` — the `skip=4` drops the header.
The resulting `DsoFrame` has `kind="memory"`, `mem_slot=slot` set, and `points=300`.

### Hold / live exclusion

While a memory load is pending, the device must not stream live 0x23 frames over the slot.
`ScopeMeterDevice.load_wave` sets `mem_hold=True` for ~3 s; the poller sends only `CMD_POLL`
(does not request `CMD_DSO_REALTIME` / `CMD_DSO_ANALYSE`) until the 0x22 arrives or the hold
is cleared via `clear_mem_hold`. In the UI, `_held_memory` keeps the last memory frame until
the user presses **Exit memory**, which clears the hold and resumes real-time.

### CSV save / open

DSO page can save the current frame (`Save CSV…`) and re-open it (`Open CSV…`). The CSV header
stores `kind`, `tim_base`, `mem_slot`, `vdiv1`, `vdiv2`, and measurement fields; rows are
`index, volts_ch1, volts_ch2`. `_load_csv` rebuilds a 600-byte CH1/CH2 payload (scaled by
`vdiv`) and returns a `kind="memory"` frame so it can be inspected and FFT-analysed like a
device-loaded waveform.

### FFT on memory frames

`FftPage.show_frame` accepts memory frames the same way as realtime/analyse: samples are
deduped to 300 unique points, `analyse_dt` uses `TIME_BASE_F[tim_base]`, and the info label
shows `memory slot N` plus window/ENBW/span.

## DSO frames

### 0x23 — real-time waveform

`payload` layout (indices relative to first payload byte, i.e. package[1:]):

```
[0]      channel mask: 1 = CH1 only, 2 = CH2 only, 3 = both
[1..600]     CH1: 600 raw screen-Y bytes (300 x-cells, 2 samples per cell)
[601..1200]  CH2: 600 raw screen-Y bytes
[1201]   channel (active channel index)
[1202]   Channel_processing
[1203]   timBase  (timebase code, index into timeBaseS/timeBaseF)
[1204..1210]  unknown (7 bytes)
[1211..1283]  CH1 info block, 73 bytes   (= payload offset 1212 in package terms)
[1284..1356]  CH2 info block, 73 bytes
```

Note: in the original code, after reading 1202 bytes the remainder starts at package offset 1202,
CH1 info = `Skip(10).Take(73)`, CH2 info = `Skip(83).Take(73)` of that remainder
(→ package offsets 1212 and 1285 — 1 byte more than the table above due to the type byte; the
Python port uses payload-relative offsets: CH1 info at payload[1211], CH2 at payload[1284]).

### 0x24 — analyse (FFT) waveform

`package length == 4254`, payload layout:

```
[0]      channel mask (1/2/3)
[1..2048]    CH1: 2048 Y bytes, **flipped**: `y = 245 - y_screen`
[2049..4096] CH2: 2048 Y bytes, **flipped**: `y = 245 - y_screen`
[4097]   channel
[4098]   Channel_processing
[4099]   timBase
[4100..4106] unknown
[4107..4179] CH1 info (73 bytes)
[4180..4252] CH2 info (73 bytes)
```

### Channel info block (73 bytes)

| offset | type   | field                                     |
|--------|--------|-------------------------------------------|
| 0      | u8     | `ch` — V/div index into `voltaBaseF`      |
| 4      | u8     | exponent: `ch_value = voltaBaseF[ch] * 10^byte[4]` (probe atten.) |
| 25     | f32 LE | VRms                                      |
| 29     | f32 LE | Vpp                                       |
| 37     | f32 LE | +Vp (`vp0`)                               |
| 41     | f32 LE | -Vp (`vp1`)                               |
| 45     | f32 LE | Period (s)                                |
| 49     | f32 LE | Freq (Hz)                                 |

## DMM frames

### 0x25 — one reading

Payload = one 9-byte DMM record (payload may be longer; only first 9 bytes used):

```
[0..3]  i32 LE   raw value (sign = polarity)
[4]     u8       range
[5]     u8       Function (measurement function code)
[6]     u8       PointNum (decimal point exponent)
[7]     u8       StatusBar
[8]     u8       rangeOUt: 0 = valid, 1 = "+OL", other = "-OL"
```

`fvalue = value / 10^PointNum`, displayed as `fvalue` + unit tail.

**Function codes** (Function byte):

| code | measurement        | unit tail (from range)                        | AC/DC prefix |
|------|--------------------|-----------------------------------------------|--------------|
| 5    | DC voltage         | range 0 → mV, else V                          | DC           |
| 6    | AC voltage         | range 0 → mV, else V                          | AC           |
| 7    | resistance         | range 0 → Ω, 1..3 → KΩ, 4..5 → MΩ             | —            |
| 9    | continuity/diode   | Ω                                             | —            |
| 10   | DC voltage         | V                                             | DC           |
| 11   | capacitance        | range 0 → nF, 1/2 → uF, 3 → mF                | Cap          |
| 14   | DC current         | mA                                            | DC           |
| 15   | AC current         | mA                                            | AC           |
| 16   | DC current         | A                                             | DC           |
| 17   | AC current         | A                                             | AC           |
| 18   | frequency          | Hz / KHz / MHz (auto-normalised while f>1000) | —            |
| 19   | duty cycle         | %                                             | —            |

`rangeOUt != 0` overrides the display with `0L` / `-OL`.

### 0x26 / 0x27 — record batches

```
payload[1..2]  i16 (unused)
payload[3..4]  i16 LE  N = number of records
payload[5..6]  i16 (unused)
payload[7..]   N * 9-byte DMM records (same layout as above)
```

### 0x28 — calibration table

package length 1301; float32 tables at payload offsets 237+4i (Coeff), 397+4i (SValue),
u32 at 557+4i (RDValue), u8 at 717+i (LockFlag), strings at 757+… — 40-ish items.
(Not needed for normal operation; kept for completeness.)

### 0x29 — production message

Payload holds several NUL/0x20-padded ASCII strings: type, time, hw ver, sw ver —
displayed as `Type:{0} Time:{1} Hardware Ver:{2} Software Ver:{3}`.

## Firmware access (attempt log)

No software path to the GD32 flash exists on this device:

* `ScopeMeter.exe` (decompiled via `dnfile`: all `sendCommand`/`sendBytes` call sites)
  uses only `0x01..0x06`, `0x09` and payloads `[0x03]` / `[0x02, slot]` — **no
  firmware/IAP/DFU commands** (`0x3A` in `getData` is just the upper bound of the
  long-frame type range, not a handler).
* USB configuration (`28e9:018b`, serial `66669DDD1939`) exposes **CDC ACM only** —
  no DFU/HID/bootloader interface (sibling GD32 boards with PID `28e9:0189` do
  expose DfuSe, this one does not).
* Empirically polled `cmd 0x00..0x3F` (with `param=0`, plus selected `param=1/2`)
  and payload frames `[0x07] [0x08] [0x0A] [0x0B] [0x10] [0x2A] [0x30] [0x05,0]
  [0x06,0]`: everything silent; device stays alive and keeps answering `0x01`.
  Only `cmd 0x09` (any param) replies `0x29`. **Caution:** payload `[0x07]` makes
  the device stop answering for a few seconds (it recovers on its own; USB PID
  unchanged) — do not send it to a production unit.
* Remaining options are physical only: SWD (SWDIO/SWCLK) or the GD32 ROM USART
  bootloader (BOOT0 high at reset). Note GD32 **readout protection (RDP) removal
  mass-erases flash**, so only read approaches should be attempted first.

## Polling model (PC side)

Three threads in the original:

1. `getData` — byte reader, assembles frames, enqueues `[type, payload...]`.
2. `DEMON` — command poller, one cycle ≈ 500..1500 ms:
   * always `cmd 0x01`;
   * DSO machine: `dsoMode 1 → cmd 0x03`, `dsoMode 2 → cmd 0x04` (sleep 1000 ms);
   * DMM machine: `dmmMode 0 → cmd 0x05`, `dmmMode 1 → cmd 0x06`, `dmmMode 4 → cmd 0x09` (then idle),
     `dmmMode 2/3` (realtime/hold) send no extra command (device streams 0x25).
3. `PackageHandleThread` — consumes frames, updates UI.

## Scale tables (from Form1 constructor)

`timeBaseS` (32) / `timeBaseF` (seconds per division):

```
'', 5nS, 10nS, 25nS, 50nS, 100ns, 200ns, 500nS, 1μS, 2μS, 5μS, 10μS, 20μS, 50μS,
100μS, 200μS, 500μS, 1mS, 2mS, 5mS, 10mS, 20mS, 50mS, 100mS, 200mS, 500mS, 1S, 2S, 5S, 10S, 20S, 50S
0, 5e-9, 1e-8, 2.5e-8, 5e-8, 1e-7, 2e-7, 5e-7, 1e-6, 2e-6, 5e-6, 1e-5, 2e-5, 5e-5,
1e-4, 2e-4, 5e-4, 1e-3, 2e-3, 5e-3, 1e-2, 2e-2, 5e-2, 1e-1, 2e-1, 5e-1, 1.0, 2.0, 5.0, 10.0, 20.0, 50.0
```

`volateBaseS` (30) / `voltaBaseF` (volts per division):

```
labels: 10mV,20mV,50mV,100mV,200mV,500mV,1V,2V,5V,10V,
        100mV,200mV,500mV,1V,2V,5V,10V,20V,50V,100V,
        1V,2V,5V,10V,20V,50V,100V,200V,500V,1000V
values: 0.01,0.02,0.05,0.1,0.2,0.5,1,2,5,10,
        0.1,0.2,0.5,1,2,5,10,20,50,100,
        1,2,5,10,20,50,100,200,500,1000
```

## Waveform scaling

Raw Y is an 8-bit screen code `0..255`. The original host paints it with
`ScaleTransform(w/300, h/192)` while the grid uses `ScaleTransform(w/10, h/8)`
over the same origin, so the plot area is **192 code-units for 8 divisions =
24 codes per division**, and the default 0 V marker sits at the plot centre
→ code `96` is 0 V. Y grows downward in GDI, so a smaller code is higher on
screen and therefore a **positive** voltage:

```
volts = (96 - code) / 24 * ch_value      # ch_value = V/div for that channel
```

Cross-checked live on ET120MC2: code span 79 × 0.1 V/div / 24 = 0.329 V vs the
device-reported `Vpp` 0.317…0.321 V, and the device's own `vp_pos`/`vp_neg`
(+0.325 / +0.003 V) match the converted sample range [+0.012, +0.342] V.

`timeSamples()` also de-duplicates step-pairs: the real-time payload stores
**300 x-cells × 2 samples** (the original writes both `Point` entries of a cell
from the same two Y bytes), so when `code[0::2] == code[1::2]` the array is
halved to 300 unique samples before plotting/FFT.

**Analyse frames (0x24) send Y flipped.** The device stores
`y_analyse = 245 - y_screen`, so raw analyse bytes land ~130 codes too low with
the formula above (the waveform "runs off the bottom"). Verified live on
ET120MC2 with paired realtime/analyse captures of the same signal: the
analyse↔realtime distributions are mirror images with `mean(p + p⁻¹) = 245.4`
and the zero-crossing anchor is exact (`149 = 245 - 96`); both channels agree
and the per-channel position offset changes sign, which only a full flip
explains. `time_samples()` un-flips first (`code = 245 - code` for
`kind == "analyse"`), then applies the same formula — realtime and analyse
then render the waveform at identical positions.

CSV note: files written before this correction stored volts on the old
`(code-128)/8` scale and carry no `# format,2` marker; `Open CSV…` detects the
absence of the marker and converts them back through the legacy inverse
(`code = 128 + v*8/vdiv`, requires the `vdiv1/vdiv2` columns) before applying
the current formula. New exports start with `# format,2` and round-trip through
`code = 96 - v*24/vdiv`.

## Sample period / frequency axis

The frequency axis is `dt = timeBaseF[timBase]` **per sample** — not per division,
and identical for real-time (0x23) and analyse (0x24) frames. Verified live:
with `timBase=16` (0.5 ms) and the built-in generator at 1 kHz, `rfftfreq(n, d=dt)`
peaks at exactly 1000.0000 Hz (12 samples per period, 17 periods in the 2048-sample
analyse window).

`calibration_ratio_x` / `calibration_ratio_y` from the original multiply the
time/voltage scales but stay at 1.0 on this unit, so they are not applied.

## FFT (analyse mode, type 0x24)

Sampled data: 2048 raw Y samples per channel (analyse bytes are flipped —
un-flip first, see Waveform scaling). Convert to volts as above, subtract
the DC mean, apply a window, then a radix-2 FFT (`FFTClass.FFT`). Window selected
by `dsoWindowMode`:

| index | window                                             |
|-------|----------------------------------------------------|
| 0     | rectangle (no window)                              |
| 1     | Hanning   `0.5 - 0.5 cos(w i)`                     |
| 2     | Hamming   `0.53836 - 0.46164 cos(w i)`             |
| 3     | Blackman  `0.42 - 0.5 cos(w i) + 0.08 cos(2 w i)`  |
| 4     | FlatTop   `1 - 1.93c1 + 1.29c2 - 0.388c3 + 0.0032c4` |
| 5     | Bartlett (triangular)                              |
| 6     | rectangle                                          |

with `w = 2π / (N-1)`.

Note on live GD32 units: the built-in generator produces a **rectangular** waveform
(~18% duty), so with a Hanning window the largest bin is the low-frequency pedestal
rather than the fundamental. The device's own `Freq` field in the channel-info block
is authoritative for frequency; the FFT peak magnitude/shape should be judged with
the Rectangle window for square-ish signals.
