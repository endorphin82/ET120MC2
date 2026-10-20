import struct

from .protocol import DMM_FUNCTIONS, TIME_BASE_F, VOLT_BASE_F


class DMMRecord:
    __slots__ = ("value", "range", "func", "point_num", "status_bar",
                 "range_out", "fvalue", "valid", "sign", "vbase")

    def __init__(self, value, range_, func, point_num, status_bar, range_out):
        self.value = value
        self.range = range_
        self.func = func
        self.point_num = point_num
        self.status_bar = status_bar
        self.range_out = range_out
        self.fvalue = value / (10.0 ** point_num)
        self.valid = range_out == 0
        self.sign = "+" if value >= 0 else "-"
        self.vbase = 1.0
        if func == 18:
            while abs(self.fvalue) > 1000.0:
                self.fvalue /= 1000.0
                self.vbase *= 1000.0

    @classmethod
    def from_bytes(cls, buf, offset=0):
        value, range_, func, point_num, status_bar, range_out = struct.unpack_from(
            "<iBBBBB", buf, offset)
        return cls(value, range_, func, point_num, status_bar, range_out)

    @property
    def unit(self):
        if self.func == 18:
            if self.vbase >= 1e6:
                return "MHz"
            if self.vbase >= 1000.0:
                return "KHz"
            return "Hz"
        info = DMM_FUNCTIONS.get(self.func)
        if info is None:
            return ""
        return info["range_unit"](self.range)

    @property
    def prefix(self):
        info = DMM_FUNCTIONS.get(self.func)
        return info["acdc"] if info else ""

    @property
    def kind(self):
        info = DMM_FUNCTIONS.get(self.func)
        return info["kind"] if info else "unknown"

    @property
    def decimals(self):
        return max(0, min(6, 4 - self.point_num))

    def display_value(self):
        if self.range_out == 1:
            return "0L"
        if self.range_out:
            return "-OL"
        if self.func == 18:
            return "%.4f" % abs(self.fvalue)
        return "%.*f" % (self.decimals, abs(self.fvalue))

    def display_text(self):
        if not self.valid:
            return "%s %s" % (self.display_value(), self.unit)
        sign = "-" if self.sign == "-" else ""
        return "%s%s %s" % (sign, self.display_value(), self.unit)

    def to_line(self):
        acdc = self.prefix
        return "%s %s" % (acdc, self.display_text()) if acdc else self.display_text()


import numpy as np

CODES_PER_DIV = 24.0
CODE_CENTER = 96.0
ANALYSE_FLIP = 245


class ChannelInfo:
    __slots__ = ("ch", "probe_exp", "ch_value", "vrms", "vpp", "vp_pos",
                 "vp_neg", "period", "freq")

    def __init__(self, ch, probe_exp, ch_value, vrms, vpp, vp_pos, vp_neg, period, freq):
        self.ch = ch
        self.probe_exp = probe_exp
        self.ch_value = ch_value
        self.vrms = vrms
        self.vpp = vpp
        self.vp_pos = vp_pos
        self.vp_neg = vp_neg
        self.period = period
        self.freq = freq

    @classmethod
    def from_bytes(cls, buf, offset=0):
        ch = buf[offset]
        exp = buf[offset + 4]
        base = VOLT_BASE_F[ch] if ch < len(VOLT_BASE_F) else 1.0
        ch_value = base * (10.0 ** exp)
        vrms, vpp = struct.unpack_from("<ff", buf, offset + 25)
        vp_pos, vp_neg = struct.unpack_from("<ff", buf, offset + 37)
        period, freq = struct.unpack_from("<ff", buf, offset + 45)
        return cls(ch, exp, ch_value, vrms, vpp, vp_pos, vp_neg, period, freq)


class DsoFrame:
    __slots__ = ("kind", "mask", "ch1", "ch2", "channel", "processing",
                 "tim_base", "info1", "info2", "points", "mem_slot")

    def __init__(self, kind, mask, ch1, ch2, channel, processing, tim_base, info1, info2):
        self.kind = kind
        self.mask = mask
        self.ch1 = ch1
        self.ch2 = ch2
        self.channel = channel
        self.processing = processing
        self.tim_base = tim_base
        self.info1 = info1
        self.info2 = info2
        if kind == "analyse":
            self.points = 2048
        elif kind == "memory":
            self.points = 300
        else:
            self.points = 300
        self.mem_slot = None

    def time_samples(self, channel):
        if channel == 1:
            raw, info = self.ch1, self.info1
        else:
            raw, info = self.ch2, self.info2
        if raw is None:
            return None
        codes = np.frombuffer(raw, dtype=np.uint8).astype(np.float64)
        if len(codes) >= 2 and np.array_equal(codes[0::2], codes[1::2]):
            codes = codes[0::2]
        if self.kind == "analyse":
            codes = float(ANALYSE_FLIP) - codes
        vdiv = info.ch_value if info else 1.0
        return (CODE_CENTER - codes) / CODES_PER_DIV * vdiv

    def sample_period(self):
        return TIME_BASE_F[self.tim_base] if 0 <= self.tim_base < len(TIME_BASE_F) else 0.0

    @classmethod
    def from_payload(cls, payload, kind="realtime", skip=0):
        p = payload[skip:]
        if p and p[0] not in (1, 2, 3) and len(p) > 1 and p[1] in (1, 2, 3):
            p = p[1:]
        mask = p[0]
        n = 2048 if kind == "analyse" else 600
        ch1 = bytes(p[1:1 + n]) if mask & 1 else None
        ch2 = bytes(p[1 + n:1 + 2 * n]) if mask & 2 else None
        hdr = 1 + 2 * n
        channel = p[hdr]
        processing = p[hdr + 1]
        tim_base = p[hdr + 2]
        info1 = ChannelInfo.from_bytes(p, hdr + 10) if mask & 1 else None
        info2 = ChannelInfo.from_bytes(p, hdr + 83) if mask & 2 else None
        return cls(kind, mask, ch1, ch2, channel, processing, tim_base, info1, info2)


class DmmBatch:
    __slots__ = ("count", "records")

    def __init__(self, count, records):
        self.count = count
        self.records = records

    @classmethod
    def from_payload(cls, payload):
        count = struct.unpack_from("<h", payload, 2)[0]
        records = []
        for i in range(max(0, count)):
            off = 6 + 9 * i
            if off + 9 > len(payload):
                break
            records.append(DMMRecord.from_bytes(payload, off))
        return cls(count, records)
