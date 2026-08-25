import hashlib
import json
import re
import secrets
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt
import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

APP_DIR = Path(__file__).resolve().parent
DATA_DIR = APP_DIR / "data"
BLOB_DIR = DATA_DIR / "parts"
DB_PATH = DATA_DIR / "taicloud.db"
CONFIG_PATH = APP_DIR / "server_config.json"

HOST = "127.0.0.1"
PORT = 8800
MAX_PART_BYTES = 110 * 1024
MAX_MAP_BYTES = 4 * 1024 * 1024
TRIAL_QUOTA = 2 * 1024**3
BASIC_QUOTA = 15 * 1024**3
PRO_QUOTA = 100 * 1024**3
TOKEN_TTL_HOURS = 24 * 7
PBKDF2_ITERS = 600_000
BASE32_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

DB_SCRIPT = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    email         TEXT UNIQUE NOT NULL COLLATE NOCASE,
    pw_hash       TEXT NOT NULL,
    recovery_hash TEXT NOT NULL,
    plan          TEXT NOT NULL DEFAULT 'trial',
    quota_bytes   INTEGER NOT NULL,
    status        TEXT NOT NULL DEFAULT 'active',
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS parts (
    id         TEXT PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id),
    size       INTEGER NOT NULL,
    sha256     TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS map_blob (
    user_id    INTEGER PRIMARY KEY REFERENCES users(id),
    data       BLOB NOT NULL,
    size       INTEGER NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS sessions (
    token_jti  TEXT PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id),
    revoked    INTEGER NOT NULL DEFAULT 0,
    expires_at TEXT NOT NULL
);
"""


def load_secret() -> str:
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text())["jwt_secret"]
    secret = secrets.token_hex(32)
    CONFIG_PATH.write_text(json.dumps({"jwt_secret": secret}))
    return secret


JWT_SECRET = load_secret()
_db_lock = threading.Lock()
_rl_lock = threading.Lock()
_rl_buckets: dict = {}


def query(sql, params=(), fetch=False):
    with _db_lock:
        conn = sqlite3.connect(DB_PATH, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            cur = conn.execute(sql, params)
            rows = [dict(r) for r in cur.fetchall()] if fetch else None
            conn.commit()
            return rows
        finally:
            conn.close()


def init_db():
    BLOB_DIR.mkdir(parents=True, exist_ok=True)
    with _db_lock:
        conn = sqlite3.connect(DB_PATH, timeout=30)
        try:
            conn.executescript(DB_SCRIPT)
            conn.commit()
        finally:
            conn.close()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITERS)
    return f"{salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt_hex, dk_hex = stored.split("$")
    except ValueError:
        return False
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), PBKDF2_ITERS)
    return secrets.compare_digest(dk.hex(), dk_hex)


def gen_recovery_key() -> str:
    return "".join(secrets.choice(BASE32_ALPHABET) for _ in range(20))


def issue_token(user_id: int) -> str:
    jti = secrets.token_hex(16)
    exp = datetime.now(timezone.utc) + timedelta(hours=TOKEN_TTL_HOURS)
    token = jwt.encode({"sub": str(user_id), "jti": jti, "exp": exp}, JWT_SECRET, algorithm="HS256")
    query(
        "INSERT INTO sessions (token_jti, user_id, expires_at) VALUES (?,?,?)",
        (jti, user_id, exp.isoformat()),
    )
    return token


def current_user(authorization: str = Header(...)) -> dict:
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "invalid_token")
    try:
        payload = jwt.decode(authorization[7:], JWT_SECRET, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(401, "invalid_token")
    jti = payload.get("jti", "")
    rows = query("SELECT user_id, revoked, expires_at FROM sessions WHERE token_jti=?", (jti,), fetch=True)
    if not rows or rows[0]["revoked"]:
        raise HTTPException(401, "invalid_token")
    if rows[0]["expires_at"] < datetime.now(timezone.utc).isoformat():
        raise HTTPException(401, "token_expired")
    users = query("SELECT * FROM users WHERE id=?", (int(payload["sub"]),), fetch=True)
    if not users or users[0]["status"] != "active":
        raise HTTPException(401, "account_inactive")
    return users[0]


def rate_limit(key: str, limit: int, window_s: int = 60):
    now = time.time()
    with _rl_lock:
        bucket = _rl_buckets.setdefault(key, [])
        while bucket and now - bucket[0] > window_s:
            bucket.pop(0)
        if len(bucket) >= limit:
            raise HTTPException(429, "rate_limited")
        bucket.append(now)


def used_bytes(user_id: int) -> int:
    total = query("SELECT COALESCE(SUM(size),0) AS s FROM parts WHERE user_id=?", (user_id,), fetch=True)[0]["s"]
    mrow = query("SELECT COALESCE(size,0) AS s FROM map_blob WHERE user_id=?", (user_id,), fetch=True)
    return total + (mrow[0]["s"] if mrow else 0)


def part_path(part_id: str) -> Path:
    return BLOB_DIR / part_id[:2] / part_id[2:4] / part_id


class AuthIn(BaseModel):
    email: str
    password: str


class RecoverIn(BaseModel):
    email: str
    recovery_key: str
    new_password: str


app = FastAPI(title="TAI Cloud API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(HTTPException)
async def http_exc_handler(request: Request, exc: HTTPException):
    code = exc.detail if isinstance(exc.detail, str) else "error"
    return JSONResponse(status_code=exc.status_code, content={"error": code, "message": code})


@app.exception_handler(Exception)
async def unhandled_exc_handler(request: Request, exc: Exception):
    return JSONResponse(status_code=500, content={"error": "internal", "message": "internal_error"})


@app.post("/api/v1/auth/signup")
def signup(body: AuthIn):
    rate_limit(f"signup:{body.email.lower()}", 5)
    if not EMAIL_RE.match(body.email):
        raise HTTPException(400, "invalid_email")
    if len(body.password) < 8:
        raise HTTPException(400, "weak_password")
    existing = query("SELECT id FROM users WHERE email=?", (body.email,), fetch=True)
    if existing:
        raise HTTPException(409, "email_taken")
    recovery_key = gen_recovery_key()
    recovery_hash = hashlib.sha256(recovery_key.encode()).hexdigest()
    cur = query(
        "INSERT INTO users (email, pw_hash, recovery_hash, plan, quota_bytes) VALUES (?,?,?,?,?)",
        (body.email, hash_password(body.password), recovery_hash, "trial", TRIAL_QUOTA),
    )
    user_id = query("SELECT id FROM users WHERE email=?", (body.email,), fetch=True)[0]["id"]
    return {
        "token": issue_token(user_id),
        "recovery_key": recovery_key,
        "quota_bytes": TRIAL_QUOTA,
        "message": "Save this recovery key NOW - it is shown only once",
    }


@app.post("/api/v1/auth/login")
def login(body: AuthIn, request: Request):
    rate_limit(f"login:{request.client.host}", 5)
    rows = query("SELECT * FROM users WHERE email=?", (body.email,), fetch=True)
    if not rows or not verify_password(body.password, rows[0]["pw_hash"]):
        raise HTTPException(401, "bad_credentials")
    if rows[0]["status"] != "active":
        raise HTTPException(403, "account_inactive")
    uid = rows[0]["id"]
    return {
        "token": issue_token(uid),
        "plan": rows[0]["plan"],
        "quota_bytes": rows[0]["quota_bytes"],
        "used_bytes": used_bytes(uid),
    }


@app.post("/api/v1/auth/recover")
def recover(body: RecoverIn):
    rate_limit(f"recover:{body.email.lower()}", 5)
    if len(body.new_password) < 8:
        raise HTTPException(400, "weak_password")
    rows = query("SELECT * FROM users WHERE email=?", (body.email,), fetch=True)
    if not rows:
        raise HTTPException(404, "unknown_email")
    provided_hash = hashlib.sha256(body.recovery_key.strip().upper().encode()).hexdigest()
    if not secrets.compare_digest(provided_hash, rows[0]["recovery_hash"]):
        raise HTTPException(401, "bad_recovery_key")
    uid = rows[0]["id"]
    query("UPDATE users SET pw_hash=? WHERE id=?", (hash_password(body.new_password), uid))
    query("UPDATE sessions SET revoked=1 WHERE user_id=?", (uid,))
    return {"token": issue_token(uid), "message": "password_reset_ok"}


@app.get("/api/v1/me")
def me(user=Depends(current_user)):
    return {
        "email": user["email"],
        "plan": user["plan"],
        "quota_bytes": user["quota_bytes"],
        "used_bytes": used_bytes(user["id"]),
    }


@app.post("/api/v1/parts", status_code=201)
async def upload_part(request: Request, user=Depends(current_user)):
    rate_limit(f"parts:{user['id']}", 120)
    data = await request.body()
    if not data:
        raise HTTPException(400, "empty_body")
    if len(data) > MAX_PART_BYTES:
        raise HTTPException(413, "part_too_large")
    quota = user["quota_bytes"]
    current = used_bytes(user["id"])
    if current + len(data) > quota:
        raise HTTPException(413, "quota_exceeded")
    part_id = secrets.token_hex(16)
    sha = hashlib.sha256(data).hexdigest()
    path = part_path(part_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    query(
        "INSERT INTO parts (id, user_id, size, sha256) VALUES (?,?,?,?)",
        (part_id, user["id"], len(data), sha),
    )
    return {"id": part_id, "sha256": sha}


@app.get("/api/v1/parts/{part_id}")
def download_part(part_id: str, user=Depends(current_user)):
    rows = query("SELECT * FROM parts WHERE id=? AND user_id=?", (part_id, user["id"]), fetch=True)
    if not rows:
        raise HTTPException(404, "not_found")
    path = part_path(part_id)
    if not path.exists():
        raise HTTPException(404, "blob_missing")
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != rows[0]["sha256"]:
        raise HTTPException(500, "corrupt_blob")
    return Response(content=data, media_type="application/octet-stream")


@app.delete("/api/v1/parts/{part_id}", status_code=204)
def delete_part(part_id: str, user=Depends(current_user)):
    rows = query("SELECT * FROM parts WHERE id=? AND user_id=?", (part_id, user["id"]), fetch=True)
    if not rows:
        raise HTTPException(404, "not_found")
    query("DELETE FROM parts WHERE id=?", (part_id,))
    path = part_path(part_id)
    if path.exists():
        path.unlink()
    return Response(status_code=204)


@app.put("/api/v1/map")
async def put_map(request: Request, user=Depends(current_user)):
    data = await request.body()
    if not data:
        raise HTTPException(400, "empty_body")
    if len(data) > MAX_MAP_BYTES:
        raise HTTPException(413, "map_too_large")
    current_parts = query("SELECT COALESCE(SUM(size),0) AS s FROM parts WHERE user_id=?", (user["id"],), fetch=True)[0]["s"]
    if current_parts + len(data) > user["quota_bytes"]:
        raise HTTPException(413, "quota_exceeded")
    query(
        "INSERT INTO map_blob (user_id, data, size, updated_at) VALUES (?,?,?,?) "
        "ON CONFLICT(user_id) DO UPDATE SET data=excluded.data, size=excluded.size, updated_at=excluded.updated_at",
        (user["id"], data, len(data), datetime.now(timezone.utc).isoformat()),
    )
    return {"size": len(data)}


@app.get("/api/v1/map")
def get_map(user=Depends(current_user)):
    rows = query("SELECT data FROM map_blob WHERE user_id=?", (user["id"],), fetch=True)
    if not rows:
        raise HTTPException(404, "no_map")
    return Response(content=rows[0]["data"], media_type="application/octet-stream")


@app.delete("/api/v1/purge", status_code=204)
def purge(user=Depends(current_user)):
    rows = query("SELECT id FROM parts WHERE user_id=?", (user["id"],), fetch=True)
    for r in rows:
        p = part_path(r["id"])
        if p.exists():
            p.unlink()
    query("DELETE FROM parts WHERE user_id=?", (user["id"],))
    query("DELETE FROM map_blob WHERE user_id=?", (user["id"],))
    return Response(status_code=204)


@app.get("/healthz")
def healthz():
    return {"ok": True, "service": "tai-cloud", "time": datetime.now(timezone.utc).isoformat()}


init_db()

if __name__ == "__main__":
    print(f"TAI Cloud API listening on http://{HOST}:{PORT}  (docs at /docs)")
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")
