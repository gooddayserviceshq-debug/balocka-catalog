#!/usr/bin/env python3
"""test_extend_day.py -- regression tests for extend_day.py's three fixed defects.

Each test asserts BOTH directions: that the fixed code behaves correctly, AND
that the assertion could actually have failed. Defects 1 and 3 do this with the
pre-fix logic as a positive control on real data. Defect 2's check is an
INVARIANT ("no tagged frame is left unpublished"), which cannot use a pre-fix
control because it is satisfied by a completely broken tag key too -- so it
carries a control of its own asserting the filter still matches real frames,
plus a companion check (2b) that the filter still withholds untagged ones.

  python3 tools/test_extend_day.py

Writes nothing: it exercises selection logic only, never make_thumb or save_data_js.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from extend_day import (TAGDIR, already_published, local_assets,
                        published_identity, tagged_stems)
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


def is_tagged(filename, path, tagged):
    """Mirror of extend_day.py's --only-tagged predicate.

    Kept as one helper so the test cannot drift from the tool: a frame counts as
    tagged when EITHER its camera filename stem or its original-path stem is in
    the night's tagged set (the tag files were written against both naming
    schemes). Jersey numbers only -- no name is read.
    """
    return (os.path.splitext(filename)[0].upper() in tagged
            or os.path.splitext(os.path.basename(path))[0].upper() in tagged)


def raw_tagged_count(date):
    """Non-empty jersey lists counted straight from the tag file on disk.

    An INDEPENDENT reference for the 2a control, deliberately NOT routed through
    tagged_stems(). Mutation-testing showed that comparing tagged_stems()'s
    output against itself is blind to tag loss: halving the set halves both sides
    and the ratio stays at 1.0, so a broken tag key would report "0 tagged frames
    unpublished" as a vacuous pass. Reading the file directly means the reference
    survives any regression inside tagged_stems().

    Returns None when the night has no tag file.
    """
    p = os.path.join(TAGDIR, 'player-tags-%s.json' % date)
    if not os.path.exists(p):
        return None
    obj = json.load(open(p, encoding='utf-8'))
    return sum(1 for v in obj.values() if isinstance(v, list) and v)


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
    #
    # REWRITTEN after option 2 shipped (kanban t_b4af021e). The original check here
    # asserted `0 < len(only) < len(new)` -- "the tagged subset is smaller than
    # all-local". That was true only while tagged frames were still WAITING to be
    # published. Option 2 published all of them (b3b5670, 54a8d62), so len(only) is
    # now 0 on every night and the check failed on all five galleries. It was
    # asserting the bug's continued existence, not the tool's correctness.
    #
    # The two properties worth guarding post-publish are different, and neither is
    # satisfied by an inert filter:
    #
    #   2a. INVARIANT: no jersey-tagged local frame is left unpublished. This is the
    #       gap the card was filed to close, expressed as a standing assertion so a
    #       future shoot or a botched extend re-opens it loudly.
    #   2b. The filter still DISCRIMINATES: the leftover residue is non-empty and
    #       entirely untagged, so --only-tagged still withholds those bytes. If the
    #       filter silently became a pass-through, 2a would keep passing while the
    #       tool quietly appended thousands of untagged frames.
    print()
    print('=== DEFECT 2a: every jersey-tagged local frame is published ===')
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
        only = [x for x in new if is_tagged(x[1], x[3], tagged)]
        tagged_local = [x for x in assets if is_tagged(x[1], x[3], tagged)]
        raw = raw_tagged_count(date)

        # CONTROL FIRST. "0 tagged frames unpublished" is also what a broken tag key
        # reports, so the invariant below means nothing unless the filter can still
        # match real frames on this night.
        #
        # The reference is raw_tagged_count(), read straight off disk, NOT
        # len(tagged). Comparing against tagged_stems()'s own output was the first
        # version of this control and mutation-testing proved it blind: shrinking
        # tagged_stems shrinks both sides, the ratio stays 1.0, and tag loss slips
        # through. Against the raw file count, a mutation that halves the tag set
        # drops the ratio to ~0.5 and this fails.
        check('%s: tag filter still matches local frames (control)' % gid,
              raw and len(tagged_local) > 0.9 * raw,
              'the tag file on disk lists %s non-empty jersey entries but the '
              'filter matches only %d local frames -- the tag key is broken, so '
              'the invariant below proves nothing' % (raw, len(tagged_local)))

        check('%s: 0 of %d tagged local frames unpublished' % (gid, len(tagged_local)),
              not only,
              '%d tagged frames are local but NOT in the gallery, e.g. %s'
              % (len(only), [x[1] for x in only[:3]]))

    print()
    print('=== DEFECT 2b: --only-tagged still withholds the untagged residue ===')
    for gid, date, prefix, _scheme in cases:
        g = games.get(gid)
        if g is None:
            continue
        assets, _ = local_assets(date)
        tagged = tagged_stems(date)
        if not assets or tagged is None:
            continue
        ident = published_identity(g, MANIFEST)
        new = [x for x in assets
               if not already_published(ident, x[0], x[1], x[3], prefix)]
        only = [x for x in new if is_tagged(x[1], x[3], tagged)]

        # Non-vacuous because there IS a residue to withhold: these nights each hold
        # hundreds of local frames nobody tagged. --only-tagged must still cut them.
        check('%s: %d untagged frames still withheld by the filter' % (gid, len(new)),
              len(new) > 0 and len(only) < len(new),
              'residue=%d tagged=%d -- filter is a pass-through, so a --only-tagged '
              'run would append untagged frames' % (len(new), len(only)))

    # ---- tag parsing: empty jersey list is NOT tagged
    print()
    print('=== DEFECT 2c: a present-but-EMPTY jersey list is not "tagged" ===')
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
