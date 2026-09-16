#!/usr/bin/env python3
"""Insert og:/twitter:/description meta tags into the four balocka-catalog pages.

Idempotent: the block is wrapped in <!-- balocka-og --> markers, so re-running
replaces in place instead of stacking duplicates (same pattern as add_analytics.py).

Absolute URLs are required — og:image with a relative path does not unfurl.
The existing `robots noindex` on /season/ is left untouched: og tags control how a
link someone was already given renders when forwarded; they do not expose the page
to search. Those are different mechanisms and the child-safety rule is unaffected.
"""
import re
import sys

BASE = "https://gooddayserviceshq-debug.github.io/balocka-catalog"

PAGES = {
    "index.html": dict(
        url=f"{BASE}/",
        title="Smyrna Bulldogs Football — Photo Catalog",
        desc="Free game photos for Smyrna Bulldogs players and families. "
             "Shot from the stands by a Bulldogs parent — the same view you had.",
        img=f"{BASE}/og/og-home.jpg",
    ),
    "season/index.html": dict(
        url=f"{BASE}/season/",
        title="Smyrna Bulldogs Football — 2026 Season Photo Hub",
        desc="Every game, every gallery, one link. Free for players and families. "
             "Always. No watermark on your copy.",
        img=f"{BASE}/og/og-season.jpg",
    ),
    "best-of.html": dict(
        url=f"{BASE}/best-of.html",
        title="Best of Smyrna Bulldogs Football",
        desc="The standout frames of the season, in one place. "
             "Free for players and families.",
        img=f"{BASE}/og/og-bestof.jpg",
    ),
    "topgun/index.html": dict(
        url=f"{BASE}/topgun/",
        title="9/11 Top Gun Run — City of Smyrna",
        desc="Event coverage for the City of Smyrna's 9/11 Top Gun Run "
             "by Balocka Creative.",
        img=f"{BASE}/og/og-topgun.jpg",
    ),
}

START, END = "<!-- balocka-og -->", "<!-- /balocka-og -->"


def esc(s):
    return s.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")


def block(p):
    return f"""{START}
<meta name="description" content="{esc(p['desc'])}">
<link rel="canonical" href="{p['url']}">
<meta property="og:type" content="website">
<meta property="og:site_name" content="Balocka Creative">
<meta property="og:url" content="{p['url']}">
<meta property="og:title" content="{esc(p['title'])}">
<meta property="og:description" content="{esc(p['desc'])}">
<meta property="og:image" content="{p['img']}">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta property="og:image:alt" content="{esc(p['title'])} — Balocka Creative">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{esc(p['title'])}">
<meta name="twitter:description" content="{esc(p['desc'])}">
<meta name="twitter:image" content="{p['img']}">
{END}"""


def main():
    rc = 0
    for path, p in PAGES.items():
        try:
            html = open(path, encoding="utf-8").read()
        except FileNotFoundError:
            print(f"MISSING {path}")
            rc = 1
            continue

        blk = block(p)
        if START in html:
            html = re.sub(re.escape(START) + r".*?" + re.escape(END), blk,
                          html, flags=re.S)
            action = "updated"
        else:
            # insert immediately after the viewport meta, else after <head>
            m = re.search(r"<meta[^>]*viewport[^>]*>", html, re.I)
            if m:
                at = m.end()
            else:
                m = re.search(r"<head[^>]*>", html, re.I)
                if not m:
                    print(f"NO HEAD {path}")
                    rc = 1
                    continue
                at = m.end()
            html = html[:at] + "\n" + blk + html[at:]
            action = "inserted"

        open(path, "w", encoding="utf-8").write(html)
        print(f"{action:9} {path}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
