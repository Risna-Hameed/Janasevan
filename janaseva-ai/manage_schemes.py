"""Scheme updater from the command line.

    python manage_schemes.py update            check every official source now
    python manage_schemes.py update demo_feed  check only the fictional demo source (works offline)
    python manage_schemes.py status            last run, new schemes, changed documents
    python manage_schemes.py hide <scheme_id>  take a wrongly added scheme off the site (show <id> to undo)

The app already runs the update once a day by itself (6 am India time; set SCHEME_UPDATE_HOUR_IST).
To run it from the operating system instead, e.g. cron:  0 6 * * *  cd /path/janaseva-ai && python manage_schemes.py update
"""
import json
import sys

from config import Config
from services import scheme_updater


def main(argv):
    scheme_updater.configure(Config.SCHEME_UPDATE_DIR)
    cmd = argv[1] if len(argv) > 1 else "status"
    if cmd == "update":
        r = scheme_updater.run_update(only_sources=argv[2:] or None)
        if r.get("busy"):
            print("An update is already running."); return 1
        print(f"New schemes: {len(r['added'])}")
        for a in r["added"]:
            print(f"  + {a['name']}  ({a['rules_read']} rules read)  id={a['id']}")
        print(f"Already known: {r['skipped_known'] + r['seen_again']}   Changed documents: {len(r['changed'])}"
              f"   No longer listed: {len(r['unlisted'])}")
        for s in r["sources"]:
            print(f"  {'OK ' if s['ok'] else 'ERR'} {s['name']}: {s.get('error') or str(s['new']) + ' new'}")
        for e in r["errors"]:
            print("  !", e)
        return 0
    if cmd == "status":
        st = scheme_updater.status(Config.SCHEME_UPDATE_HOUR_IST)
        print(json.dumps({k: st[k] for k in ("last_run", "next_run", "auto_added", "doc_changes")}, indent=1, ensure_ascii=False))
        return 0
    if cmd in ("hide", "show") and len(argv) > 2:
        ok = scheme_updater.set_hidden(argv[2], cmd == "hide")
        print("Done." if ok else "No automatically added scheme with that id."); return 0 if ok else 1
    print(__doc__); return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
