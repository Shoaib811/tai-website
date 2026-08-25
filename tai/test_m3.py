import hashlib
import os
import secrets
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import requests

import main
import taicloud

BASE = "http://127.0.0.1:8800"
PASS = 0
FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} {extra}")


def main_flow():
    print("== SETUP ==")
    st = requests.get(BASE + "/healthz", timeout=10).status_code
    check("server healthz", st == 200, f"got {st}")

    email = f"m3_{secrets.token_hex(5)}@taitest.local"
    pw = "M3Password123!"
    res = taicloud.api_signup(email, pw)
    recovery_key = res["recovery_key"]
    check("signup ok", taicloud.has_session())
    check("recovery_key mila", len(recovery_key) == 20)

    data = secrets.token_bytes(350 * 1024 + 123)
    tai_tmp = os.path.join(tempfile.gettempdir(), "TAI")
    os.makedirs(tai_tmp, exist_ok=True)
    src = os.path.join(tai_tmp, "m3_secret_test.bin")
    with open(src, "wb") as fh:
        fh.write(data)
    orig_sha = hashlib.sha256(data).hexdigest()

    print("== OFFLOAD (CLOUD MODE) ==")
    check("cloud_active", main.cloud_active())
    result = main.offload_core(src, make_shortcut=False)
    check("offload returned", bool(result.get("cloud_name")))
    expected_parts = -(-(len(data)) // main.MAX_PLAIN)
    check(f"parts count = {expected_parts}", result["parts"] == expected_parts, str(result))
    check("source file consumed", not os.path.exists(src))

    print("== MAPPING ON CLOUD ==")
    mapping = main.cloud_load_mapping()
    entry = mapping.get(result["cloud_name"])
    check("mapping entry hai", entry is not None)
    check("entry original name", entry and entry["original"] == "m3_secret_test.bin")
    check("entry parts ids", entry and all("id" in p for p in entry["parts"]))
    check("entry sizes", entry and sum(p["len"] for p in entry["parts"]) == len(data))

    print("== QUOTA ==")
    q = taicloud.api_me()
    min_used = expected_parts * main.CHUNK_SIZE
    check("quota tracked", q["used_bytes"] > min_used, str(q))
    check("used sane", q["used_bytes"] < min_used + main.CHUNK_SIZE, str(q))

    print("== RESTORE (CLOUD MODE) ==")
    out_path = main.restore_cloud(result["cloud_name"])
    check("restore file bana", os.path.exists(out_path))
    with open(out_path, "rb") as fh:
        restored = fh.read()
    check("BYTE-PERFECT roundtrip", hashlib.sha256(restored).hexdigest() == orig_sha)
    os.remove(out_path)

    print("== LOGOUT / LOCAL DISPATCH ==")
    taicloud.clear_session()
    check("session cleared", not main.cloud_active())

    print("== PURGE ==")
    taicloud.set_session(res["token"], email)
    taicloud.api_purge()
    q2 = taicloud.api_me()
    check("purge freed sab kuch", q2["used_bytes"] == 0, str(q2))

    taicloud.clear_session()

    print(f"\nRESULT: {PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main_flow())
