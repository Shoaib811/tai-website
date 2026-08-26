import ctypes
import hashlib
import hmac
import json
import os
import secrets
from ctypes import wintypes

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tai_config.json")
PBKDF2_ITERATIONS = 600_000
NONCE_SIZE = 12
TAG_SIZE = 16
VERIFY_MSG = b"TAI-CRYPT-VERIFY-V1"

_state = {"key": None}


class DATA_BLOB(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_char)),
    ]


def _make_blob(data: bytes) -> DATA_BLOB:
    buf = ctypes.create_string_buffer(data, len(data))
    return DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))


def dpapi_protect(data: bytes) -> bytes:
    inp = _make_blob(data)
    out = DATA_BLOB()
    ok = ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(inp), None, None, None, None, 0, ctypes.byref(out)
    )
    if not ok:
        raise OSError("DPAPI protect failed")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


def dpapi_unwrap(data: bytes) -> bytes:
    inp = _make_blob(data)
    out = DATA_BLOB()
    ok = ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(inp), None, None, None, None, 0, ctypes.byref(out)
    )
    if not ok:
        raise OSError("DPAPI unwrap failed")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


def derive_key(password: str, salt: bytes, iterations: int = PBKDF2_ITERATIONS) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)


def _verifier(key: bytes) -> str:
    return hmac.new(key, VERIFY_MSG, hashlib.sha256).hexdigest()


def setup_master_key(password: str) -> None:
    salt = secrets.token_bytes(16)
    key = derive_key(password, salt)
    config = {
        "v": 1,
        "iterations": PBKDF2_ITERATIONS,
        "salt": salt.hex(),
        "verifier": _verifier(key),
        "wrapped": dpapi_protect(key).hex(),
    }
    with open(CONFIG_PATH, "w", encoding="utf-8") as fh:
        json.dump(config, fh, indent=2)
    _state["key"] = key


def master_key() -> bytes | None:
    global _state
    if _state["key"] is not None:
        return _state["key"]
    if not os.path.exists(CONFIG_PATH):
        return None
    with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
        config = json.load(fh)
    key = dpapi_unwrap(bytes.fromhex(config["wrapped"]))
    if _verifier(key) != config["verifier"]:
        raise RuntimeError("Key integrity fail")
    _state["key"] = key
    return key


def verify_password(password: str) -> bool:
    if not os.path.exists(CONFIG_PATH):
        return False
    with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
        config = json.load(fh)
    key = derive_key(password, bytes.fromhex(config["salt"]), config["iterations"])
    return _verifier(key) == config["verifier"]


def change_password(old_password: str, new_password: str) -> bool:
    if not verify_password(old_password):
        return False
    setup_master_key(new_password)
    return True


def setup_app_lock(password: str) -> None:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITERATIONS)
    lock = {"salt": salt.hex(), "hash": dk.hex()}
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
            config = json.load(fh)
    else:
        config = {}
    config["app_lock"] = lock
    with open(CONFIG_PATH, "w", encoding="utf-8") as fh:
        json.dump(config, fh, indent=2)


def verify_app_lock(password: str) -> bool:
    if not os.path.exists(CONFIG_PATH):
        return False
    with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
        config = json.load(fh)
    lock = config.get("app_lock")
    if not lock:
        return False
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(),
                              bytes.fromhex(lock["salt"]), PBKDF2_ITERATIONS)
    return secrets.compare_digest(dk.hex(), lock["hash"])


def has_app_lock() -> bool:
    if not os.path.exists(CONFIG_PATH):
        return False
    with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
        config = json.load(fh)
    return "app_lock" in config


def tai_xor_stream(data: bytes, context: str, key: bytes) -> bytes:
    stream = bytearray()
    counter = 0
    while len(stream) < len(data):
        block = hashlib.sha256(
            b"TAI-XFORM|" + context.encode("utf-8") + b"|" + key + b"|" + counter.to_bytes(8, "little")
        ).digest()
        stream.extend(block)
        counter += 1
    x = int.from_bytes(data, "little") ^ int.from_bytes(bytes(stream[: len(data)]), "little")
    return x.to_bytes(len(data), "little")


def aes_encrypt(key: bytes, plaintext: bytes, context: str) -> bytes:
    nonce = secrets.token_bytes(NONCE_SIZE)
    ct = AESGCM(key).encrypt(nonce, plaintext, context.encode("utf-8"))
    return nonce + ct


def aes_decrypt(key: bytes, blob: bytes, context: str) -> bytes:
    return AESGCM(key).decrypt(blob[:NONCE_SIZE], blob[NONCE_SIZE:], context.encode("utf-8"))


def seal(key: bytes, data: bytes, context: str) -> bytes:
    return aes_encrypt(key, tai_xor_stream(data, context, key), context)


def unseal(key: bytes, blob: bytes, context: str) -> bytes:
    return tai_xor_stream(aes_decrypt(key, blob, context), context, key)
