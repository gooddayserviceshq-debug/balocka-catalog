#!/usr/bin/env python3
"""publish_day.py — create a hub game entry for a shoot date that has none.

8/31, 9/14 and 9/17 were sorted and tagged and never reached the hub, so the QR
cards for those nights land on "That gallery link is not live yet."

What this does, per date:
  1. selects the frames that are LOCAL originals (iCloud-only frames cannot be
     published — a DB column claiming local is not the file being local, so this
     globs the filesystem)
  2. writes 600px web thumbnails into season/t/ with EXPLICIT rotation, because
     sips -Z copies EXIF Orientation through instead of baking it in and every
     Orientation=6 frame would render sideways in the grid
  3. appends a game entry to window.SEASON_DATA.games with ids prefixed per game
     so a recycled Canon filename can never collide with another night's photo
  4. appends the same rows to season-t-manifest.csv (the delivery verifier reads it)

It does NOT grade or rank: the hub renders an ungraded game as "fresh off the
card, not graded yet" and disables the tier filter, which is honest. Grading is a
separate pass. It also never writes a name — only jersey numbers travel.

    python3 publish_day.py 2026-09-17 --label "vs The Rock" --sub "Varsity" --dry-run
    python3 publish_day.py 2026-09-17 --label "vs The Rock" --sub "Varsity" --apply
"""
import argparse
import csv
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time

HOME = os.path.expanduser('~')
DB = os.path.join(HOME, 'Pictures/Photos Library.photoslibrary/database/Photos.sqlite')
ORIG = os.path.join(HOME, 'Pictures/Photos Library.photoslibrary/originals')
REPO = os.path.join(HOME, 'Desktop/_balocka/balocka-catalog-repo')
DATA_JS = os.path.join(REPO, 'season', 'data.js')
TDIR = os.path.join(REPO, 'season', 't')
MANIFEST = os.path.join(REPO, 'season-t-manifest.csv')
APPLE_EPOCH = 978307200
THUMB_W = 600
# Match the thumbnails already in the catalog: every existing one samples at
# JPEGQualityEstimate 60 (~30KB at 600px). Do not raise this without checking
# season/t's total weight against the 1GB GitHub Pages budget.
JPEG_QUALITY = 60


def load_data_js():
    raw = open(DATA_JS, encoding='utf-8').read()
    m = re.match(r'\s*window\.SEASON_DATA\s*=\s*', raw)
    if not m:
        sys.exit('FAIL: unexpected data.js header')
    return json.loads(raw[m.end():].strip().rstrip(';'))


def backup_data_js(suffix='bak'):
    """Copy data.js to a rollback file OUTSIDE the served directory.

    WHY NOT NEXT TO THE FILE
    ------------------------
    These tools used to write `season/data.js.bak-<epoch>`, i.e. inside season/,
    which GitHub Pages SERVES. One such backup was committed in fdac16d and was
    publicly fetchable at .../season/data.js.bak-1790519527 (HTTP 200, 630,248
    bytes). It happened to be clean, but a backup is a PRE-EDIT SNAPSHOT: one
    taken before a privacy fix preserves exactly what the fix removed, and
    verify_no_names.py only scans tracked files, so an untracked backup in a
    served directory was checked by nothing.

    Backups now go to .backups/ at the repo root -- not served, not tracked --
    and the path is returned so callers can print it.
    """
    d = os.path.join(REPO, '.backups')
    os.makedirs(d, exist_ok=True)
    dst = os.path.join(d, 'data.js.%s-%d' % (suffix, int(time.time())))
    shutil.copy2(DATA_JS, dst)
    return dst


def save_data_js(d):
    tmp = DATA_JS + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        f.write('window.SEASON_DATA = ')
        json.dump(d, f, ensure_ascii=False, separators=(', ', ': '))
        f.write(';\n')
    os.replace(tmp, DATA_JS)
    # Pages serves data.js with max-age=600, so an unversioned <script src>
    # keeps returning visitors on the OLD catalog for ten minutes after a
    # publish. Re-stamp the tag with the new content hash.
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from stamp_data_version import stamp
    stamp(quiet=True)


