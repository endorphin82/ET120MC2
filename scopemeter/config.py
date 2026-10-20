import os

DEFAULTS = {
    "0": {
        "DmmMode": "2",
        "DSOMode": "1",
        "dsoXLineMode": "0",
        "port": "",
        "baudrate": "115200",
        "window": "0",
        "coupling": "0",
        "dsoChannel": "1",
    },
    "1": {
        "NUM": "0",
    },
}


def config_path():
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "scopemeter", "scope.ini")


def load(path=None):
    path = path or config_path()
    import configparser
    cfg = configparser.ConfigParser()
    for section, values in DEFAULTS.items():
        cfg[section] = dict(values)
    if os.path.exists(path):
        cfg.read(path)
    return cfg


def save(cfg, path=None):
    path = path or config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        cfg.write(f)
