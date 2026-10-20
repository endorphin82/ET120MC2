import math
import struct
import tempfile
import unittest

import numpy as np

from scopemeter import protocol as proto
from scopemeter.models import DsoFrame
from scopemeter.ui.fft_page import FftPage, analyse_dt
from scopemeter.ui.dso_page import DsoPage


def channel_info_bytes(ch=6, exp=0, vrms=1.5, vpp=3.0, vp0=1.5, vp1=-1.5,
                       period=0.02, freq=50.0):
    info = bytearray(73)
    info[0] = ch
    info[4] = exp
    struct.pack_into("<ff", info, 25, vrms, vpp)
    struct.pack_into("<ff", info, 37, vp0, vp1)
    struct.pack_into("<ff", info, 45, period, freq)
    return bytes(info)


def _codes_sine(n, tim_base, freq, amp=40.0):
    dt = proto.TIME_BASE_F[tim_base]
    codes = bytearray(n)
    for i in range(n):
        phase = 2 * math.pi * freq * i * dt
        codes[i] = max(0, min(255, int(round(128 + amp * math.sin(phase)))))
    return bytes(codes)


def memory_frame(n=600, tim_base=16, freq=1000.0, slot=5):
    codes = _codes_sine(n, tim_base, freq)
    payload = bytearray([0, 0, 0, 0, 3])
    payload.extend(codes)
    payload.extend(bytes(n))
    payload.extend(bytes([0, 0, tim_base, 0, 0, 0, 0, 0, 0, 0]))
    payload.extend(channel_info_bytes(ch=6, exp=0))
    payload.extend(channel_info_bytes())
    frame = DsoFrame.from_payload(bytes(payload), "memory", skip=4)
    frame.mem_slot = slot
    return frame


class TestFftMemory(unittest.TestCase):
    def test_memory_fft_spectrum(self):
        page = FftPage()
        tim_base = 16
        freq = 100.0
        frame = memory_frame(n=600, tim_base=tim_base, freq=freq)
        page.show_frame(frame)
        self.assertIsNotNone(page._spectrum)
        self.assertIsNotNone(page._freqs)
        self.assertEqual(len(page._spectrum), len(page._freqs))
        idx = int(np.argmax(page._spectrum[1:]) + 1)
        peak = float(page._freqs[idx])
        self.assertAlmostEqual(peak, freq, delta=freq * 0.05)

    def test_memory_fft_label(self):
        page = FftPage()
        frame = memory_frame(slot=7)
        page.show_frame(frame)
        text = page.info_label.get_text()
        self.assertIn("memory slot 7", text)

    def test_memory_fft_analyse_dt(self):
        frame = memory_frame(n=600, tim_base=16)
        self.assertIsNotNone(analyse_dt(frame))


