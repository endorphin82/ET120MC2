import os
import threading

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk

from .. import config as cfg
from ..device import (DSO_MODE_ANALYSE, DSO_MODE_REALTIME, MACHINE_DSO,
                      ScopeMeterDevice, list_ports, open_port)
from ..mock import MockSerial
from .dso_page import DsoPage
from .fft_page import FftPage

CSS = b"""
window { background: #12151d; }
headerbar { background: #1b2029; color: #d7dce6; border: none; box-shadow: none; }
.headerbar { background: #1b2029; }
headerbar label { color: #c9d2e4; }
.dim-label { color: #77808f; }
separator { background: #262d3d; }
button { background: #222837; color: #d7dce6; border: 1px solid #2e3547;
         border-radius: 3px; padding: 4px 10px; text-shadow: none;
         box-shadow: none; }
button:hover { background: #2b3346; border-color: #3b4560; }
button:disabled { color: #5c6577; }
radio, check { color: #d7dce6; }
combobox { color: #d7dce6; }
combobox button { background: #222837; }

.tabs button { background: transparent; border: none; box-shadow: none;
               padding: 9px 26px; color: #8fa0bd; font-weight: bold;
               letter-spacing: 1px; }
.tabs button:checked { color: #ffffff; background: #1d2330;
                       box-shadow: inset 0 -3px 0 #ff9f1e; }
.tabs button:hover { color: #cdd7ea; }

.topstrip { background: #171b26; padding: 6px 10px;
            border-bottom: 1px solid #202636; }
.topstrip label { color: #aeb8cc; }
.topstrip label.dim-label { color: #77808f; }
.modebox radio { background: #1a1f2b; color: #8fa0bd; border: 1px solid #2e3547;
                 padding: 4px 16px; box-shadow: none; }
.modebox radio:checked { background: #2f6cd0; color: #ffffff; border-color: #2f6cd0;
                         font-weight: bold; }
.modebox check, .modebox radio check, .modebox radio radio { min-width: 0; padding: 0;
     border: none; background: transparent; box-shadow: none; }

.chip { font-weight: bold; padding: 4px 12px; border-radius: 3px;
        background: #1a1f2b; border: 1px solid #2e3547; color: #7a8296; }
.chip:hover { background: #232a3b; }
.chip:checked { color: #ffffff; background: #2f3a52; border-color: #4a5a7d; }
.chip-ch1:checked { color: #4de077; background: #102a18; border-color: #2a6b3c; }
.chip-ch2:checked { color: #ffe600; background: #2a2510; border-color: #6b5f10; }

.softkeys { background: #141824; padding: 8px; border-left: 1px solid #202636; }
.softkey-caption { color: #6d7891; font-size: 11px; font-weight: bold;
                   letter-spacing: 2px; padding: 2px 0 6px 0; }
.softkey { background: #1a1f2b; border: 1px solid #262d3d; border-radius: 2px;
           padding: 7px; box-shadow: none; text-shadow: none; }
.softkey:hover { background: #232a3b; border-color: #3a465f; }
.softkey-danger { border-color: #4a2a2a; color: #ff9f9f; }

.meas-strip { background: #141824; border-top: 1px solid #202636;
              padding: 6px 10px; }
.meas-key { color: #6d7891; font-size: 10px; }
.meas-value { color: #e6ebf7; font-size: 12px; font-family: monospace;
              font-weight: bold; }
.meas-chip { font-size: 12px; font-weight: bold; padding: 2px 6px;
             border-radius: 3px; }
.meas-chip.chip-ch1 { background: #4de077; color: #000000; }
.meas-chip.chip-ch2 { background: #ffe600; color: #000000; }

.statusbar { background: #0c0f16; border-top: 1px solid #232a38; }
.seg { padding: 5px 16px; }
.seg-title { color: #6d7891; font-size: 11px; font-weight: bold;
             letter-spacing: 1px; }
.seg-value { color: #d9e1f2; font-size: 13px; font-family: monospace; }
.seg-mode { color: #ffb000; font-size: 13px; font-weight: bold;
            font-family: monospace; padding: 5px 16px; }

.status-ok { color: #7cfc9a; }
.status-warn { color: #ffc107; }
"""


class MockLink:
    def __init__(self, machine="dso"):
        self.serial = MockSerial(machine=machine)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)

    def _loop(self):
        while not self._stop.is_set():
            self.serial.tick()
            self._stop.wait(0.25)

    def open(self):
        self._thread.start()
        return self.serial

    def close(self):
        self._stop.set()


