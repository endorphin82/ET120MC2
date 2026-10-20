SYNC = 0xA5

CMD_POLL = 0x01
CMD_LOAD_WAVE = 0x02
CMD_DSO_REALTIME = 0x03
CMD_DSO_ANALYSE = 0x04
CMD_DMM_AUTORECORD = 0x05
CMD_DMM_CALIBRATION = 0x06
CMD_PRODUCTION_INFO = 0x09

MEM_SLOT_MIN = 1
MEM_SLOT_MAX = 255

TYPE_STATUS = 0x21
TYPE_OLD_DRAW = 0x22
TYPE_DSO_REALTIME = 0x23
TYPE_DSO_ANALYSE = 0x24
TYPE_DMM_REALTIME = 0x25
TYPE_DMM_RECORD1 = 0x26
TYPE_DMM_RECORD2 = 0x27
TYPE_CALIBRATION = 0x28
TYPE_PRODUCTION = 0x29

LONG_TYPES = frozenset(range(0x22, 0x2A)) | {0x3A}

TIME_BASE_LABELS = [
    "", "5nS", "10nS", "25nS", "50nS", "100ns", "200ns", "500nS",
    "1μS", "2μS", "5μS", "10μS", "20μS", "50μS", "100μS", "200μS",
    "500μS", "1mS", "2mS", "5mS", "10mS", "20mS", "50mS", "100mS",
    "200mS", "500mS", "1S", "2S", "5S", "10S", "20S", "50S",
]

TIME_BASE_F = [
    0.0, 5e-9, 1e-8, 2.5e-8, 5e-8, 1e-7, 2e-7, 5e-7,
    1e-6, 2e-6, 5e-6, 1e-5, 2e-5, 5e-5, 1e-4, 2e-4,
    5e-4, 1e-3, 2e-3, 5e-3, 1e-2, 2e-2, 5e-2, 1e-1,
    2e-1, 5e-1, 1.0, 2.0, 5.0, 10.0, 20.0, 50.0,
]

VOLT_BASE_LABELS = [
    "10mV", "20mV", "50mV", "100mV", "200mV", "500mV", "1V", "2V", "5V", "10V",
    "100mV", "200mV", "500mV", "1V", "2V", "5V", "10V", "20V", "50V", "100V",
    "1V", "2V", "5V", "10V", "20V", "50V", "100V", "200V", "500V", "1000V",
]

VOLT_BASE_F = [
    0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1.0, 2.0, 5.0, 10.0,
    0.1, 0.2, 0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 50.0, 100.0,
    1.0, 2.0, 5.0, 10.0, 20.0, 50.0, 100.0, 200.0, 500.0, 1000.0,
]

FFT_WINDOWS = ["Rectangle", "Hanning", "Hamming", "Blackman", "FlatTop", "Bartlett", "Rectangle2"]

DMM_FUNCTIONS = {
    5:  {"kind": "voltage", "acdc": "DC", "range_unit": lambda r: "mV" if r == 0 else "V"},
    6:  {"kind": "voltage", "acdc": "AC", "range_unit": lambda r: "mV" if r == 0 else "V"},
    7:  {"kind": "resistance", "acdc": "", "range_unit": lambda r: "Ω" if r == 0 else ("KΩ" if r <= 3 else "MΩ")},
    9:  {"kind": "continuity", "acdc": "", "range_unit": lambda r: "Ω"},
    10: {"kind": "voltage", "acdc": "DC", "range_unit": lambda r: "V"},
    11: {"kind": "capacitance", "acdc": "Cap", "range_unit": lambda r: ("nF", "uF", "uF", "mF")[min(r, 3)]},
    14: {"kind": "current", "acdc": "DC", "range_unit": lambda r: "mA"},
    15: {"kind": "current", "acdc": "AC", "range_unit": lambda r: "mA"},
    16: {"kind": "current", "acdc": "DC", "range_unit": lambda r: "A"},
    17: {"kind": "current", "acdc": "AC", "range_unit": lambda r: "A"},
    18: {"kind": "frequency", "acdc": "", "range_unit": lambda r: "Hz"},
    19: {"kind": "duty", "acdc": "", "range_unit": lambda r: "%"},
}


def checksum(data):
    return (-sum(data)) & 0xFF


def build_command(cmd, param=0):
    frame = bytearray([SYNC, cmd & 0xFF, param & 0xFF])
    frame.append(checksum(frame))
    return bytes(frame)


def build_payload(payload):
    frame = bytearray([SYNC])
    frame.extend(payload)
    frame.append(checksum(frame))
    return bytes(frame)


def build_load_wave(slot):
    if not MEM_SLOT_MIN <= slot <= MEM_SLOT_MAX:
        raise ValueError("slot must be %d..%d" % (MEM_SLOT_MIN, MEM_SLOT_MAX))
    return build_payload(bytes([CMD_LOAD_WAVE, slot & 0xFF]))


class Frame:
    __slots__ = ("type", "payload")

    def __init__(self, type_, payload):
        self.type = type_
        self.payload = payload


class FrameParser:
    def __init__(self, strict=False):
        self._buf = bytearray()
        self.strict = strict

    def feed(self, data):
        self._buf.extend(data)
        frames = []
        while True:
            frame = self._parse_one()
            if frame is None:
                break
            frames.append(frame)
        return frames

    def _parse_one(self):
        while True:
            buf = self._buf
            try:
                start = buf.index(SYNC)
            except ValueError:
                del buf[:]
                return None
            if start:
                del buf[:start]
            if len(buf) < 2:
                return None
            type_ = buf[1]
            if type_ == TYPE_STATUS:
                if len(buf) < 4:
                    return None
                total = (buf[0] + buf[1] + buf[2] + buf[3]) & 0xFF
                if total != 0:
                    del buf[:1]
                    continue
                payload = bytes([buf[2]])
                del buf[:4]
                return Frame(type_, payload)
            if type_ in LONG_TYPES:
                if len(buf) < 5:
                    return None
                length = buf[2] | (buf[3] << 8)
                if length > 5000:
                    del buf[:1]
                    continue
                total_win = 5 + length
                if len(buf) >= total_win and self._sum(buf[0:total_win]) == 0:
                    payload = bytes(buf[4:4 + length])
                    del buf[:total_win]
                    return Frame(type_, payload)
                total_gd = 6 + length
                if len(buf) >= total_gd and self._sum(buf[0:total_gd]) == 0:
                    payload = bytes(buf[4:4 + length])
                    del buf[:total_gd]
                    return Frame(type_, payload)
                if len(buf) < total_gd and not self.strict:
                    return None
                if len(buf) < total_win:
                    return None
                del buf[:1]
            else:
                del buf[:1]

    @staticmethod
    def _sum(data):
        s = 0
        for b in data:
            s = (s + b) & 0xFF
        return s
