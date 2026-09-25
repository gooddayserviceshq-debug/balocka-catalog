#!/usr/bin/env python3
"""End-to-end verification of the share-unfurl fix against the LIVE site.

Gates:
  1. every page serves a complete og/twitter tag set
  2. every og:image URL returns 200 with an image content-type
  3. og:image is a real 1200x630 JPEG when downloaded (not a 404 HTML page)
  4. canonical URL resolves 200
  5. pages displaying minors serve noindex
"""
import io
import re
import sys
import urllib.request

BASE = "https://gooddayserviceshq-debug.github.io/balocka-catalog"
PAGES = {
    "/": dict(minors=True),
    "/season/": dict(minors=True),
    "/best-of.html": dict(minors=True),
    "/topgun/": dict(minors=False),
}
REQUIRED = ["og:title", "og:description", "og:image", "og:url", "og:type",
            "og:image:width", "og:image:height", "twitter:card", "twitter:image"]

UA = {"User-Agent": "facebookexternalhit/1.1"}  # behave like a real unfurl scraper


def get(url, binary=False):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        return (r.read() if binary else r.read().decode("utf-8", "replace")), r


fails = []
for path, cfg in PAGES.items():
    url = BASE + path
    print(f"\n=== {url}")
    try:
        html, _ = get(url)
    except Exception as e:
        fails.append(f"{path} unreachable: {e}")
        print(f"  FAIL unreachable {e}")
        continue

    # gate 1: tag completeness
    missing = [t for t in REQUIRED
               if not re.search(rf'(property|name)=["\']{re.escape(t)}["\']', html)]
    print(f"  tags: {'ALL PRESENT' if not missing else 'MISSING ' + str(missing)}")
    if missing:
        fails.append(f"{path} missing {missing}")

    # gate 5: noindex where minors are shown
    m = re.search(r'<meta[^>]*name=["\']robots["\'][^>]*content=["\']([^"\']*)', html, re.I)
    robots = m.group(1) if m else ""
    if cfg["minors"]:
        ok = "noindex" in robots.lower()
        print(f"  noindex (minors page): {'OK' if ok else 'FAIL'} -> '{robots}'")
        if not ok:
            fails.append(f"{path} minors page without noindex")
    else:
        print(f"  noindex: n/a (public civic page) -> '{robots or 'none'}'")

    # gate 4: canonical
    cm = re.search(r'<link[^>]*rel=["\']canonical["\'][^>]*href=["\']([^"\']+)', html, re.I)
    if cm:
        try:
            _, r = get(cm.group(1))
            print(f"  canonical {r.status} {cm.group(1)}")
            if r.status != 200:
                fails.append(f"{path} canonical {r.status}")
        except Exception as e:
            fails.append(f"{path} canonical error {e}")
            print(f"  FAIL canonical {e}")

    # gates 2+3: og:image really fetchable and really an image
    im = re.search(r'property=["\']og:image["\'][^>]*content=["\']([^"\']+)', html)
    if im:
        iu = im.group(1)
        try:
            data, r = get(iu, binary=True)
            ct = r.headers.get("Content-Type", "")
            from PIL import Image
            img = Image.open(io.BytesIO(data))
            good = r.status == 200 and img.size == (1200, 630) and "image" in ct
            print(f"  og:image {r.status} {ct} {img.size} {len(data)//1024}KB "
                  f"-> {'OK' if good else 'FAIL'}")
            if not good:
                fails.append(f"{path} og:image bad {r.status} {ct} {img.size}")
        except Exception as e:
            fails.append(f"{path} og:image unfetchable {e}")
            print(f"  FAIL og:image {e}")

print("\n" + "=" * 52)
print("ALL GATES PASS" if not fails else "FAILURES:\n  " + "\n  ".join(fails))
sys.exit(1 if fails else 0)
