# TAI CLOUD — Technical Blueprint v1.0

> Tricrypt AI as a Service — Zero-Knowledge Encrypted Cloud Storage
> Status: PLANNING | Target: MVP in 4–6 weeks | Last updated: 2026-08-24

---

## 1. Vision

Users subscribe on the website, download the TAI app, and get 10–15 GB of
zero-knowledge encrypted cloud storage. Files are encrypted, fragmented,
and scattered on OUR infrastructure — the server never sees plaintext
data or even original filenames.

**Core promise:** "We can't read your files even if we wanted to."

---

## 2. Architecture Overview

```
┌──────────────────────┐          HTTPS/TLS           ┌───────────────────────────┐
│  CLIENT (Windows app) │  ─────────────────────────> │  SERVER (VPS)              │
│                       │                              │                            │
│  main.py (UI/logic)   │   POST /parts  (binary)      │  FastAPI app               │
│  taicrypt.py (AES-GCM)│ <─────────────────────────   │  ├─ /api/v1/auth/*         │
│  DPAPI key wrapping   │   GET  /parts/{id}           │  ├─ /api/v1/parts/*        │
│                       │                              │  ├─ /api/v1/map            │
│  Encryption happens   │   JSON Web Tokens (JWT)      │  ├─ /api/v1/quota          │
│  ON DEVICE ONLY       │                              │  ├─ SQLite (users/quota)   │
└──────────────────────┘                              │  └─ Blob store (tparts)    │
                                                       └───────────────────────────┘
                                                            │
                                                       ┌────┴─────┐
                                                       │ Website   │  Landing, pricing,
                                                       │ (Phase 3) │  signup, payments,
                                                       └──────────┘  installer download
```

### Golden Rules (non-negotiable)
1. **Client-side encryption only** — taicrypt.py stays on device. Server receives ONLY sealed tparts.
2. **Filenames never leave the device** — original names live ONLY inside the encrypted TAIMAP.
3. **Server stores opaque blobs** with random IDs. No readable structure server-side.
4. **No password reset possible** — Recovery Key issued once at signup (see §7).
5. All traffic over TLS. No exceptions.

---

## 3. Tech Stack (MVP)

| Layer      | Choice                          | Why |
|------------|----------------------------------|-----|
| Server     | Python 3.12 + FastAPI + uvicorn  | Async, fast, auto API docs (/docs) |
| Database   | SQLite (WAL mode)                | Zero-config MVP; migrate to Postgres later |
| Blob store | Local disk `/data/parts/aa/bb/`  | Hash-prefix sharding; swap to S3/Wasabi later |
| Auth       | JWT (PyJWT) + PBKDF2 password hash | Same KDF already used by taicrypt |
| Reverse proxy | Caddy                         | Free automatic HTTPS |
| Deploy     | One VPS (Hetzner CX22 ~€4/mo)    | Start small, scale later |
| Client HTTP| requests (already ecosystem-fit)  | Simple, retry support |

**Dev-first strategy:** Build & test everything on `http://127.0.0.1:8800`
locally BEFORE spending money on VPS/domain.

---

## 4. Database Schema (SQLite)

```sql
CREATE TABLE users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    email         TEXT UNIQUE NOT NULL COLLATE NOCASE,
    pw_hash       TEXT NOT NULL,           -- PBKDF2-SHA256, 600k iters, per-user salt
    recovery_hash TEXT NOT NULL,           -- SHA256(recovery_key); raw key shown ONCE
    plan          TEXT NOT NULL DEFAULT 'trial',   -- trial | basic | pro
    quota_bytes   INTEGER NOT NULL,        -- set by plan
    used_bytes    INTEGER NOT NULL DEFAULT 0,
    status        TEXT NOT NULL DEFAULT 'active',  -- active | suspended | deleted
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE parts (
    id          TEXT PRIMARY KEY,          -- random hex id = server-side name
    user_id     INTEGER NOT NULL REFERENCES users(id),
    size        INTEGER NOT NULL,          -- exact bytes (for quota math)
    sha256      TEXT NOT NULL,             -- integrity check on download
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE map_blob (                  -- ONE encrypted index blob per user
    user_id     INTEGER PRIMARY KEY REFERENCES users(id),
    data        BLOB NOT NULL,             -- sealed TAIMAP (opaque to us)
    size        INTEGER NOT NULL,
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE sessions (
    token_jti   TEXT PRIMARY KEY,          -- JWT id (enables revocation)
    user_id     INTEGER NOT NULL REFERENCES users(id),
    expires_at  TEXT NOT NULL
);
```

