import argparse
import os
import sys
from pathlib import Path
_RUN_PATH = os.path.dirname(os.path.dirname(__file__))


def find_all_shell(file_dir=None, target_suffix='.sh'):
    all_files = []
    walk_generator = os.walk(file_dir)
    for root_path, dirs, files in walk_generator:
        if len(files) < 1:
            continue
        for file in files:
            file_name, suffix_name = os.path.splitext(file)
            if suffix_name == target_suffix:
                all_files.append(os.path.join(root_path, file))
    return all_files

def package_path():
    return Path(_RUN_PATH)

def repo_root_path():
    return package_path().parent

def default_config_dir():
    return repo_root_path().parent / "sprinkler_config"

def default_log_root():
    return repo_root_path() / "sprinkler_logs"

def positive_int_error(value, name="value"):
    text = str(value).strip()
    if not text.isdecimal():
        return f"{name} must be positive"

    if int(text) <= 0:
        return f"{name} must be positive"
    return ""

def positive_int(value, name="value"):
    error = positive_int_error(value, name)
    if error:
        raise ValueError(error)
    return int(str(value).strip())

def argparse_positive_int(value):
    error = positive_int_error(value)
    if error:
        raise argparse.ArgumentTypeError(error)
    return int(str(value).strip())

def positive_int_or_default(value, default, name="value"):
    if value is None or str(value).strip() == "":
        value = default
    return positive_int(value, name)

def encode_text_b64(text):
    return base64.b64encode(str(text or "").encode("utf-8")).decode("ascii")

def decode_text_b64(value, name="value"):
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        return base64.b64decode(text.encode("ascii")).decode("utf-8")
    except Exception as exc:
        raise ValueError(f"bad base64 text for {name}") from exc

def argv_has_flag(name):
    """检查 sys.argv 中是否出现了指定的命令行标志（用于区分"用户显式传入"和"argparse默认值"）."""
    name = str(name).strip()
    return any(arg == name for arg in sys.argv[1:])


def encode_json_b64(data):
    return encode_text_b64(json.dumps(data, ensure_ascii=False))

def decode_json_b64(value, name="json"):
    text = decode_text_b64(value, name=name)
    if not text:
        return {}
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError(f"bad {name}: expected object")
    return data

def env_text_b64(name):
    return decode_text_b64(os.environ.get(name, ""), name=name)

def env_json_b64(name):
    return decode_json_b64(os.environ.get(name, ""), name=name)

def validate_port(value):
    value = argparse_positive_int(value)
    if not (0 < value < 65535):
        error = f"{value} must in (0, 65535)"
        raise argparse.ArgumentTypeError(error)
    return value

def read_text_command_file(file_path):
    path = Path(file_path).expanduser()
    if not path.exists():
        raise FileNotFoundError(f"command file not found: {path}")
    if path.suffix.lower() in {".xlsx", ".xls", ".xlsm", ".xlsb"}:
        raise ValueError(f"-file expects a text command file, not Excel: {path}")
    data = path.read_bytes()
    if data.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")):
        raise ValueError(f"-file expects a text command file, not a zipped/binary file: {path}")
    if b"\x00" in data[:4096]:
        raise ValueError(f"-file expects a text command file, not binary: {path}")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError(f"-file must be a UTF-8 text command file: {path}") from exc
