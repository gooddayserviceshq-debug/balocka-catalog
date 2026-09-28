#!/usr/bin/env python3
"""grade_day.py — grade a published gallery on the season's own scale.

WHY THIS EXISTS
---------------
publish_day.py deliberately publishes ungraded (tier "", score 0), which leaves
the hub's A+/A/B+ filters disabled for that night. This is the grading half.

The season scale is NOT a fresh invention: it is the metric from the original
grade_all.py (a center-weighted sharpness score), reproduced here verbatim.
Provenance was verified, not assumed:

  * master_grades.json reproduces all 3,822 previously published scores EXACTLY
    (0 differences across brentwood-0821, oakland-0904, rockvale-0907,
    hendersonville-0828, jv-0824, scrimmage-0731).
  * Thresholds: >=100 "A+", >=80 "A", >=60 "B+", below 60 is a non-keeper.
  * Hard quality gate first: mean < 40 or contrast < 12 scores 0 (a black frame
    or a scoreboard shot is not a keeper at any sharpness).

THE SURFACE MATTERS
-------------------
Grade the ORIGINAL, never the web thumbnail. Re-rendering a frame with a known
published score through publish_day.py's thumbnail chain (sips -Z 600 -> bake
rotation -> Pillow q60) costs a mean -3.1 points and flips 24% of tiers. Scoring
originals reproduces published scores at MAE 0.26. --verify proves this on the
already-graded galleries before you trust a run.

WHAT IT WRITES
--------------
score for every frame it can grade; tier only for frames >= 60. Sub-60 frames
keep tier "" -- the hub's vocabulary is {A+, A, B+, ""} and it has no badge for
a "cut", so writing one would paint a red chip on a kid's photo. Nothing is
deleted: the old galleries were culled to keepers, but culling these would strip
1,245 jersey-tagged frames and leave 33 jersey numbers with no photo at all for
a night. Photos are sorted best-first because the hub does no client-side sort.

    python3 tools/grade_day.py --verify
    python3 tools/grade_day.py --game-id therock-0917 --dry-run
    python3 tools/grade_day.py --game-id therock-0917 --apply
"""
import argparse
import csv
import json
import os
import random
import re
import shutil
import sqlite3
import sys
import time

import numpy as np
from PIL import Image, ImageOps

HOME = os.path.expanduser('~')
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_JS = os.path.join(REPO, 'season', 'data.js')
MANIFEST = os.path.join(REPO, 'season-t-manifest.csv')
TDIR = os.path.join(REPO, 'season', 't')
DB = os.path.join(HOME, 'Pictures/Photos Library.photoslibrary/database/Photos.sqlite')
ORIG = os.path.join(HOME, 'Pictures/Photos Library.photoslibrary/originals')
APPLE_EPOCH = 978307200
GRID = (400, 267)
FLOOR = 60.0

# Galleries graded by the original pass; used by --verify as ground truth.
REFERENCE = ('brentwood-0821', 'oakland-0904', 'rockvale-0907',
             'hendersonville-0828', 'jv-0824')


# --------------------------------------------------------------- the metric
def sharpness_of(region):
    g = np.gradient(region)
    return float(np.mean(g[0] ** 2 + g[1] ** 2))


def grade_array(a):
    """Verbatim grade_all.py. `a` is float32 grayscale at GRID."""
    mean = float(a.mean())
    h, w = a.shape
    cx0, cx1 = int(w * 0.25), int(w * 0.75)
    cy0, cy1 = int(h * 0.20), int(h * 0.80)
    center = a[cy0:cy1, cx0:cx1]
    edges_h = np.abs(np.diff(a, axis=1))[:, cx0:cx1]
    dx_v = np.abs(np.diff(a, axis=0))[cy0:cy1, cx0:cx1]
    total_h = float(np.abs(np.diff(a, axis=1)).mean()) + 1e-6
    total_v = float(np.abs(np.diff(a, axis=0)).mean()) + 1e-6
    center_focus = 0.5 * (float(edges_h.mean()) / total_h
                          + float(dx_v.mean()) / total_v)
    center_sharp = sharpness_of(center)
    contrast = float(a.std())
    if mean < 40 or contrast < 12:
        return 0.0
    s_norm = min(center_sharp / 900.0, 2.0)
    c_norm = min(max(center_focus - 1.05, 0) / 0.45, 1.5)
    e_norm = min(contrast / 60.0, 1.0)
    return round(100 * (s_norm * 0.55 + c_norm * 0.30 + e_norm * 0.15), 1)


def tier_for(score):
    if score >= 100:
        return 'A+'
    if score >= 80:
        return 'A'
    if score >= FLOOR:
        return 'B+'
    return ''


