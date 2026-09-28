#!/usr/bin/env python3
"""build_thumbtier.py — build season/tn, the grid's thumbnail tier, atomically.

WHY THIS EXISTS
---------------
season/index.html rendered every grid cell from `p.t`, the 400-600px tile
(median 35,741 B). PAGE_SIZE is 300, so opening the Oakland tab downloaded
14.70 MB before a parent on stadium LTE saw anything. The `thumb` key already
existed on 10,132 of 10,306 records and was read by NOTHING -- and 3,822 of
those values pointed at directories that do not exist in this repo
(`season/thumbs/`, `season/oakland_0904/thumbs/`, `season/sept0507/thumbs/`).
A dead key is harmless; the moment anyone wires it up naively, 37% of the grid
ships broken. So the key gets REBUILT here against files that provably exist,
and any record we cannot produce a derivative for has the key REMOVED rather
than left dangling.

THE ENCODE IS MEASURED, NOT GUESSED
-----------------------------------
Calibrated on a 60-tile random sample of season/t (mean 35,953 B):

    edge  q    mean B    300 tiles
    160   68    5,316      1.59 MB
    200   62    6,893      2.07 MB
    200   72    8,034      2.41 MB   <-- chosen
    240   60    8,954      2.69 MB
    240   68   10,068      3.02 MB

200px q72 was then checked BY EYE against 240px at 3x nearest-neighbour on
three frames (jersey legibility is the thing a byte count cannot answer): 240px
is visibly cleaner under magnification, but the grid tile renders at ~190px CSS
width, below which that difference is not delivered to the viewer. 200px is the
smallest edge that still covers the widest tile without upscaling. q72 over q62
costs 0.34 MB a page and keeps the digits off the blocking floor.

ATOMIC, NEVER A PER-FILE LOOP
-----------------------------
One `magick mogrify` process over an internal glob does the whole set (~110s
for 10,492 files). A crashed per-file loop is indistinguishable from a
half-finished one; a crashed single process leaves a count mismatch this script
then refuses. Every write into TNDIR funnels through build() so the guards
cannot be bypassed from a caller site -- the same reason publish_day's guards
live inside make_thumb().

    python3 tools/build_thumbtier.py --dry-run
    python3 tools/build_thumbtier.py --apply
    python3 tools/build_thumbtier.py --verify     # guards only, no writes

Exit codes: 0 clean, 1 an invariant failed.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
SEASON = os.path.join(REPO, 'season')
DATA_JS = os.path.join(SEASON, 'data.js')
HTML = os.path.join(SEASON, 'index.html')
TDIR = os.path.join(SEASON, 't')
TNDIR = os.path.join(SEASON, 'tn')

# Long edge and JPEG quality for the thumb tier. See the module docstring for
# the measurement these came from. THUMB2_W is what tools/check_thumbs.py is
# handed as --limit when it scans tn, so the encoder and the scanner can never
# disagree about what "too big" means.
THUMB2_W = 200
THUMB2_QUALITY = 72

# A tn file bigger than this is not a thumbnail whatever its dimensions say.
# The calibration mean is 8,034 B and the fattest frame in the sample was
# ~11 KB; 40 KB is well clear of a legitimate thumb and well under a tile.
MAX_TN_BYTES = 40_000

# macOS's collision suffix: "<stem> 2.jpg". Reused from publish_day so there is
# one definition of the 99-stray / 1.1GB incident shape in the repo.
sys.path.insert(0, HERE)
from publish_day import COLLISION_SUFFIX_RE  # noqa: E402


class ThumbTierError(RuntimeError):
    """A tn invariant broke. Raised, never caught in-tree, never warned about."""


# ---------------------------------------------------------------------------
# data.js
# ---------------------------------------------------------------------------
def load_data_js():
    raw = open(DATA_JS, encoding='utf-8').read()
    m = re.match(r'\s*window\.SEASON_DATA\s*=\s*', raw)
    if not m:
        raise ThumbTierError('unexpected data.js header')
    return json.loads(raw[m.end():].strip().rstrip(';'))


def save_data_js(d):
    tmp = DATA_JS + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        f.write('window.SEASON_DATA = ')
        json.dump(d, f, ensure_ascii=False, separators=(', ', ': '))
        f.write(';\n')
    os.replace(tmp, DATA_JS)
    from stamp_data_version import stamp
    return stamp(quiet=True)


def backup_data_js(suffix='tn-bak'):
    """Rollback copy OUTSIDE the served directory (see publish_day docstring:
    a pre-edit snapshot in season/ was once publicly fetchable)."""
    d = os.path.join(REPO, '.backups')
    os.makedirs(d, exist_ok=True)
    dst = os.path.join(d, 'data.js.%s-%d' % (suffix, int(time.time())))
    shutil.copy2(DATA_JS, dst)
    return dst


def all_records(d):
    """Every photo record in the catalog, games plus catchall.

    The catchall tab renders through the same photoCards() as the games, so it
    needs the tier too -- it was the one place a "games only" pass would have
    left 172 heavy tiles behind.
    """
    for g in d['games']:
        for p in g['photos']:
            yield p
    if d.get('catchall'):
        for p in d['catchall']['photos']:
            yield p


# ---------------------------------------------------------------------------
# write boundary
# ---------------------------------------------------------------------------
def guard_tn_names(names):
    """No collision-suffixed names, ever. Pure string check, no decode."""
    bad = [f for f in names if COLLISION_SUFFIX_RE.match(f)]
    if bad:
        raise ThumbTierError(
            'REFUSING season/tn: %d collision-suffixed name(s) — "<stem> N.jpg" '
            'can only be an unintended duplicate write beside the real '
            'thumbnail. This is the shape of the 99-stray / 1.1GB incident. '
            'First few: %s' % (len(bad), bad[:5]))


def guard_tn_tree(quiet=False):
    """Post-write verification of the WHOLE directory.

    mogrify exiting 0 is not evidence it resized anything, and a truncated
    encode is a readable file with the right name. So this measures what landed:
    every name legal, every long edge inside THUMB2_W, every file under
    MAX_TN_BYTES, and every file decodable. It RAISES -- a warn-and-continue is
    exactly how 1.1GB shipped once already.
    """
    if not os.path.isdir(TNDIR):
        raise ThumbTierError('%s is not a directory' % TNDIR)
    names = sorted(f for f in os.listdir(TNDIR)
                   if f.lower().endswith(('.jpg', '.jpeg')))
    if not names:
        raise ThumbTierError('%s is empty' % TNDIR)
    guard_tn_names(names)

    # Batched dimension read: reuse check_thumbs.dims_batch so tn and t are
    # measured by the same code (one sips call per 500 files, not per file).
    import check_thumbs
    paths = [os.path.join(TNDIR, f) for f in names]
    got = check_thumbs.dims_batch(paths)

    oversize, fat, unreadable = [], [], []
    total = 0
    for p in paths:
        size = os.path.getsize(p)
        total += size
        wh = got.get(os.path.realpath(p))
        if not wh:
            unreadable.append(os.path.basename(p))
            continue
        if max(wh) > THUMB2_W:
            oversize.append((os.path.basename(p), wh, size))
        if size > MAX_TN_BYTES:
            fat.append((os.path.basename(p), wh, size))

    problems = []
    if oversize:
        problems.append('%d file(s) exceed the %dpx long edge, e.g. %s'
                        % (len(oversize), THUMB2_W,
                           ['%s %dx%d' % (n, w, h) for n, (w, h), _ in oversize[:5]]))
    if fat:
        problems.append('%d file(s) exceed MAX_TN_BYTES=%s, e.g. %s'
                        % (len(fat), format(MAX_TN_BYTES, ','),
                           ['%s %s B' % (n, format(s, ',')) for n, _, s in fat[:5]]))
    if unreadable:
        problems.append('%d file(s) could not be measured, e.g. %s'
                        % (len(unreadable), unreadable[:5]))
    if problems:
        raise ThumbTierError('season/tn failed verification: ' + '; '.join(problems))

    if not quiet:
        sizes = sorted(os.path.getsize(p) for p in paths)
        print('  tn tree: %s files, %.1f MB, median %s B, max %s B, long edge <= %dpx'
              % (format(len(names), ','), total / 1e6,
                 format(sizes[len(sizes) // 2], ','), format(sizes[-1], ','),
                 THUMB2_W))
    return names, total


def make_one(tile_path, quiet=True):
    """Derive the tn thumbnail for ONE season/t tile, guards included.

    WHY THIS IS CALLED FROM publish_day.make_thumb()
    ------------------------------------------------
    The tier is only as good as its weakest writer. publish_day and extend_day
    both append records carrying `'thumb': 't/<id>.jpg'` -- correct before this
    tier existed, and now a silent regression: a night published without a
    matching tn pass puts full tiles back in the grid for those frames, and
    nothing on a green board would say so. Measured once already: a concurrent
    publish added 148 records mid-session and they were only wired to tn/
    because someone re-ran the batch builder by hand.

    Remembering a second command is not an invariant. So the derivative is
    produced by the SAME function that produces the tile, which is the one
    funnel every TDIR write already passes through. Callers keep writing
    't/<id>.jpg' into the record; build_thumbtier --apply rewrites the key and
    is idempotent, so both paths converge.

    Returns the tn path, or raises ThumbTierError. Never silently skips: a
    missing derivative is the defect this whole card exists to remove.
    """
    base = os.path.basename(tile_path)
    guard_tn_names([base])
    os.makedirs(TNDIR, exist_ok=True)
    dst = os.path.join(TNDIR, base)
    r = subprocess.run(
        ['magick', tile_path,
         '-resize', '%dx%d' % (THUMB2_W, THUMB2_W),
         '-quality', str(THUMB2_QUALITY),
         '-strip', '-interlace', 'Plane',
         '-sampling-factor', '4:2:0', '-colorspace', 'sRGB', dst],
        capture_output=True, text=True)
    if r.returncode != 0:
        raise ThumbTierError('could not derive %s: %s'
                             % (base, (r.stderr or '')[-300:]))
    if not os.path.exists(dst):
        raise ThumbTierError('magick wrote no file at %s' % dst)
    # Same post-write measurement the batch path applies, on one file: magick
    # exiting 0 is not evidence it resized anything.
    size = os.path.getsize(dst)
    if size > MAX_TN_BYTES:
        os.unlink(dst)
        raise ThumbTierError(
            'REFUSING %s in season/tn: %s bytes exceeds MAX_TN_BYTES=%s, so the '
            'resize did not take effect. Offender deleted.'
            % (base, format(size, ','), format(MAX_TN_BYTES, ',')))
    import check_thumbs
    wh = check_thumbs.dims_batch([dst]).get(os.path.realpath(dst))
    if not wh:
        os.unlink(dst)
        raise ThumbTierError('could not measure %s after writing it' % base)
    if max(wh) > THUMB2_W:
        os.unlink(dst)
        raise ThumbTierError(
            'REFUSING %s in season/tn: long edge %dpx exceeds THUMB2_W=%d '
            '(%dx%d). Offender deleted.' % (base, max(wh), THUMB2_W, wh[0], wh[1]))
    if not quiet:
        print('   tn: %s %dx%d %s B' % (base, wh[0], wh[1], format(size, ',')))
    return dst


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------
def build(apply=False):
    d = load_data_js()
    records = list(all_records(d))

    tiles = sorted(f for f in os.listdir(TDIR)
                   if f.lower().endswith(('.jpg', '.jpeg')))
    referenced = {os.path.basename(p['t']) for p in records}
    orphans = sorted(set(tiles) - referenced)

    # Encode ONLY tiles some record actually points at. The 186 orphans in
    # season/t are dead weight already (a separate cull); giving them a
    # derivative would double the dead weight and put ~1.4 MB in git forever.
    want = sorted(referenced & set(tiles))
    missing_tiles = sorted(referenced - set(tiles))

    print('records:        %s (%d games + %d catchall)'
          % (format(len(records), ','), len(d['games']),
             len(d['catchall']['photos']) if d.get('catchall') else 0))
    print('tiles on disk:  %s' % format(len(tiles), ','))
    print('  referenced:   %s   orphaned: %s   referenced-but-absent: %s'
          % (format(len(want), ','), format(len(orphans), ','),
             format(len(missing_tiles), ',')))
    if missing_tiles:
        print('  NOTE: %d record(s) reference a tile that is not on disk; those '
              'keep no thumb key. First few: %s'
              % (len(missing_tiles), missing_tiles[:5]))

    if not apply:
        est = len(want) * 8034
        print('\ndry run: would encode %s tiles -> %s at %dpx q%d '
              '(~%.0f MB, ~%.2f MB per 300-tile page)'
              % (format(len(want), ','), os.path.relpath(TNDIR, REPO),
                 THUMB2_W, THUMB2_QUALITY, est / 1e6, 8034 * 300 / 1e6))
        return 0

    os.makedirs(TNDIR, exist_ok=True)
    # Names are checked BEFORE any bytes land: mogrify -path keeps the source
    # basename, so an illegal name in season/t would become an illegal name in
    # season/tn.
    guard_tn_names(want)

    # ONE process over an internal glob. Quoted so the shell never expands it
    # and the argv stays short; mogrify walks the directory itself.
    #   -strip            no EXIF on a public gallery (no GPS, no camera serial)
    #   -interlace Plane  progressive, so a tile paints top-down on slow LTE
    #   -sampling-factor  4:2:0 chroma subsampling, the web default
    #   -colorspace sRGB  a tile tagged something else would shift on Safari
    print('\nencoding %s tiles at %dpx q%d (one mogrify process)...'
          % (format(len(want), ','), THUMB2_W, THUMB2_QUALITY))
    t0 = time.time()
    r = subprocess.run(
        ['magick', 'mogrify', '-path', TNDIR,
         '-resize', '%dx%d' % (THUMB2_W, THUMB2_W),
         '-quality', str(THUMB2_QUALITY),
         '-strip', '-interlace', 'Plane',
         '-sampling-factor', '4:2:0', '-colorspace', 'sRGB',
         os.path.join(TDIR, '*.jpg')],
        capture_output=True, text=True)
    dt = time.time() - t0
    if r.returncode != 0:
        raise ThumbTierError('mogrify exited %d after %.0fs: %s'
                             % (r.returncode, dt, (r.stderr or '')[-800:]))
    if r.stderr.strip():
        print('  mogrify stderr (non-fatal):\n    %s'
              % r.stderr.strip()[:500].replace('\n', '\n    '))
    print('  done in %.0fs' % dt)

    # mogrify globs the whole of season/t, so the orphans get encoded too.
    # Remove their derivatives rather than commit 186 files nothing links to.
    got = {f for f in os.listdir(TNDIR) if f.lower().endswith('.jpg')}
    stray = sorted(got - set(want))
    for f in stray:
        os.unlink(os.path.join(TNDIR, f))
    if stray:
        print('  removed %d derivative(s) of orphaned tiles' % len(stray))

    # THE COUNT ASSERTION the card asks for, before anything touches data.js.
    names, total = guard_tn_tree()
    if set(names) != set(want):
        short = sorted(set(want) - set(names))
        extra = sorted(set(names) - set(want))
        raise ThumbTierError(
            'count mismatch: %d thumbs for %d referenced tiles (%d missing, '
            '%d unexpected). First missing: %s'
            % (len(names), len(want), len(short), len(extra), short[:5]))
    print('  count assertion OK: %s thumbs == %s referenced tiles'
          % (format(len(names), ','), format(len(want), ',')))

    # --- rewrite the thumb key -------------------------------------------
    have = set(names)
    wired = dropped = 0
    for p in records:
        base = os.path.basename(p['t'])
        if base in have:
            p['thumb'] = 'tn/' + base
            wired += 1
        else:
            # NEVER leave a path that is not a file. That dangling key on 3,822
            # records is the bug this card exists to close.
            p.pop('thumb', None)
            dropped += 1

    bak = backup_data_js()
    ver = save_data_js(d)
    print('\ndata.js: %s records wired to tn/, %d thumb key(s) removed'
          % (format(wired, ','), dropped))
    print('  backup %s   script tag now data.js?v=%s'
          % (os.path.relpath(bak, REPO), ver))

    return verify(quiet=False)


# ---------------------------------------------------------------------------
# verify (runs standalone too, so it can gate a commit)
# ---------------------------------------------------------------------------
def verify(quiet=False):
    """Every tn invariant, against the tree and data.js as they stand."""
    fails = []

    def chk(label, ok, detail=''):
        if not quiet:
            print('  %-56s %s%s' % (label, 'OK' if ok else 'FAIL',
                                    '  ' + detail if detail else ''))
        if not ok:
            fails.append(label)

    if not quiet:
        print('\nverifying season/tn + data.js')
    try:
        names, total = guard_tn_tree(quiet=quiet)
        chk('tn tree passes the write guard', True,
            '%s files, %.1f MB' % (format(len(names), ','), total / 1e6))
    except ThumbTierError as e:
        chk('tn tree passes the write guard', False, str(e)[:160])
        names = set()

    d = load_data_js()
    records = list(all_records(d))
    have = set(names)

    dangling = [p['id'] for p in records
                if p.get('thumb')
                and not os.path.exists(os.path.join(SEASON, p['thumb']))]
    chk('zero dangling thumb keys in data.js', not dangling,
        '%d dangling: %s' % (len(dangling), dangling[:5]) if dangling else '0')

    off_tier = sorted({p['thumb'] for p in records
                       if p.get('thumb') and not p['thumb'].startswith('tn/')})
    chk('every thumb value points into tn/', not off_tier, str(off_tier[:5]))

    nokey = [p['id'] for p in records if not p.get('thumb')]
    expected_nokey = [p['id'] for p in records
                      if os.path.basename(p['t']) not in have]
    chk('records without a thumb key are exactly those with no derivative',
        sorted(nokey) == sorted(expected_nokey),
        '%d without key, %d without derivative' % (len(nokey), len(expected_nokey)))

    # The tier is pointless if the HTML still asks for the tile.
    html = open(HTML, encoding='utf-8').read()
    chk('index.html reads p.thumb', 'p.thumb' in html)
    chk('index.html has an onerror fallback to the tile', 'onerror' in html)
    chk('lightbox still opens the full tile', "lightimg').src = p.t" in html
        or "lightimg').src=p.t" in html)

    # Weight: the number the card is actually about.
    PAGE_SIZE = 300
    if not quiet:
        print('\n  first-page weight at PAGE_SIZE=%d (local bytes):' % PAGE_SIZE)
        print('    %-24s %12s %12s %8s' % ('game', 'tiles MB', 'thumbs MB', 'ratio'))
    worst = 0.0
    for g in sorted(d['games'], key=lambda g: -len(g['photos'])):
        ps = g['photos'][:PAGE_SIZE]
        if not ps:
            continue
        tb = sum(os.path.getsize(os.path.join(SEASON, p['t']))
                 for p in ps if os.path.exists(os.path.join(SEASON, p['t'])))
        nb = sum(os.path.getsize(os.path.join(SEASON, p['thumb']))
                 for p in ps if p.get('thumb')
                 and os.path.exists(os.path.join(SEASON, p['thumb'])))
        worst = max(worst, nb / 1e6)
        if not quiet:
            print('    %-24s %12.2f %12.2f %7.1fx'
                  % (g['id'], tb / 1e6, nb / 1e6, (tb / nb) if nb else 0))
    chk('worst first page under 5 MB of thumbnails', worst < 5.0,
        '%.2f MB' % worst)

    if fails:
        print('\nFAIL: %d invariant(s) broken: %s' % (len(fails), fails))
        return 1
    if not quiet:
        print('\nOK: the thumb tier is consistent with data.js and the HTML.')
    return 0


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--apply', action='store_true', help='encode and rewrite data.js')
    ap.add_argument('--dry-run', action='store_true', help='report, write nothing')
    ap.add_argument('--verify', action='store_true',
                    help='check the invariants only (no writes) — safe pre-commit gate')
    a = ap.parse_args()
    if a.verify:
        return verify()
    if not (a.apply or a.dry_run):
        ap.error('pick --dry-run, --apply or --verify')
    try:
        return build(apply=a.apply)
    except ThumbTierError as e:
        print('\nFAIL: %s' % e)
        return 1


if __name__ == '__main__':
    sys.exit(main())
