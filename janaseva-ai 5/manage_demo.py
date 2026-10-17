"""Demo data and licence management (development only).

  python manage_demo.py seed                 add fictional demo panchayats if missing
  python manage_demo.py reset                remove ONLY demo panchayat data, then re-seed
  python manage_demo.py licence CODE STATUS  set a panchayat licence: active | suspended | expired
  python manage_demo.py accounts             list demo sign-in accounts

Student data (profiles, drafts, saved schemes) is never touched.
"""
import sys
from datetime import date, timedelta

from app import create_app
from config import Config
from database.db import connect, now
from database.seed_panchayat import DEMO_PASSWORD, OFFICIALS, reset, seed


class NoSeed(Config):
    SEED_DEMO = False


def main(argv):
    app = create_app(NoSeed)
    cmd = argv[1] if len(argv) > 1 else "help"
    with app.app_context():
        db = connect(app.config["DATABASE"])
        if cmd == "seed":
            print("Seeded." if seed(db) else "Demo data already present.")
        elif cmd == "reset":
            print(f"Removed demo data for {reset(db)} panchayat(s).")
            seed(db); print("Fresh demo data seeded.")
        elif cmd == "licence" and len(argv) == 4 and argv[3] in ("active", "suspended", "expired"):
            row = db.execute("SELECT id FROM panchayats WHERE code=?", (argv[2].upper(),)).fetchone()
            if not row:
                print("Unknown panchayat code."); return 1
            exp = (date.today() - timedelta(days=1)).isoformat() if argv[3] == "expired" else (date.today() + timedelta(days=365)).isoformat()
            db.execute("UPDATE panchayat_licences SET status=?, expires_on=?, updated_at=? WHERE panchayat_id=?",
                       ("active" if argv[3] == "expired" else argv[3], exp, now(), row["id"]))
            print(f"Licence for {argv[2].upper()} set to {argv[3]}.")
        elif cmd == "accounts":
            print(f"Demo password for every account: {DEMO_PASSWORD}")
            for u, name, role, code, wards in OFFICIALS:
                print(f"  {u:16} {role:22} {code:7} wards: {wards or 'all'}")
        else:
            print(__doc__); return 0 if cmd == "help" else 1
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
