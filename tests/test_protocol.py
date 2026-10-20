import math
import struct
import unittest

import numpy as np

from scopemeter import protocol as proto
from scopemeter.models import ChannelInfo, DMMRecord, DsoFrame, DmmBatch


def dmm_bytes(value, range_, func, point, status=0, range_out=0):
    return struct.pack("<iBBBBB", value, range_, func, point, status, range_out)


def channel_info_bytes(ch=6, exp=0, vrms=1.5, vpp=3.0, vp0=1.5, vp1=-1.5,
                       period=0.02, freq=50.0):
    info = bytearray(73)
    info[0] = ch
    info[4] = exp
    struct.pack_into("<ff", info, 25, vrms, vpp)
    struct.pack_into("<ff", info, 37, vp0, vp1)
    struct.pack_into("<ff", info, 45, period, freq)
    return bytes(info)


def long_frame(type_, payload):
    length = len(payload)
    body = bytearray([type_, length & 0xFF, (length >> 8) & 0xFF])
    body.extend(payload)
    frame = bytearray([proto.SYNC])
    frame.extend(body)
    frame.append(proto.checksum(frame))
    return bytes(frame)


def status_frame(mode):
    frame = bytearray([proto.SYNC, proto.TYPE_STATUS, mode & 0xFF])
    frame.append(proto.checksum(frame))
    return bytes(frame)


class TestFraming(unittest.TestCase):
    def test_command_frame(self):
        frame = proto.build_command(proto.CMD_POLL)
        self.assertEqual(frame, bytes([0xA5, 0x01, 0x00, 0x5A]))
        self.assertEqual(sum(frame) & 0xFF, 0)

    def test_build_load_wave(self):
        frame = proto.build_load_wave(5)
        self.assertEqual(frame, bytes([0xA5, 0x02, 0x05, 0x54]))
        self.assertEqual(sum(frame) & 0xFF, 0)

    def test_build_load_wave_invalid_slot(self):
        with self.assertRaises(ValueError):
            proto.build_load_wave(0)
        with self.assertRaises(ValueError):
            proto.build_load_wave(256)

    def test_payload_frame(self):
        frame = proto.build_payload(bytes([0x03]))
        self.assertEqual(frame, bytes([0xA5, 0x03, 0x58]))
        self.assertEqual(sum(frame) & 0xFF, 0)

    def test_parser_status(self):
        parser = proto.FrameParser()
        frames = parser.feed(status_frame(0))
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0].type, proto.TYPE_STATUS)
        self.assertEqual(frames[0].payload, b"\x00")

    def test_parser_long(self):
        parser = proto.FrameParser()
        frames = parser.feed(long_frame(0x25, dmm_bytes(1234, 1, 10, 3)))
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0].type, 0x25)
        self.assertEqual(len(frames[0].payload), 9)

    def test_parser_long_gd32_extra_byte(self):
        payload = b"\xe6\x01" + bytes(10)
        length = len(payload)
        body = bytearray([0x23, length & 0xFF, (length >> 8) & 0xFF])
        body.extend(payload)
        frame = bytearray([proto.SYNC])
        frame.extend(body)
        frame.append(0x00)
        frame.append(proto.checksum(frame))
        parser = proto.FrameParser()
        frames = parser.feed(bytes(frame))
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0].type, 0x23)
        self.assertEqual(frames[0].payload, payload)

    def test_dso_frame_gd32_prefix(self):
        payload = bytearray(1357)
        payload[0] = 0xE6
        payload[1] = 0x01
        info = channel_info_bytes(ch=1, exp=0, vrms=0.5, vpp=1.0, vp0=0.5, vp1=-0.5,
                                  period=0.02, freq=50.0)
        payload[1212:1212 + 73] = info
        frame = DsoFrame.from_payload(bytes(payload))
        self.assertEqual(frame.mask, 1)
        self.assertEqual(frame.info1.vpp, 1.0)

    def test_parser_split_and_garbage(self):
        parser = proto.FrameParser()
        data = b"\x00\xff" + long_frame(0x25, dmm_bytes(1, 1, 10, 3))
        collected = []
        for i in range(len(data)):
            collected.extend(parser.feed(data[i:i + 1]))
        self.assertEqual(len(collected), 1)
        self.assertEqual(collected[0].type, 0x25)

    def test_parser_bad_checksum_resync(self):
        parser = proto.FrameParser()
        bad = bytearray(status_frame(0))
        bad[2] = 5
        frames = parser.feed(bytes(bad) + status_frame(1))
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0].payload, b"\x01")

    def test_parser_two_frames_one_feed(self):
        parser = proto.FrameParser()
        frames = parser.feed(status_frame(0) + status_frame(1))
        self.assertEqual([f.payload for f in frames], [b"\x00", b"\x01"])


