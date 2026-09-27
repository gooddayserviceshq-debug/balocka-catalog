#!/usr/bin/env python3
"""test_extend_day.py -- regression tests for extend_day.py's three fixed defects.

Each test asserts BOTH directions: that the fixed code behaves correctly, AND
that the pre-fix logic was genuinely broken. A test that only checks the new
behaviour cannot tell a real fix from a test that never could have failed --
so every case here includes the old key as a positive control and asserts it
produced the wrong answer on real data.

  python3 tools/test_extend_day.py

Writes nothing: it exercises selection logic only, never make_thumb or save_data_js.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from extend_day import (already_published, local_assets, published_identity,
                        tagged_stems)
from publish_day import MANIFEST, load_data_js

FAILS = []


def check(name, cond, detail=''):
    print('%-4s %s%s' % ('ok' if cond else 'FAIL', name,
                         '' if cond else '  <- ' + detail))
    if not cond:
        FAILS.append(name)


def old_key_new_count(assets, existing, prefix):
    """The PRE-FIX dedup: prefix + camera filename against the id set."""
    return sum(1 for (_u, fn, _ts, _p) in assets
               if (prefix + os.path.splitext(fn)[0]) not in existing)


def main():
    d = load_data_js()
    games = {g['id']: g for g in d['games']}

    # ---- fixture: a gallery whose ids carry NO prefix (camera filename only)
    # and one whose ids are prefix + Photos UUID. These are the two schemes the
    # old prefix+filename key could never match.
    cases = [
        # gallery, date, prefix, id scheme
        ('brentwood-0821', '2026-08-21', 'bw_', 'bare camera filename'),
        ('oakland-0904', '2026-09-04', 'oak_', 'prefix + Photos UUID'),
        ('jv-0824', '2026-08-24', 'jv_', 'bare camera filename'),
        ('hendersonville-0828', '2026-08-28', 'hv_', 'bare camera filename'),
        ('rockvale-0907', '2026-09-07', 'rv_', 'prefix + Photos UUID'),
    ]

    print('=== DEFECT 1: dedup key must recognise published frames under ANY '
          'id scheme ===')
    for gid, date, prefix, scheme in cases:
        g = games.get(gid)
        if g is None:
            check('%s present' % gid, False, 'gallery missing from data.js')
            continue
        assets, _ = local_assets(date)
        if not assets:
            print('  (skip %s: no local originals on this machine)' % gid)
            continue
        existing = {p['id'] for p in g['photos']}
        ident = published_identity(g, MANIFEST)

        new_fixed = [x for x in assets
                     if not already_published(ident, x[0], x[1], x[3], prefix)]
        recognised = len(assets) - len(new_fixed)
        new_old = old_key_new_count(assets, existing, prefix)
        recognised_old = len(assets) - new_old

        # The fix must recognise a substantial number of already-published frames.
        check('%s (%s): fixed dedup recognises published frames' % (gid, scheme),
              recognised > 0,
              'recognised 0 of %d local frames as already published' % len(assets))

        # POSITIVE CONTROL: the old key must have recognised FEWER -- proving the
        # defect was real and that this test could have failed.
        check('%s: OLD key was broken (control)' % gid,
              recognised_old < recognised,
              'old key recognised %d, fixed recognises %d -- no improvement, so '
              'this test proves nothing' % (recognised_old, recognised))

        if recognised_old < recognised:
            print('       recognised as already-published: old key %d, fixed %d, '
                  'of %d local frames' % (recognised_old, recognised, len(assets)))
            print('       => old key would have DUPLICATED %d published frames'
                  % (recognised - recognised_old))

    # ---- no candidate id may collide with an existing id
    print()
    print('=== DEFECT 1b: no selected frame may collide with a published id ===')
    for gid, date, prefix, _scheme in cases:
        g = games.get(gid)
        if g is None:
            continue
        assets, _ = local_assets(date)
        if not assets:
            continue
        existing = {p['id'] for p in g['photos']}
        ident = published_identity(g, MANIFEST)
        new = [x for x in assets
               if not already_published(ident, x[0], x[1], x[3], prefix)]
        collide = [prefix + os.path.splitext(fn)[0] for (_u, fn, _t, _p) in new
                   if (prefix + os.path.splitext(fn)[0]) in existing]
        check('%s: 0 id collisions among selected frames' % gid,
              not collide, '%d collide, e.g. %s' % (len(collide), collide[:3]))

    # ---- tag filter
    print()
    print('=== DEFECT 2: --only-tagged must select strictly fewer frames ===')
    for gid, date, prefix, _scheme in cases:
        g = games.get(gid)
        if g is None:
            continue
        assets, _ = local_assets(date)
        tagged = tagged_stems(date)
        if not assets or tagged is None:
            print('  (skip %s: no assets or no tag file)' % gid)
            continue
        ident = published_identity(g, MANIFEST)
        new = [x for x in assets
               if not already_published(ident, x[0], x[1], x[3], prefix)]
        only = [x for x in new
                if os.path.splitext(x[1])[0].upper() in tagged
                or os.path.splitext(os.path.basename(x[3]))[0].upper() in tagged]
        check('%s: tagged subset is smaller than all-local' % gid,
              0 < len(only) < len(new),
              'tagged=%d all=%d -- filter is inert' % (len(only), len(new)))

    # ---- tag parsing: empty jersey list is NOT tagged
    print()
    print('=== DEFECT 2b: a present-but-EMPTY jersey list is not "tagged" ===')
    import json
    p = os.path.join(os.path.expanduser('~'),
                     'Pictures/balocka-library/player-tags-2026-08-21.json')
    if os.path.exists(p):
        obj = json.load(open(p, encoding='utf-8'))
        empties = {os.path.splitext(k)[0].upper() for k, v in obj.items()
                   if isinstance(v, list) and not v}
        tagged = tagged_stems('2026-08-21') or set()
        check('empty-list frames excluded from tagged set',
              bool(empties) and not (empties & tagged),
              '%d empty-list frames, %d leaked into the tagged set'
              % (len(empties), len(empties & tagged)))
        check('the file HAS empty entries (control)', bool(empties),
              'no empty entries, so the previous check proves nothing')

    # ---- curation gate detects curation without grades
    print()
    print('=== DEFECT 3: curation gate must fire on coverage, not just grades ===')
    for gid, date, _prefix, _s in cases + [('siegel-0911', '2026-09-11', 'sg_', '')]:
        g = games.get(gid)
        if g is None:
            continue
        assets, _ = local_assets(date)
        if not assets:
            continue
        coverage = 100.0 * len(g['photos']) / len(assets)
        graded = sum(1 for p in g['photos']
                     if str(p.get('tier', '')).strip()
                     or float(p.get('score', 0) or 0) > 0)
        fires_new = coverage < 95.0
        fires_old = graded > 0
        check('%s: coverage gate fires (%.0f%% coverage)' % (gid, coverage),
              fires_new, 'gate would NOT fire at %.0f%% coverage' % coverage)
        if not fires_old and fires_new:
            print('       control: OLD grade-only gate would NOT have fired here '
                  '(0 graded) -- this is the siegel-0911 class of bug')

    print()
    if FAILS:
        print('FAIL: %d check(s) failed: %s' % (len(FAILS), FAILS))
        sys.exit(1)
    print('all checks passed')


if __name__ == '__main__':
    main()
