#!/usr/bin/env python3
"""check_thumbs.py — cheap pre-push invariant check on season/t.

WHY THIS EXISTS
---------------
On 2026-09-27 the live catalog went OVER the 1GB GitHub Pages limit because 99
full-res originals (1,112,137,163 B) were sitting in season/t under macOS's
"<stem> 2.jpg" collision-suffix names. Each had a correct ~33.6KB twin, so the
encoder had not failed -- an unguarded second write had landed and nothing
looked at the directory afterwards.

publish_day.make_thumb() now refuses both failure modes at the write boundary.
This script is the SECOND line: it re-checks the invariant on the tree as it
stands, without running a publish, so it can be run before a commit or push.

    python3 tools/check_thumbs.py          # scan season/t, exit 1 on violation
    python3 tools/check_thumbs.py --quiet  # only print violations

Exit codes: 0 clean, 1 violations found, 2 could not scan.
"""
import argparse
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
# THUMB_W lives in publish_day so this script and the write guard can never
# disagree about what "too big" means.
from publish_day import THUMB_W, TDIR, COLLISION_SUFFIX_RE  # noqa: E402

# A KNOWN, FROZEN set of pre-existing files that break the long-edge rule.
#
# The first scan of the real tree found 319 such files. They are NOT the
# incident shape: they are 600x796 / 400x714 portrait thumbs, none over 90,635
# bytes, 18,499,211 bytes in total, and 316 of the 319 are LIVE gallery images
# that entered in commit 4836907 ("balocka-catalog: clean tree") -- before these
# tools existed, from an older width-600 resize convention rather than
# long-edge-600. Deleting them would 404 316 published photos.
#
# So they are recorded rather than ignored: every name is listed in
# tools/thumb_baseline.txt WITH its dimensions and byte count, and a file is
# grandfathered only when all three match -- name, measured dims, size. A
# different file under a baselined name is a violation, not inherited debt. ANY
# OTHER violation fails too. That means the invariant is enforced from today
# forward and the legacy debt is visible and countable instead of being swallowed
# by a loosened threshold. Re-cutting the baseline is a deliberate act
# (--rewrite-baseline), never automatic, and it refuses to run while a live
# violation is present.
BASELINE = os.path.join(HERE, 'thumb_baseline.txt')


def load_baseline(path=BASELINE):
    """name -> (width, height, bytes) for every frozen legacy file.

    IDENTITY, not name. An earlier version returned bare names, which meant any
    file occupying one of the 319 baselined names passed the size check no matter
    what it actually was -- a 6000x4000 original dropped onto a baselined name
    scanned clean and got counted as "known legacy debt". The baseline
    grandfathers THE EXACT FILES that were measured, so the dims and byte count
    are part of the key: change the file and it becomes a violation again.
    """
    if not os.path.exists(path):
        return {}
    rows = {}
    for line in open(path, encoding='utf-8'):
        line = line.rstrip('\n')
        if not line.strip() or line.startswith('#'):
            continue
        parts = line.split('\t')
        if len(parts) != 3:
            continue
        name, dims, size = parts[0], parts[1].strip(), parts[2].strip()
        w, _, h = dims.partition('x')
        if not (w.isdigit() and h.isdigit() and size.isdigit()):
            continue
        rows[name] = (int(w), int(h), int(size))
    return rows


def dims_batch(paths):
    """Width/height for many files in ONE sips call.

    Per-file spawns cost ~40ms; a 10,000-file season/t would take seven minutes
    that way and nobody would run it before a push. One batched call scans the
    whole directory in seconds.
    """
    out = {}
    if not paths:
        return out

    # sips prints, per file, an unindented PATH line followed by indented
    # "  key: value" lines -- and it resolves the path it echoes (/tmp becomes
    # /private/tmp), so results are keyed by realpath and looked up the same way.
    CHUNK = 500
    for i in range(0, len(paths), CHUNK):
        batch = paths[i:i + CHUNK]
        r = subprocess.run(['sips', '-g', 'pixelWidth', '-g', 'pixelHeight'] + batch,
                           capture_output=True, text=True)
        cur, w, h = None, None, None

        def flush():
            if cur and w is not None and h is not None:
                out[os.path.realpath(cur)] = (w, h)

        for line in r.stdout.splitlines():
            if line and not line[0].isspace():
                flush()
                cur, w, h = line.rstrip(), None, None
                continue
            key, _, val = line.strip().partition(':')
            val = val.strip()
            if key == 'pixelWidth' and val.isdigit():
                w = int(val)
            elif key == 'pixelHeight' and val.isdigit():
                h = int(val)
        flush()
    return out


