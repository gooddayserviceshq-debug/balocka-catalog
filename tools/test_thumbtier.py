#!/usr/bin/env python3
"""test_thumbtier.py — the season/tn write boundary must stay closed.

build_thumbtier.py adds a SECOND derivative tier (200px, ~8KB) under
season/tn and rewires data.js's long-dead `thumb` key to it. Two failure
shapes are already on this repo's record, and both apply to tn:

  * 2026-09-27: 99 full-res originals reached the live site in season/t under
    macOS "<stem> 2.jpg" collision names -- 1.1GB, because nothing measured the
    directory after the write.
  * The same day, sips' ~q93 default put 3,109 files 163MB over budget: an
    encode that "succeeded" at the wrong weight.

And one specific to this card: 3,822 of the old `thumb` values pointed at
directories that do not exist (`season/thumbs/`, `oakland_0904/thumbs/`,
`sept0507/thumbs/`). Harmless while nothing read the key; 37% of the grid
broken the moment anything did. So "no dangling thumb key" is itself an
invariant with a test, not a one-off cleanup.

Every check here is a MUTATION: plant the specific bad thing and assert the
guard fails. A guard nobody has watched fail is not known to work.

    python3 tools/test_thumbtier.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import build_thumbtier as bt  # noqa: E402
from build_thumbtier import (THUMB2_W, THUMB2_QUALITY, MAX_TN_BYTES,  # noqa: E402
                             ThumbTierError, guard_tn_names, guard_tn_tree,
                             all_records, load_data_js)

results = []


def check(label, ok, detail=''):
    results.append((label, ok, detail))
    print('%-62s %s%s' % (label, 'PASS' if ok else 'FAIL',
                          '  ' + detail if detail else ''))


def with_tndir(path):
    """Point the module's guards at a sandbox directory."""
    bt.TNDIR = path