class TestDMM(unittest.TestCase):
    def test_dc_voltage(self):
        rec = DMMRecord.from_bytes(dmm_bytes(12345, 1, 10, 3))
        self.assertAlmostEqual(rec.fvalue, 12.345)
        self.assertEqual(rec.unit, "V")
        self.assertEqual(rec.prefix, "DC")
        self.assertEqual(rec.display_value(), "12.3")

    def test_negative(self):
        rec = DMMRecord.from_bytes(dmm_bytes(-20000, 0, 5, 3))
        self.assertAlmostEqual(rec.fvalue, -20.0)
        self.assertEqual(rec.unit, "mV")
        self.assertEqual(rec.prefix, "DC")
        self.assertTrue(rec.display_text().startswith("-20.0"))

    def test_resistance_ranges(self):
        self.assertEqual(DMMRecord.from_bytes(dmm_bytes(100, 0, 7, 0)).unit, "Ω")
        self.assertEqual(DMMRecord.from_bytes(dmm_bytes(100, 2, 7, 0)).unit, "KΩ")
        self.assertEqual(DMMRecord.from_bytes(dmm_bytes(100, 5, 7, 0)).unit, "MΩ")

    def test_capacitance(self):
        self.assertEqual(DMMRecord.from_bytes(dmm_bytes(1000, 0, 11, 0)).unit, "nF")
        self.assertEqual(DMMRecord.from_bytes(dmm_bytes(1000, 1, 11, 0)).unit, "uF")
        self.assertEqual(DMMRecord.from_bytes(dmm_bytes(1000, 3, 11, 0)).unit, "mF")

    def test_frequency_normalization(self):
        rec = DMMRecord.from_bytes(dmm_bytes(1500000, 0, 18, 3))
        self.assertAlmostEqual(rec.fvalue, 1.5)
        self.assertEqual(rec.unit, "KHz")

    def test_ol(self):
        rec = DMMRecord.from_bytes(dmm_bytes(100, 1, 10, 3, range_out=1))
        self.assertEqual(rec.display_value(), "0L")
        rec = DMMRecord.from_bytes(dmm_bytes(100, 1, 10, 3, range_out=2))
        self.assertEqual(rec.display_value(), "-OL")

    def test_ac_voltage_prefix(self):
        rec = DMMRecord.from_bytes(dmm_bytes(500, 1, 6, 3))
        self.assertEqual(rec.prefix, "AC")
        self.assertEqual(rec.unit, "V")

    def test_batch(self):
        recs = b"".join(dmm_bytes(1000 + i, 1, 10, 3) for i in range(5))
        payload = struct.pack("<h", 0) + struct.pack("<h", 5) + struct.pack("<h", 0) + recs
        batch = DmmBatch.from_payload(payload)
        self.assertEqual(batch.count, 5)
        self.assertEqual(len(batch.records), 5)
        self.assertAlmostEqual(batch.records[4].fvalue, 1.004)


