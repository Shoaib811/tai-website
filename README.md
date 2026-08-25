# Tricrypt AI (TAI)

AI-powered **Cyber Security** project.

## Project Info

| Item | Detail |
|------|--------|
| Full Name | Tricrypt AI |
| Short Name | TAI |
| Domain | Cyber Security |
| Status | MVP Planning |

## Folder Structure

```
OneDrive\TAI Cloud\          <- REAL CLOUD SYNC! (phone/web se bhi dikhega)
├── Storage\                 <- root khaali
│   ├── A\  B\  C\           <- encrypted 100KB parts + hidden index
E:\tricrypt AI\
├── TAI.bat                  <- app launch
├── TAI-API.bat              <- REST API server
└── tai\main.py, taicrypt.py, server.py   <- code + keys (sirf PC pe!)
```

## How It Works

1. User `TAI.bat` run karta hai → TAI window khulti hai
2. **FILES LOAD KARO** button se ek ya multiple files select karo
3. File cloud mein random naam se jati hai → phir **100KB parts** mein cut hoti hai
4. Parts `cloud\Storage` mein save hote hain — **har part exactly 100KB** (chhota part pad hota hai)
5. Original jagah pe **shortcut (.lnk)** rehti hai
6. `cloud\mapping.json` mein sab record: original naam ↔ parts ↔ shortcut ↔ size

## Storage System

| Item | Detail |
|------|--------|
| Part size | 100 KB (102400 bytes) fixed |
| Part name | `TAIP-{random-id}-{serial}.tpart` |
| Distribution | Parts round-robin: A → B → C → A... (serial hisaab se) |
| Padding | Aakhri chhota part null-bytes se 100KB bhara jata hai |
| Mapping | `TAIMAP-*.tpart` parts mein chhupi hoti hai (4-byte length header) |
| Rejoin | Shortcut click → parts A/B/C se jud kar file khulti hai |

**Note:** `mapping.json` ab disk pe nahi rehti — pura index `TAIMAP-*` parts ke andar hai. Cloud folder mein sirf `Storage` folder hota hai.

## MVP Features

1. **Behaviour Check** — User behaviour analysis (login attempts, file access patterns)
2. **Encryption** — data/files encrypt karna

## Notes

- `cloud` folder ko hum cloud ki tarah use karenge (filhaal local hai).

## REST API Mode

`TAI-API.bat` chalao → `http://127.0.0.1:8765` pe REST API live:

| Method | Endpoint | Kaam |
|--------|----------|------|
| GET | `/status` | Server info + files count |
| GET | `/files` | Saari files ki list |
| POST | `/protect?name=<file>` | File bytes bhejo → encrypted parts ban jayenge |
| GET | `/restore/<cloud_name>` | Wapas decrypted file milegi |
| DELETE | `/file/<cloud_name>` | File + uske parts delete |

Koi bhi program (Java/.NET/web/curl) TAI se aise baat kar sakta hai — **integration ready!**

## Decisions Log

- [2026-08-22] Project start — naam finalize: Tricrypt AI (TAI)
- [2026-08-22] MVP features finalize: user behaviour analysis + encryption
- [2026-08-22] Language: Python (security + AI libraries ke liye best)
- [2026-08-22] Approach: step by step banayenge
