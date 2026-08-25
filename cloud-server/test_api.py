import hashlib
import json
import os
import secrets
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8800/api/v1"
PASS = 0
FAIL = 0


def call(method, path, body=None, data=None, token=None):
    url = BASE + path
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    payload = None
    if body is not None:
        payload = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if data is not None:
        payload = data
        headers["Content-Type"] = "application/octet-stream"
    req = urllib.request.Request(url, data=payload, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            try:
                return resp.status, json.loads(raw) if raw and resp.headers.get("content-type", "").startswith("application/json") else raw
            except json.JSONDecodeError:
                return resp.status, raw
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            return e.code, raw


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} {extra}")


def main():
    email = f"test_{secrets.token_hex(6)}@taitest.local"
    password = "SuperSecret123!"
    new_password = "NewSecret456!"

    print("== AUTH ==")
    st, r = call("POST", "/auth/signup", body={"email": email, "password": password})
    check("signup 200", st == 200, f"got {st} {r}")
    recovery_key = r.get("recovery_key", "")
    tok = r.get("token", "")
    check("recovery_key 20 chars", len(recovery_key) == 20, repr(recovery_key))
    check("trial quota = 2GB", r.get("quota_bytes") == 2 * 1024**3)

    st, r2 = call("POST", "/auth/signup", body={"email": email, "password": password})
    check("duplicate signup 409", st == 409, f"got {st}")

    st, r = call("POST", "/auth/login", body={"email": email, "password": "WrongPass999"})
    check("bad login 401", st == 401, f"got {st}")
    st, r = call("POST", "/auth/login", body={"email": email, "password": password})
    check("login 200", st == 200 and "token" in r, f"got {st} {r}")

    print("== QUOTA ==")
    st, r = call("GET", "/me", token=tok)
    check("/me 200", st == 200, f"got {st}")
    check("used_bytes starts 0", r.get("used_bytes") == 0, str(r))

    print("== PARTS ==")
    blob = secrets.token_bytes(100 * 1024)
    sha = hashlib.sha256(blob).hexdigest()
    st, r = call("POST", "/parts", data=blob, token=tok)
    check("upload part 201", st == 201, f"got {st} {r}")
    pid = r.get("id", "")
    check("sha256 matches", r.get("sha256") == sha)

    st, back = call("GET", f"/parts/{pid}", token=tok)
    check("download roundtrip", st == 200 and hashlib.sha256(back).hexdigest() == sha)

    st, _ = call("GET", f"/parts/{pid}", token="fake.token.here")
    check("fake token 401", st == 401, f"got {st}")

    st, r = call("GET", "/me", token=tok)
    check("used_bytes = 102400", r.get("used_bytes") == 100 * 1024, str(r))

    oversize = secrets.token_bytes(111 * 1024)
    st, r = call("POST", "/parts", data=oversize, token=tok)
    check("oversize part 413", st == 413, f"got {st}")

    print("== MAP ==")
    map_blob = secrets.token_bytes(4096)
    st, r = call("PUT", "/map", data=map_blob, token=tok)
    check("map put 200", st == 200, f"got {st} {r}")
    st, back = call("GET", "/map", token=tok)
    check("map roundtrip", st == 200 and back == map_blob)
    st, r = call("GET", "/me", token=tok)
    expected_used = 100 * 1024 + 4096
    check("used includes map", r.get("used_bytes") == expected_used, str(r))

    print("== DELETE PART ==")
    st, _ = call("DELETE", f"/parts/{pid}", token=tok)
    check("delete part 204", st == 204, f"got {st}")
    st, r = call("GET", "/me", token=tok)
    check("quota freed", r.get("used_bytes") == 4096, str(r))
    st, _ = call("GET", f"/parts/{pid}", token=tok)
    check("deleted part 404", st == 404, f"got {st}")

    print("== RECOVERY ==")
    st, r = call("POST", "/auth/recover", body={"email": email, "recovery_key": "X" * 20, "new_password": new_password})
    check("wrong recovery key 401", st == 401, f"got {st}")
    st, r = call("POST", "/auth/recover", body={"email": email, "recovery_key": recovery_key.lower(), "new_password": new_password})
    check("recovery case-insensitive 200", st == 200, f"got {st} {r}")
    new_tok = r.get("token", "")
    st, _ = call("GET", "/me", token=tok)
    check("old token revoked 401", st == 401, f"got {st}")
    st, r = call("POST", "/auth/login", body={"email": email, "password": new_password})
    check("login with new password", st == 200, f"got {st}")

    print("== PURGE ==")
    st, r = call("POST", "/parts", data=blob, token=new_tok)
    pid2 = r.get("id", "")
    st, _ = call("DELETE", "/purge", token=new_tok)
    check("purge 204", st == 204, f"got {st}")
    st, r = call("GET", "/me", token=new_tok)
    check("purge freed all", r.get("used_bytes") == 0, str(r))

    print(f"\nRESULT: {PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