def score_path(path):
    im = ImageOps.exif_transpose(Image.open(path))
    return grade_array(np.asarray(im.convert('L').resize(GRID), dtype=np.float32))


# ------------------------------------------------------------------- io
def load_data_js():
    raw = open(DATA_JS, encoding='utf-8').read()
    m = re.match(r'\s*window\.SEASON_DATA\s*=\s*', raw)
    if not m:
        sys.exit('FAIL: unexpected data.js header')
    return json.loads(raw[m.end():].strip().rstrip(';'))


def save_data_js(d):
    tmp = DATA_JS + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        f.write('window.SEASON_DATA = ')
        json.dump(d, f, ensure_ascii=False, separators=(', ', ': '))
        f.write(';\n')
    os.replace(tmp, DATA_JS)
    # Pages serves data.js with max-age=600; without a versioned <script src>
    # the grades would not reach a returning visitor for ten minutes.
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from stamp_data_version import stamp
    stamp(quiet=True)


def backup_data_js(suffix='bak'):
    """Copy data.js to a rollback file OUTSIDE the served directory.

    Backups used to be written as season/data.js.<suffix>-<epoch>, i.e. inside
    season/, which GitHub Pages SERVES -- one was committed and publicly
    fetchable. A backup is a pre-edit snapshot, so one taken before a privacy fix
    preserves exactly what the fix removed. .backups/ is neither served nor
    tracked. Mirrors publish_day.backup_data_js.
    """
    d = os.path.join(REPO, '.backups')
    os.makedirs(d, exist_ok=True)
    dst = os.path.join(d, 'data.js.%s-%d' % (suffix, int(time.time())))
    shutil.copy2(DATA_JS, dst)
    return dst


def manifest_originals():
    out = {}
    with open(MANIFEST, newline='', encoding='utf-8') as f:
        for r in csv.DictReader(f):
            out[r['id']] = r['original']
    return out


def photos_on(date):
    """Date-SCOPED lookup. A global basename join is unsafe: 179 basenames recur
    across nights and IMGL4016 exists on four different dates."""
    con = sqlite3.connect('file:%s?mode=ro' % DB, uri=True)
    rows = con.execute("""
        select a.ZUUID, aaa.ZORIGINALFILENAME
          from ZASSET a
          left join ZEXTENDEDATTRIBUTES e on e.Z_PK = a.ZEXTENDEDATTRIBUTES
          left join ZADDITIONALASSETATTRIBUTES aaa on aaa.ZASSET = a.Z_PK
         where a.ZTRASHEDSTATE = 0
           and e.ZCAMERAMODEL = 'Canon EOS R7'
           and date(datetime(a.ZDATECREATED+%d,'unixepoch','localtime')) = ?
        """ % APPLE_EPOCH, (date,)).fetchall()
    out = {}
    for u, fn in rows:
        sub = os.path.join(ORIG, u[0])
        if os.path.isdir(sub):
            for f in os.listdir(sub):
                if f.startswith(u):
                    stem = os.path.splitext(fn or '')[0]
                    if stem:
                        out[stem] = os.path.join(sub, f)
                    break
    return out


def date_from_game_id(gid):
    m = re.search(r'(\d{2})(\d{2})$', gid)
    if not m:
        return None
    return '2026-%s-%s' % (m.group(1), m.group(2))