def local_assets(date):
    """R7 frames for a date whose ORIGINAL is really on this disk."""
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
         order by a.ZDATECREATED""" % (APPLE_EPOCH, APPLE_EPOCH), (date,))
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


def orientations(paths):
    """Read EXIF Orientation for many files in ONE exiftool call.

    Per-file exiftool costs two process spawns per frame, which is what made a
    421-frame day exceed a minute. exiftool's -@ argfile reads the whole batch in
    one pass.
    """
    if not paths:
        return {}
    import tempfile
    with tempfile.NamedTemporaryFile('w', suffix='.args', delete=False) as f:
        f.write('\n'.join(paths))
        argfile = f.name
    try:
        r = subprocess.run(['exiftool', '-s3', '-n', '-Orientation', '-T',
                            '-filepath', '-@', argfile],
                           capture_output=True, text=True)
        out = {}
        for line in r.stdout.splitlines():
            parts = line.rsplit('\t', 1)
            if len(parts) != 2:
                continue
            val, path = parts
            try:
                out[path] = int(val)
            except ValueError:
                out[path] = 1
        return out
    finally:
        os.unlink(argfile)


# EXIF Orientation -> the sips operations that bake it into the pixels.
ROT = {1: [], 2: [['--flip', 'horizontal']], 3: [['--rotate', '180']],
       4: [['--flip', 'vertical']],
       5: [['--rotate', '90'], ['--flip', 'horizontal']],
       6: [['--rotate', '90']],
       7: [['--rotate', '270'], ['--flip', 'horizontal']],
       8: [['--rotate', '270']]}


def make_thumb(src, dst, o):
    """600px web thumb, orientation baked into the pixels, encoded at catalog quality.

    Two traps live in this function, both learned the hard way:

    1. `sips -Z` COPIES the EXIF Orientation tag instead of applying it. 369 of
       the 8/31 frames are Orientation=6 and rendered sideways in the grid until
       the rotation was baked into the pixel data here.
    2. sips re-encodes at roughly q93. Every thumbnail already in this catalog is
       q60/~30KB, so publishing at the default put 3.1x the necessary bytes on
       disk -- 163MB of avoidable weight in a single 3,109-frame run, against a
       1GB Pages budget, permanently in git history. Pillow re-encodes at the
       catalog's quality and jersey numbers stay legible.
    """
    shutil.copy2(src, dst)
    subprocess.run(['sips', '-Z', str(THUMB_W), dst], capture_output=True, check=True)
    for op in ROT.get(o, []):
        subprocess.run(['sips'] + op + [dst], capture_output=True, check=True)
    try:
        from PIL import Image
        with Image.open(dst) as im:
            rgb = im.convert('RGB')
        rgb.save(dst, 'JPEG', quality=JPEG_QUALITY, optimize=True)
    except Exception as e:
        # Never silently ship an oversized thumb: say so loudly.
        print('   WARN: could not re-encode %s at q%d (%s) — it will be ~3x '
              'heavier than the rest of the catalog' % (os.path.basename(dst),
                                                        JPEG_QUALITY, e))
        if o != 1:
            subprocess.run(['exiftool', '-overwrite_original', '-Orientation=1',
                            '-n', dst], capture_output=True)
    return o


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('date')
    ap.add_argument('--game-id', required=True)
    ap.add_argument('--label', required=True)
    ap.add_argument('--sub', default='')
    ap.add_argument('--prefix', required=True, help='id prefix, e.g. rock_')
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()
    if not (a.apply or a.dry_run):
        ap.error('pick --dry-run or --apply')
    if not re.fullmatch(r'[a-z0-9]+_', a.prefix):
        ap.error('prefix must be lowercase and end with _ , e.g. rock_')

    d = load_data_js()
    if any(g['id'] == a.game_id for g in d['games']):
        sys.exit('FAIL: game id %s already exists in data.js' % a.game_id)
    # The hub derives sort order from the LAST FOUR DIGITS of the game id (MMDD),
    # so an id whose MMDD does not match the shoot date sorts into the wrong place.
    mmdd = a.date[5:7] + a.date[8:10]
    if not a.game_id.endswith(mmdd):
        sys.exit('FAIL: game id %r must end in %s to sort correctly' % (a.game_id, mmdd))

    assets, missing = local_assets(a.date)
    if a.limit:
        assets = assets[:a.limit]
    print('%s: %d local frames selected, %d iCloud-only and NOT publishable'
          % (a.date, len(assets), missing))
    if not assets:
        sys.exit('FAIL: nothing local to publish for %s' % a.date)

    photos, rows = [], []
    rot_counts = {}
    if a.apply:
        omap = orientations([p for _, _, _, p in assets])
        for i, (u, fn, ts, p) in enumerate(assets):
            pid = a.prefix + os.path.splitext(fn)[0]
            dst = os.path.join(TDIR, pid + '.jpg')
            o = make_thumb(p, dst, omap.get(p, 1))
            rot_counts[o] = rot_counts.get(o, 0) + 1
            photos.append({'id': pid, 'score': 0, 'tier': '',
                           'thumb': 't/%s.jpg' % pid, 't': 't/%s.jpg' % pid})
            rows.append({'id': pid, 'file': pid + '.jpg', 'game_id': a.game_id,
                         'game_label': a.label, 'tier': '', 'score': '0',
                         'original': p})
            if (i + 1) % 250 == 0:
                print('   ...%d/%d thumbs' % (i + 1, len(assets)))
        print('   orientations baked:', rot_counts)
        entry = {'id': a.game_id, 'label': a.label, 'sub': a.sub, 'photos': photos}
        d['games'].append(entry)
        bak = backup_data_js()
        save_data_js(d)
        with open(MANIFEST, 'a', newline='', encoding='utf-8') as f:
            csv.DictWriter(f, fieldnames=['id', 'file', 'game_id', 'game_label',
                                          'tier', 'score', 'original']).writerows(rows)
        chk = load_data_js()
        g = [x for x in chk['games'] if x['id'] == a.game_id][0]
        miss = [p['id'] for p in g['photos']
                if not os.path.exists(os.path.join(TDIR, p['id'] + '.jpg'))]
        if miss:
            sys.exit('FAIL: %d published ids have no thumbnail on disk' % len(miss))
        print('published %s: %d photos, every id has a thumbnail (backup %s)'
              % (a.game_id, len(g['photos']), os.path.basename(bak)))
    else:
        print('dry run: would publish %d photos as %s (%s) with prefix %s'
              % (len(assets), a.game_id, a.label, a.prefix))
        print('   first:', assets[0][1], ' last:', assets[-1][1])


if __name__ == '__main__':
    main()
