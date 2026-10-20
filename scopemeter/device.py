import queue
import threading
import time

from . import protocol as proto
from .models import DMMRecord, DmmBatch, DsoFrame


MACHINE_UNKNOWN = 2
MACHINE_DSO = 0
MACHINE_DMM = 1

DSO_MODE_OLD = 0
DSO_MODE_REALTIME = 1
DSO_MODE_ANALYSE = 2

DMM_MODE_AUTORECORD = 0
DMM_MODE_CALIBRATION = 1
DMM_MODE_REALTIME = 2
DMM_MODE_HOLD = 3
DMM_MODE_INFO = 4


class ScopeMeterDevice:
    def __init__(self):
        self._serial = None
        self._write_lock = threading.Lock()
        self._rx_queue = queue.Queue()
        self._stop = threading.Event()
        self._threads = []
        self.machine = MACHINE_UNKNOWN
        self.dso_mode = DSO_MODE_REALTIME
        self.dmm_mode = DMM_MODE_REALTIME
        self.connected = False
        self.port_name = ""
        self.mem_slot = None
        self.mem_hold = False
        self._mem_frame_slot = None
        self._mem_retries = 0
        self._mem_pause_until = 0.0
        self.on_status = None
        self.on_dso = None
        self.on_dmm = None
        self.on_dmm_batch = None
        self.on_production = None
        self.on_connection = None

    def open(self, serial_port):
        self.close()
        self._serial = serial_port
        self.port_name = getattr(serial_port, "port", "") or ""
        self._stop.clear()
        self._threads = [
            threading.Thread(target=self._reader_loop, daemon=True),
            threading.Thread(target=self._poller_loop, daemon=True),
        ]
        for t in self._threads:
            t.start()
        self._send_command(proto.CMD_POLL)
        self.connected = True
        if self.on_connection:
            self.on_connection(True, self.port_name)

    def close(self):
        self._stop.set()
        ser = self._serial
        self._serial = None
        if ser is not None:
            try:
                ser.close()
            except Exception:
                pass
        self.connected = False
        self.machine = MACHINE_UNKNOWN
        while not self._rx_queue.empty():
            try:
                self._rx_queue.get_nowait()
            except queue.Empty:
                break
        if self.on_connection:
            self.on_connection(False, self.port_name)

    def set_dso_mode(self, mode):
        self.dso_mode = mode
        if mode == DSO_MODE_REALTIME:
            self._send_command(proto.CMD_DSO_REALTIME)
        elif mode == DSO_MODE_ANALYSE:
            self._send_command(proto.CMD_DSO_ANALYSE)

    def load_wave(self, slot):
        if not proto.MEM_SLOT_MIN <= slot <= proto.MEM_SLOT_MAX:
            raise ValueError("slot must be %d..%d" % (proto.MEM_SLOT_MIN, proto.MEM_SLOT_MAX))
        self.mem_slot = slot
        self._mem_frame_slot = slot
        self.mem_hold = True
        self._mem_pause_until = time.time() + 3.0
        self._mem_retries = 3
        self._send_raw(proto.build_load_wave(slot))

    def clear_mem_hold(self):
        self.mem_hold = False
        self._mem_frame_slot = None
        self.mem_slot = None
        self._mem_pause_until = 0.0

    def set_dmm_mode(self, mode):
        self.dmm_mode = mode

    def request_production_info(self):
        self._send_command(proto.CMD_PRODUCTION_INFO)

    def _send_command(self, cmd, param=0):
        ser = self._serial
        if ser is None:
            return
        try:
            with self._write_lock:
                ser.write(proto.build_command(cmd, param))
        except Exception:
            pass

    def _send_payload(self, payload):
        ser = self._serial
        if ser is None:
            return
        try:
            with self._write_lock:
                ser.write(proto.build_payload(payload))
        except Exception:
            pass

    def _send_raw(self, frame):
        ser = self._serial
        if ser is None:
            return
        try:
            with self._write_lock:
                ser.write(frame)
        except Exception:
            pass

    def _reader_loop(self):
        parser = proto.FrameParser()
        while not self._stop.is_set():
            ser = self._serial
            if ser is None:
                time.sleep(0.05)
                continue
            try:
                waiting = getattr(ser, "in_waiting", 0) or 0
                if waiting:
                    data = ser.read(min(8192, waiting))
                else:
                    data = ser.read(64)
            except Exception:
                data = b""
            if not data:
                continue
            for frame in parser.feed(data):
                self._rx_queue.put(frame)

    def _poller_loop(self):
        while not self._stop.is_set():
            if self._serial is None:
                time.sleep(0.1)
                continue
            if self.mem_hold and self.machine == MACHINE_DSO:
                if self._mem_frame_slot is not None and self._mem_retries > 0:
                    self._send_raw(proto.build_load_wave(self._mem_frame_slot))
                    self._mem_retries -= 1
                self._drain_queue()
                self._stop.wait(0.4)
                continue
            self._send_command(proto.CMD_POLL)
            delay = 0.5
            if self.machine == MACHINE_DSO:
                if time.time() < self._mem_pause_until:
                    delay = 0.2
                elif self.dso_mode == DSO_MODE_REALTIME:
                    self._send_command(proto.CMD_DSO_REALTIME)
                    delay = 0.15
                elif self.dso_mode == DSO_MODE_ANALYSE:
                    self._send_command(proto.CMD_DSO_ANALYSE)
                    delay = 0.4
            elif self.machine == MACHINE_DMM:
                if self.dmm_mode == DMM_MODE_AUTORECORD:
                    self._send_command(proto.CMD_DMM_AUTORECORD)
                elif self.dmm_mode == DMM_MODE_CALIBRATION:
                    self._send_command(proto.CMD_DMM_CALIBRATION)
                elif self.dmm_mode == DMM_MODE_INFO:
                    self._send_command(proto.CMD_PRODUCTION_INFO)
                    self.dmm_mode = DMM_MODE_REALTIME
            self._drain_queue()
            self._stop.wait(delay)

    def _drain_queue(self):
        while True:
            try:
                frame = self._rx_queue.get_nowait()
            except queue.Empty:
                return
            self._dispatch(frame)

    def _dispatch(self, frame):
        t = frame.type
        p = frame.payload
        if t == proto.TYPE_STATUS:
            mode = p[0]
            new_machine = MACHINE_DSO if mode == 0 else MACHINE_DMM
            changed = new_machine != self.machine
            self.machine = new_machine
            if self.on_status:
                self.on_status(mode)
            if changed and new_machine == MACHINE_DSO and self.dso_mode == DSO_MODE_REALTIME:
                self._send_command(proto.CMD_DSO_REALTIME)
        elif t == proto.TYPE_DSO_REALTIME:
            self._emit_dso(DsoFrame.from_payload(p, "realtime"))
        elif t == proto.TYPE_OLD_DRAW:
            frame = DsoFrame.from_payload(p, "memory", skip=4)
            frame.mem_slot = self.mem_slot
            self._mem_retries = 0
            self._emit_dso(frame)
        elif t == proto.TYPE_DSO_ANALYSE:
            self._emit_dso(DsoFrame.from_payload(p, "analyse"))
        elif t == proto.TYPE_DMM_REALTIME:
            if len(p) >= 9 and self.on_dmm:
                self.on_dmm(DMMRecord.from_bytes(p, 0))
        elif t in (proto.TYPE_DMM_RECORD1, proto.TYPE_DMM_RECORD2):
            if self.on_dmm_batch:
                self.on_dmm_batch(DmmBatch.from_payload(p))
        elif t == proto.TYPE_PRODUCTION:
            if self.on_production:
                self.on_production(p)
        elif t == proto.TYPE_CALIBRATION:
            pass

    def _emit_dso(self, dso):
        if self.on_dso:
            self.on_dso(dso)


def list_ports():
    try:
        from serial.tools import list_ports as lp
    except ImportError:
        return []
    result = []
    for p in lp.comports():
        vid = p.vid
        pid = p.pid
        if vid == 0x2E88 and pid == 0x4603:
            result.insert(0, p)
        elif vid == 0x28E9 and pid == 0x018B:
            result.insert(1, p)
        else:
            result.append(p)
    return result


def open_port(port, baudrate=115200):
    import serial
    ser = serial.Serial(port=port, baudrate=baudrate, bytesize=8,
                        parity="N", stopbits=1, timeout=0.05, write_timeout=1)
    return ser
