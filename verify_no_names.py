#!/usr/bin/env python3
"""verify_no_names.py -- fail the build if player identity leaks back into the catalog.

WHY THIS EXISTS
---------------
Commit f5aaa1b ("Privacy: remove all player names") stripped all 71 names.
Commit b4dcbab ("Season hub v2 ... jersey search") silently put every one back,
and they stayed live until 2026-09-22. Nothing caught it, because:

  * verify_safety.py only checks <meta name="robots" content="noindex"> on
    *.html. A noindex META TAG DOES NOT APPLY TO A DATA FILE. season/data.js is
    its own URL; a crawler or a human fetches it directly and the tag on
    season/index.html is irrelevant to it.
  * There were TWO name stores, not one -- `roster` (list of dicts) and
    `rosterByNum` (jersey -> name lookup, what the search bar actually reads).
    Blanking only `roster` looks fixed and leaves the lookup table intact.

So this checks the DATA, in every *.js / *.json under the catalog, not the page.

USAGE
    python3 verify_no_names.py            # local files
    python3 verify_no_names.py --live     # also fetch the published copies
Exit 0 = clean, exit 1 = a name is present. Wire it into any publish step.
"""
import json
import re
import sys
import glob
import os
import subprocess
import urllib.request

# Paths whose data must never carry a personal name. Add to this list, never remove.
# .csv is included deliberately: a manifest is a data file too, and one already
# leaked absolute local paths ("/Users/blake/Desktop/...") into the repo tree.
#
# .html is included because the "data vs page" distinction was wrong: index.html
# carried a hand-written `const players = [...]` array with two live names. Any
# tracked text file can hold a name, so the scan follows git, not a glob list.
DATA_GLOBS = ["season/*.js", "season/*.json", "season/*.csv",
              "photos/*.js", "photos/*.json", "photos/*.csv",
              "memorial/**/*.js", "memorial/**/*.json",
              "*.js", "*.json", "*.csv"]

# Every tracked file with one of these suffixes is scanned, wherever it lives.
TRACKED_SUFFIXES = (".js", ".json", ".csv", ".html", ".htm", ".txt", ".md")


def tracked_files():
    """Every git-tracked text file. A glob list only covers the places someone
    thought of; git covers the places that actually ship."""
    try:
        r = subprocess.run(["git", "ls-files"], capture_output=True, text=True)
        return [f for f in r.stdout.splitlines()
                if f.endswith(TRACKED_SUFFIXES) and "/node_modules/" not in f]
    except Exception:
        return []

# Absolute local paths reveal the operator's real name and machine layout.
LOCAL_PATH = re.compile(r"/Users/[A-Za-z0-9._-]+/")

# Keys known to have held names. Any of these being non-empty is a hard failure.
NAME_KEYS = {"name", "player", "playerName", "athlete", "fullName", "rosterByNum"}

# Strings that legitimately look like "Firstname Lastname" in this data.
ALLOWED_PHRASES = {
    "Full Game", "Fri Sep", "Sunday Sep", "Scrimmage Jul", "Scrimmage Aug",
    "Best Of", "Top Gun", "Night Run", "Photo Catalog", "Balocka Creative",
    "Smyrna Bulldogs", "Town Of", "Depot Days", "Car Show", "Photos Library",
    # Venues and event labels, not people. Each was verified by hand before
    # being added -- this list is the only place a two-word capitalized string
    # is permitted, so it stays short and every entry gets checked.
    "Ridgemont Park", "JV Scrimmage",
}

HUMAN_NAME = re.compile(r"\b[A-Z][a-z]{1,15} [A-Z][a-z]{1,15}\b")

# "AJ Jackson", "RJ Williams", "TJ Moore" -- initials have NO lowercase letter
# after the leading capital, so HUMAN_NAME cannot match them. Two real names sat
# live in index.html for days because both this file and a hand-written sweep
# used only the Firstname-Lastname shape. Kids go by initials constantly.
INITIALS_NAME = re.compile(r"\b[A-Z]{2,3} [A-Z][a-z]{1,15}\b")


