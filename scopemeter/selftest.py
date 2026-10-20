import time

from .device import (DSO_MODE_ANALYSE, DSO_MODE_REALTIME, MACHINE_DSO,
                     ScopeMeterDevice)
from .mock import MockSerial, start_stream


def run_selftest(timeout=6.0):
    results = []

    def check(name, ok, detail=""):
        results.append((name, ok, detail))
        print("%s  %s %s" % ("PASS" if ok else "FAIL", name, detail))

    dev = ScopeMeterDevice()
    mock = MockSerial(machine="dso")

    dso_seen = []
    status_seen = []
    prod_seen = []

    dev.on_dso = dso_seen.append
    dev.on_status = status_seen.append
    dev.on_production = prod_seen.append

    dev.open(mock)
    stop = start_stream(mock)
    deadline = time.time() + timeout
    try:
        while time.time() < deadline and not status_seen:
            time.sleep(0.05)
        check("status frames", bool(status_seen), "n=%d" % len(status_seen))
        check("machine is DSO", dev.machine == MACHINE_DSO)

        deadline = time.time() + timeout
        while time.time() < deadline and not any(f.kind == "realtime" for f in dso_seen):
            time.sleep(0.05)
        dso_frames = [f for f in dso_seen if f.kind == "realtime"]
        check("DSO realtime frames", bool(dso_frames), "n=%d" % len(dso_frames))
        if dso_frames:
            f = dso_frames[-1]
            check("DSO CH1 samples", f.ch1 is not None and len(f.ch1) == 600)
            check("DSO CH2 samples", f.ch2 is not None and len(f.ch2) == 600)
            check("DSO info", f.info1 is not None and f.info1.vpp > 0)
            check("DSO timbase", f.tim_base == 20, "tim=%d" % f.tim_base)

        dso_seen.clear()
        dev.set_dso_mode(DSO_MODE_ANALYSE)
        deadline = time.time() + timeout
        while time.time() < deadline and not any(f.kind == "analyse" for f in dso_seen):
            time.sleep(0.05)
        analyse = [f for f in dso_seen if f.kind == "analyse"]
        check("DSO analyse frames", bool(analyse), "n=%d" % len(analyse))
        if analyse:
            f = analyse[-1]
            check("analyse 2048 samples", f.ch1 is not None and len(f.ch1) == 2048)

        dso_seen.clear()
        dev.load_wave(7)
        deadline = time.time() + timeout
        while time.time() < deadline and not any(f.kind == "memory" for f in dso_seen):
            time.sleep(0.05)
        mem = [f for f in dso_seen if f.kind == "memory"]
        check("memory load frames", bool(mem), "n=%d" % len(mem))
        check("memory hold active", dev.mem_hold, "hold=%s" % dev.mem_hold)
        if mem:
            f = mem[-1]
            check("memory CH1 samples", f.ch1 is not None and len(f.ch1) == 600)
            check("memory slot", f.mem_slot == 7, "slot=%s" % f.mem_slot)

        dev.clear_mem_hold()
        check("memory hold cleared", not dev.mem_hold, "hold=%s" % dev.mem_hold)
        dso_seen.clear()
        dev.set_dso_mode(DSO_MODE_REALTIME)
        deadline = time.time() + timeout
        while time.time() < deadline and not any(f.kind == "realtime" for f in dso_seen):
            time.sleep(0.05)
        check("realtime resumed", any(f.kind == "realtime" for f in dso_seen),
              "n=%d" % len(dso_seen))

        prod_seen.clear()
        dev.request_production_info()
        deadline = time.time() + timeout
        while time.time() < deadline and not prod_seen:
            time.sleep(0.05)
        check("production info", bool(prod_seen), "n=%d" % len(prod_seen))
        if prod_seen:
            check("production model", b"ET120MC2" in prod_seen[0])
    finally:
        stop()
        dev.close()

    passed = sum(1 for _, ok, _ in results if ok)
    print("%d/%d checks passed" % (passed, len(results)))
    return 0 if passed == len(results) else 1