class TestDsoCsv(unittest.TestCase):
    def test_open_csv_sets_memory(self):
        page = DsoPage(lambda mode: None)
        frame = memory_frame(n=600, tim_base=16, freq=100.0, slot=3)
        src = frame.time_samples(1)
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as fh:
            path = fh.name
        page._save_csv(path, frame)
        loaded = page._load_csv(path)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.kind, "memory")
        self.assertEqual(loaded.mem_slot, 3)
        self.assertEqual(loaded.tim_base, 16)
        self.assertEqual(len(loaded.ch1), 600)
        got = loaded.time_samples(1)
        self.assertEqual(len(got), len(src))
        self.assertTrue(np.allclose(got, src, atol=0.05))
        page.set_memory_mode(True)
        page.show_frame(loaded)
        self.assertEqual(page._mem_mode, True)
        self.assertTrue(page.exit_btn.get_sensitive())
        self.assertIn("memory slot 3", page.scale_label.get_text())

    def test_save_csv_metadata(self):
        page = DsoPage(lambda mode: None)
        frame = memory_frame(n=600, tim_base=16, freq=1000.0, slot=9)
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as fh:
            path = fh.name
        page._save_csv(path, frame)
        with open(path) as fh:
            head = fh.read(400)
        self.assertIn("memory", head)
        self.assertIn("tim_base", head)

    def test_load_legacy_csv(self):
        page = DsoPage(lambda mode: None)
        vdiv = 2.0
        old_volts = [(code - 128) / 8.0 * vdiv for code in (96, 120, 72)]
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as fh:
            path = fh.name
            fh.write("# kind, tim_base, mem_slot, vdiv1, vdiv2, info1.vpp\n")
            fh.write("memory,14,1,%g,1,0,0,0,0,0,0,0,0\n" % vdiv)
            fh.write("# index, volts_ch1, volts_ch2\n")
            for i, v in enumerate(old_volts):
                fh.write("%d,%g,0\n" % (i, v))
        loaded = page._load_csv(path)
        self.assertIsNotNone(loaded)
        expected = [(96 - (128 + v * 8.0 / vdiv)) / 24.0 * vdiv for v in old_volts]
        self.assertTrue(np.allclose(np.asarray(loaded.time_samples(1)[:3]),
                                    np.asarray(expected)))

    def test_cursors_auto_and_readout(self):
        page = DsoPage(lambda mode: None)
        frame = memory_frame(n=600, tim_base=16, freq=100.0, slot=1)
        page.show_frame(frame)
        page.set_cursors(True)
        t1, t2, dt, v1, v2, dv = page._cursor_values()
        info = frame.info1
        self.assertAlmostEqual(dt, info.period, delta=info.period * 0.01)
        self.assertAlmostEqual(dv, info.vpp, delta=info.vpp * 0.01)
        page.set_cursors(False)
        self.assertFalse(page.cursors_check.get_active())

    def test_decade_labels(self):
        from scopemeter.ui.dso_page import is_decade_label
        for v in (0.0, 1.0, -1.0, 10.0, 0.1, -100.0, 1e-3):
            self.assertTrue(is_decade_label(v), v)
        for v in (2.0, 3.0, 0.2, -5.0, 25.0, 0.05):
            self.assertFalse(is_decade_label(v), v)


class TestFftCursors(unittest.TestCase):
    def _page(self, freq=100.0):
        page = FftPage()
        frame = memory_frame(n=600, tim_base=16, freq=freq, slot=2)
        page.show_frame(frame)
        return page

    def test_nice_step(self):
        from scopemeter.ui.fft_page import nice_step
        self.assertEqual(nice_step(50.0, 10), 5.0)
        self.assertEqual(nice_step(61035.0, 10), 5000.0)
        self.assertEqual(nice_step(1.869, 8), 0.2)
        self.assertEqual(nice_step(0.0, 10), 1.0)
        self.assertEqual(nice_step(0.07, 10), 0.005)

    def test_freq_axis_labels(self):
        from scopemeter.ui.fft_page import freq_axis_labels
        labels = freq_axis_labels(100.0)
        self.assertEqual(labels[0], (0.0, "0"))
        texts = [t for _f, t in labels]
        self.assertIn("10 Hz", texts)
        self.assertIn("50 Hz", texts)
        self.assertIn("100 Hz", texts)
        self.assertEqual(len(labels), 11)
        big = freq_axis_labels(61035.0)
        self.assertEqual(big[0][1], "0")
        self.assertEqual(big[-1][1], "60 kHz")
        self.assertTrue(all(f <= 61035.0 * (1 + 1e-9) for f, _t in big))

    def test_cursors_auto_and_readout(self):
        page = self._page(freq=100.0)
        page.set_cursors(True)
        self.assertTrue(page.cursors_check.get_active())
        self.assertTrue(page.cursor_auto_btn.get_sensitive())
        f1, f2, df, a1, a2, da = page._cursor_values()
        self.assertAlmostEqual(f1, 100.0, delta=100.0 * 0.05)
        self.assertGreater(df, 0.0)
        self.assertGreater(a1, a2)
        self.assertAlmostEqual(da, a1 - a2)
        self.assertGreater(a1, 0.1)
        page.set_cursors(False)
        self.assertFalse(page.cursors_check.get_active())
        self.assertFalse(page.cursor_auto_btn.get_sensitive())

    def test_cursor_hit_and_drag(self):
        page = self._page()
        page.set_cursors(True)
        width = 800
        x0 = page._f_to_x(page._cur_f[0], width)
        self.assertEqual(page._cursor_hit(x0 + 3, width), 0)
        x1 = page._f_to_x(page._cur_f[1], width)
        self.assertEqual(page._cursor_hit(x1 - 4, width), 1)
        self.assertIsNone(page._cursor_hit(width / 2, width))
        page._cur_drag = 0
        page._cursor_update_drag(width * 0.5, width)
        fmax = page._freqs[-1]
        self.assertAlmostEqual(page._cur_f[0], fmax * 0.5, delta=fmax * 0.01)
        page._cursor_update_drag(-100, width)
        self.assertEqual(page._cur_f[0], 0.0)
        page._cur_drag = None

    def test_pending_auto_after_frame(self):
        page = FftPage()
        page.set_cursors(True)
        self.assertIsNone(page._spectrum)
        frame = memory_frame(n=600, tim_base=16, freq=100.0, slot=4)
        page.show_frame(frame)
        self.assertFalse(page._cursor_auto_pending)
        self.assertAlmostEqual(page._cur_f[0], 100.0, delta=100.0 * 0.05)

    def test_mag_at_cursor_interpolates(self):
        page = self._page()
        mag = page._spectrum
        fmax = page._freqs[-1]
        self.assertAlmostEqual(page._mag_at_f(0.0), mag[0])
        self.assertAlmostEqual(page._mag_at_f(fmax), mag[-1])
        mid = page._mag_at_f(fmax / 2.0)
        i = len(mag) // 2
        self.assertTrue(mag[i] - 1e-6 <= mid <= mag[i] + 1e-6)


