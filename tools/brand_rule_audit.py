#!/usr/bin/env python3
"""Brand-rule audit for the balocka-catalog repo.

Hard rules being enforced (fan-vantage positioning):
  - No page may claim sideline/field/media access.
  - No page may claim school knowledge, approval, or affiliation.
    An EXPLICIT disclaimer ("not affiliated with...") is allowed.
  - No page may route a family request through a coach, AD, booster,
    or athletic staff member. "team parent" is family-direct and fine.

Why this script exists: the 2026-09-16 audit graded this PASS after
fetching 4 of the repo's HTML pages. This enumerates EVERY *.html and
prints the count so a subset can never pass silently again.

Usage:
  python3 tools/brand_rule_audit.py            # audit the repo working tree
  python3 tools/brand_rule_audit.py --live     # audit the live GitHub Pages site

Exit 0 = clean, exit 1 = violations found.
"""

import argparse
import os
import re
import sys
import urllib.error
import urllib.request

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIVE_BASE = "https://gooddayserviceshq-debug.github.io/balocka-catalog"

# Narrow exemptions. Each entry is (pattern, rule_ids) where rule_ids is the
# set of rules that line is exempt from — or None meaning "exempt from all".
#
# Exemptions are SCOPED ON PURPOSE. An earlier version exempted the whole line
# from every rule whenever it matched, which meant a line reading
#   "Not affiliated with Smyrna HS. I shoot from the sideline with the
#    school's permission, ask any coach."
# audited clean: the disclaimer bought a free pass on three real violations.
# A disclaimer answers the affiliation question ONLY; it never licenses an
# access, permission, or staff-routing claim sitting on the same line.
ALLOWLIST = [
    # Explicit non-affiliation disclaimers are the correct pattern — but they
    # only excuse the affiliation rule.
    (re.compile(r"not affiliated with", re.I), {"school-affiliation"}),
    # Gallery DATA ROWS are photo descriptions — subject matter, never access
    # claims. Shape: ["ID","#jersey","description",...] one per line, as emitted
    # into the inline arrays in index.html and best-of.html. Words like
    # "sideline" or "on field" describe where the PLAYERS were, not Blake.
    # Matched by structural shape, not by keyword, so prose can never qualify.
    (re.compile(r'^\s*\["[A-Za-z0-9_]+","[^"]*","[^"]*"'), None),
    # UI category filter button for huddle/sideline photo grouping.
    (re.compile(r"filterCards\('huddle'\)", re.I), {"access-position"}),
]

RULES = [
    (
        "access-position",
        re.compile(
            r"\b(from|on|at|near|along|by|in|onto|beside)\s+(the\s+)?"
            r"(sideline|side line|field|end ?zone|bench|track)\b",
            re.I,
        ),
        "claims sideline / field-level position (fan vantage is the stands)",
    ),
    (
        "access-credential",
        re.compile(
            r"\b((media|press|photo|sideline|field)\s+"
            r"(pass|passes|credential\w*|badge|armband|access|row|vest)"
            r"|press box|field level|credentialed)\b",
            re.I,
        ),
        "claims media / press credentials or restricted access",
    ),
    (
        "school-knowledge",
        re.compile(
            r"(school|coach\w*|athletic\w*|AD|administration)'?s?"
            r"[^.<>]{0,30}\b(knowledge|permission|approval|blessing|"
            r"okay|ok with|consent|invit\w+|sanction\w*)",
            re.I,
        ),
        "claims school knowledge / permission / approval",
    ),
    (
        "school-affiliation",
        re.compile(
            r"(photographer|shooter|staff|team)\b[^.<>]{0,30}?"
            r"(\bfor\b|\bof\b|\bwith\b|\bat\b|,|&mdash;|—|-)\s*"
            r"[^.<>]{0,20}?"
            r"(Smyrna High|Smyrna HS|RCS|the school\b|school athletics|"
            r"High School athletics|Bulldogs? (athletics|program))",
            re.I,
        ),
        "asserts school affiliation without a disclaimer",
    ),
    (
        "staff-routing",
        re.compile(
            r"\b(coach|coaches|athletic staff|athletic director|"
            r"\bAD\b|booster|boosters|front office)\b",
            re.I,
        ),
        "routes a request through school staff (coach / AD / booster)",
    ),
]


def html_files():
    found = []
    for dirpath, dirnames, filenames in os.walk(REPO_ROOT):
        dirnames[:] = [d for d in dirnames if d not in (".git", "tools")]
        for name in sorted(filenames):
            if name.endswith(".html"):
                found.append(os.path.join(dirpath, name))
    return sorted(found)


def rel(path):
    return os.path.relpath(path, REPO_ROOT)


def live_url(path):
    r = rel(path)
    if r == "index.html":
        return LIVE_BASE + "/"
    if r.endswith("/index.html"):
        return LIVE_BASE + "/" + r[: -len("index.html")]
    return LIVE_BASE + "/" + r


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "balocka-brand-audit"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.status, resp.read().decode("utf-8", "replace")


def scan(label, text):
    hits = []
    for lineno, line in enumerate(text.splitlines(), 1):
        exempt = set()
        skip_line = False
        for pattern, rule_ids in ALLOWLIST:
            if pattern.search(line):
                if rule_ids is None:
                    skip_line = True
                    break
                exempt |= rule_ids
        if skip_line:
            continue
        for rule_id, pattern, why in RULES:
            if rule_id in exempt:
                continue
            m = pattern.search(line)
            if m:
                hits.append((label, lineno, rule_id, why, line.strip()[:160], m.group(0)))
    return hits


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true",
                    help="audit the live GitHub Pages site instead of the working tree")
    args = ap.parse_args()

    files = html_files()
    mode = "LIVE" if args.live else "REPO"
    print(f"Balocka brand-rule audit — {mode}")
    print(f"Repo root: {REPO_ROOT}")
    print(f"HTML pages enumerated: {len(files)}")
    for f in files:
        print(f"  - {rel(f)}" + (f"  -> {live_url(f)}" if args.live else ""))
    print()

    all_hits = []
    errors = []
    for f in files:
        if args.live:
            url = live_url(f)
            try:
                status, text = fetch(url)
            except urllib.error.HTTPError as e:
                errors.append(f"{url} HTTP {e.code}")
                continue
            except Exception as e:  # noqa: BLE001
                errors.append(f"{url} {type(e).__name__}: {e}")
                continue
            if status != 200:
                errors.append(f"{url} HTTP {status}")
                continue
            label = url
        else:
            with open(f, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
            label = rel(f)
        all_hits.extend(scan(label, text))

    if errors:
        print("FETCH ERRORS:")
        for e in errors:
            print(f"  ! {e}")
        print()

    if all_hits:
        print(f"VIOLATIONS: {len(all_hits)}")
        for label, lineno, rule_id, why, line, matched in all_hits:
            print(f"\n  {label}:{lineno}  [{rule_id}] {why}")
            print(f"    matched: {matched!r}")
            print(f"    line:    {line}")
    else:
        print(f"PASS — 0 violations across {len(files)} pages.")

    return 1 if (all_hits or errors) else 0


if __name__ == "__main__":
    sys.exit(main())