def walk(obj, path=""):
    """Yield (json_path, key, value) for every string leaf."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from walk(v, f"{path}.{k}")
            if isinstance(v, str) and v.strip():
                yield (path, k, v)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from walk(v, f"{path}[{i}]")


def load_jsonish(text):
    """Parse a bare JSON file or a `window.X = {...};` assignment."""
    t = text.strip()
    if not t.startswith(("{", "[")):
        i = t.find("{")
        if i < 0:
            return None
        t = t[i:]
    t = t.rstrip().rstrip(";")
    try:
        return json.loads(t)
    except Exception:
        return None


def check_text(label, text, failures, html=False):
    if html:
        # A page is mostly prose, and prose is full of two-capitalized-word
        # phrases ("Mayor Reed", "Photo Grid", "Lee Victory"). Raw-scanning it
        # produces dozens of false hits, and a check that cries wolf gets
        # ignored -- which is how the 152 survived. Names that reach a browser
        # as DATA live in <script> string literals, so scan exactly those.
        scripts = re.findall(r"<script\b[^>]*>(.*?)</script>", text, re.S | re.I)
        if not scripts:
            return
        text = "\n".join(scripts)
        quoted = re.findall(r'"([^"\\\n]{2,40})"|\'([^\'\\\n]{2,40})\'', text)
        text = "\n".join(a or b for a, b in quoted)
    data = load_jsonish(text)
    if data is not None:
        for parent, key, val in walk(data):
            if key in NAME_KEYS and val.strip():
                failures.append(f"{label}: key '{key}' at {parent} is non-empty: {val[:40]!r}")
        # rosterByNum is a dict of jersey -> name; a non-empty VALUE is the leak
        rbn = data.get("rosterByNum") if isinstance(data, dict) else None
        if isinstance(rbn, dict):
            live = {k: v for k, v in rbn.items() if isinstance(v, str) and v.strip()}
            if live:
                failures.append(f"{label}: rosterByNum has {len(live)} non-empty names, e.g. {list(live.items())[:2]}")
    # belt-and-braces: raw scan, catches a name smuggled into a field we don't know
    for m in set(HUMAN_NAME.findall(text)) | set(INITIALS_NAME.findall(text)):
        if m not in ALLOWED_PHRASES:
            failures.append(f"{label}: looks like a personal name in the raw text: {m!r}")
    for m in set(LOCAL_PATH.findall(text)):
        failures.append(f"{label}: absolute local path leaks a home directory: {m!r}")


def git_ignored(paths):
    """Files git ignores never reach the server -- don't fail the build on them."""
    if not paths:
        return set()
    try:
        r = subprocess.run(["git", "check-ignore"] + sorted(paths),
                           capture_output=True, text=True)
        return set(r.stdout.split())
    except Exception:
        return set()


def main():
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    failures = []
    candidates = set()
    for g in DATA_GLOBS:
        for f in glob.glob(g, recursive=True):
            if "/node_modules/" not in f:
                candidates.add(f)
    candidates |= set(tracked_files())
    ignored = git_ignored(candidates)
    if ignored:
        print(f"=== skipping {len(ignored)} git-ignored file(s): {sorted(ignored)} ===")
    seen = set()
    for f in sorted(candidates):
        if f in ignored or not os.path.exists(f):
            continue
        seen.add(f)
        check_text(f, open(f, encoding="utf-8", errors="replace").read(), failures,
                   html=f.endswith((".html", ".htm")))
    print(f"=== local: scanned {len(seen)} tracked text files ===")
    for f in sorted(seen):
        print(f"  {f}")

    if "--live" in sys.argv:
        base = open("CNAME").read().strip() if os.path.exists("CNAME") else None
        base = f"https://{base}" if base else "https://gooddayserviceshq-debug.github.io/balocka-catalog"
        print(f"\n=== live: {base} ===")
        for rel in ["season/data.js"]:
            url = f"{base}/{rel}?cachebust={os.getpid()}"
            try:
                with urllib.request.urlopen(url, timeout=30) as r:
                    check_text(f"LIVE {rel}", r.read().decode("utf-8", "replace"), failures)
                print(f"  fetched {rel}")
            except Exception as e:
                failures.append(f"LIVE {rel}: could not verify ({e})")

    print()
    if failures:
        print("FAIL -- personal names present:")
        for f in failures:
            print("  *", f)
        print("\nNames must stay blank. Jersey number + position + class is the")
        print("identifier; a number is not a search term that leads to a child.")
        return 1
    print("PASS -- no personal names in any catalog data file.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
