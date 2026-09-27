#!/usr/bin/env python3
"""extend_day.py — add newly-local frames to a gallery that ALREADY exists.

WHY THIS IS NOT publish_day.py
------------------------------
publish_day.py creates a gallery and refuses a duplicate game id, correctly: a
second gallery for one night splits a parent's QR link across two tabs. But after
an iCloud download the need is the opposite — the gallery is right, it is simply
missing the frames whose bytes were absent on publish day. This appends those
frames in place, reusing publish_day's thumbnail routine so the rotation-baking
and q60 encoding rules cannot drift between the two tools.

THE SAFETY GATE THAT MATTERS (do not remove it)
-----------------------------------------------
Only a gallery that was published as "every local frame that night" may be
extended. Measured 2026-09-27, the galleries split cleanly in two:

  game-0831, game-0914, therock-0917   ungraded, published == every local frame
  brentwood-0821, jv-0824, ...         GRADED, published = 13-53% of local frames

For the first group, "some frames are missing because they were in iCloud" is a
defect and appending repairs it. For the second, the missing frames are missing
because Blake graded them out; appending would silently overrule a human's
curation and put rejected frames in front of families. So this refuses a gallery
that carries any tier/score unless --force-curated is passed, and prints the
coverage it measured either way.

USAGE
    python3 extend_day.py 2026-09-17 --game-id therock-0917 --prefix rock_ --dry-run
    python3 extend_day.py 2026-09-17 --game-id therock-0917 --prefix rock_ --apply
"""
import argparse
import csv
import json
import os
import re
import shutil
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from publish_day import (APPLE_EPOCH, DATA_JS, MANIFEST, ORIG, TDIR, DB,
                         load_data_js, make_thumb, orientations, save_data_js)


