import math
import struct
import threading
import time

from . import protocol as proto
from .models import ANALYSE_FLIP


def _long_frame(type_, payload):
    length = len(payload)
    body = bytearray([type_, length & 0xFF, (length >> 8) & 0xFF])
    body.extend(payload)
    frame = bytearray([proto.SYNC])
    frame.extend(body)
    frame.append(proto.checksum(frame))
    return bytes(frame)


def _status_frame(mode):
    frame = bytearray([proto.SYNC, proto.TYPE_STATUS, mode & 0xFF])
    frame.append(proto.checksum(frame))
    return bytes(frame)


def _dmm_record(value, point, func, range_=1):
    return struct.pack("<iBBBBB", value, range_, func, point, 0, 0)


def _channel_info(ch_index, vrms, vpp, vp_pos, vp_neg, period, freq, exp=0):
    info = bytearray(73)
    info[0] = ch_index
    info[4] = exp
    struct.pack_into("<ff", info, 25, vrms, vpp)
    struct.pack_into("<ff", info, 37, vp_pos, vp_neg)
    struct.pack_into("<ff", info, 45, period, freq)
    return bytes(info)


def _dso_realtime(ch1, ch2, tim_base=20):
    mask = (1 if ch1 is not None else 0) | (2 if ch2 is not None else 0)
    payload = bytearray([mask])
    payload.extend(ch1 if ch1 is not None else bytes(600))
    payload.extend(ch2 if ch2 is not None else bytes(600))
    payload.extend(bytes([0, 0, tim_base, 0, 0, 0, 0, 0, 0, 0]))
    payload.extend(_channel_info(6, 0.71, 2.0, 1.0, -1.0, 0.02, 50.0))
    payload.extend(_channel_info(6, 0.5, 1.4, 0.7, -0.7, 0.02, 50.0))
    return _long_frame(proto.TYPE_DSO_REALTIME, payload)


def _dso_analyse(ch1, ch2, tim_base=20):
    ch1 = bytes(ANALYSE_FLIP - b for b in ch1)
    ch2 = bytes(ANALYSE_FLIP - b for b in ch2)
    mask = (1 if ch1 is not None else 0) | (2 if ch2 is not None else 0)
    payload = bytearray([mask])
    payload.extend(ch1 if ch1 is not None else bytes(2048))
    payload.extend(ch2 if ch2 is not None else bytes(2048))
    payload.extend(bytes([0, 0, tim_base, 0, 0, 0, 0, 0, 0, 0]))
    payload.extend(_channel_info(6, 0.71, 2.0, 1.0, -1.0, 0.02, 50.0))
    payload.extend(_channel_info(6, 0.5, 1.4, 0.7, -0.7, 0.02, 50.0))
    return _long_frame(proto.TYPE_DSO_ANALYSE, payload)


def _dso_old_draw(ch1, ch2, tim_base=20):
    inner = bytearray([0, 0, 0, 0])
    mask = (1 if ch1 is not None else 0) | (2 if ch2 is not None else 0)
    inner.append(mask)
    inner.extend(ch1 if ch1 is not None else bytes(600))
    inner.extend(ch2 if ch2 is not None else bytes(600))
    inner.extend(bytes([0, 0, tim_base, 0, 0, 0, 0, 0, 0, 0]))
    inner.extend(_channel_info(6, 0.71, 2.0, 1.0, -1.0, 0.02, 50.0))
    inner.extend(_channel_info(6, 0.5, 1.4, 0.7, -0.7, 0.02, 50.0))
    return _long_frame(proto.TYPE_OLD_DRAW, bytes(inner))


def _record_batch(records):
    payload = bytearray(struct.pack("<h", 0))
    payload += struct.pack("<h", len(records))
    payload += struct.pack("<h", 0)
    for rec in records:
        payload += rec
    return _long_frame(proto.TYPE_DMM_RECORD1, payload)


