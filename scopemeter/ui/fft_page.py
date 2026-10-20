import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gtk
import math
import numpy as np

from ..protocol import FFT_WINDOWS
from .dso_page import (CH1_COLOR, CH2_COLOR, GRID_COLS, GRID_ROWS,
                       SCREEN_BG, format_hz)


def apply_window(name, samples):
    n = len(samples)
    if n < 2 or name.startswith("Rectangle"):
        return samples
    i = np.arange(n)
    w = 2.0 * np.pi / (n - 1)
    if name == "Hanning":
        win = 0.5 - 0.5 * np.cos(w * i)
    elif name == "Hamming":
        win = 0.53836 - 0.46164 * np.cos(w * i)
    elif name == "Blackman":
        win = 0.42 - 0.5 * np.cos(w * i) + 0.08 * np.cos(2 * w * i)
    elif name == "FlatTop":
        win = (1.0 - 1.93 * np.cos(w * i) + 1.29 * np.cos(2 * w * i)
               - 0.388 * np.cos(3 * w * i) + 0.0032 * np.cos(4 * w * i))
    elif name == "Bartlett":
        half = (n - 1) / 2.0
        win = (half - np.abs(half - i)) / half
    else:
        return samples
    return samples * win


def window_enbw(name, n):
    win = apply_window(name, np.ones(n))
    if not isinstance(win, np.ndarray):
        return 1.0
    return float(n * np.sum(win ** 2) / (np.sum(win) ** 2))


def analyse_dt(frame):
    return frame.sample_period() or 1e-6


def nice_step(max_value, divisions):
    if not (max_value > 0) or divisions <= 0:
        return 1.0
    raw = max_value / float(divisions)
    exp = math.floor(math.log10(raw))
    base = 10.0 ** exp
    cands = [mult * base for mult in (1.0, 2.0, 5.0, 10.0)]
    return min(cands, key=lambda c: (abs(c - raw), c))


def freq_axis_labels(fmax, divisions=GRID_COLS):
    if not (fmax > 0):
        return [(0.0, "0")]
    step = nice_step(fmax, divisions)
    labels = [(0.0, "0")]
    k = 1
    while k * step <= fmax * (1.0 + 1e-9):
        labels.append((k * step, format_hz(k * step)))
        k += 1
    return labels


