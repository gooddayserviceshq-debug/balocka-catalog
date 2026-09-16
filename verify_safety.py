#!/usr/bin/env python3
"""Verify the child-safety protections on the catalog.

robots.txt at /balocka-catalog/robots.txt is NOT honored by crawlers -- robots.txt
is only read from the ORIGIN ROOT (https://gooddayserviceshq-debug.github.io/robots.txt),
which 404s. So the per-page `<meta name="robots" content="noindex">` tag is the
only real protection. This checks every page has it.
"""
import os
import re
import glob
import urllib.request

pages = sorted(set(glob.glob("*.html") + glob.glob("*/index.html")))
print("=== local: meta robots noindex per page ===")
bad = []
for f in pages:
    h = open(f, encoding="utf-8", errors="replace").read()
    m = re.search(r'<meta[^>]*name=["\']robots["\'][^>]*>', h, re.I)
    tag = m.group(0) if m else ""
    ok = "noindex" in tag.lower()
    print(f"  {f:24} noindex={'YES' if ok else 'NO '}  {tag[:60]}")
    if not ok:
        bad.append(f)

print("\n=== live: what the sensitive pages actually serve ===")
BASE = "https://gooddayserviceshq-debug.github.io/balocka-catalog"
for p in ["season/", "photos/", "memorial/", "topgun/", ""]:
    try:
        with urllib.request.urlopen(f"{BASE}/{p}", timeout=20) as r:
            body = r.read().decode("utf-8", "replace")
            xrt = r.headers.get("X-Robots-Tag", "-")
        m = re.search(r'<meta[^>]*name=["\']robots["\'][^>]*>', body, re.I)
        print(f"  /{p:11} meta={(m.group(0)[:52] if m else 'NONE')}  X-Robots-Tag={xrt}")
    except Exception as e:
        print(f"  /{p:11} ERROR {e}")

print("\n=== origin-root robots.txt (the only one crawlers read) ===")
try:
    urllib.request.urlopen("https://gooddayserviceshq-debug.github.io/robots.txt", timeout=20)
    print("  200 - exists")
except Exception as e:
    print(f"  {e} -> absent, so NO robots.txt governs this site")

print("\nPAGES WITHOUT noindex:", bad if bad else "none")