class TestFftChannels(unittest.TestCase):
    def _frame(self):
        return memory_frame(n=600, tim_base=16, freq=100.0, slot=6)

    def test_chips_like_dso(self):
        page = FftPage()
        self.assertTrue(page.ch1_check.get_active())
        self.assertTrue(page.ch2_check.get_active())
        self.assertFalse(hasattr(page, "channel_combo"))
        for btn, cls in ((page.ch1_check, "chip-ch1"),
                         (page.ch2_check, "chip-ch2")):
            style = btn.get_style_context()
            self.assertTrue(style.has_class("chip"))
            self.assertTrue(style.has_class(cls))

    def test_both_spectra_and_primary(self):
        page = FftPage()
        page.show_frame(self._frame())
        self.assertIsNotNone(page._spec1)
        self.assertIsNotNone(page._spec2)
        self.assertEqual(len(page._spec1), len(page._freqs1))
        self.assertEqual(len(page._spec2), len(page._freqs2))
        self.assertEqual(len(page._spectrum), len(page._freqs))
        self.assertIs(page._spectrum, page._spec1)
        self.assertIs(page._freqs, page._freqs1)
        self.assertEqual(page._primary, 1)
        self.assertIn("CH1 peak", page.peak_label.get_text())
        self.assertIn("CH2 peak", page.peak_label.get_text())

    def test_single_channel_selection(self):
        page = FftPage()
        page.show_frame(self._frame())
        page.ch1_check.set_active(False)
        self.assertIsNone(page._spec1)
        self.assertIsNotNone(page._spec2)
        self.assertIs(page._spectrum, page._spec2)
        self.assertEqual(page._primary, 2)
        self.assertNotIn("CH1", page.peak_label.get_text().split("|")[0])
        page.ch2_check.set_active(False)
        self.assertIsNone(page._spectrum)
        page.ch1_check.set_active(True)
        self.assertIs(page._spectrum, page._spec1)
        self.assertEqual(page._primary, 1)

    def test_statusbar_uses_primary(self):
        page = FftPage()
        page.show_frame(self._frame())
        segs = page.status_segments()
        self.assertEqual(segs["mode"], "FFT")
        self.assertIn("CH1", segs["ver"])
        page.ch1_check.set_active(False)
        segs = page.status_segments()
        self.assertIn("CH2", segs["ver"])


if __name__ == "__main__":
    unittest.main()