class MockSerial:
    def __init__(self, machine="dso", frequency=50.0):
        self.port = "MOCK"
        self.is_open = True
        self._machine = machine
        self._freq = frequency
        self._rx = bytearray()
        self._lock = threading.Lock()
        self._cmd_buf = bytearray()
        self._t0 = time.time()
        self._frame_count = 0
        self._mem_slot = None

    def set_machine(self, machine):
        self._machine = machine

    @property
    def in_waiting(self):
        with self._lock:
            return len(self._rx)

    def read(self, n=1):
        with self._lock:
            if not self._rx:
                return b""
            data = bytes(self._rx[:n])
            del self._rx[:n]
            return data

    def write(self, data):
        self._cmd_buf.extend(data)
        while True:
            try:
                start = self._cmd_buf.index(proto.SYNC)
            except ValueError:
                del self._cmd_buf[:]
                return b""
            if start:
                del self._cmd_buf[:start]
            if len(self._cmd_buf) < 3:
                return b""
            if len(self._cmd_buf) >= 3 and self._cmd_buf[1] in (proto.CMD_DSO_REALTIME, proto.CMD_DSO_ANALYSE, proto.CMD_LOAD_WAVE):
                n = 4 if self._cmd_buf[1] == proto.CMD_LOAD_WAVE else 3
                if len(self._cmd_buf) >= n and self._cmd_buf[n - 1] == proto.checksum(self._cmd_buf[:n - 1]):
                    cmd = self._cmd_buf[1]
                    param = self._cmd_buf[2] if n == 4 else 0
                    del self._cmd_buf[:n]
                    self._handle_payload(cmd, param)
                    continue
            if len(self._cmd_buf) >= 4:
                cmd_frame = bytes(self._cmd_buf[:4])
                if cmd_frame[0] == proto.SYNC and cmd_frame[3] == proto.checksum(cmd_frame[:3]):
                    del self._cmd_buf[:4]
                    self._handle_command(cmd_frame[1])
                    continue
            del self._cmd_buf[:1]
        return b""

    def _handle_payload(self, cmd, param):
        if cmd == proto.CMD_LOAD_WAVE:
            self._handle_load(param)
        elif cmd == proto.CMD_DSO_REALTIME:
            self._emit(_dso_realtime(*self._wave_pair(600), tim_base=20))
        elif cmd == proto.CMD_DSO_ANALYSE:
            self._emit(_dso_analyse(*self._wave_pair(2048), tim_base=20))

    def _handle_load(self, slot):
        self._mem_slot = slot
        self._emit(_dso_old_draw(*self._wave_pair(600, slot=slot), tim_base=20))

    def close(self):
        self.is_open = False

    def _emit(self, data):
        with self._lock:
            self._rx.extend(data)

    def _handle_command(self, cmd):
        self._frame_count += 1
        if cmd == proto.CMD_POLL:
            self._emit(_status_frame(0 if self._machine == "dso" else 1))
            return
        if cmd == proto.CMD_PRODUCTION_INFO:
            payload = bytearray(b"ET120MC2") + bytes(8)
            payload += b"2024-01-01" + bytes(6)
            payload += b"HW100" + bytes(2)
            payload += b"SW105"
            self._emit(_long_frame(proto.TYPE_PRODUCTION, payload))
            return
        if self._machine != "dso":
            if cmd == proto.CMD_DMM_AUTORECORD:
                recs = [_dmm_record(*self._dmm_record_bytes()) for _ in range(8)]
                self._emit(_record_batch(recs))
            return
        if cmd == proto.CMD_DSO_REALTIME:
            self._emit(_dso_realtime(*self._wave_pair(600), tim_base=20))
        elif cmd == proto.CMD_DSO_ANALYSE:
            self._emit(_dso_analyse(*self._wave_pair(2048), tim_base=20))

    def _dmm_record_bytes(self):
        t = time.time() - self._t0
        volts = math.sin(2 * math.pi * self._freq * t)
        value = int(round(volts * 1000))
        return value, 3, 10, 1

    def tick(self):
        if self._machine == "dmm":
            self.stream_dmm()

    def stream_dmm(self):
        value, point, func, rng = self._dmm_record_bytes()
        payload = struct.pack("<iBBBBB", value, rng, func, point, 0, 0)
        self._emit(_long_frame(proto.TYPE_DMM_REALTIME, payload))

    def _wave_pair(self, n, slot=None):
        t = time.time() - self._t0
        if slot is not None:
            t = slot * 0.37
        ch1 = bytearray(n)
        ch2 = bytearray(n)
        for i in range(n):
            phase = 2 * math.pi * self._freq * t + 2 * math.pi * i / 300.0
            ch1[i] = max(0, min(255, int(96 + 90 * math.sin(phase))))
            ch2[i] = max(0, min(255, int(96 + 60 * math.sin(phase + 1.0))))
        return bytes(ch1), bytes(ch2)


def start_stream(mock, interval=0.1):
    stop = threading.Event()

    def _loop():
        while not stop.is_set():
            mock.tick()
            stop.wait(interval)

    thread = threading.Thread(target=_loop, daemon=True)
    thread.start()

    def stop_stream():
        stop.set()
        thread.join(timeout=1.0)

    return stop_stream
