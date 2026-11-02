"""Password storage and retrieval with config file support."""
import base64
import os
from pathlib import Path

from sprinkler.utils.aesc_cryptor import AESCryptor


_cryptor = AESCryptor()

ENCRYPTED_DEFAULTS = {
    "sfu_com": "LYAPI1JM1e9jZ5z9nmLG1eJDM/nzQUWgn2lNFvnSZA0=",
    "bmc": [
        "8E5WvytRD2Fxu0u8ooLNBdsUoTBOXsMnFtFx0ZQdv7c=",
        "s7WYvFjdIsHkc+eauMTwXjUCEAgDayXHFhM+LBLfrKQ=",
    ],
    "1213": [
        "LFkidANkVBFrMRClVnH08dgQsYtwNSZNisFlhPrcohg=",
        "lCFJKJhVxjnHO+/DAdcCoisL95CpHh1E6IhQUjY36c0=",
        "YcPYJdyPefBko/tTL4O8KxK5LaOZzy7IQYVpZkztZbY=",
    ],
    "huawei": [
        "6nyfodh+rTaJQn2Fmj+DrYteMd6EWJ+Zxapxtiwgacw=",
    ],
}


def decrypt_password(encrypted: str) -> str:
    if not encrypted:
        return ""
    return _cryptor.decrypt(base64.b64decode(encrypted)).decode("utf-8")


def encrypt_password(plaintext: str) -> str:
    """密码 AES 加密 + base64；空串原样返回空串。"""
    if not plaintext:
        return ""
    return base64.b64encode(_cryptor.encrypt(str(plaintext).encode("utf-8"))).decode("utf-8")


def _get_config_passwords(category: str) -> list[str]:
    config_path = Path.home() / ".sprinkler" / "config.yaml"
    if not config_path.exists():
        return []

    try:
        import yaml
        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
        passwords = config.get(category, [])
        if isinstance(passwords, str):
            return [passwords]
        return list(passwords) if passwords else []
    except Exception:
        return []


def get_default_passwords(category: str) -> list[str]:
    config_passwords = _get_config_passwords(category)
    encrypted_value = ENCRYPTED_DEFAULTS.get(category)

    if encrypted_value is None:
        return config_passwords

    if isinstance(encrypted_value, list):
        decrypted = [decrypt_password(v) for v in encrypted_value]
    else:
        decrypted = [decrypt_password(encrypted_value)]

    if config_passwords:
        seen = set()
        combined = []
        for pwd in config_passwords + decrypted:
            if pwd not in seen:
                seen.add(pwd)
                combined.append(pwd)
        return combined

    return decrypted
