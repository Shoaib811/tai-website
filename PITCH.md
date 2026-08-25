# 🛡️ Tricrypt AI (TAI) — Technical Pitch

**Tagline:** *"Data churana possible hai — use karna impossible."*

## Elevator Pitch (30 sec)

Tricrypt AI (TAI) is a next-generation data protection system that makes stolen data useless.
Instead of only locking files, TAI shreds every file into identical encrypted 100KB fragments,
scatters them across randomized storage zones, hides the index itself as an encrypted fragment,
and rotates all filenames automatically every hour.

Even if an attacker breaches cloud storage, they find thousands of meaningless,
ever-changing blobs — no names, no order, no keys.

**Hackers can still steal data — TAI makes sure they can't use it.**

## The Problem

- Traditional encryption has a single failure point: stolen encrypted files give attackers unlimited time against the key.
- Cloud breaches expose complete, organized files.
- Metadata (filenames, structure, sizes) leaks information even before decryption.

## The Solution — 5-Layer Moving Target Defense

| Layer | Technology | What the attacker faces |
|-------|-----------|--------------------------|
| 1. Fragmentation | Files split into fixed 100KB AES-256-GCM encrypted chunks | No complete file exists at rest |
| 2. Dispersion | Fragments scattered round-robin across zones A/B/C | No single location holds a file |
| 3. Hidden Index | Mapping itself stored as an encrypted fragment; location rotates randomly | No metadata leak |
| 4. Decoy Flood | Ghost fragments (random bytes, realistic names) keep zone counts equal | Real vs fake indistinguishable |
| 5. Auto-Rotation | Scheduler renames ALL fragments every hour | The target never stands still |

## Architecture Highlights

- **Client-side encryption** — data sealed before leaving the device (Zero-Knowledge: storage provider never sees plaintext or keys).
- **TAI-Crypt engine:** PBKDF2-SHA256 (600k iterations) → custom XOR-stream transform layer → AES-256-GCM authenticated encryption.
- **Key management:** Windows DPAPI-bound key wrapping; master password never stored in plaintext.
- **REST API mode** — `/protect`, `/restore`, `/files` — any enterprise software integrates in minutes.
- **Cloud-agnostic storage backend** — OneDrive today, S3/Azure tomorrow. Provider sees only anonymous blobs.

## Proven MVP Results (working today)

- 6 real files → 840+ indistinguishable encrypted fragments live on OneDrive sync.
- Hourly automatic renaming via Windows Task Scheduler — verified operational.
- Round-trip integrity: fragment → rejoin → decrypt = byte-perfect original.
- REST API protect/restore cycle tested end-to-end.

## Tech Stack

Python 3.14 · Tkinter UI · cryptography lib (AES-256-GCM) · PBKDF2-HMAC-SHA256 ·
Windows DPAPI · PowerShell automation (.lnk generation, unblock) · Task Scheduler ·
stdlib HTTP REST server · OneDrive sync backend

## Target Market

- Enterprises handling sensitive documents (legal, healthcare, finance)
- Government / defense data-at-rest protection
- Backup & archival systems where a breach is catastrophic
- Any organization needing zero-knowledge cloud storage

## Roadmap

1. Shamir's Secret Sharing — master key split across multiple custodians
2. Central admin dashboard + audit logging
3. Native S3 / Azure Blob backends
4. Per-user keys and secure team sharing
5. Behaviour-analysis module (access pattern anomaly detection)

## Contact / Demo

Live demo available: load any file → watch it vanish into encrypted fragments →
restore it with a single click. Storage viewable from any device — unreadable from everywhere.
