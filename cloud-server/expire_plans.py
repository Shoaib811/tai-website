"""
TAI Cloud - Daily Plan Expiry Job

Expired plans ko trial mein downgrade karta hai.
Windows Scheduled Task "TAI Plan Expiry" ise roz raat chalata hai.

Usage:
  python expire_plans.py              # expire kar do
  python expire_plans.py --dry-run    # sirf batao, kuch mat badlo
"""
import os
import sqlite3
import sys
from datetime import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, "data", "taicloud.db")
TRIAL_QUOTA = 2 * 1024**3


def ensure_migration(cur) -> None:
    try:
        cur.execute("ALTER TABLE users ADD COLUMN plan_expires_at TEXT")
    except sqlite3.OperationalError:
        pass


def main() -> None:
    dry = "--dry-run" in sys.argv
    now = datetime.now().isoformat(timespec="seconds")

    if not os.path.exists(DB):
        print("ERROR: database not found:", DB)
        sys.exit(1)

    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    ensure_migration(cur)

    rows = cur.execute(
        "SELECT id, email, plan FROM users "
        "WHERE plan != 'trial' AND plan_expires_at IS NOT NULL "
        "AND plan_expires_at < ?",
        (now,),
    ).fetchall()

    if not rows:
        print("[{}] Sab plans valid - kuch expire nahi hua.".format(now[:16]))
        conn.close()
        return

    for r in rows:
        print("[{}] EXPIRED: {} ({}) -> trial".format(now[:16], r["email"], r["plan"]))
        if not dry:
            cur.execute(
                "UPDATE users SET plan='trial', quota_bytes=?, plan_expires_at=NULL WHERE id=?",
                (TRIAL_QUOTA, r["id"]),
            )

    if dry:
        print("(dry-run: kuch nahi badla)")
    else:
        conn.commit()
        print("Total downgraded: {}".format(len(rows)))
    conn.close()


if __name__ == "__main__":
    main()
