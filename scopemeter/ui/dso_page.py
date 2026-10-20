import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk
import cairo
import math
import numpy as np

from ..protocol import TIME_BASE_F


GRID_COLS = 10
GRID_ROWS = 8
ZOOM_MIN = 0.1
ZOOM_MAX = 50.0

CH1_COLOR = (0.3, 0.9, 0.4)     # green
CH2_COLOR = (1.0, 0.85, 0.15)   # yellow
SCREEN_BG = (0.023, 0.043, 0.086)


def format_time(seconds):
    if seconds < 0:
        return "-" + format_time(-seconds)
    if seconds <= 0:
        return "-"
    for limit, unit in ((1e-9, "ns"), (1e-6, "us"), (1e-3, "ms")):
        if seconds < limit * 1000:
            return "%.4g %s" % (seconds / limit, unit)
    return "%.4g s" % seconds


def format_hz(freq):
    if freq <= 0:
        return "-"
    if freq >= 1e6:
        return "%.4g MHz" % (freq / 1e6)
    if freq >= 1e3:
        return "%.4g kHz" % (freq / 1e3)
    return "%.4g Hz" % freq


def is_decade_label(value):
    if value == 0:
        return True
    r = math.log10(abs(value))
    return abs(r - round(r)) < 1e-9


class DsoPage(Gtk.Box):
    def __init__(self, on_mode_change, on_load_slot=None, cfg=None,
                 on_exit_memory=None, on_request_info=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.set_border_width(0)
        self._on_mode_change = on_mode_change
        self._on_load_slot = on_load_slot
        self._cfg = cfg
        self._on_exit_memory = on_exit_memory
        self._on_request_info = on_request_info
        self._mem_mode = False
        self._frame = None
        self._show_ch1 = True
        self._show_ch2 = True
        self._tim_base = 0
        self._zoom_x = 1.0
        self._zoom_y = 1.0
        self._pan_x = 0.0
        self._pan_y = 0.0
        self._dragging = False
        self._drag_last = None
        self._xy_mode = False
        self._cursors_on = False
        self._cur_t = [-2.0, 2.0]
        self._cur_v = [1.0, -1.0]
        self._cur_drag = None
        self._cursor_auto_pending = False

        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        top.get_style_context().add_class("topstrip")

        self.mode_realtime = Gtk.RadioButton.new_with_label(None, "RealTime")
        self.mode_analyse = Gtk.RadioButton.new_with_label_from_widget(self.mode_realtime, "Analyse")
        self.mode_realtime.connect("toggled", self._on_mode, 1)
        self.mode_analyse.connect("toggled", self._on_mode, 2)
        mode_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        mode_box.get_style_context().add_class("modebox")
        mode_box.pack_start(self.mode_realtime, False, False, 0)
        mode_box.pack_start(self.mode_analyse, False, False, 0)
        top.pack_start(mode_box, False, False, 0)

        top.pack_start(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL), False, False, 4)

        self.ch1_check = Gtk.ToggleButton(label="CH1")
        self.ch1_check.get_style_context().add_class("chip")
        self.ch1_check.get_style_context().add_class("chip-ch1")
        self.ch2_check = Gtk.ToggleButton(label="CH2")
        self.ch2_check.get_style_context().add_class("chip")
        self.ch2_check.get_style_context().add_class("chip-ch2")
        self.ch1_check.set_active(True)
        self.ch2_check.set_active(True)
        self.ch1_check.connect("toggled", self._on_channels)
        self.ch2_check.connect("toggled", self._on_channels)
        top.pack_start(self.ch1_check, False, False, 0)
        top.pack_start(self.ch2_check, False, False, 0)

        self.xy_check = Gtk.ToggleButton(label="XY")
        self.xy_check.get_style_context().add_class("chip")
        self.xy_check.set_tooltip_text("CH1=X, CH2=Y (Lissajous)")
        self.xy_check.connect("toggled", self._on_xy_toggled)
        top.pack_start(self.xy_check, False, False, 0)

        self.cursors_check = Gtk.ToggleButton(label="Cursors")
        self.cursors_check.get_style_context().add_class("chip")
        self.cursors_check.set_tooltip_text(
            "Drag orange t1/t2 and blue V1/V2 markers for dV / dt readout")
        self.cursors_check.connect("toggled", self._on_cursors_toggled)
        top.pack_start(self.cursors_check, False, False, 0)

        self.scale_label = Gtk.Label(label="")
        self.scale_label.get_style_context().add_class("dim-label")
        self.scale_label.set_halign(Gtk.Align.END)
        self.scale_label.set_hexpand(True)
        top.pack_start(self.scale_label, True, True, 0)
        self.pack_start(top, False, False, 0)

        body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)

        self.area = Gtk.DrawingArea()
        self.area.set_size_request(700, 440)
        self.area.set_vexpand(True)
        self.area.set_can_focus(True)
        self.area.connect("draw", self._draw)
        self.area.connect("realize", self._on_area_realize)
        self.area.connect("scroll-event", self._on_scroll)
        self.area.connect("button-press-event", self._on_button_press)
        self.area.connect("button-release-event", self._on_button_release)
        self.area.connect("motion-notify-event", self._on_motion)
        body.pack_start(self.area, True, True, 0)

        softkeys = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        softkeys.set_size_request(200, -1)
        softkeys.get_style_context().add_class("softkeys")

        self.softkey_caption = Gtk.Label(label="WAVEFORM")
        self.softkey_caption.set_halign(Gtk.Align.START)
        self.softkey_caption.get_style_context().add_class("softkey-caption")
        softkeys.pack_start(self.softkey_caption, False, False, 0)

        def softkey(label, cb, cls=None):
            btn = Gtk.Button(label=label)
            btn.get_style_context().add_class("softkey")
            if cls:
                btn.get_style_context().add_class(cls)
            btn.connect("clicked", cb)
            softkeys.pack_start(btn, False, False, 0)
            return btn

        def softkey_sep():
            softkeys.pack_start(
                Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL),
                False, False, 0)

        self.load_btn = softkey("Load slot…", self._on_load_clicked)
        self.exit_btn = softkey("Exit memory", self._on_exit_clicked, "softkey-danger")
        self.exit_btn.set_sensitive(False)
        softkey_sep()
        self.save_btn = softkey("Save CSV…", self._on_save_clicked)
        self.open_btn = softkey("Open CSV…", self._on_open_clicked)
        self.png_btn = softkey("Save PNG…", self._on_png_clicked)
        softkey_sep()
        self.zoom_in_btn = softkey("Zoom +", lambda b: self._zoom_step(1.25))
        self.zoom_out_btn = softkey("Zoom −", lambda b: self._zoom_step(1 / 1.25))
        self.reset_zoom_btn = softkey("Reset zoom", self._on_reset_zoom)
        softkey_sep()
        self.cursor_auto_btn = softkey("Cursor auto", self._on_cursor_auto)
        self.cursor_auto_btn.set_sensitive(False)
        body.pack_start(softkeys, False, False, 0)
        self.pack_start(body, True, True, 0)

        strip = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        strip.get_style_context().add_class("meas-strip")
        self._meas_widgets = {1: {}, 2: {}}
        keys = [
            "Vpp", "Vrms", "Vavg", "Vmax", "Vmin",
            "Freq", "Period", "Duty+", "Probe", "V/div", "t/div",
        ]
        for ch in (1, 2):
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            chip = Gtk.Label(label="CH%d" % ch)
            chip.set_size_request(34, -1)
            chip.get_style_context().add_class("meas-chip")
            chip.get_style_context().add_class("chip-ch%d" % ch)
            row.pack_start(chip, False, False, 0)
            grid = Gtk.Grid()
            grid.set_column_spacing(12)
            for i, key in enumerate(keys):
                cell = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
                k = Gtk.Label(label=key)
                k.set_halign(Gtk.Align.START)
                k.get_style_context().add_class("meas-key")
                v = Gtk.Label(label="-")
                v.set_halign(Gtk.Align.START)
                v.get_style_context().add_class("meas-value")
                cell.pack_start(k, False, False, 0)
                cell.pack_start(v, False, False, 0)
                grid.attach(cell, i, 0, 1, 1)
                self._meas_widgets[ch][key] = v
            row.pack_start(grid, True, True, 0)
            strip.pack_start(row, False, False, 0)
        self.pack_start(strip, False, False, 0)

    def _on_mode(self, button, mode):
        if button.get_active():
            self._on_mode_change(mode)

    def _on_channels(self, *_):
        self._show_ch1 = self.ch1_check.get_active()
        self._show_ch2 = self.ch2_check.get_active()
        self.area.queue_draw()

    def _on_reset_zoom(self, *_):
        self._zoom_x = 1.0
        self._zoom_y = 1.0
        self._pan_x = 0.0
        self._pan_y = 0.0
        self.area.queue_draw()

    def _zoom_step(self, factor):
        self._zoom_x = max(ZOOM_MIN, min(ZOOM_MAX, self._zoom_x * factor))
        self._zoom_y = max(ZOOM_MIN, min(ZOOM_MAX, self._zoom_y * factor))
        self.area.queue_draw()

    def _on_area_realize(self, widget):
        widget.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK |
            Gdk.EventMask.BUTTON_RELEASE_MASK |
            Gdk.EventMask.POINTER_MOTION_MASK |
            Gdk.EventMask.SCROLL_MASK |
            Gdk.EventMask.SMOOTH_SCROLL_MASK)

    def _on_xy_toggled(self, button):
        self._xy_mode = button.get_active()
        self._on_reset_zoom()
        self.area.queue_draw()

    def set_cursors(self, on):
        if self.cursors_check.get_active() != on:
            self.cursors_check.set_active(on)
        else:
            self._on_cursors_toggled(self.cursors_check)

    def _on_cursors_toggled(self, button):
        self._cursors_on = button.get_active()
        self._cur_drag = None
        self.cursor_auto_btn.set_sensitive(self._cursors_on)
        if self._cursors_on:
            self._cursor_auto_pending = True
            if self._frame is not None:
                self._cursor_auto()
                self._cursor_auto_pending = False
        self.area.queue_draw()

    def _on_cursor_auto(self, button):
        if self._cursors_on:
            self._cursor_auto()

    def _cursor_auto(self, *_):
        frame = self._frame
        tdiv = TIME_BASE_F[self._tim_base] if 0 <= self._tim_base < len(TIME_BASE_F) else 0.0
        info = None
        if frame is not None:
            info = frame.info1 if self._show_ch1 and frame.info1 else frame.info2
            if info is None:
                info = frame.info1 or frame.info2
        period = info.period if info is not None else 0.0
        if period and period > 0 and tdiv > 0:
            half_div = (period / 2.0) / tdiv
        else:
            half_div = 2.0
        self._cur_t = [-half_div, half_div]
        if info is not None and info.vp_pos != info.vp_neg:
            v1, v2 = info.vp_pos, info.vp_neg
        else:
            vdiv = info.ch_value if info is not None else 1.0
            v1, v2 = vdiv, -vdiv
        self._cur_v = [v1, v2]
        self.area.queue_draw()

    def _cursor_values(self):
        tdiv = TIME_BASE_F[self._tim_base] if 0 <= self._tim_base < len(TIME_BASE_F) else 0.0
        t1 = self._cur_t[0] * tdiv
        t2 = self._cur_t[1] * tdiv
        dt = abs(t2 - t1)
        v1, v2 = self._cur_v[0], self._cur_v[1]
        dv = abs(v1 - v2)
        return t1, t2, dt, v1, v2, dv

    def _cursor_hit(self, x, y, w, h):
        vdiv = self._current_vdiv()
        tdiv = TIME_BASE_F[self._tim_base] if 0 <= self._tim_base < len(TIME_BASE_F) else 0.0
        v_half = vdiv * GRID_ROWS / 2.0
        best = None
        best_d = 9.0
        for i in range(2):
            frac = 0.5 + self._cur_t[i] / GRID_COLS
            cx = self._time_frac_to_x(frac, w)
            if -8 <= cx <= w + 8 and abs(cx - x) < best_d:
                best_d = abs(cx - x)
                best = ("t", i)
        for i in range(2):
            cy = self._volt_to_y(self._cur_v[i], v_half, h)
            if -8 <= cy <= h + 8 and abs(cy - y) < best_d:
                best_d = abs(cy - y)
                best = ("v", i)
        if tdiv <= 0 and best is not None and best[0] == "t":
            return None
        return best

    def _cursor_update_drag(self, x, y, w, h):
        kind, i = self._cur_drag
        vdiv = self._current_vdiv()
        if kind == "t":
            frac = ((x / w) - 0.5) / self._zoom_x + 0.5 + self._pan_x
            self._cur_t[i] = (frac - 0.5) * GRID_COLS
        else:
            ny_s = y / h
            ny = (ny_s - 0.5) / self._zoom_y + 0.5 + self._pan_y
            v_half = vdiv * GRID_ROWS / 2.0
            self._cur_v[i] = (0.5 - ny) * 2.0 * v_half

    def _draw_cursors(self, cr, width, height, vdiv, tdiv):
        if not self._cursors_on or self._xy_mode:
            return
        v_half = vdiv * GRID_ROWS / 2.0
        t1, t2, dt, v1, v2, dv = self._cursor_values()
        cr.set_dash([6, 4])
        cr.set_line_width(1.2)
        cr.set_font_size(10)

        col_t = (1.0, 0.72, 0.3)
        for i, ct in enumerate(self._cur_t):
            frac = 0.5 + ct / GRID_COLS
            x = self._time_frac_to_x(frac, width)
            if -2 <= x <= width + 2:
                cr.set_source_rgb(*col_t)
                cr.move_to(x, 0)
                cr.line_to(x, height)
                cr.stroke()
                label = "t%d %s" % (i + 1, format_time(ct * tdiv))
                tx = x + 4 if x <= width - 70 else x - 66
                cr.set_source_rgb(*col_t)
                cr.move_to(tx, height - 36)
                cr.show_text(label)

        col_v = (0.3, 0.76, 1.0)
        for i, cv in enumerate(self._cur_v):
            y = self._volt_to_y(cv, v_half, height)
            if -2 <= y <= height + 2:
                cr.set_source_rgb(*col_v)
                cr.move_to(0, y)
                cr.line_to(width, y)
                cr.stroke()
                label = "V%d %.4g V" % (i + 1, cv)
                cr.move_to(width - 92, y - 4)
                cr.show_text(label)

        cr.set_dash([])
        lines = [
            "dt   %s .. %s    d %s" % (format_time(t1), format_time(t2),
                                       format_time(dt)),
            "f    1/dt  %s" % (format_hz(1.0 / dt) if dt > 0 else "-"),
            "dV   %.4g V .. %.4g V    d %.4g V" % (v1, v2, dv),
        ]
        box_x, box_y, box_w, box_h = 8, 62, 205, 58
        cr.set_source_rgba(0.0, 0.0, 0.0, 0.55)
        cr.rectangle(box_x, box_y, box_w, box_h)
        cr.fill()
        cr.set_source_rgba(0.85, 0.9, 1.0, 0.95)
        cr.set_font_size(11)
        for j, text in enumerate(lines):
            cr.move_to(box_x + 8, box_y + 17 + j * 15)
            cr.show_text(text)

    def _current_vdiv(self):
        frame = self._frame
        if frame is None:
            return 1.0
        if self._show_ch1 and frame.info1:
            return frame.info1.ch_value
        if self._show_ch2 and frame.info2:
            return frame.info2.ch_value
        if frame.info1:
            return frame.info1.ch_value
        if frame.info2:
            return frame.info2.ch_value
        return 1.0

    def _apply_zoom(self, dx, dy, ctrl=False, shift=False):
        if dx == 0 and dy == 0:
            return
        factor = 1.15 ** (-dy)
        if ctrl:
            self._zoom_y = max(ZOOM_MIN, min(ZOOM_MAX, self._zoom_y * factor))
        elif shift:
            self._zoom_x = max(ZOOM_MIN, min(ZOOM_MAX, self._zoom_x * factor))
        else:
            self._zoom_x = max(ZOOM_MIN, min(ZOOM_MAX, self._zoom_x * factor))
            self._zoom_y = max(ZOOM_MIN, min(ZOOM_MAX, self._zoom_y * factor))
        self.area.queue_draw()

    def _on_scroll(self, widget, event):
        direction = event.direction
        if direction == Gdk.ScrollDirection.SMOOTH:
            try:
                dx, dy = event.get_scroll_deltas()
            except Exception:
                dx, dy = 0.0, 0.0
            if dx == 0 and dy == 0:
                dx = getattr(event, "delta_x", 0.0)
                dy = getattr(event, "delta_y", 0.0)
        elif direction == Gdk.ScrollDirection.UP:
            dx, dy = 0.0, -1.0
        elif direction == Gdk.ScrollDirection.DOWN:
            dx, dy = 0.0, 1.0
        elif direction == Gdk.ScrollDirection.LEFT:
            dx, dy = -1.0, 0.0
        elif direction == Gdk.ScrollDirection.RIGHT:
            dx, dy = 1.0, 0.0
        else:
            return False
        ctrl = bool(event.state & Gdk.ModifierType.CONTROL_MASK)
        shift = bool(event.state & Gdk.ModifierType.SHIFT_MASK)
        self._apply_zoom(dx, dy, ctrl=ctrl, shift=shift)
        return True

    def _on_button_press(self, widget, event):
        if event.button == 4:
            self._apply_zoom(0, -1)
            return True
        if event.button == 5:
            self._apply_zoom(0, 1)
            return True
        if event.button == 1:
            if self._cursors_on and not self._xy_mode:
                alloc = widget.get_allocation()
                hit = self._cursor_hit(event.x, event.y,
                                       max(alloc.width, 1), max(alloc.height, 1))
                if hit is not None:
                    self._cur_drag = hit
                    widget.grab_focus()
                    return True
            self._dragging = True
            self._drag_last = (event.x, event.y)
            widget.grab_focus()
            return True
        if event.button == 3:
            self._on_reset_zoom()
            return True
        return False

    def _on_button_release(self, widget, event):
        if event.button == 1:
            self._dragging = False
            self._drag_last = None
            self._cur_drag = None
            return True
        return False

    def _on_motion(self, widget, event):
        if self._cur_drag is not None:
            alloc = widget.get_allocation()
            w = max(alloc.width, 1)
            h = max(alloc.height, 1)
            self._cursor_update_drag(event.x, event.y, w, h)
            self.area.queue_draw()
            return True
        if not self._dragging or self._drag_last is None:
            return False
        alloc = widget.get_allocation()
        w = max(alloc.width, 1)
        h = max(alloc.height, 1)
        dx = (event.x - self._drag_last[0]) / w
        dy = (event.y - self._drag_last[1]) / h
        self._pan_x = max(-2.0, min(2.0, self._pan_x - dx))
        self._pan_y = max(-2.0, min(2.0, self._pan_y - dy))
        self._drag_last = (event.x, event.y)
        self.area.queue_draw()
        return True

    def set_analyse_active(self, active):
        if active:
            self.mode_analyse.set_active(True)
        else:
            self.mode_realtime.set_active(True)

    def _on_exit_clicked(self, button):
        if self._on_exit_memory is not None:
            self._on_exit_memory()

    def set_memory_mode(self, active):
        self._mem_mode = active
        self.exit_btn.set_sensitive(active)
        self.softkey_caption.set_text("MEMORY" if active else "WAVEFORM")

    def status_segments(self):
        segs = {"hor": "-", "ver": "-", "trig": "-", "mode": "-"}
        mode = ("Memory" if self._mem_mode else
                "Analyse" if self.mode_analyse.get_active() else "RealTime")
        segs["mode"] = mode
        tdiv = TIME_BASE_F[self._tim_base] if 0 <= self._tim_base < len(TIME_BASE_F) else 0.0
        if self._xy_mode:
            segs["hor"] = "XY  x%.2f" % self._zoom_x
            segs["trig"] = "XY  y%.2f" % self._zoom_y
        else:
            segs["hor"] = "%s/  x%.2f" % (format_time(tdiv), self._zoom_x)
            frame = self._frame
            trig_src = None
            parts = []
            for ch, on, info in ((1, self._show_ch1, frame.info1 if frame else None),
                                 (2, self._show_ch2, frame.info2 if frame else None)):
                if info is None:
                    continue
                if on:
                    parts.append("CH%d %.4g V/" % (ch, info.ch_value))
                    if trig_src is None:
                        trig_src = info
            segs["ver"] = "  ".join(parts) if parts else "-"
            if self._mem_mode:
                slot = frame.mem_slot if frame and frame.mem_slot is not None else "?"
                segs["trig"] = "MEM slot %s" % slot
            elif trig_src is not None:
                segs["trig"] = "Edge  %s" % format_hz(trig_src.freq)
            else:
                segs["trig"] = "-"
        return segs

    def _set_meas(self, ch, key, text):
        w = self._meas_widgets.get(ch, {}).get(key)
        if w is not None:
            w.set_text(text)

    def show_frame(self, frame):
        if frame.kind == "memory":
            self._mem_mode = True
        self._frame = frame
        self._tim_base = frame.tim_base

        info1 = frame.info1
        info2 = frame.info2
        self._update_measurements(frame, 1, info1)
        self._update_measurements(frame, 2, info2)

        if frame.kind == "memory":
            label = "memory slot %s, %d pts/ch" % (
                frame.mem_slot if frame.mem_slot is not None else "?",
                len(frame.ch1) if frame.ch1 else 0)
        else:
            label = "%s, %d pts/ch" % (frame.kind, len(frame.ch1) if frame.ch1 else 0)
        self.scale_label.set_text(label)
        self.exit_btn.set_sensitive(self._mem_mode)
        self.softkey_caption.set_text("MEMORY" if self._mem_mode else "WAVEFORM")
        if self._cursors_on and self._cursor_auto_pending:
            self._cursor_auto_pending = False
            self._cursor_auto()
        self.area.queue_draw()

    def _clear_measurements(self):
        for ch in (1, 2):
            for key in self._meas_widgets[ch]:
                self._set_meas(ch, key, "-")

    def _update_measurements(self, frame, ch, info):
        if info is None:
            for key in self._meas_widgets[ch]:
                self._set_meas(ch, key, "-")
            return
        probe = "10X" if info.probe_exp else "1X"
        volts = frame.time_samples(ch)
        self._set_meas(ch, "Probe", probe)
        self._set_meas(ch, "V/div", "%.4g V" % info.ch_value)
        self._set_meas(ch, "Vpp", "%.4g V" % info.vpp)
        self._set_meas(ch, "Vrms", "%.4g V" % info.vrms)
        self._set_meas(ch, "Vmax", "%.4g V" % info.vp_pos)
        self._set_meas(ch, "Vmin", "%.4g V" % info.vp_neg)
        self._set_meas(ch, "Freq", format_hz(info.freq))
        self._set_meas(ch, "Period", format_time(info.period))
        self._set_meas(ch, "t/div", format_time(TIME_BASE_F[self._tim_base] if 0 <= self._tim_base < len(TIME_BASE_F) else 0.0))
        if volts is not None and len(volts) > 0:
            vavg = float(np.mean(volts))
            self._set_meas(ch, "Vavg", "%.4g V" % vavg)
            mid = float((info.vp_pos + info.vp_neg) / 2.0)
            high = np.sum(volts > mid)
            duty = 100.0 * high / len(volts)
            self._set_meas(ch, "Duty+", "%.1f%%" % duty)
        else:
            self._set_meas(ch, "Vavg", "-")
            self._set_meas(ch, "Duty+", "-")

    def _on_load_clicked(self, button):
        last = 1
        if self._cfg is not None:
            last = self._cfg.getint("1", "NUM", fallback=1)
        dialog = Gtk.Dialog(title="Load from device memory", transient_for=self.get_toplevel(), modal=True)
        dialog.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL, "Load", Gtk.ResponseType.OK)
        spin = Gtk.SpinButton.new_with_range(1, 255, 1)
        spin.set_value(last)
        box = dialog.get_content_area()
        box.set_border_width(12)
        box.set_spacing(8)
        lbl = Gtk.Label(label="Waveform memory slot (ID):")
        lbl.set_halign(Gtk.Align.START)
        box.pack_start(lbl, False, False, 0)
        box.pack_start(spin, False, False, 0)
        dialog.show_all()
        resp = dialog.run()
        slot = spin.get_value_as_int()
        dialog.destroy()
        if resp == Gtk.ResponseType.OK:
            if self._cfg is not None:
                self._cfg.set("1", "NUM", str(slot))
            if self._on_load_slot is not None:
                self._on_load_slot(slot)

    def _on_save_clicked(self, button):
        frame = self._frame
        if frame is None:
            return
        dialog = Gtk.FileChooserDialog(title="Save waveform CSV", parent=self.get_toplevel(), action=Gtk.FileChooserAction.SAVE)
        dialog.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL, Gtk.STOCK_SAVE, Gtk.ResponseType.OK)
        dialog.set_current_name("%s_wave.csv" % frame.kind)
        resp = dialog.run()
        path = dialog.get_filename() if resp == Gtk.ResponseType.OK else None
        dialog.destroy()
        if path:
            self._save_csv(path, frame)

    def _on_open_clicked(self, button):
        dialog = Gtk.FileChooserDialog(title="Open waveform CSV", parent=self.get_toplevel(), action=Gtk.FileChooserAction.OPEN)
        dialog.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL, Gtk.STOCK_OPEN, Gtk.ResponseType.OK)
        filt = Gtk.FileFilter()
        filt.set_name("CSV")
        filt.add_pattern("*.csv")
        dialog.add_filter(filt)
        resp = dialog.run()
        path = dialog.get_filename() if resp == Gtk.ResponseType.OK else None
        dialog.destroy()
        if path:
            frame = self._load_csv(path)
            if frame is not None:
                self.set_memory_mode(True)
                self.show_frame(frame)

    def _on_png_clicked(self, button):
        frame = self._frame
        if frame is None:
            return
        dialog = Gtk.FileChooserDialog(title="Save waveform PNG", parent=self.get_toplevel(), action=Gtk.FileChooserAction.SAVE)
        dialog.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL, Gtk.STOCK_SAVE, Gtk.ResponseType.OK)
        dialog.set_current_name("%s_wave.png" % frame.kind)
        filt = Gtk.FileFilter()
        filt.set_name("PNG")
        filt.add_pattern("*.png")
        dialog.add_filter(filt)
        resp = dialog.run()
        path = dialog.get_filename() if resp == Gtk.ResponseType.OK else None
        dialog.destroy()
        if path:
            self._save_png(path)

    def _save_png(self, path):
        from gi.repository import Gdk
        alloc = self.area.get_allocation()
        w, h = max(alloc.width, 400), max(alloc.height, 300)
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
        cr = cairo.Context(surface)
        self._draw(self.area, cr)
        surface.write_to_png(path)

    def _save_csv(self, path, frame):
        import numpy as np
        v1 = frame.time_samples(1)
        v2 = frame.time_samples(2)
        n = len(v1) if v1 is not None else (len(v2) if v2 is not None else 0)
        info1 = frame.info1
        info2 = frame.info2
        vdiv1 = info1.ch_value if info1 else 1.0
        vdiv2 = info2.ch_value if info2 else 1.0
        header = ("# kind, tim_base, mem_slot, vdiv1, vdiv2, "
                  "info1.vpp, info1.vrms, info1.freq, info1.period, "
                  "info2.vpp, info2.vrms, info2.freq, info2.period\n")

        def f(x):
            return "%.6g" % x if x is not None else ""

        row = "%s,%d,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s\n" % (
            frame.kind, frame.tim_base, frame.mem_slot if frame.mem_slot is not None else "",
            "%.6g" % vdiv1, "%.6g" % vdiv2,
            f(info1.vpp if info1 else None), f(info1.vrms if info1 else None),
            f(info1.freq if info1 else None), f(info1.period if info1 else None),
            f(info2.vpp if info2 else None), f(info2.vrms if info2 else None),
            f(info2.freq if info2 else None), f(info2.period if info2 else None))
        with open(path, "w") as fh:
            fh.write("# format,2\n")
            fh.write(header)
            fh.write(row)
            fh.write("# index, volts_ch1, volts_ch2\n")
            for i in range(n):
                a = "%.6g" % v1[i] if v1 is not None else ""
                b = "%.6g" % v2[i] if v2 is not None and i < len(v2) else ""
                fh.write("%d,%s,%s\n" % (i, a, b))

    def _load_csv(self, path):
        import numpy as np
        from ..models import CODE_CENTER, CODES_PER_DIV, DsoFrame
        meta = None
        rows = []
        fmt2 = False
        with open(path) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                if line.startswith("#"):
                    if line == "# format,2":
                        fmt2 = True
                    continue
                parts = line.split(",")
                if meta is None and len(parts) >= 11 and parts[0] in ("realtime", "analyse", "memory"):
                    try:
                        tim = int(parts[1])
                        slot = int(parts[2]) if parts[2] else None
                        meta = (parts[0], tim, slot, parts)
                        continue
                    except ValueError:
                        pass
                if parts[0].isdigit():
                    rows.append(parts)
        if meta is None or not rows:
            return None
        kind, tim, slot, meta_parts = meta
        n = min(len(rows), 600)
        raw1 = [0] * n
        raw2 = [0] * n
        has2 = False
        for i, parts in enumerate(rows[:n]):
            if len(parts) >= 2 and parts[1]:
                raw1[i] = float(parts[1])
            if len(parts) >= 3 and parts[2]:
                raw2[i] = float(parts[2])
                has2 = True
        vdiv1 = 1.0
        vdiv2 = 1.0
        if len(meta_parts) >= 13:
            try:
                vdiv1 = float(meta_parts[3]) or 1.0
                vdiv2 = float(meta_parts[4]) or 1.0
            except ValueError:
                pass
        codes1 = bytearray(600)
        codes2 = bytearray(600)
        for i in range(n):
            if fmt2:
                c1 = CODE_CENTER - raw1[i] * CODES_PER_DIV / vdiv1
                c2 = CODE_CENTER - raw2[i] * CODES_PER_DIV / vdiv2
            else:
                c1 = 128.0 + raw1[i] * 8.0 / vdiv1
                c2 = 128.0 + raw2[i] * 8.0 / vdiv2
            codes1[i] = max(0, min(255, int(round(c1))))
            codes2[i] = max(0, min(255, int(round(c2))))
        if n > 0:
            for arr in (codes1, codes2):
                tail = arr[n - 1]
                for j in range(n, 600):
                    arr[j] = tail
        payload = bytearray([3 if has2 else 1])
        payload.extend(codes1)
        payload.extend(codes2 if has2 else bytes(600))
        payload.extend(bytes([0, 0, tim, 0, 0, 0, 0, 0, 0, 0]))
        import struct

        def info_bytes(vdiv):
            from ..protocol import VOLT_BASE_F
            ch = 6
            exp = 0
            for i, base in enumerate(VOLT_BASE_F):
                if base <= 0:
                    continue
                if abs(base - vdiv) < 1e-9:
                    ch, exp = i, 0
                    break
                if vdiv > base * 10 and abs(base * 10 - vdiv) < 1e-9:
                    ch, exp = i, 1
                    break
            else:
                vdiv = 1.0
            info = bytearray(73)
            info[0] = ch
            info[4] = exp
            struct.pack_into("<ff", info, 25, 0.0, 2.0)
            struct.pack_into("<ff", info, 37, 1.0, -1.0)
            struct.pack_into("<ff", info, 45, 0.02, 50.0)
            return bytes(info)

        payload.extend(info_bytes(vdiv1))
        payload.extend(info_bytes(vdiv2))
        frame = DsoFrame.from_payload(bytes(payload), "memory")
        frame.mem_slot = slot
        frame.tim_base = tim
        return frame

    def _measurement_text(self, frame):
        parts = []
        for idx, info in ((1, frame.info1), (2, frame.info2)):
            if info is None:
                continue
            parts.append(
                "CH%d Vpp:%.3gV +Vp:%.3gV -Vp:%.3gV Vrms:%.3gV Freq:%s Period:%s"
                % (idx, info.vpp, info.vp_pos, info.vp_neg, info.vrms,
                   format_hz(info.freq), format_time(info.period)))
        return "   ".join(parts)

    def _draw(self, widget, cr):
        alloc = widget.get_allocation()
        width = float(alloc.width)
        height = float(alloc.height)

        cr.set_source_rgb(*SCREEN_BG)
        cr.paint()

        frame = self._frame
        vdiv = self._current_vdiv()
        tdiv = TIME_BASE_F[self._tim_base] if 0 <= self._tim_base < len(TIME_BASE_F) else 0.0

        if not self._xy_mode:
            self._draw_grid(cr, width, height, vdiv, tdiv)

        if frame is None:
            cr.set_source_rgba(0.75, 0.8, 0.9, 0.45)
            cr.set_font_size(14)
            cr.move_to(width / 2 - 40, height / 2)
            cr.show_text("No data")
            return False

        if self._xy_mode:
            self._draw_xy(cr, frame, width, height)
        else:
            v_half = vdiv * GRID_ROWS / 2.0
            badges = []
            if self._show_ch1 and frame.ch1 is not None:
                self._draw_channel(cr, frame, 1, width, height, CH1_COLOR)
                if frame.info1:
                    badges.append((1, frame.info1))
                    self._draw_ground_marker(
                        cr, self._volt_to_y(0.0, v_half, height), CH1_COLOR)
            if self._show_ch2 and frame.ch2 is not None:
                self._draw_channel(cr, frame, 2, width, height, CH2_COLOR)
                if frame.info2:
                    badges.append((2, frame.info2))
                    self._draw_ground_marker(
                        cr, self._volt_to_y(0.0, v_half, height), CH2_COLOR)
            for i, (ch, info) in enumerate(badges):
                self._draw_badge(cr, 8, 8 + i * 24, "CH%d  %.4g V/" % (ch, info.ch_value),
                                 CH1_COLOR if ch == 1 else CH2_COLOR)

        self._draw_cursors(cr, width, height, vdiv, tdiv)

        if self._zoom_x != 1.0 or self._zoom_y != 1.0 or self._pan_x or self._pan_y:
            cr.set_source_rgba(0.6, 0.8, 1.0, 0.9)
            cr.set_font_size(10)
            cr.move_to(8, height - 8)
            cr.show_text("zoom x%.2f y%.2f  pan %.2f %.2f" % (
                self._zoom_x, self._zoom_y, self._pan_x, self._pan_y))
        return False

    def _draw_badge(self, cr, x, y, text, color):
        cr.save()
        cr.set_font_size(12)
        ext = cr.text_extents(text)
        tw = getattr(ext, "width", None)
        if tw is None:
            tw = ext[2]
        th = getattr(ext, "height", None)
        if th is None:
            th = ext[3]
        cr.set_source_rgb(*color)
        cr.rectangle(x, y, tw + 14, th + 9)
        cr.fill()
        cr.set_source_rgb(0.0, 0.0, 0.0)
        cr.move_to(x + 7, y + th + 2)
        cr.show_text(text)
        cr.restore()

    def _draw_ground_marker(self, cr, y, color):
        cr.save()
        cr.set_source_rgb(*color)
        cr.move_to(0, y - 5)
        cr.line_to(9, y)
        cr.line_to(0, y + 5)
        cr.close_path()
        cr.fill()
        cr.restore()

    def _volt_to_y(self, volts, v_half, height):
        ny = 0.5 - 0.5 * (volts / v_half) if v_half else 0.5
        cy = 0.5 + self._pan_y
        return ((ny - cy) * self._zoom_y + 0.5) * height

    def _time_frac_to_x(self, frac, width):
        cx = 0.5 + self._pan_x
        return ((frac - cx) * self._zoom_x + 0.5) * width

    def _draw_grid(self, cr, width, height, vdiv, tdiv):
        if vdiv <= 0:
            vdiv = 1.0
        v_half = vdiv * GRID_ROWS / 2.0
        volts_per_div = vdiv
        t_per_div = tdiv
        lim_x = int(GRID_COLS * max(1.0, 1.0 / abs(self._zoom_x))) + 2
        lim_y = int(GRID_ROWS * max(1.0, 1.0 / abs(self._zoom_y))) + 2

        cr.set_line_width(1)

        cr.set_source_rgba(0.62, 0.75, 1.0, 0.10)
        for i in range(-lim_x, lim_x + 1):
            frac = 0.5 + i / float(GRID_COLS)
            x = self._time_frac_to_x(frac, width)
            if -1 <= x <= width + 1:
                cr.move_to(x, 0)
                cr.line_to(x, height)
        for i in range(-lim_y * 2, lim_y * 2 + 1):
            v = i * volts_per_div
            y = self._volt_to_y(v, v_half, height)
            if -1 <= y <= height + 1:
                cr.move_to(0, y)
                cr.line_to(width, y)
        cr.stroke()

        y0 = self._volt_to_y(0.0, v_half, height)
        x0 = self._time_frac_to_x(0.5, width)
        cr.set_source_rgba(0.62, 0.75, 1.0, 0.34)
        cr.move_to(0, y0)
        cr.line_to(width, y0)
        cr.move_to(x0, 0)
        cr.line_to(x0, height)
        cr.stroke()

        cr.set_source_rgba(0.62, 0.75, 1.0, 0.30)
        for i in range(-lim_x * 5, lim_x * 5 + 1):
            if i % 5 == 0:
                continue
            frac = 0.5 + i / float(GRID_COLS * 5)
            x = self._time_frac_to_x(frac, width)
            if 0 <= x <= width:
                cr.move_to(x, y0 - 3)
                cr.line_to(x, y0 + 3)
        for i in range(-lim_y * 10, lim_y * 10 + 1):
            if i % 10 == 0:
                continue
            v = (i / 10.0) * volts_per_div
            y = self._volt_to_y(v, v_half, height)
            if 0 <= y <= height:
                cr.move_to(x0 - 3, y)
                cr.line_to(x0 + 3, y)
        cr.stroke()

        cr.set_source_rgba(0.72, 0.78, 0.92, 0.55)
        cr.set_font_size(10)
        for i in range(-lim_y * 2, lim_y * 2 + 1):
            v = i * volts_per_div
            if not is_decade_label(v):
                continue
            y = self._volt_to_y(v, v_half, height)
            if 14 <= y <= height - 14:
                cr.move_to(14, y - 3)
                cr.show_text("%.4g V" % v)
        if tdiv > 0 and not self._xy_mode:
            for i in range(-lim_x, lim_x + 1):
                frac = 0.5 + (i / float(GRID_COLS))
                x = self._time_frac_to_x(frac, width)
                if 44 <= x <= width - 8:
                    t = i * t_per_div
                    cr.move_to(x - 14, height - 6)
                    cr.show_text("0" if i == 0 else format_time(t))

    def _draw_xy(self, cr, frame, width, height):
        if frame is None or frame.ch1 is None or frame.ch2 is None:
            cr.set_source_rgba(1, 1, 1, 0.5)
            cr.move_to(width / 2 - 60, height / 2)
            cr.show_text("XY needs both CH1 and CH2")
            return
        vx = frame.time_samples(1)
        vy = frame.time_samples(2)
        n = min(len(vx), len(vy))
        if n < 2:
            return
        vx = vx[:n]
        vy = vy[:n]
        i1 = frame.info1
        i2 = frame.info2
        vdiv_x = i1.ch_value if i1 else 1.0
        vdiv_y = i2.ch_value if i2 else 1.0
        v_half_x = vdiv_x * GRID_COLS / 2.0
        v_half_y = vdiv_y * GRID_ROWS / 2.0

        zx, zy = self._zoom_x, self._zoom_y
        cx, cy = 0.5 + self._pan_x, 0.5 + self._pan_y
        nx = 0.5 + 0.5 * (vx / v_half_x)
        ny = 0.5 - 0.5 * (vy / v_half_y)
        xs = ((nx - cx) * zx + 0.5) * width
        ys = ((ny - cy) * zy + 0.5) * height
        def trace():
            cr.move_to(xs[0], ys[0])
            for i in range(1, n):
                cr.line_to(xs[i], ys[i])

        cr.set_line_width(6.0)
        cr.set_source_rgba(CH2_COLOR[0], CH2_COLOR[1], CH2_COLOR[2], 0.16)
        trace()
        cr.stroke()
        cr.set_line_width(1.6)
        cr.set_source_rgb(*CH2_COLOR)
        trace()
        cr.stroke()

        cr.set_source_rgba(0.62, 0.75, 1.0, 0.10)
        cr.set_line_width(1)
        for i in range(-GRID_COLS, GRID_COLS + 1):
            x = ((0.5 + i / float(GRID_COLS) - cx) * zx + 0.5) * width
            if -1 <= x <= width + 1:
                cr.move_to(x, 0)
                cr.line_to(x, height)
        for i in range(-GRID_ROWS, GRID_ROWS + 1):
            y = ((0.5 + i / float(GRID_ROWS) - cy) * zy + 0.5) * height
            if -1 <= y <= height + 1:
                cr.move_to(0, y)
                cr.line_to(width, y)
        cr.stroke()

        cr.set_source_rgba(0.62, 0.75, 1.0, 0.34)
        x0 = ((0.5 - cx) * zx + 0.5) * width
        y0 = ((0.5 - cy) * zy + 0.5) * height
        cr.move_to(0, y0)
        cr.line_to(width, y0)
        cr.move_to(x0, 0)
        cr.line_to(x0, height)
        cr.stroke()

        cr.set_source_rgba(0.72, 0.78, 0.92, 0.55)
        cr.set_font_size(10)
        volts_per_x = vdiv_x / zx if zx else vdiv_x
        for i in range(-GRID_COLS, GRID_COLS + 1):
            v = i * volts_per_x
            x = ((0.5 + i / float(GRID_COLS) - cx) * zx + 0.5) * width
            if 44 <= x <= width - 8 and is_decade_label(v):
                cr.move_to(x - 10, height - 6)
                cr.show_text("%.4g V" % v)
        volts_per_y = vdiv_y / zy if zy else vdiv_y
        for i in range(-GRID_ROWS, GRID_ROWS + 1):
            v = i * volts_per_y
            y = ((0.5 + i / float(GRID_ROWS) - cy) * zy + 0.5) * height
            if 14 <= y <= height - 14 and is_decade_label(v):
                cr.move_to(14, y - 3)
                cr.show_text("%.4g V" % v)

        cr.set_font_size(12)
        cr.set_source_rgb(*CH1_COLOR)
        cr.move_to(8, 16)
        cr.show_text("XY  X=CH1  Y=CH2")
        cr.set_source_rgba(0.72, 0.78, 0.92, 0.85)
        cr.set_font_size(11)
        cr.move_to(8, 34)
        f1 = format_hz(i1.freq) if i1 else "-"
        f2 = format_hz(i2.freq) if i2 else "-"
        ratio = (i2.freq / i1.freq) if i1 and i1.freq and i2 else 0.0
        cr.show_text("Freq X=%s  Y=%s  ratio=%.4g" % (f1, f2, ratio))

    def _draw_channel(self, cr, frame, channel, width, height, color):
        volts = frame.time_samples(channel)
        info = frame.info1 if channel == 1 else frame.info2
        n = len(volts)
        if n == 0:
            return
        vdiv = info.ch_value if info else 1.0

        zx, zy = self._zoom_x, self._zoom_y
        cx = 0.5 + self._pan_x
        cy = 0.5 + self._pan_y
        nx = np.arange(n, dtype=np.float64) / max(n - 1, 1)
        v_half = vdiv * GRID_ROWS / 2.0
        xs = ((nx - cx) * zx + 0.5) * width
        ys = ((0.5 - 0.5 * (volts / v_half) - cy) * zy + 0.5) * height

        def trace():
            cr.move_to(xs[0], ys[0])
            for i in range(1, n):
                cr.line_to(xs[i], ys[i])

        cr.set_line_width(6.0)
        cr.set_source_rgba(color[0], color[1], color[2], 0.16)
        trace()
        cr.stroke()

        cr.set_line_width(1.6)
        cr.set_source_rgb(*color)
        trace()
        cr.stroke()