def scan(tdir=TDIR, limit=THUMB_W, quiet=False, baseline=None, write_baseline=False):
    if not os.path.isdir(tdir):
        print('FAIL: %s is not a directory' % tdir)
        return 2

    if baseline is None:
        baseline = load_baseline()

    names = sorted(f for f in os.listdir(tdir)
                   if f.lower().endswith(('.jpg', '.jpeg')))
    paths = [os.path.join(tdir, f) for f in names]

    # (a) collision-suffixed names. Pure string check, no image decode.
    # NEVER baselined: there are none today and there is no legitimate reason
    # for one to appear.
    collided = [f for f in names if COLLISION_SUFFIX_RE.match(f)]

    # (b) anything whose long edge exceeds the thumbnail limit.
    got = dims_batch(paths)
    oversize, unreadable, grandfathered, changed_baselined = [], [], [], []
    for p in paths:
        f = os.path.basename(p)
        wh = got.get(os.path.realpath(p))
        if not wh:
            unreadable.append(p)
            continue
        if max(wh) > limit:
            size = os.path.getsize(p)
            rec = (p, wh, size)
            # Three-way identity match: name AND measured dims AND byte size must
            # equal the frozen record. A *different* file under a baselined name
            # is a violation, not inherited debt.
            frozen = baseline.get(f)
            if frozen is not None and frozen == (wh[0], wh[1], size):
                grandfathered.append(rec)
            else:
                oversize.append(rec)
                if frozen is not None:
                    changed_baselined.append((f, frozen, (wh[0], wh[1], size)))

    if write_baseline:
        # Refuse to freeze a live violation. Re-cutting the baseline is meant to
        # record pre-existing legacy debt, not to launder whatever bad write is
        # sitting in the tree right now.
        if collided:
            print('REFUSING to rewrite the baseline: %d collision-suffixed name(s) '
                  'present. Remove them first — a collision suffix is never '
                  'baselineable.' % len(collided))
            for f in collided[:20]:
                print('   %s' % f)
            return 1
        if changed_baselined:
            print('REFUSING to rewrite the baseline: %d baselined name(s) now hold a '
                  'DIFFERENT file. Resolve those before freezing.'
                  % len(changed_baselined))
            for f, old, new in changed_baselined[:20]:
                print('   %s  frozen %dx%d/%s B  ->  now %dx%d/%s B'
                      % (f, old[0], old[1], format(old[2], ','),
                         new[0], new[1], format(new[2], ',')))
            return 1
        prev = load_baseline()
        rows = sorted((os.path.basename(p), wh, s)
                      for p, wh, s in grandfathered + oversize)
        added = [r for r in rows if prev.get(r[0]) != (r[1][0], r[1][1], r[2])]
        dropped = [n for n in prev if n not in {r[0] for r in rows}]
        with open(BASELINE, 'w', encoding='utf-8') as fh:
            fh.write('# Pre-existing season/t files whose long edge exceeds '
                     'THUMB_W=%d.\n' % limit)
            fh.write('# Frozen legacy debt, NOT permission: these predate the write '
                     'guard in\n# publish_day.make_thumb(). Anything not listed here '
                     'fails tools/check_thumbs.py.\n')
            fh.write('# %d files, %s bytes total. Regenerate only deliberately, with '
                     '--rewrite-baseline.\n' % (len(rows), format(sum(r[2] for r in rows), ',')))
            fh.write('# name\tWxH\tbytes\n')
            for n, (w, h), s in rows:
                fh.write('%s\t%dx%d\t%d\n' % (n, w, h, s))
        print('wrote baseline %s (%d files, %s bytes)'
              % (BASELINE, len(rows), format(sum(r[2] for r in rows), ',')))
        print('  newly added:  %d' % len(added))
        for n, (w, h), s in added[:20]:
            print('     + %s  %dx%d  %s bytes' % (n, w, h, format(s, ',')))
        if len(added) > 20:
            print('     ... and %d more' % (len(added) - 20))
        print('  removed:      %d' % len(dropped))
        for n in dropped[:20]:
            print('     - %s' % n)
        return 0

    if not quiet:
        print('scanned %s' % tdir)
        print('  files:          %s' % format(len(names), ','))
        print('  limit:          long edge <= %dpx' % limit)
        if grandfathered:
            print('  baselined:      %s known legacy oversize (%s bytes) — see %s'
                  % (format(len(grandfathered), ','),
                     format(sum(s for _, _, s in grandfathered), ','),
                     os.path.basename(BASELINE)))

    bad = 0
    if collided:
        bad += len(collided)
        print('\nVIOLATION: %d collision-suffixed name(s) — these can only be '
              'duplicate writes:' % len(collided))
        for f in collided[:20]:
            print('   %s  (%s bytes)'
                  % (f, format(os.path.getsize(os.path.join(tdir, f)), ',')))
        if len(collided) > 20:
            print('   ... and %d more' % (len(collided) - 20))

    if oversize:
        bad += len(oversize)
        total = sum(s for _, _, s in oversize)
        print('\nVIOLATION: %d NEW file(s) exceed the %dpx thumbnail limit — %s bytes '
              'that are not in the frozen baseline:'
              % (len(oversize), limit, format(total, ',')))
        for p, (w, h) in [(p, wh) for p, wh, _ in oversize[:20]]:
            print('   %s  %dx%d  (%s bytes)'
                  % (os.path.basename(p), w, h, format(os.path.getsize(p), ',')))
        if len(oversize) > 20:
            print('   ... and %d more' % (len(oversize) - 20))

    if changed_baselined:
        # Already counted inside `oversize`; called out separately because this
        # is the sneaky shape -- a known legacy NAME now holding a different file.
        print('\n  ^ %d of those occupy a BASELINED name but are not the baselined '
              'file (the baseline grandfathers exact files, not names):'
              % len(changed_baselined))
        for f, old, new in changed_baselined[:20]:
            print('   %s  frozen %dx%d/%s B  ->  now %dx%d/%s B'
                  % (f, old[0], old[1], format(old[2], ','),
                     new[0], new[1], format(new[2], ',')))
        if len(changed_baselined) > 20:
            print('   ... and %d more' % (len(changed_baselined) - 20))

    if unreadable:
        bad += len(unreadable)
        print('\nVIOLATION: %d file(s) sips could not measure — a file in season/t '
              'that is not a readable image should not be there:' % len(unreadable))
        for p in unreadable[:20]:
            print('   %s' % os.path.basename(p))

    if bad:
        print('\nFAIL: %d violation(s) in %s. Do not push.' % (bad, tdir))
        return 1
    if not quiet:
        print('\nOK: no new oversize, no collision-suffixed names. %s file(s) within '
              '%dpx%s.'
              % (format(len(names) - len(grandfathered), ','), limit,
                 ' plus %d baselined legacy files' % len(grandfathered)
                 if grandfathered else ''))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--dir', default=TDIR, help='directory to scan (default season/t)')
    ap.add_argument('--limit', type=int, default=THUMB_W,
                    help='max allowed long edge in px (default THUMB_W=%d)' % THUMB_W)
    ap.add_argument('--quiet', action='store_true', help='print only violations')
    ap.add_argument('--no-baseline', action='store_true',
                    help='ignore thumb_baseline.txt and report ALL oversize files')
    ap.add_argument('--rewrite-baseline', action='store_true',
                    help='freeze the CURRENT oversize set as the new baseline '
                         '(deliberate act — review the diff before committing)')
    a = ap.parse_args()
    base = {} if a.no_baseline else None
    sys.exit(scan(a.dir, a.limit, a.quiet, base, a.rewrite_baseline))


if __name__ == '__main__':
    main()