def main():
    real_tn = os.path.join(REPO, 'season', 'tn')
    real_t = os.path.join(REPO, 'season', 't')

    # ---- name guard: pure string logic ----------------------------------
    for bad in ['oak_IMGL7652 2.jpg', 'x 3.jpg', 'a b c 10.JPG']:
        try:
            guard_tn_names(['ok.jpg', bad])
            check('name guard rejects %r' % bad, False, 'not rejected')
        except ThumbTierError:
            check('name guard rejects %r' % bad, True)
    try:
        guard_tn_names(['oak_IMGL7652.jpg', 'rv_2.jpg', 'x2.jpg',
                        'sgx_65A94B68-EC1B.jpg'])
        check('name guard ALLOWS legitimate ids', True)
    except ThumbTierError as e:
        check('name guard ALLOWS legitimate ids', False, str(e)[:70])

    if not os.path.isdir(real_tn):
        check('season/tn exists', False, 'run --apply first; tree checks skipped')
        return summarise()

    tn_names = sorted(f for f in os.listdir(real_tn) if f.endswith('.jpg'))
    sample = tn_names[0]
    good = os.path.join(real_tn, sample)
    check('season/tn exists', True, '%s files' % format(len(tn_names), ','))

    sandbox = tempfile.mkdtemp(prefix='tntier_test_',
                               dir=os.environ.get('TMPDIR') or '/tmp')
    saved_tn = bt.TNDIR
    try:
        # ---- clean sandbox passes ---------------------------------------
        clean = os.path.join(sandbox, 'clean')
        os.makedirs(clean)
        for f in tn_names[:20]:
            shutil.copy2(os.path.join(real_tn, f), clean)
        with_tndir(clean)
        try:
            names, total = guard_tn_tree(quiet=True)
            check('tree guard passes a clean tn sample', len(names) == 20,
                  '%d files, %s B' % (len(names), format(total, ',')))
        except ThumbTierError as e:
            check('tree guard passes a clean tn sample', False, str(e)[:90])

        # ---- MUTATION 1: an oversized impostor (the 163MB shape) --------
        over = os.path.join(sandbox, 'oversize')
        os.makedirs(over)
        shutil.copy2(good, over)
        # a real season/t tile: 600px long edge, ~35KB -- legal in t, illegal in tn
        big_src = os.path.join(real_t, os.listdir(real_t)[0])
        shutil.copy2(big_src, os.path.join(over, 'planted_oversize.jpg'))
        with_tndir(over)
        try:
            guard_tn_tree(quiet=True)
            check('tree guard REJECTS a full tile planted in tn', False, 'accepted')
        except ThumbTierError as e:
            check('tree guard REJECTS a full tile planted in tn',
                  'long edge' in str(e), str(e)[:80])

        # ---- MUTATION 2: right dimensions, wrong weight (q93 shape) -----
        # An encode can hit 200px and still be 3x too heavy. The dimension
        # check alone would pass it, which is why MAX_TN_BYTES exists.
        fat = os.path.join(sandbox, 'fat')
        os.makedirs(fat)
        shutil.copy2(good, fat)
        fatp = os.path.join(fat, 'planted_fat.jpg')
        subprocess.run(['magick', big_src, '-resize', '%dx%d' % (THUMB2_W, THUMB2_W),
                        '-quality', '100', '-define', 'jpeg:extent=0',
                        fatp], check=True, capture_output=True)
        # pad to clear the threshold regardless of how well q100 compresses
        with open(fatp, 'ab') as fh:
            fh.write(b'\x00' * max(0, MAX_TN_BYTES + 1 - os.path.getsize(fatp)))
        with_tndir(fat)
        try:
            guard_tn_tree(quiet=True)
            check('tree guard REJECTS a 200px file that is too heavy', False,
                  'accepted %s B' % format(os.path.getsize(fatp), ','))
        except ThumbTierError as e:
            check('tree guard REJECTS a 200px file that is too heavy',
                  'MAX_TN_BYTES' in str(e),
                  '%s B planted' % format(os.path.getsize(fatp), ','))

        # ---- MUTATION 3: collision-suffixed name (the 1.1GB shape) ------
        coll = os.path.join(sandbox, 'collide')
        os.makedirs(coll)
        shutil.copy2(good, coll)
        shutil.copy2(good, os.path.join(coll, 'planted 2.jpg'))
        with_tndir(coll)
        try:
            guard_tn_tree(quiet=True)
            check('tree guard REJECTS a collision-suffixed name', False, 'accepted')
        except ThumbTierError as e:
            check('tree guard REJECTS a collision-suffixed name',
                  'collision' in str(e), str(e)[:60])

        # ---- MUTATION 4: a truncated / undecodable file -----------------
        trunc = os.path.join(sandbox, 'trunc')
        os.makedirs(trunc)
        shutil.copy2(good, trunc)
        with open(os.path.join(trunc, 'planted_trunc.jpg'), 'wb') as fh:
            fh.write(b'\xff\xd8\xff\xe0 not really a jpeg')
        with_tndir(trunc)
        try:
            guard_tn_tree(quiet=True)
            check('tree guard REJECTS an undecodable file', False, 'accepted')
        except ThumbTierError as e:
            check('tree guard REJECTS an undecodable file',
                  'could not be measured' in str(e), str(e)[:70])

        # ---- MUTATION 5: an empty tn ------------------------------------
        empty = os.path.join(sandbox, 'empty')
        os.makedirs(empty)
        with_tndir(empty)
        try:
            guard_tn_tree(quiet=True)
            check('tree guard REJECTS an empty tn', False, 'accepted')
        except ThumbTierError:
            check('tree guard REJECTS an empty tn', True)
    finally:
        with_tndir(saved_tn)
        shutil.rmtree(sandbox, ignore_errors=True)

    # ---- the live tree and data.js ---------------------------------------
    r = subprocess.run([sys.executable, os.path.join(HERE, 'build_thumbtier.py'),
                        '--verify'], capture_output=True, text=True)
    check('build_thumbtier.py --verify exits 0 on the real tree',
          r.returncode == 0, 'exit %d' % r.returncode)

    d = load_data_js()
    records = list(all_records(d))
    season = os.path.join(REPO, 'season')

    dangling = [p['id'] for p in records if p.get('thumb')
                and not os.path.exists(os.path.join(season, p['thumb']))]
    check('ZERO dangling thumb keys (the 3,822-record bug)', not dangling,
          '%d dangling' % len(dangling))

    off = {p['thumb'].split('/')[0] for p in records if p.get('thumb')}
    check('every thumb value lives under tn/', off == {'tn'}, str(sorted(off)))

    nokey = [p['id'] for p in records if not p.get('thumb')]
    check('every record has a thumb key', not nokey, '%d without' % len(nokey))

    # The tier is pointless unless the HTML asks for it, and dangerous unless
    # the lightbox still hands over the full tile.
    html = open(os.path.join(season, 'index.html'), encoding='utf-8').read()
    check('index.html grid reads p.thumb', 'p.thumb || p.t' in html)
    check('index.html falls back to the tile on error',
          'this.onerror=null;this.src=this.dataset.full' in html)
    check('lightbox still opens the full tile', "lightimg').src = p.t" in html)
    check('jersey search renders the derivative too (BY_PHOTO lookup)',
          'BY_PHOTO[id] || { id: id' in html)

    # ---- weight, the number this card is about ---------------------------
    PAGE_SIZE = 300
    worst_tile = worst_tn = 0.0
    worst_name = ''
    for g in d['games']:
        ps = g['photos'][:PAGE_SIZE]
        if not ps:
            continue
        tb = sum(os.path.getsize(os.path.join(season, p['t'])) for p in ps)
        nb = sum(os.path.getsize(os.path.join(season, p['thumb'])) for p in ps)
        if nb > worst_tn:
            worst_tn, worst_tile, worst_name = nb, tb, g['id']
    check('worst first page under 5MB of thumbnails', worst_tn / 1e6 < 5.0,
          '%s: %.2f MB (was %.2f MB of tiles, %.1fx)'
          % (worst_name, worst_tn / 1e6, worst_tile / 1e6, worst_tile / worst_tn))

    # ---- the scanner must cover tn, not just t ---------------------------
    chk = os.path.join(HERE, 'check_thumbs.py')
    r = subprocess.run([sys.executable, chk, '--dir', real_tn,
                        '--limit', str(THUMB2_W), '--no-baseline', '--quiet'],
                       capture_output=True, text=True)
    check('check_thumbs.py scans tn clean at --limit %d' % THUMB2_W,
          r.returncode == 0, 'exit %d %s' % (r.returncode, r.stdout.strip()[:80]))

    return summarise()


def summarise():
    bad = [r for r in results if not r[1]]
    print()
    if bad:
        print('FAIL: %d of %d check(s) failed: %s'
              % (len(bad), len(results), [b[0] for b in bad]))
        return 1
    print('PASS — %d/%d checks passed. The season/tn write boundary is closed '
          'and no thumb key dangles.' % (len(results), len(results)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