Quota rule: `used_bytes` = SUM(parts.size) + map_blob.size.
Reject uploads when `used_bytes + incoming > quota_bytes`.

---

## 5. API Contract v1 (frozen before client coding)

Base URL: `https://api.<domain>/api/v1` (dev: `http://127.0.0.1:8800/api/v1`)
Auth: `Authorization: Bearer <JWT>` on every route except signup/login.

| Method | Path                | Body / Params              | Returns |
|--------|---------------------|----------------------------|---------|
| POST   | `/auth/signup`      `{email, password}`        | `{token, recovery_key, quota_bytes}` — recovery_key shown ONCE |
| POST   | `/auth/login`       `{email, password}`        | `{token, quota_bytes, used_bytes}` |
| POST   | `/auth/recover`     `{email, recovery_key, new_password}` | new JWT (old sessions revoked) |
| GET    | `/me`               —                          | `{email, plan, quota_bytes, used_bytes}` |
| POST   | `/parts`            raw bytes (≤110 KB)        | `201 {id, sha256}` — 413 if quota exceeded |
| GET    | `/parts/{id}`       —                          | raw bytes (404 if not owner) |
| DELETE | `/parts/{id}`       —                          | `204`, frees quota |
| PUT    | `/map`              raw sealed blob            | `200 {size}` |
| GET    | `/map`              —                          | raw sealed blob |
| DELETE | `/purge`            —                          | wipes ALL user blobs+parts (GDPR-style right) |

Error shape (uniform): `{ "error": "code", "message": "human text" }`

Rate limits (MVP): 60 req/min per token; login: 5/min per IP.

---

## 6. Client Changes (main.py)

| Area | Change |
|------|--------|
| New module | `taicloud.py` — thin wrapper around requests: login/signup/upload/download/quota |
| STORAGE_DIR | Becomes REMOTE. `split_into_parts()` yields chunks → uploader POSTs each; restore downloads → `join_parts()` |
| TAIMAP | Sealed blob PUT to `/map` instead of scattered TAIMAP parts (server-blind) |
| Ghosts/shuffle | Server-side concept DIES for cloud accounts. Random part IDs + encryption are the obfuscation now. (Shuffle logic stays for local/offline mode.) |
| Login window | First-run Tk dialog → email/password → JWT cached DPAPI-wrapped in tai_config.json |
| Quota meter | Status bar in UI: "4.2 GB / 15 GB" |
| Offline queue | Failed uploads retry with backoff; pending count shown |
| Recovery key | One-time modal after signup with COPY button + "print this" warning |

Keep LOCAL mode working (`--local` flag) so existing single-user flow never breaks.

---

## 7. Zero-Knowledge & Recovery Design

- Master file key (current DPAPI-wrapped key) stays device-bound.
- Account password ≠ encryption key. Changing account password NEVER re-encrypts data.
- Recovery Key: 20-char base32 secret generated at signup. Server stores only SHA256(recovery_key).
  Losing BOTH password AND recovery key = permanent data loss (show this clearly in UI).
- Rationale copy for marketing: "Even our admins see only meaningless fragments."

---

## 8. Plans & Pricing (draft)

| Plan | Storage | Price | Notes |
|------|---------|-------|-------|
| Trial | 2 GB | Free 14 days | No card required |
| Basic | 15 GB | ₹99/mo or ₹999/yr | Core market |
| Pro | 100 GB | ₹299/mo or ₹2,999/yr | Power users |

Payments (Phase 3): Razorpay (India-first) or Lemon Squeezy/Paddle (global, merchant-of-record).
Requires business registration + GST (India) — start sole proprietorship.

---

## 9. Milestones

- [x] M0 — Local product works (DONE)
- [ ] M1 — Blueprint frozen (THIS DOC)
- [ ] M2 — Cloud server MVP running on localhost (auth+parts+map+quota)
- [ ] M3 — Client taicloud.py + UI login; full offload→restore against localhost
- [ ] M4 — VPS deploy + real domain + HTTPS (first rupee-worthy build)
- [ ] M5 — Website + payments + installer
- [ ] M6 — Beta with 5–10 pilot users → testimonials → public launch

## 10. Known Risks

| Risk | Mitigation |
|------|-----------|
| Solo-dev bandwidth | Ruthless scope cuts; website LAST not first |
| Users lose recovery keys | Onboarding warnings + printable PDF |
| VPS disk fills | Alert at 80%; sharded layout ready for S3 migration |
| Abuse (piracy/C2 hiding) | ToS, rate limits, abuse-report takedown process |
