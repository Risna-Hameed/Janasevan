"""Find new schemes online with Tavily, from the command line.

    python3 manage_schemes.py scrape        search the official sites now (uses about 4 Tavily credits)
    python3 manage_schemes.py status        what has been found so far
    python3 manage_schemes.py hide <id>     take a wrong result off the site   (show <id> to undo)

Needs TAVILY_API_KEY in the .env file.
"""
import json
import sys

from config import Config
from services import tavily_scraper


def main(argv):
    tavily_scraper.configure(Config.TAVILY_STORE_PATH)
    cmd = argv[1] if len(argv) > 1 else "status"
    if cmd == "scrape":
        r = tavily_scraper.scrape(force=True)
        if not r.get("ok"):
            print("Could not search:", r.get("error")); return 1
        print(f"New schemes found: {len(r['added'])}")
        for a in r["added"]:
            print(f"  + {a['name']}  ({a['rules_read']} rules read)\n    {a['url']}")
        print(f"Already in the list: {r['already_known']}   Not a scheme page: {r['skipped']}")
        for e in r["errors"]:
            print("  !", e)
        return 0
    if cmd == "status":
        print(json.dumps(tavily_scraper.status(), indent=1, ensure_ascii=False)); return 0
    if cmd in ("hide", "show") and len(argv) > 2:
        ok = tavily_scraper.set_hidden(argv[2], cmd == "hide")
        print("Done." if ok else "No scheme with that id was found online."); return 0 if ok else 1
    print(__doc__); return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