class App(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="io.github.scopemeter")
        self.device = ScopeMeterDevice()
        self._held_memory = None
        self.mock_link = None
        self.cfg = cfg.load()
        self.dso_page = None
        self.fft_page = None

    def do_activate(self):
        win = self.props.active_window
        if not win:
            win = self._build_window()
        win.present()

    def _build_window(self):
        css = Gtk.CssProvider()
        css.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        win = Gtk.ApplicationWindow(application=self)
        win.set_default_size(1180, 780)
        win.set_title("ScopeMeter ET120MC2")

        header = Gtk.HeaderBar()
        header.set_show_close_button(True)
        header.set_title("ScopeMeter ET120MC2")
        header.set_subtitle("Oscilloscope host")
        win.set_titlebar(header)

        self.port_combo = Gtk.ComboBoxText()
        self.port_combo.set_size_request(220, -1)
        self._refresh_ports()
        header.pack_start(self.port_combo)

        refresh_btn = Gtk.Button.new_from_icon_name("view-refresh-symbolic", Gtk.IconSize.BUTTON)
        refresh_btn.connect("clicked", lambda b: self._refresh_ports())
        header.pack_start(refresh_btn)

        self.connect_btn = Gtk.Button(label="Connect")
        self.connect_btn.connect("clicked", self._toggle_connect)
        header.pack_start(self.connect_btn)

        self.info_btn = Gtk.Button(label="Device info")
        self.info_btn.set_sensitive(False)
        self.info_btn.connect("clicked", self._on_device_info)
        header.pack_start(self.info_btn)

        self.status_label = Gtk.Label(label="disconnected")
        self.status_label.get_style_context().add_class("dim-label")
        header.pack_end(self.status_label)

        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)

        self.dso_page = DsoPage(self._on_dso_mode, on_load_slot=self._on_load_slot,
                                cfg=self.cfg, on_exit_memory=self._on_exit_memory,
                                on_request_info=self._on_device_info)
        self.fft_page = FftPage()

        self.stack.add_titled(self.dso_page, "dso", "DSO")
        self.stack.add_titled(self.fft_page, "fft", "FFT")

        switcher = Gtk.StackSwitcher()
        switcher.set_stack(self.stack)
        switcher.get_style_context().add_class("tabs")

        statusbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        statusbar.get_style_context().add_class("statusbar")
        self._seg_values = {}
        for key, title in (("hor", "HOR"), ("ver", "VER"), ("trig", "TRIG")):
            seg = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            seg.get_style_context().add_class("seg")
            t = Gtk.Label(label=title)
            t.get_style_context().add_class("seg-title")
            v = Gtk.Label(label="-")
            v.set_xalign(0.0)
            v.get_style_context().add_class("seg-value")
            seg.pack_start(t, False, False, 0)
            seg.pack_start(v, False, False, 0)
            statusbar.pack_start(seg, False, False, 0)
            statusbar.pack_start(
                Gtk.Separator(orientation=Gtk.Orientation.VERTICAL),
                False, False, 0)
            self._seg_values[key] = v
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        statusbar.pack_start(spacer, True, True, 0)
        mode_lbl = Gtk.Label(label="-")
        mode_lbl.set_xalign(1.0)
        mode_lbl.get_style_context().add_class("seg-mode")
        statusbar.pack_start(mode_lbl, False, False, 0)
        self._seg_values["mode"] = mode_lbl

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        vbox.pack_start(switcher, False, False, 0)
        vbox.pack_start(self.stack, True, True, 0)
        vbox.pack_start(statusbar, False, False, 0)
        win.add(vbox)

        self._wire_device()
        win.connect("destroy", self._on_destroy)
        win.show_all()
        page = os.environ.get("SCOPEMETER_PAGE")
        if page in ("dso", "fft"):
            self.stack.set_visible_child_name(page)
        if os.environ.get("SCOPEMETER_AUTOCONNECT"):
            GLib.idle_add(self._toggle_connect, None)
        if os.environ.get("SCOPEMETER_CURSORS"):
            self.dso_page.set_cursors(True)
            self.fft_page.set_cursors(True)
        if os.environ.get("SCOPEMETER_MODE") == "analyse":
            self.dso_page.set_analyse_active(True)
        self._update_statusbar()
        GLib.timeout_add(500, self._update_statusbar)
        return win

    def _update_statusbar(self):
        child = self.stack.get_visible_child()
        segs = {}
        if child is not None and hasattr(child, "status_segments"):
            segs = child.status_segments()
        for key in ("hor", "ver", "trig", "mode"):
            self._seg_values[key].set_text(segs.get(key, "-"))
        return True

    def _wire_device(self):
        dev = self.device
        dev.on_connection = lambda ok, port: GLib.idle_add(self._on_connection, ok, port)
        dev.on_status = lambda mode: GLib.idle_add(self._on_status, mode)
        dev.on_dso = lambda frame: GLib.idle_add(self._on_dso_frame, frame)
        dev.on_production = lambda payload: GLib.idle_add(self._on_production, payload)

    def _refresh_ports(self):
        self.port_combo.remove_all()
        for p in list_ports():
            label = "%s  %s" % (p.device, (p.description or "")[:40])
            self.port_combo.append(p.device, label)
        self.port_combo.append("MOCK", "MOCK (virtual device)")
        default = os.environ.get("SCOPEMETER_DEFAULT_PORT")
        saved = default or self.cfg.get("0", "port", fallback="")
        if saved:
            self.port_combo.set_active_id(saved)
        if self.port_combo.get_active_id() is None:
            self.port_combo.set_active(0)

    def _toggle_connect(self, button):
        if self.device.connected:
            self.device.close()
            return
        port = self.port_combo.get_active_id()
        if not port:
            return
        try:
            if port == "MOCK":
                self.mock_link = MockLink(machine="dso")
                ser = self.mock_link.open()
            else:
                ser = open_port(port)
                self.mock_link = None
            self.cfg.set("0", "port", port)
            cfg.save(self.cfg)
            self.device.open(ser)
        except Exception as exc:
            self.status_label.set_text("error: %s" % exc)

    def _on_connection(self, ok, port):
        if ok:
            self.connect_btn.set_label("Disconnect")
            self.status_label.set_text("connected: %s" % port)
            self.info_btn.set_sensitive(True)
        else:
            self.connect_btn.set_label("Connect")
            self.status_label.set_text("disconnected")
            self.info_btn.set_sensitive(False)
            if self.mock_link:
                self.mock_link.close()
                self.mock_link = None
        return False

    def _on_status(self, mode):
        if self.device.connected:
            self.status_label.set_text("DSO mode" if mode == 0 else "mode=%s" % mode)
        return False

    def _on_device_info(self, *_):
        if not self.device.connected:
            return
        self.device.request_production_info()
        self.status_label.set_text("requesting device info…")

    def _on_production(self, payload):
        text = payload.decode("ascii", errors="replace").strip("\x00").strip()
        parts = [p for p in text.split("\x00") if p]
        if len(parts) >= 4:
            msg = "Type:%s  Time:%s  HW:%s  SW:%s" % tuple(parts[:4])
        else:
            msg = text[:80] if text else "no info"
        self.status_label.set_text(msg)
        dlg = Gtk.MessageDialog(
            transient_for=self.props.active_window,
            flags=Gtk.DialogFlags.MODAL,
            message_type=Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.OK,
            text="Device info — ET120MC2")
        dlg.format_secondary_text(msg)
        dlg.run()
        dlg.destroy()
        return False

    def _on_dso_mode(self, mode):
        self.device.set_dso_mode(DSO_MODE_ANALYSE if mode == 2 else DSO_MODE_REALTIME)

    def _on_load_slot(self, slot):
        try:
            self.device.load_wave(slot)
            self._held_memory = None
            self.stack.set_visible_child_name("dso")
            self.dso_page.set_analyse_active(False)
        except Exception as exc:
            self.status_label.set_text("load error: %s" % exc)

    def _on_exit_memory(self):
        self.device.clear_mem_hold()
        self._held_memory = None
        self.dso_page.set_analyse_active(False)
        self.device.set_dso_mode(DSO_MODE_REALTIME)

    def _on_dso_frame(self, frame):
        if frame.kind == "memory":
            self._held_memory = frame
            self.dso_page.show_frame(frame)
            self.fft_page.show_frame(frame)
            self.stack.set_visible_child_name("dso")
            self.status_label.set_text("memory slot %s loaded" % frame.mem_slot)
            return False
        if self.device.mem_hold and self._held_memory is not None:
            return False
        self.dso_page.show_frame(frame)
        self.fft_page.show_frame(frame)
        return False

    def _on_destroy(self, *args):
        if self.device.connected:
            self.device.close()
        if self.mock_link:
            self.mock_link.close()
        Gtk.main_quit()


def run():
    app = App()
    app.run([])
