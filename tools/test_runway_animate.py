#!/usr/bin/env python3
"""Regression harness for tools/runway_animate.py's privacy gate.

The gate is the only thing standing between a typo and a minor's photo being
uploaded to a third-party generative model. So: MUST_REFUSE paths are real
player-photo locations (and escape tricks) that have to be blocked, and
MUST_ACCEPT are the face-free og/ cards that must keep working.

Run:  python3 tools/test_runway_animate.py
Exit 0 = gate behaves, exit 1 = gate is broken. No network, no SDK needed.
"""

import glob
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import runway_animate as ra  # noqa: E402

REPO = ra.REPO
fails = 0


def expect(desc, ok):
    global fails
    print(("PASS " if ok else "FAIL ") + desc)
    if not ok:
        fails += 1


def refused(path):
    try:
        ra.check_input(path)
        return False
    except ra.Refused:
        return True


# Real player-photo locations: pick an actual file from each when present.
for pattern in ("season/t/*", "season/tn/*", "raw/*", "enhanced/*",
                "smyrna-topgun/**/*.jp*g", "memorial/**/*.jp*g", "photos/**/*.jp*g"):
    hits = [h for h in glob.glob(os.path.join(REPO, pattern), recursive=True) if os.path.isfile(h)]
    if hits:
        expect(f"refuses player photo {os.path.relpath(hits[0], REPO)}", refused(hits[0]))

expect("refuses hero_bg.jpg (readable jersey numbers)", refused(os.path.join(REPO, "hero_bg.jpg")))
expect("refuses bg_stadium.jpg (readable jersey numbers)", refused(os.path.join(REPO, "bg_stadium.jpg")))
expect("refuses ../ escape out of og/", refused(os.path.join(REPO, "og", "..", "hero_bg.jpg")))
expect("refuses a missing og/ file", refused(os.path.join(REPO, "og", "nope.jpg")))

with tempfile.TemporaryDirectory() as d:
    # A symlink placed inside og/ that points at a player photo must be refused.
    link = os.path.join(REPO, "og", "zz-test-link.jpg")
    try:
        os.symlink(os.path.join(REPO, "hero_bg.jpg"), link)
        expect("refuses og/ symlink pointing at a player photo", refused(link))
    finally:
        if os.path.lexists(link):
            os.remove(link)
    sub = os.path.join(REPO, "og", "zz-test-sub")
    os.makedirs(sub, exist_ok=True)
    try:
        f = os.path.join(sub, "x.jpg")
        open(f, "wb").write(b"\xff\xd8\xff")
        expect("refuses a subdirectory of og/ (allowlist is og/ itself only)", refused(f))
    finally:
        for p in glob.glob(os.path.join(sub, "*")):
            os.remove(p)
        os.rmdir(sub)

for card in sorted(glob.glob(os.path.join(REPO, "og", "*.jpg"))):
    expect(f"accepts {os.path.relpath(card, REPO)}", not refused(card))

for bad in [("16:9", 5, "x"), ("1280:720", 1, "x"), ("1280:720", 11, "x"),
            ("1280:720", 5, ""), ("1280:720", 5, "x" * 1001)]:
    try:
        ra.check_params(*bad)
        expect(f"rejects params {bad[:2]}", False)
    except ra.Refused:
        expect(f"rejects params {bad[:2]} / prompt len {len(bad[2])}", True)

req = ra.build_request(os.path.join(REPO, "og", "og-home.jpg"), "1280:720", 5, "x")
expect("request uses gen4_turbo + data:image/jpeg URI",
       req["model"] == "gen4_turbo" and req["prompt_image"].startswith("data:image/jpeg;base64,"))

print(f"\n{'OK' if not fails else f'{fails} FAILED'}")
sys.exit(1 if fails else 0)
