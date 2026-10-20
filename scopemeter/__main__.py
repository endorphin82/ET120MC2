import argparse
import sys


def main(argv=None):
    parser = argparse.ArgumentParser(description="ScopeMeter host for TOOLTOP ET120MC2")
    parser.add_argument("--port", help="serial port, e.g. /dev/ttyACM0 (default: auto)")
    parser.add_argument("--mock", action="store_true", help="connect to built-in virtual device")
    parser.add_argument("--list-ports", action="store_true", help="list serial ports and exit")
    parser.add_argument("--selftest", action="store_true", help="run protocol self-test without GUI")
    args = parser.parse_args(argv)

    if args.list_ports:
        from .device import list_ports
        for p in list_ports():
            print("%s  vid=%s pid=%s  %s" % (p.device, p.vid, p.pid, p.description))
        return 0

    if args.selftest:
        from .selftest import run_selftest
        return run_selftest()

    from .ui.app import run
    if args.port or args.mock:
        import os
        os.environ.setdefault("SCOPEMETER_DEFAULT_PORT", args.port or "MOCK")
    run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
