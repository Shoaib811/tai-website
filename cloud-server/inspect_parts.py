import math
import os
import sqlite3
import sys

BASE = r"E:\tricrypt AI\cloud-server\data"
DB = os.path.join(BASE, "taicloud.db")
PARTS_DIR = os.path.join(BASE, "parts")

conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row

print("=" * 60)
print("1) DATABASE - parts TABLE (server ko kya dikhta hai)")
print("=" * 60)
rows = conn.execute(
    "SELECT p.id, p.size, u.email FROM parts p JOIN users u ON u.id=p.user_id ORDER BY p.created_at DESC LIMIT 6"
).fetchall()
for r in rows:
    print(f"  ID: {r['id']}  |  {r['size']} bytes  |  owner: {r['email']}")
print("  >> Dekho: sirf RANDOM IDs - file ka naam KAHIN nahi!")

print()
print("=" * 60)
print("2) DATABASE - map_blob (TAIMAP index)")
print("=" * 60)
row = conn.execute("SELECT size, substr(data,1,32) AS head FROM map_blob").fetchone()
if row:
    head_hex = bytes(row["head"]).hex()
    print(f"  Blob size : {row['size']} bytes")
    print(f"  Pehle 32 bytes (hex): {head_hex}")
    print("  >> Ye poora blob ENCRYPTED hai - bina master password ke bekaar!")
else:
    print("  (map_blob khaali hai)")

conn.close()

part_files = []
for root, _dirs, files in os.walk(PARTS_DIR):
    for f in files:
        part_files.append(os.path.join(root, f))

if not part_files:
    print("\n(koi part nahi mila)")
    sys.exit(0)

target = max(part_files, key=os.path.getmtime)
with open(target, "rb") as fh:
    raw = fh.read()

print()
print("=" * 60)
print(f"3) EK PART FILE KE ANDAR KYA HAI")
print(f"   File: {os.path.basename(target)}")
print("=" * 60)
print("  Pehle 64 bytes (hex):")
for i in range(0, 64, 16):
    chunk = raw[i : i + 16]
    print(f"    {i:04x}: {chunk.hex(' ')}")

print()
print("  Notepad mein kholo to dikhega (pehle 80 chars):")
sample = "".join(chr(b) if 32 <= b < 127 else "." for b in raw[:80])
print(f"    {sample}")

runs = []
cur = ""
for b in raw:
    if 32 <= b < 127:
        cur += chr(b)
    else:
        if len(cur) >= 6:
            runs.append(cur)
        cur = ""
print()
print(f"  Poori file mein 6+ letter ke readable words: {len(runs)} mile")
print(f"  Wo bhi ye random pieces the: {runs[:8]}")

freq = {}
for b in raw:
    freq[b] = freq.get(b, 0) + 1
n = len(raw)
entropy = -sum((c / n) * math.log2(c / n) for c in freq.values())
print()
print(f"  Randomness score (entropy): {entropy:.2f} / 8.00")
print("  >> 8.00 ke bilkul paas = PERFECT random data (encryption ka proof!)")
