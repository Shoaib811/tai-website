"""
TAI Cloud - Plan Activation Tool v2 (subscription management)

Usage:
  python activate_plan.py --list                     # sab users + expiry
  python activate_plan.py --expiring                 # jo expire hue / hone wale (3 din)
  python activate_plan.py "<email>" <plan>           # 30 din ka plan activate
  python activate_plan.py "<email>" <plan> --days 365 # custom duration

Plans: trial | basic | pro
Examples:
  python activate_plan.py ali@gmail.com basic
  python activate_plan.py ali@gmail.com pro --days 30
"""
import os
import sqlite3
import sys
from datetime import datetime, timedelta

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, "data", "taicloud.db")
QUOTAS = {
    "trial": 2 * 1024**3,
    "basic": 15 * 1024**3,
    "pro": 100 * 1024**3,
}
DEFAULT_DAYS = 30


def ensure_migration(cur) -> None:
    try:
        cur.execute("ALTER TABLE users ADD COLUMN plan_expires_at TEXT")
    except sqlite3.OperationalError:
        pass


def main() -> None:
    args = sys.argv[1:]

    if not os.path.exists(DB):
        print("ERROR: database not found:", DB)
        sys.exit(1)

    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    ensure_migration(cur)

    if args == ["--list"]:
        print("=== USERS ===")
        rows = cur.execute(
            "SELECT email, plan, quota_bytes, plan_expires_at FROM users ORDER BY id"
        ).fetchall()
        for r in rows:
            exp = r["plan_expires_at"][:10] if r["plan_expires_at"] else "-"
            print("  {:32s} | {:6s} | {:4.0f} GB | expires: {}".format(
                r["email"], r["plan"], r["quota_bytes"] / 1024**3, exp))
        if not rows:
            print("  (koi user nahi)")
        conn.commit()
        conn.close()
        return

    if args == ["--expiring"]:
        now = datetime.now().isoformat(timespec="seconds")
        soon = (datetime.now() + timedelta(days=3)).isoformat(timespec="seconds")
        rows = cur.execute(
            "SELECT email, plan, plan_expires_at FROM users "
            "WHERE plan != 'trial' AND plan_expires_at IS NOT NULL "
            "AND plan_expires_at <= ? ORDER BY plan_expires_at",
            (soon,),
        ).fetchall()
        print("=== EXPIRED YA 3 DIN MEIN EXPIRE HONE WALE ===")
        if not rows:
            print("  (koi nahi - sab theek)")
        for r in rows:
            status = "EXPIRED!" if r["plan_expires_at"] < now else "expiring soon"
            print("  {:32s} | {:6s} | {} | {}".format(
                r["email"], r["plan"], r["plan_expires_at"][:10], status))
        conn.commit()
        conn.close()
        return

    days = DEFAULT_DAYS
    if "--days" in args:
        i = args.index("--days")
        try:
            days = int(args[i + 1])
            del args[i:i + 2]
        except (IndexError, ValueError):
            print("ERROR: --days ke baad number chahiye")
            sys.exit(1)

    if len(args) != 2 or args[1].lower() not in QUOTAS:
        print(__doc__)
        conn.close()
        sys.exit(1)

    email, plan = args[0].strip(), args[1].lower()
    row = cur.execute(
        "SELECT id FROM users WHERE email=? COLLATE NOCASE", (email,)
    ).fetchone()

    if not row:
        print("ERROR: is email ka account nahi mila:", email)
        conn.close()
        sys.exit(1)

    expires = None if plan == "trial" else (
        datetime.now() + timedelta(days=days)).isoformat(timespec="seconds")

    cur.execute(
        "UPDATE users SET plan=?, quota_bytes=?, plan_expires_at=? WHERE id=?",
        (plan, QUOTAS[plan], expires, row["id"]),
    )
    conn.commit()

    if plan == "trial":
        print("DONE: {} -> trial (koi expiry nahi)".format(email))
    else:
        print("DONE: {} -> {} ({:.0f} GB), {} din, expiry: {}".format(
            email, plan, QUOTAS[plan] / 1024**3, days, expires[:10]))
        print("     Renewal reminder: WhatsApp pe yaad dila dena!")
    conn.close()


if __name__ == "__main__":
    main()