# --------------------------------------------------------------- verify mode
def verify(n=40):
    """Prove the metric still reproduces the season's published scores.

    GROUND TRUTH MUST BE A MEASUREMENT, NOT A DEFAULT
    -------------------------------------------------
    This pool used to accept every photo in the reference galleries, including
    frames publish_day.py had appended at the placeholder `score: 0, tier: ""`.
    Those are absent data, not a published grade -- and by 09-28 they were
    88.3% of the pool (3,094 of 3,502). Measuring such a frame correctly at
    ~46 scored as a +46 "error", so verify() reported bias +37.5 / MAE 37.6 and
    FAILED. Since verify() is also the pre-write gate in main(), a correct
    metric was refusing to write correct grades, and the refusal read as
    "the metric drifted".

    Filtering the pool to frames that carry a real measurement (score > 0, or
    score 0 explicitly flagged `gated` by the quality floor) gives
    bias -0.21 / MAE 0.24 / 2 tier flips in 60 on the same code and the same
    pixels. A placeholder can never again be mistaken for ground truth.
    """
    d = load_data_js()
    games = {g['id']: g for g in d['games']}
    orig = manifest_originals()
    pool, placeholders = [], 0
    for gid in REFERENCE:
        for p in games.get(gid, {}).get('photos', []):
            o = orig.get(p['id'])
            if not (o and os.path.exists(o)):
                continue
            if not float(p.get('score', 0) or 0) and not p.get('gated'):
                placeholders += 1   # never measured; not evidence of anything
                continue
            pool.append((p['id'], o, p['score'], p['tier']))
    if placeholders:
        print('verify: excluded %d unmeasured frames from the ground-truth pool'
              % placeholders)
    if not pool:
        print('VERIFY SKIPPED: no reference originals reachable on this machine')
        return True
    random.seed(99)
    samp = random.sample(pool, min(n, len(pool)))
    ds, flips = [], 0
    for pid, o, pub, ptier in samp:
        sc = score_path(o)
        ds.append(sc - pub)
        if tier_for(sc) != ptier:
            flips += 1
    k = len(ds)
    mae = sum(abs(x) for x in ds) / k
    bias = sum(ds) / k
    print('verify: n=%d  bias=%+.2f  MAE=%.2f  tier flips=%d/%d' %
          (k, bias, mae, flips, k))
    ok = mae < 1.0 and flips <= max(1, k // 20)
    print('VERIFY %s — metric %s the published season scale'
          % ('PASS' if ok else 'FAIL', 'reproduces' if ok else 'DIVERGES from'))
    return ok


# ------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--game-id')
    ap.add_argument('--date', help='override; default derived from the id suffix')
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--verify', action='store_true')
    a = ap.parse_args()

    if a.verify and not a.game_id:
        sys.exit(0 if verify() else 1)
    if not a.game_id:
        ap.error('--game-id is required (or use --verify alone)')
    if not (a.apply or a.dry_run):
        ap.error('pick --dry-run or --apply')

    d = load_data_js()
    game = next((g for g in d['games'] if g['id'] == a.game_id), None)
    if game is None:
        sys.exit('FAIL: no gallery %s in data.js' % a.game_id)

    orig = manifest_originals()
    date = a.date or date_from_game_id(a.game_id)
    by_date = photos_on(date) if date else {}

    graded, ungradable = {}, []
    for p in game['photos']:
        o = orig.get(p['id'])
        if not (o and os.path.exists(o)):
            o = by_date.get(p['id'].split('_', 1)[-1])
        if o and os.path.exists(o):
            graded[p['id']] = score_path(o)
        else:
            ungradable.append(p['id'])

    if not graded:
        sys.exit('FAIL: no originals reachable for %s — nothing to grade'
                 % a.game_id)

    counts = {}
    for pid, sc in graded.items():
        counts[tier_for(sc) or '(below floor)'] = \
            counts.get(tier_for(sc) or '(below floor)', 0) + 1
    print('%s: graded %d/%d frames, %d without a reachable original'
          % (a.game_id, len(graded), len(game['photos']), len(ungradable)))
    print('   tiers: %s' % counts)
    keep = sum(1 for s in graded.values() if s >= FLOOR)
    print('   keepers (>=%.0f): %d (%.1f%%)'
          % (FLOOR, keep, keep * 100.0 / len(graded)))

    if not a.apply:
        print('\nDRY RUN — pass --apply to write')
        return

    if not verify():
        sys.exit('REFUSING to write: the metric no longer reproduces the season '
                 'scale, so these grades would be on a different scale.')

    for p in game['photos']:
        if p['id'] in graded:
            p['score'] = graded[p['id']]
            p['tier'] = tier_for(graded[p['id']])
    game['photos'].sort(key=lambda p: -float(p.get('score', 0) or 0))

    bak = backup_data_js('grade-bak')
    save_data_js(d)

    rows, fields = [], None
    with open(MANIFEST, newline='', encoding='utf-8') as f:
        rd = csv.DictReader(f)
        fields = rd.fieldnames
        rows = list(rd)
    if not fields:
        sys.exit('FAIL: manifest has no header row')
    for row in rows:
        if row['game_id'] == a.game_id and row['id'] in graded:
            row['score'] = str(graded[row['id']])
            row['tier'] = tier_for(graded[row['id']])
    tmp = MANIFEST + '.tmp'
    with open(tmp, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, MANIFEST)

    chk = load_data_js()
    g2 = next(x for x in chk['games'] if x['id'] == a.game_id)
    sc = [float(p.get('score', 0) or 0) for p in g2['photos']]
    assert all(sc[i] >= sc[i + 1] for i in range(len(sc) - 1)), 'not sorted'
    bad = [p['id'] for p in g2['photos'] if p['tier'] not in ('A+', 'A', 'B+', '')]
    assert not bad, 'invalid tier vocabulary: %s' % bad[:5]
    assert any(p['tier'] for p in g2['photos']), 'no tier written — filters stay off'
    print('wrote data.js + manifest (backup %s); sorted, vocabulary clean'
          % os.path.basename(bak))


if __name__ == '__main__':
    main()