class TestDSO(unittest.TestCase):
    def test_realtime_parse(self):
        mask = 3
        ch1 = bytes((i * 3) % 256 for i in range(600))
        ch2 = bytes((i * 7) % 256 for i in range(600))
        payload = bytearray([mask])
        payload.extend(ch1)
        payload.extend(ch2)
        payload.extend(bytes([0, 0, 20, 0, 0, 0, 0, 0, 0, 0]))
        payload.extend(channel_info_bytes(ch=6, exp=0, vpp=3.2))
        payload.extend(channel_info_bytes(ch=6, exp=1, vpp=1.1))
        frame = DsoFrame.from_payload(bytes(payload), "realtime")
        self.assertEqual(frame.kind, "realtime")
        self.assertEqual(frame.mask, 3)
        self.assertEqual(frame.ch1, ch1)
        self.assertEqual(frame.ch2, ch2)
        self.assertEqual(frame.tim_base, 20)
        self.assertAlmostEqual(frame.info1.vpp, 3.2)
        self.assertAlmostEqual(frame.info2.vpp, 1.1)
        self.assertAlmostEqual(frame.info1.ch_value, 1.0)
        self.assertAlmostEqual(frame.info2.ch_value, 10.0)
        self.assertAlmostEqual(frame.info1.freq, 50.0)

    def test_realtime_skip_old_draw(self):
        mask = 1
        ch1 = bytes([128] * 600)
        payload = bytearray([0, 0, 0, 0, mask])
        payload.extend(ch1)
        payload.extend(bytes(600))
        payload.extend(bytes([0, 0, 12, 0, 0, 0, 0, 0, 0, 0]))
        payload.extend(channel_info_bytes())
        payload.extend(channel_info_bytes())
        frame = DsoFrame.from_payload(bytes(payload), "memory", skip=4)
        self.assertEqual(frame.kind, "memory")
        self.assertEqual(frame.ch1, ch1)
        self.assertEqual(frame.tim_base, 12)
        self.assertEqual(frame.points, 300)

    def test_analyse_parse(self):
        ch1 = bytes((i % 256) for i in range(2048))
        ch2 = bytes(2048)
        payload = bytearray([1])
        payload.extend(ch1)
        payload.extend(ch2)
        payload.extend(bytes([0, 0, 20, 0, 0, 0, 0, 0, 0, 0]))
        payload.extend(channel_info_bytes())
        payload.extend(channel_info_bytes())
        frame = DsoFrame.from_payload(bytes(payload), "analyse")
        self.assertEqual(len(frame.ch1), 2048)
        self.assertEqual(frame.ch1, ch1)
        self.assertIsNone(frame.ch2)
        self.assertEqual(frame.points, 2048)

    def _analyse_ch_frame(self, ch1):
        payload = bytearray([1])
        payload.extend(ch1)
        payload.extend(bytes(2048))
        payload.extend(bytes([0, 0, 20, 0, 0, 0, 0, 0, 0, 0]))
        payload.extend(channel_info_bytes(ch=6, exp=0))
        payload.extend(channel_info_bytes())
        return DsoFrame.from_payload(bytes(payload), "analyse")

    def test_analyse_volts_unflips(self):
        from scopemeter.models import ANALYSE_FLIP
        frame = self._analyse_ch_frame(bytes([149] * 2048))
        self.assertAlmostEqual(float(frame.time_samples(1)[0]), 0.0)
        frame = self._analyse_ch_frame(bytes([ANALYSE_FLIP - 72] * 2048))
        self.assertAlmostEqual(float(frame.time_samples(1)[0]), 1.0)
        frame = self._analyse_ch_frame(bytes([ANALYSE_FLIP - 0] * 2048))
        self.assertAlmostEqual(float(frame.time_samples(1)[0]), 4.0)

    def test_channel_info_probe_exp(self):
        info = ChannelInfo.from_bytes(channel_info_bytes(ch=9, exp=2))
        from scopemeter.protocol import VOLT_BASE_F
        self.assertAlmostEqual(info.ch_value, VOLT_BASE_F[9] * 100.0)

    def _single_ch_frame(self, ch1, vdiv_index=6, tim_base=12):
        payload = bytearray([1])
        payload.extend(ch1)
        payload.extend(bytes(600))
        payload.extend(bytes([0, 0, tim_base, 0, 0, 0, 0, 0, 0, 0]))
        payload.extend(channel_info_bytes(ch=vdiv_index, exp=0))
        payload.extend(channel_info_bytes())
        return DsoFrame.from_payload(bytes(payload), "realtime")

    def test_volts_code_center_is_zero(self):
        frame = self._single_ch_frame(bytes([96] * 600))
        volts = frame.time_samples(1)
        self.assertAlmostEqual(float(volts[0]), 0.0)
        self.assertAlmostEqual(float(volts[-1]), 0.0)

    def test_volts_codes_per_div(self):
        # ch=6 -> 1 V/div; 24 codes = 1 div; smaller code = higher = positive
        frame = self._single_ch_frame(bytes([72] * 600), vdiv_index=6)
        volts = frame.time_samples(1)
        self.assertAlmostEqual(float(volts[0]), 1.0)
        frame = self._single_ch_frame(bytes([0] * 600), vdiv_index=6)
        self.assertAlmostEqual(float(frame.time_samples(1)[0]), 4.0)
        frame = self._single_ch_frame(bytes([120] * 600), vdiv_index=6)
        self.assertAlmostEqual(float(frame.time_samples(1)[0]), -1.0)

    def test_volts_triangle_peak_to_peak(self):
        codes = bytes([96 + (i % 20) for i in range(600)])
        frame = self._single_ch_frame(codes, vdiv_index=3)  # 0.1 V/div
        volts = frame.time_samples(1)
        self.assertAlmostEqual(float(volts.max() - volts.min()), 19 / 24.0 * 0.1)

    def _dso_frame(self, codes, kind, tim_base, n):
        payload = bytearray([1])
        payload.extend(codes)
        payload.extend(bytes(n))
        payload.extend(bytes([0, 0, tim_base, 0, 0, 0, 0, 0, 0, 0]))
        payload.extend(channel_info_bytes(ch=3, exp=0))
        payload.extend(channel_info_bytes())
        return DsoFrame.from_payload(bytes(payload), kind)

    def _fft_peak_freq(self, frame):
        from scopemeter.ui.fft_page import analyse_dt
        samples = frame.time_samples(1)
        n = len(samples)
        freqs = np.fft.rfftfreq(n, d=analyse_dt(frame))
        windowed = samples - samples.mean()
        mag = np.abs(np.fft.rfft(windowed))
        return float(freqs[int(mag.argmax())])

    def _codes_sine(self, n, tim_base, freq, amp=40.0):
        dt = proto.TIME_BASE_F[tim_base]
        codes = bytearray(n)
        for i in range(n):
            phase = 2 * math.pi * freq * i * dt
            codes[i] = max(0, min(255, int(round(128 + amp * math.sin(phase)))))
        return bytes(codes)

    def test_analyse_dt_frequency_scale(self):
        freq, n, tim_base = 1000.0, 2048, 12
        frame = self._dso_frame(self._codes_sine(n, tim_base, freq),
                                 "analyse", tim_base, n)
        self.assertAlmostEqual(self._fft_peak_freq(frame), freq, delta=freq * 0.02)

    def test_fft_from_realtime_frame(self):
        freq, n, tim_base = 1000.0, 600, 12
        frame = self._dso_frame(self._codes_sine(n, tim_base, freq),
                                 "realtime", tim_base, n)
        self.assertAlmostEqual(self._fft_peak_freq(frame), freq, delta=freq * 0.02)

    def test_time_samples_dedupes_step_pairs(self):
        freq, n_points, tim_base = 1000.0, 300, 12
        codes = bytearray()
        for i in range(n_points):
            phase = 2 * math.pi * freq * i * proto.TIME_BASE_F[tim_base]
            code = max(0, min(255, int(round(128 + 40 * math.sin(phase)))))
            codes.extend([code, code])
        frame = self._dso_frame(bytes(codes), "realtime", tim_base, 600)
        self.assertEqual(len(frame.time_samples(1)), n_points)
        self.assertAlmostEqual(self._fft_peak_freq(frame), freq, delta=freq * 0.02)


if __name__ == "__main__":
    unittest.main()
