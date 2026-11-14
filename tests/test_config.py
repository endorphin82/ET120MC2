import os
import tempfile
import unittest

from scopemeter import config as cfg


class ConfigPathTest(unittest.TestCase):
    def test_posix_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = os.environ.get("XDG_CONFIG_HOME")
            os.environ["XDG_CONFIG_HOME"] = tmp
            try:
                self.assertEqual(cfg.config_path(),
                                 os.path.join(tmp, "scopemeter", "scope.ini"))
            finally:
                if old is None:
                    os.environ.pop("XDG_CONFIG_HOME", None)
                else:
                    os.environ["XDG_CONFIG_HOME"] = old

    def test_windows_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            old_appdata = os.environ.get("APPDATA")
            os.environ["APPDATA"] = tmp
            old_name = os.name
            try:
                os.name = "nt"
                self.assertEqual(cfg.config_path(),
                                 os.path.join(tmp, "scopemeter", "scope.ini"))
            finally:
                os.name = old_name
                if old_appdata is None:
                    os.environ.pop("APPDATA", None)
                else:
                    os.environ["APPDATA"] = old_appdata

    def test_save_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "sub", "scope.ini")
            c = cfg.load(path)
            c.set("0", "port", "COM5")
            cfg.save(c, path)
            c2 = cfg.load(path)
            self.assertEqual(c2.get("0", "port"), "COM5")


if __name__ == "__main__":
    unittest.main()