class FftPage(Gtk.Box):
    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.set_border_width(8)
        self._frame = None
        self._spectrum = None
        self._freqs = None
        self._spec1 = None
        self._spec2 = None
        self._freqs1 = None
        self._freqs2 = None
        self._primary = 1
        self._cursors_on = False
        self._cur_f = [0.0, 0.0]
        self._cur_drag = None
        self._cursor_auto_pending = False

        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        toolbar.get_style_context().add_class("topstrip")
        toolbar.pack_start(Gtk.Label(label="Window:"), False, False, 0)
        self.window_combo = Gtk.ComboBoxText()
        for name in FFT_WINDOWS[:6]:
            self.window_combo.append_text(name)
        self.window_combo.set_active(1)
        self.window_combo.connect("changed", self._on_window)
        toolbar.pack_start(self.window_combo, False, False, 0)
        toolbar.pack_start(
            Gtk.Separator(orientation=Gtk.Orientation.VERTICAL),
            False, False, 4)
        self.ch1_check = Gtk.ToggleButton(label="CH1")
        self.ch1_check.get_style_context().add_class("chip")
        self.ch1_check.get_style_context().add_class("chip-ch1")
        self.ch2_check = Gtk.ToggleButton(label="CH2")
        self.ch2_check.get_style_context().add_class("chip")
        self.ch2_check.get_style_context().add_class("chip-ch2")
        self.ch1_check.set_active(True)
        self.ch2_check.set_active(True)
        self.ch1_check.connect("toggled", self._on_window)
        self.ch2_check.connect("toggled", self._on_window)
        toolbar.pack_start(self.ch1_check, False, False, 0)
        toolbar.pack_start(self.ch2_check, False, False, 0)
        self.cursors_check = Gtk.ToggleButton(label="Cursors")
        self.cursors_check.get_style_context().add_class("chip")
        self.cursors_check.set_tooltip_text(
            "Frequency cursors f1/f2 — drag on the plot, "
            "read df and signal levels")
        self.cursors_check.connect("toggled", self._on_cursors_toggled)
        toolbar.pack_start(self.cursors_check, False, False, 0)
        self.cursor_auto_btn = Gtk.Button(label="Cursor auto")
        self.cursor_auto_btn.get_style_context().add_class("chip")
        self.cursor_auto_btn.set_sensitive(False)
        self.cursor_auto_btn.connect("clicked", self._on_cursor_auto)
        toolbar.pack_start(self.cursor_auto_btn, False, False, 0)
        self.peak_label = Gtk.Label(label="")
        self.peak_label.set_halign(Gtk.Align.END)
        self.peak_label.set_hexpand(True)
        toolbar.pack_start(self.peak_label, True, True, 0)
        self.pack_start(toolbar, False, False, 0)

        self.area = Gtk.DrawingArea()
        self.area.set_size_request(720, 420)
        self.area.set_vexpand(True)
        self.area.set_can_focus(True)
        self.area.add_events(Gdk.EventMask.BUTTON_PRESS_MASK
                             | Gdk.EventMask.BUTTON_RELEASE_MASK
                             | Gdk.EventMask.POINTER_MOTION_MASK)
        self.area.connect("draw", self._draw)
        self.area.connect("button-press-event", self._on_button_press)
        self.area.connect("button-release-event", self._on_button_release)
        self.area.connect("motion-notify-event", self._on_motion)
        self.pack_start(self.area, True, True, 0)

        self.info_label = Gtk.Label(label="FFT of the live DSO waveform")
        self.info_label.get_style_context().add_class("dim-label")
        self.pack_start(self.info_label, False, False, 0)

    def _on_window(self, *_):
        if self._frame is not None:
            self._recompute(self._frame)
        self.area.queue_draw()

    def set_cursors(self, on):
        self.cursors_check.set_active(bool(on))

    def _on_cursors_toggled(self, btn):
        self._cursors_on = btn.get_active()
        self.cursor_auto_btn.set_sensitive(
            self._cursors_on and self._spectrum is not None)
        if self._cursors_on:
            if self._spectrum is None:
                self._cursor_auto_pending = True
            else:
                self._cursor_auto()
        self.area.queue_draw()

    def _on_cursor_auto(self, *_):
        if self._spectrum is None:
            self._cursor_auto_pending = True
            return
        self._cursor_auto()
        self.area.queue_draw()

    def _cursor_auto(self):
        mag = self._spectrum
        freqs = self._freqs
        if mag is None or freqs is None or len(mag) < 3:
            return
        peak = int(np.argmax(mag[1:]) + 1)
        fmax = self._fmax()
        self._cur_f[0] = min(fmax, max(0.0, float(freqs[peak])))
        guard = max(2, int(round(len(mag) * 0.01)))
        masked = np.array(mag, copy=True)
        masked[max(1, peak - guard):min(len(masked), peak + guard + 1)] = -1.0
        masked[0] = -1.0
        second = int(np.argmax(masked))
        self._cur_f[1] = min(fmax, max(0.0, float(freqs[second])))
        self._cursor_auto_pending = False

    def _fmax(self):
        if self._freqs is None or not len(self._freqs):
            return 1.0
        return float(self._freqs[-1]) or 1.0

    def _f_to_x(self, f, width):
        return float(width) * float(f) / self._fmax()

    def _x_to_f(self, x, width):
        w = max(float(width), 1.0)
        return min(self._fmax(), max(0.0, float(x) / w * self._fmax()))

    def _mag_at_f(self, f):
        if self._spectrum is None or not len(self._spectrum):
            return 0.0
        mag = self._spectrum
        pos = float(f) / self._fmax() * (len(mag) - 1)
        i0 = int(pos)
        if i0 < 0:
            i0 = 0
        if i0 >= len(mag) - 1:
            return float(mag[-1])
        t = pos - i0
        return float(mag[i0] * (1.0 - t) + mag[i0 + 1] * t)

    def _cursor_values(self):
        f1, f2 = float(self._cur_f[0]), float(self._cur_f[1])
        a1 = self._mag_at_f(f1)
        a2 = self._mag_at_f(f2)
        return f1, f2, abs(f2 - f1), a1, a2, a1 - a2

    def _cursor_hit(self, x, width):
        if not self._cursors_on or self._spectrum is None:
            return None
        for i in range(2):
            if abs(x - self._f_to_x(self._cur_f[i], width)) <= 9:
                return i
        return None

    def _cursor_update_drag(self, x, width):
        if self._cur_drag is None:
            return
        self._cur_f[self._cur_drag] = self._x_to_f(x, width)

    def _on_button_press(self, widget, event):
        if event.button != 1:
            return False
        alloc = widget.get_allocation()
        hit = self._cursor_hit(event.x, max(alloc.width, 1))
        if hit is not None:
            self._cur_drag = hit
        widget.grab_focus()
        return True

    def _on_button_release(self, widget, event):
        if event.button == 1:
            self._cur_drag = None
            return True
        return False

    def _on_motion(self, widget, event):
        if self._cur_drag is None:
            return False
        alloc = widget.get_allocation()
        self._cursor_update_drag(event.x, max(alloc.width, 1))
        self.area.queue_draw()
        return True

    def status_segments(self):
        segs = {"hor": "-", "ver": "-", "trig": "-", "mode": "FFT"}
        if self._spectrum is not None and self._freqs is not None and len(self._freqs):
            segs["hor"] = ".. %s   %d pts" % (format_hz(self._freqs[-1]),
                                              len(self._spectrum))
        idx = self._primary - 1
        info = None
        if self._frame is not None:
            info = self._frame.info1 if idx == 0 else self._frame.info2
        if info is not None:
            ch = idx + 1
            segs["ver"] = "CH%d %.4g V/" % (ch, info.ch_value)
            if (self._freqs is not None and len(self._freqs)
                    and self._spectrum is not None and len(self._spectrum) > 1):
                peak = int(np.argmax(self._spectrum[1:]) + 1)
                segs["trig"] = "Peak  %s" % format_hz(self._freqs[peak])
            else:
                segs["trig"] = "Edge  %s" % format_hz(info.freq)
        return segs

    def show_frame(self, frame):
        if frame.ch1 is None and frame.ch2 is None:
            return
        self._recompute(frame)
        self.area.queue_draw()

    def _active_channels(self):
        act = []
        if self.ch1_check.get_active():
            act.append(1)
        if self.ch2_check.get_active():
            act.append(2)
        return act

    def _recompute(self, frame):
        self._frame = frame
        window = self.window_combo.get_active_text() or "Rectangle"
        dt = analyse_dt(frame)
        self._spec1 = None
        self._spec2 = None
        self._freqs1 = None
        self._freqs2 = None
        self._freqs = None
        n = 0
        for ch in self._active_channels():
            raw = frame.ch1 if ch == 1 else frame.ch2
            if raw is None:
                continue
            volts = frame.time_samples(ch)
            volts = volts - volts.mean()
            windowed = apply_window(window, volts)
            n = len(windowed)
            spec = np.fft.rfft(windowed)
            mag = np.abs(spec) * 2.0 / n
            mag[0] /= 2.0
            if n % 2 == 0:
                mag[-1] /= 2.0
            freqs = np.fft.rfftfreq(n, d=dt)
            if ch == 1:
                self._spec1 = mag
                self._freqs1 = freqs
            else:
                self._spec2 = mag
                self._freqs2 = freqs
        self._spectrum = None
        for ch, mag, freqs in ((1, self._spec1, self._freqs1),
                               (2, self._spec2, self._freqs2)):
            if mag is not None:
                self._spectrum = mag
                self._freqs = freqs
                self._primary = ch
                break
        if self._spectrum is None:
            self.info_label.set_text("No active channel with data")
            self.peak_label.set_text("")
            return
        enbw = window_enbw(window, n)
        self.info_label.set_text(
            "%s — %d samples, window %s (ENBW %.3g), span .. %s, dt=%.3gs"
            % ("memory slot %s" % frame.mem_slot if frame.kind == "memory" else "FFT",
               n, window, enbw, format_hz(self._freqs[-1]), dt))
        both = self._spec1 is not None and self._spec2 is not None
        parts = []
        for ch, mag, freqs in ((1, self._spec1, self._freqs1),
                               (2, self._spec2, self._freqs2)):
            if mag is None:
                continue
            peak = int(np.argmax(mag[1:]) + 1) if len(mag) > 1 else 0
            text = "%s @ %.4g V" % (format_hz(freqs[peak]), mag[peak])
            if both:
                text = "CH%d peak %s" % (ch, text)
            else:
                text = "Peak: " + text
            parts.append(text)
        self.peak_label.set_text("   |   ".join(parts))
        info = frame.info1 if self._primary == 1 else frame.info2
        if info is not None:
            self.peak_label.set_text(
                self.peak_label.get_text() +
                "   |   measured Freq=%s Vrms=%.4g V" % (format_hz(info.freq), info.vrms))
        fmax = self._fmax()
        self._cur_f[0] = min(fmax, max(0.0, self._cur_f[0]))
        self._cur_f[1] = min(fmax, max(0.0, self._cur_f[1]))
        if self._cursor_auto_pending and self._cursors_on:
            self._cursor_auto()
        self.cursor_auto_btn.set_sensitive(
            self._cursors_on and self._spectrum is not None)

    def _draw_axes(self, cr, width, height, max_mag):
        fmax = self._fmax()
        cr.set_line_width(1)

        cr.set_source_rgba(0.62, 0.75, 1.0, 0.28)
        for f, _text in freq_axis_labels(fmax):
            if f <= 0:
                continue
            x = self._f_to_x(f, width)
            if 0 < x < width:
                cr.move_to(x, 0)
                cr.line_to(x, height)
        vstep = nice_step(max_mag, GRID_ROWS)
        m = 1
        while m * vstep <= max_mag * (1.0 + 1e-9):
            y = height * (1.0 - (m * vstep) / max_mag)
            cr.move_to(0, y)
            cr.line_to(width, y)
            m += 1
        cr.stroke()

        cr.set_source_rgba(0.62, 0.75, 1.0, 0.30)
        fstep = nice_step(fmax, GRID_COLS)
        k = 1
        while True:
            x = self._f_to_x(k * fstep / 5.0, width)
            if x >= width:
                break
            if k % 5 != 0:
                cr.move_to(x, height - 3)
                cr.line_to(x, height + 3)
            k += 1
        m = 1
        while True:
            y = height * (1.0 - (m * vstep / 5.0) / max_mag)
            if y < 0:
                break
            if m % 5 != 0:
                cr.move_to(0, y)
                cr.line_to(3, y)
            m += 1
        cr.stroke()

        cr.set_source_rgba(0.72, 0.78, 0.92, 0.55)
        cr.set_font_size(10)
        m = 0
        while m * vstep <= max_mag * (1.0 + 1e-9):
            v = m * vstep
            y = height * (1.0 - v / max_mag)
            if 14 <= y <= height - 14:
                cr.move_to(14, y - 3)
                cr.show_text("%.4g V" % v)
            m += 1
        for f, text in freq_axis_labels(fmax):
            x = self._f_to_x(f, width)
            if f == 0:
                cr.move_to(4, height - 6)
                cr.show_text("0")
            elif x <= width - 40:
                cr.move_to(x - 14, height - 6)
                cr.show_text(text)

    def _draw_peak(self, cr, width, height, max_mag, mag, freqs, color,
                   prefix="", label_dy=0):
        if mag is None or freqs is None or len(mag) < 3:
            return
        pk = int(np.argmax(mag[1:]) + 1)
        x = self._f_to_x(float(freqs[pk]), width)
        y = height * (1.0 - float(mag[pk]) / max_mag)
        cr.set_source_rgb(*color)
        cr.arc(x, y, 3.4, 0, 2 * math.pi)
        cr.fill()
        cr.set_source_rgba(0.9, 0.95, 1.0, 0.95)
        cr.set_font_size(11)
        label = "%s%s   %.4g V" % (prefix, format_hz(freqs[pk]), mag[pk])
        if x + 8 + 7 * len(label) <= width:
            tx = x + 8
        else:
            tx = x - 8 - 7 * len(label)
        cr.move_to(tx, max(y - 8, 14) + label_dy)
        cr.show_text(label)

    def _draw_cursors(self, cr, width, height, max_mag):
        if not self._cursors_on or self._spectrum is None:
            return
        f1, f2, df, a1, a2, da = self._cursor_values()
        col = (1.0, 0.72, 0.3)
        cr.set_dash([6, 4])
        cr.set_line_width(1.2)
        cr.set_font_size(10)
        for i, f in enumerate(self._cur_f):
            x = self._f_to_x(f, width)
            if -2 <= x <= width + 2:
                cr.set_source_rgb(*col)
                cr.move_to(x, 0)
                cr.line_to(x, height)
                cr.stroke()
                cr.arc(x, height * (1.0 - self._mag_at_f(f) / max_mag),
                       3.0, 0, 2 * math.pi)
                cr.fill()
                label = "f%d %s" % (i + 1, format_hz(f))
                tx = x + 4 if x <= width - 70 else x - 66
                cr.move_to(tx, height - 54)
                cr.show_text(label)
        cr.set_dash([])
        lines = [
            "f1   %s   A  %.4g V" % (format_hz(f1), a1),
            "f2   %s   A  %.4g V" % (format_hz(f2), a2),
            "df   %s   dA  %.4g V" % (format_hz(df), da),
        ]
        box_x, box_y, box_w, box_h = 8, 62, 210, 58
        cr.set_source_rgba(0.0, 0.0, 0.0, 0.55)
        cr.rectangle(box_x, box_y, box_w, box_h)
        cr.fill()
        cr.set_source_rgba(0.85, 0.9, 1.0, 0.95)
        cr.set_font_size(11)
        for j, text in enumerate(lines):
            cr.move_to(box_x + 8, box_y + 17 + j * 15)
            cr.show_text(text)

    def _draw(self, widget, cr):
        alloc = widget.get_allocation()
        width = alloc.width
        height = alloc.height
        cr.set_source_rgb(*SCREEN_BG)
        cr.paint()
        cr.set_source_rgba(0.62, 0.75, 1.0, 0.10)
        cr.set_line_width(1)
        for i in range(GRID_COLS + 1):
            x = width * i / GRID_COLS
            cr.move_to(x, 0)
            cr.line_to(x, height)
        for i in range(GRID_ROWS + 1):
            y = height * i / GRID_ROWS
            cr.move_to(0, y)
            cr.line_to(width, y)
        cr.stroke()
        cr.set_source_rgba(0.62, 0.75, 1.0, 0.34)
        cr.move_to(0, height / 2)
        cr.line_to(width, height / 2)
        cr.move_to(width / 2, 0)
        cr.line_to(width / 2, height)
        cr.stroke()

        specs = []
        if self._spec1 is not None:
            specs.append((self._spec1, self._freqs1, CH1_COLOR, 1))
        if self._spec2 is not None:
            specs.append((self._spec2, self._freqs2, CH2_COLOR, 2))
        if not specs or self._freqs is None:
            cr.set_source_rgba(0.75, 0.8, 0.9, 0.45)
            cr.set_font_size(14)
            cr.move_to(width / 2 - 60, height / 2)
            cr.show_text("No FFT data")
            return False

        max_mag = max(float(m.max()) if len(m) else 0.0
                      for m, _f, _c, _ch in specs)
        if max_mag <= 0:
            max_mag = 1.0

        self._draw_axes(cr, width, height, max_mag)

        both = len(specs) > 1
        for mag, freqs, color, ch in specs:
            count = len(mag)
            step = width / float(max(1, count - 1))

            def trace(mag=mag, count=count, step=step):
                cr.move_to(0, height)
                for i in range(count):
                    cr.line_to(i * step, height * (1.0 - mag[i] / max_mag))

            cr.set_line_width(5.0)
            cr.set_source_rgba(color[0], color[1], color[2], 0.16)
            trace()
            cr.stroke()
            cr.set_line_width(1.6)
            cr.set_source_rgb(*color)
            trace()
            cr.stroke()

            self._draw_peak(cr, width, height, max_mag, mag, freqs, color,
                            "CH%d " % ch if both else "",
                            (ch - 1) * 16 if both else 0)

        self._draw_cursors(cr, width, height, max_mag)
        return False
