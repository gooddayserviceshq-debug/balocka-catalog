#!/usr/bin/env python3
"""test_publish_writes_tn.py — a published frame must get its tn derivative
from the SAME call that writes its tile.

WHY
---
publish_day and extend_day append records carrying 'thumb': 't/<id>.jpg'. Once
season/tn exists and the grid reads it, a publish with no matching tn pass
silently puts 35KB tiles back in the grid for those frames, and nothing on a
green board reports it. This happened live: 148 records were appended
mid-session and were only wired to tn/ because the batch builder was re-run by
hand. "Someone remembers the second command" is not an invariant.

make_thumb() now derives the tn file as write boundary part 3. These are
MUTATION tests of that hook: break the derivative and assert make_thumb raises
rather than returning a tile with no thumbnail.

    python3 tools/test_publish_writes_tn.py
"""
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import build_thumbtier as bt  # noqa: E402
import publish_day as pd  # noqa: E402
from build_thumbtier import THUMB2_W, MAX_TN_BYTES, ThumbTierError  # noqa: E402

results = []


def check(label, ok, detail=''):
    results.append((label, ok, detail))
    print('%-62s %s%s' % (label, 'PASS' if ok else 'FAIL',
                          '  ' + detail if detail else ''))


def find_source():
    """A real oversized JPEG, so the encode path is genuinely exercised."""
    for d in ('/Users/blake/Desktop/_balocka/recapwall_frames',
              '/Users/blake/Desktop/_balocka/export_2026-09-07',
              '/Users/blake/Desktop/_balocka/exports_2026-09-11'):
        if os.path.isdir(d):
            for f in sorted(os.listdir(d)):
                if f.lower().endswith(('.jpg', '.jpeg')):
                    p = os.path.join(d, f)
                    if os.path.getsize(p) > 2_000_000:
                        return p
    return None


def main():
    src = find_source()
    if not src:
        check('oversized source image available', False, 'skipped')
        return summarise()
    check('oversized source image available', True, os.path.basename(src))

    sandbox = tempfile.mkdtemp(prefix='pubtn_', dir=os.environ.get('TMPDIR') or '/tmp')
    real_tn = bt.TNDIR
    try:
        t_sand = os.path.join(sandbox, 't')
        tn_sand = os.path.join(sandbox, 'tn')
        os.makedirs(t_sand)
        bt.TNDIR = tn_sand

        # ---- the hook fires on a normal publish -------------------------
        dst = os.path.join(t_sand, 'test_IMGL9001.jpg')
        pd.make_thumb(src, dst, 1)
        tn = os.path.join(tn_sand, 'test_IMGL9001.jpg')
        check('make_thumb writes the tile', os.path.exists(dst),
              '%s B' % format(os.path.getsize(dst), ',') if os.path.exists(dst) else '')
        check('make_thumb ALSO writes the tn derivative — no second command',
              os.path.exists(tn),
              '%s B' % format(os.path.getsize(tn), ',') if os.path.exists(tn) else 'MISSING')
        if os.path.exists(tn):
            import check_thumbs
            wh = check_thumbs.dims_batch([tn]).get(os.path.realpath(tn))
            check('the derivative is within THUMB2_W', wh and max(wh) <= THUMB2_W,
                  '%dx%d' % wh if wh else 'unmeasurable')
            check('the derivative is under MAX_TN_BYTES',
                  os.path.getsize(tn) < MAX_TN_BYTES,
                  '%s B < %s' % (format(os.path.getsize(tn), ','),
                                 format(MAX_TN_BYTES, ',')))
            check('the derivative carries no EXIF (-strip on a public gallery)',
                  b'Exif' not in open(tn, 'rb').read(4096))

        # ---- MUTATION: the derivative step fails -> make_thumb must raise
        # A tile with no derivative is the defect; it must not return quietly.
        real_run = subprocess.run

        def fake_run(cmd, *a, **k):
            if cmd and cmd[0] == 'magick':
                class R:
                    returncode, stdout, stderr = 1, '', 'simulated magick failure'
                return R()
            return real_run(cmd, *a, **k)

        dst2 = os.path.join(t_sand, 'test_IMGL9002.jpg')
        bt.subprocess.run = fake_run
        try:
            pd.make_thumb(src, dst2, 1)
            check('make_thumb RAISES when the tn derivative fails', False,
                  'returned quietly with no derivative')
        except ThumbTierError:
            check('make_thumb RAISES when the tn derivative fails', True)
        finally:
            bt.subprocess.run = real_run

        # ---- MUTATION: a collision-suffixed id never reaches tn ---------
        dst3 = os.path.join(t_sand, 'test_IMGL9003 2.jpg')
        try:
            pd.make_thumb(src, dst3, 1)
            check('a collision-suffixed id is rejected before any tn write',
                  False, 'accepted')
        except Exception as e:
            check('a collision-suffixed id is rejected before any tn write',
                  'collision' in str(e).lower(), type(e).__name__)
        check('no collision-suffixed file left in tn',
              not os.path.exists(os.path.join(tn_sand, 'test_IMGL9003 2.jpg')))

        # ---- MUTATION: an oversized derivative is deleted, not shipped --
        fatdir = os.path.join(sandbox, 'fat_tn')
        os.makedirs(fatdir)
        bt.TNDIR = fatdir

        def fat_run(cmd, *a, **k):
            # simulate an encode that "succeeds" without resizing
            if cmd and cmd[0] == 'magick':
                shutil.copy2(src, cmd[-1])

                class R:
                    returncode, stdout, stderr = 0, '', ''
                return R()
            return real_run(cmd, *a, **k)

        bt.subprocess.run = fat_run
        dst4 = os.path.join(t_sand, 'test_IMGL9004.jpg')
        try:
            pd.make_thumb(src, dst4, 1)
            check('an unresized derivative is refused', False, 'accepted')
        except ThumbTierError as e:
            check('an unresized derivative is refused',
                  'MAX_TN_BYTES' in str(e) or 'THUMB2_W' in str(e), str(e)[:60])
        finally:
            bt.subprocess.run = real_run
        check('the refused oversized derivative was DELETED, not left on disk',
              not os.path.exists(os.path.join(fatdir, 'test_IMGL9004.jpg')))
    finally:
        bt.TNDIR = real_tn
        shutil.rmtree(sandbox, ignore_errors=True)

    return summarise()


def summarise():
    bad = [r for r in results if not r[1]]
    print()
    if bad:
        print('FAIL: %d of %d: %s' % (len(bad), len(results), [b[0] for b in bad]))
        return 1
    print('PASS — %d/%d. A published frame cannot reach the grid without its '
          'tn derivative.' % (len(results), len(results)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