def local_assets(date):
    """Every R7 frame that night whose ORIGINAL really exists on this disk.

    Same filesystem-truth rule as publish_day: a DB column claiming local is not
    the bytes being present. Returns (uuid, filename, timestamp, path).
    """
    con = sqlite3.connect('file:%s?mode=ro' % DB, uri=True)
    rows = con.execute("""
        select a.ZUUID, aaa.ZORIGINALFILENAME,
               datetime(a.ZDATECREATED+%d,'unixepoch','localtime')
          from ZASSET a
          left join ZEXTENDEDATTRIBUTES e on e.Z_PK = a.ZEXTENDEDATTRIBUTES
          left join ZADDITIONALASSETATTRIBUTES aaa on aaa.ZASSET = a.Z_PK
         where a.ZTRASHEDSTATE = 0
           and e.ZCAMERAMODEL = 'Canon EOS R7'
           and date(datetime(a.ZDATECREATED+%d,'unixepoch','localtime')) = ?
         order by a.ZDATECREATED""" % (APPLE_EPOCH, APPLE_EPOCH), (date,)).fetchall()
    con.close()
    out, missing = [], 0
    for u, fn, ts in rows:
        sub = os.path.join(ORIG, u[0])
        p = None
        if os.path.isdir(sub):
            for f in os.listdir(sub):
                if f.startswith(u):
                    p = os.path.join(sub, f)
                    break
        if p:
            out.append((u, fn or (u + '.JPG'), ts, p))
        else:
            missing += 1
    return out, missing


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('date')
    ap.add_argument('--game-id', required=True)
    ap.add_argument('--prefix', required=True)
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--force-curated', action='store_true',
                    help='append to a GRADED gallery, overriding human curation')
    a = ap.parse_args()
    if not (a.apply or a.dry_run):
        ap.error('pick --dry-run or --apply')
    if not re.fullmatch(r'[a-z0-9]+_', a.prefix):
        ap.error('prefix must be lowercase and end with _')

    d = load_data_js()
    game = next((g for g in d['games'] if g['id'] == a.game_id), None)
    if game is None:
        sys.exit('FAIL: no gallery %s in data.js (use publish_day.py to create one)'
                 % a.game_id)

    existing = {p['id'] for p in game['photos']}
    assets, still_icloud = local_assets(a.date)

    graded = sum(1 for p in game['photos']
                 if str(p.get('tier', '')).strip() or float(p.get('score', 0) or 0) > 0)
    coverage = 100.0 * len(game['photos']) / max(len(assets), 1)
    print('%s / %s: %d local frames tonight, %d already published (%.0f%% coverage), '
          '%d graded, %d still iCloud-only'
          % (a.date, a.game_id, len(assets), len(game['photos']), coverage, graded,
             still_icloud))

    if graded and not a.force_curated:
        sys.exit('REFUSING: %s carries %d graded frames, so its %d published photos are '
                 'a CURATED selection, not "every local frame". Appending would put '
                 'frames a human graded out in front of families. Re-run with '
                 '--force-curated only if that is genuinely intended.'
                 % (a.game_id, graded, len(game['photos'])))

    new = [(u, fn, ts, p) for (u, fn, ts, p) in assets
           if (a.prefix + os.path.splitext(fn)[0]) not in existing]
    print('   %d frames are local but NOT in the gallery' % len(new))
    if not new:
        print('nothing to do: gallery already holds every local frame')
        return

    # A recycled Canon filename would collide with an id from ANOTHER night; the
    # per-game prefix prevents cross-game collisions, but two frames in the SAME
    # night sharing a filename would silently overwrite one thumbnail.
    stems = [os.path.splitext(fn)[0] for _, fn, _, _ in new]
    dupes = {s for s in stems if stems.count(s) > 1}
    if dupes:
        sys.exit('FAIL: %d filename(s) occur twice in this one night (%s) — one thumb '
                 'would overwrite the other' % (len(dupes), sorted(dupes)[:5]))

    if a.dry_run:
        print('dry run: would append %d photos to %s' % (len(new), a.game_id))
        print('   first:', new[0][1], ' last:', new[-1][1])
        return

    omap = orientations([p for _, _, _, p in new])
    rot = {}
    rows = []
    added = 0
    for i, (u, fn, ts, p) in enumerate(new):
        pid = a.prefix + os.path.splitext(fn)[0]
        dst = os.path.join(TDIR, pid + '.jpg')
        o = make_thumb(p, dst, omap.get(p, 1))
        rot[o] = rot.get(o, 0) + 1
        game['photos'].append({'id': pid, 'score': 0, 'tier': '',
                               'thumb': 't/%s.jpg' % pid, 't': 't/%s.jpg' % pid})
        rows.append({'id': pid, 'file': pid + '.jpg', 'game_id': a.game_id,
                     'game_label': game.get('label', ''), 'tier': '', 'score': '0',
                     'original': p})
        added += 1
        if added % 50 == 0:
            print('   ...%d/%d thumbs' % (added, len(new)))
    print('   orientations baked:', rot)

    bak = DATA_JS + '.bak-%d' % int(time.time())
    shutil.copy2(DATA_JS, bak)
    save_data_js(d)
    with open(MANIFEST, 'a', newline='', encoding='utf-8') as f:
        csv.DictWriter(f, fieldnames=['id', 'file', 'game_id', 'game_label',
                                      'tier', 'score', 'original']).writerows(rows)

    chk = load_data_js()
    g = next(x for x in chk['games'] if x['id'] == a.game_id)
    ids = [p['id'] for p in g['photos']]
    if len(ids) != len(set(ids)):
        sys.exit('FAIL: gallery %s now holds duplicate ids' % a.game_id)
    miss = [i for i in ids if not os.path.exists(os.path.join(TDIR, i + '.jpg'))]
    if miss:
        sys.exit('FAIL: %d ids have no thumbnail on disk (%s)' % (len(miss), miss[:5]))
    print('extended %s: %d -> %d photos, every id thumbed (backup %s)'
          % (a.game_id, len(ids) - added, len(ids), os.path.basename(bak)))


if __name__ == '__main__':
    main()
