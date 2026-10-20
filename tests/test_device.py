import time
import unittest

from scopemeter.device import (DSO_MODE_ANALYSE, DSO_MODE_REALTIME,
                               MACHINE_DSO, ScopeMeterDevice)
from scopemeter.mock import MockSerial, start_stream


class TestDeviceWithMock(unittest.TestCase):
    def _connect(self, machine="dso"):
        dev = ScopeMeterDevice()
        mock = MockSerial(machine=machine)
        events = {"dso": [], "status": [], "prod": []}
        dev.on_dso = events["dso"].append
        dev.on_status = events["status"].append
        dev.on_production = events["prod"].append
        dev.open(mock)
        stop = start_stream(mock)
        self.addCleanup(stop)
        self.addCleanup(dev.close)
        return dev, mock, events

    def _wait(self, cond, timeout=4.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if cond():
                return True
            time.sleep(0.03)
        return False

    def test_dso_realtime(self):
        dev, mock, events = self._connect("dso")
        dev.set_dso_mode(DSO_MODE_REALTIME)
        self.assertTrue(self._wait(lambda: dev.machine == MACHINE_DSO))
        self.assertTrue(self._wait(lambda: any(f.kind == "realtime" for f in events["dso"])))
        frame = [f for f in events["dso"] if f.kind == "realtime"][-1]
        self.assertEqual(len(frame.ch1), 600)
        self.assertEqual(len(frame.ch2), 600)
        self.assertIsNotNone(frame.info1)
        self.assertGreater(frame.info1.vpp, 0)

    def test_dso_analyse(self):
        dev, mock, events = self._connect("dso")
        dev.set_dso_mode(DSO_MODE_ANALYSE)
        self.assertTrue(self._wait(lambda: dev.machine == MACHINE_DSO))
        self.assertTrue(self._wait(lambda: any(f.kind == "analyse" for f in events["dso"])))
        frame = [f for f in events["dso"] if f.kind == "analyse"][-1]
        self.assertEqual(len(frame.ch1), 2048)

    def test_load_wave(self):
        dev, mock, events = self._connect("dso")
        dev.set_dso_mode(DSO_MODE_REALTIME)
        self.assertTrue(self._wait(lambda: dev.machine == MACHINE_DSO))
        events["dso"].clear()
        dev.load_wave(5)
        self.assertTrue(self._wait(lambda: any(f.kind == "memory" for f in events["dso"])))
        frame = [f for f in events["dso"] if f.kind == "memory"][-1]
        self.assertEqual(frame.mem_slot, 5)
        self.assertEqual(len(frame.ch1), 600)

    def test_load_wave_hold_persists(self):
        dev, mock, events = self._connect("dso")
        dev.set_dso_mode(DSO_MODE_REALTIME)
        self.assertTrue(self._wait(lambda: dev.machine == MACHINE_DSO))
        events["dso"].clear()
        dev.load_wave(7)
        self.assertTrue(self._wait(lambda: any(f.kind == "memory" for f in events["dso"])))
        time.sleep(1.5)
        mem = [f for f in events["dso"] if f.kind == "memory"]
        self.assertGreaterEqual(len(mem), 1)
        self.assertEqual(mem[0].mem_slot, 7)

    def test_exit_memory_restores_realtime(self):
        dev, mock, events = self._connect("dso")
        dev.set_dso_mode(DSO_MODE_REALTIME)
        self.assertTrue(self._wait(lambda: dev.machine == MACHINE_DSO))
        dev.load_wave(2)
        self.assertTrue(self._wait(lambda: any(f.kind == "memory" for f in events["dso"])))
        dev.clear_mem_hold()
        dev.set_dso_mode(DSO_MODE_REALTIME)
        self.assertTrue(self._wait(lambda: any(f.kind == "realtime" for f in events["dso"])))

    def test_production_info(self):
        dev, mock, events = self._connect("dso")
        self.assertTrue(self._wait(lambda: dev.machine == MACHINE_DSO))
        dev.request_production_info()
        self.assertTrue(self._wait(lambda: events["prod"]))
        self.assertIn(b"ET120MC2", events["prod"][0])


if __name__ == "__main__":
    unittest.main()
