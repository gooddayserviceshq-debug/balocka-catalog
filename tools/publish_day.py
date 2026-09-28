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


# ---------------------------------------------------------------------------
# A DATE IS NOT AN EVENT
# ---------------------------------------------------------------------------
# local_assets() used to select on camera + calendar date alone. That is only
# correct when the camera shot exactly one thing that day, and on this library it
# does not hold: 2026-09-11 carries 1,883 frames of the "9/11 Top Gun Run- City
# of Smyrna" 5K AND 346 frames of the Siegel football game, interleaved between
# 18:49 and 21:37 (verified from the pixels 2026-09-28 -- runners with race bibs
# on a lit path vs. helmets and shoulder pads under stadium lights). No time
# filter splits them: race frames run to 20:46 and football frames start at
# 20:44, a 2.5-minute overlap where the photographer was walking between venues.
#
# A date-only select on that night hands a football gallery 1,647 photographs of
# a community road race. Album membership is the splitter that works, so event
# scoping lives here, in the one function both tools select through.
#
# ALBUM_JOIN is discovered rather than hard-coded: the Z_<n>ASSETS join table is
# renumbered by Photos version upgrades, and a stale constant would silently
# select nothing (which reads as "this night is empty", not as "the query broke").
def album_join_table(con):
    """(join_table, album_col, asset_col) for this Photos schema version."""
    for (t,) in con.execute(
            "select name from sqlite_master where type='table' "
            "and name like 'Z\\_%ASSETS' escape '\\'"):
        cols = [c[1] for c in con.execute('pragma table_info("%s")' % t)]
        alb = [c for c in cols if c.endswith('ALBUMS')]
        ass = [c for c in cols if c.endswith('ASSETS') and not c.startswith('Z_FOK')]
        if alb and ass:
            return t, alb[0], ass[0]
    raise SystemExit('FAIL: cannot find the album join table in this Photos library')


def date_events(date):
    """Album title -> R7 frame count for one shoot date, plus the no-album count.

    This is what makes a mixed-event night visible BEFORE anything is published.
    """
    con = sqlite3.connect('file:%s?mode=ro' % DB, uri=True)
    jt, albcol, asscol = album_join_table(con)
    rows = con.execute("""
        select a.Z_PK from ZASSET a
          left join ZEXTENDEDATTRIBUTES e on e.Z_PK = a.ZEXTENDEDATTRIBUTES
         where a.ZTRASHEDSTATE = 0 and e.ZCAMERAMODEL = 'Canon EOS R7'
           and date(datetime(a.ZDATECREATED+?,'unixepoch','localtime')) = ?""",
        (APPLE_EPOCH, date)).fetchall()
    pks = [r[0] for r in rows]
    # Count DISTINCT frames per title. There are two albums both titled
    # 'FOOTBALL IG ' on this library (Z_PK 93 normal, 112 shared), and a frame in
    # both was counted twice -- 09-11's football set reported 690 instead of 346.
    # An inflated minority count only ever makes the gate more eager, but a count
    # this tool PRINTS as evidence has to be the true one.
    per_title, seen = {}, set()
    if pks:
        qs = ','.join('?' * len(pks))
        for pk, title in con.execute(
                'select j."%s", al.ZTITLE from "%s" j '
                'join ZGENERICALBUM al on al.Z_PK = j."%s" where j."%s" in (%s)'
                % (asscol, jt, albcol, asscol, qs), pks):
            per_title.setdefault((title or '').strip(), set()).add(pk)
            seen.add(pk)
    counts = {t: len(v) for t, v in per_title.items()}
    con.close()
    return counts, len(pks) - len(seen), len(pks)


def local_assets(date, album=None):
    """R7 frames for a date whose ORIGINAL is really on this disk.

    `album` scopes the select to one event on a multi-event night (substring
    match on the album title, case-insensitive, so the caller need not reproduce
    the trailing space in 'FOOTBALL IG '). Without it the select is date-wide,
    which is correct ONLY for a single-event day -- callers must gate on
    date_events() before trusting that.
    """
    con = sqlite3.connect('file:%s?mode=ro' % DB, uri=True)
    rows = con.execute("""
        select a.Z_PK, a.ZUUID, aaa.ZORIGINALFILENAME,
               datetime(a.ZDATECREATED+%d,'unixepoch','localtime')
          from ZASSET a
          left join ZEXTENDEDATTRIBUTES e on e.Z_PK = a.ZEXTENDEDATTRIBUTES
          left join ZADDITIONALASSETATTRIBUTES aaa on aaa.ZASSET = a.Z_PK
         where a.ZTRASHEDSTATE = 0
           and e.ZCAMERAMODEL = 'Canon EOS R7'
           and date(datetime(a.ZDATECREATED+%d,'unixepoch','localtime')) = ?
         order by a.ZDATECREATED""" % (APPLE_EPOCH, APPLE_EPOCH), (date,)).fetchall()

    keep = None
    if album is not None:
        jt, albcol, asscol = album_join_table(con)
        pks = [r[0] for r in rows]
        keep = set()
        if pks:
            qs = ','.join('?' * len(pks))
            for pk, title in con.execute(
                    'select j."%s", al.ZTITLE from "%s" j '
                    'join ZGENERICALBUM al on al.Z_PK = j."%s" where j."%s" in (%s)'
                    % (asscol, jt, albcol, asscol, qs), pks):
                if album.strip().lower() in (title or '').strip().lower():
                    keep.add(pk)
        if not keep:
            con.close()
            sys.exit('FAIL: album %r matches 0 R7 frames on %s. Refusing to fall '
                     'back to the whole date -- that is a different and much '
                     'larger selection, and on a mixed-event night it is the '
                     'wrong event.' % (album, date))
    con.close()

    out, missing = [], 0
    for pk, u, fn, ts in rows:
        if keep is not None and pk not in keep:
            continue
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


def album_overlap(date, album):
    """Frames in `album` that ALSO sit in another material album that night.

    A frame can legitimately live in two albums: on 2026-09-11 two photographs of
    police vehicles staged at the 9/11 memorial are filed under BOTH the road
    race and the football album (verified from the pixels -- they are parked
    vehicles, not runners and not football action). That is real, so scoping
    cannot assume the two events partition the night cleanly. It IS worth
    printing: a frame reaching a gallery through a second event's album is the
    one case where album scoping still lets something through.
    """
    con = sqlite3.connect('file:%s?mode=ro' % DB, uri=True)
    jt, albcol, asscol = album_join_table(con)
    rows = con.execute("""
        select a.Z_PK from ZASSET a
          left join ZEXTENDEDATTRIBUTES e on e.Z_PK = a.ZEXTENDEDATTRIBUTES
         where a.ZTRASHEDSTATE = 0 and e.ZCAMERAMODEL = 'Canon EOS R7'
           and date(datetime(a.ZDATECREATED+?,'unixepoch','localtime')) = ?""",
        (APPLE_EPOCH, date)).fetchall()
    pks = [r[0] for r in rows]
    if not pks:
        con.close()
        return set(), {}
    qs = ','.join('?' * len(pks))
    per_pk = {}
    for pk, title in con.execute(
            'select j."%s", al.ZTITLE from "%s" j '
            'join ZGENERICALBUM al on al.Z_PK = j."%s" where j."%s" in (%s)'
            % (asscol, jt, albcol, asscol, qs), pks):
        per_pk.setdefault(pk, set()).add((title or '').strip())
    con.close()
    counts, _na, total = date_events(date)
    material = {t for t, n in counts.items()
                if n >= MIXED_EVENT_MIN_FRAMES and n >= MIXED_EVENT_MIN_SHARE * total}
    want = album.strip().lower()
    over = set()
    for pk, titles in per_pk.items():
        if not any(want in t.lower() for t in titles):
            continue
        others = {t for t in titles if t in material and want not in t.lower()}
        if others:
            over.add(pk)
    return over, counts


# A night is "mixed" when a second album holds a material share of the frames.
# Below this the minority is stray tagging noise (a handful of frames someone
# also dropped in 'Instagram'), not a second event worth blocking a publish over.
MIXED_EVENT_MIN_SHARE = 0.05
MIXED_EVENT_MIN_FRAMES = 25


def assert_single_event(date, album, tool_hint=''):
    """Refuse a date-wide selection on a night that holds two events.

    Called by BOTH publish_day and extend_day before selecting. When --album is
    given the caller has already answered the question, so this only reports --
    including any frames that reach the scope through a second event's album.
    """
    counts, no_album, total = date_events(date)
    material = {t: n for t, n in counts.items()
                if n >= MIXED_EVENT_MIN_FRAMES and n >= MIXED_EVENT_MIN_SHARE * total}
    if album:
        print('   event scope: album ~%r on %s (albums that night: %s)'
              % (album, date, counts or 'none'))
        if len(material) > 1:
            over, _c = album_overlap(date, album)
            if over:
                print('   NOTE: %d frame(s) in this scope are ALSO filed under '
                      'another event that night. Album membership is a human '
                      'judgement, not a partition -- eyeball them before publish.'
                      % len(over))
        return
    if len(material) > 1:
        lines = '\n     '.join('%-46s %5d frames' % (t or '(untitled)', n)
                               for t, n in sorted(material.items(),
                                                  key=lambda kv: -kv[1]))
        sys.exit(
            'REFUSING: %s is a MIXED-EVENT night -- %d R7 frames spread across %d '
            'albums:\n     %s\n     %-46s %5d frames\n'
            'A calendar date is not an event. Selecting date-wide would put one '
            'event\'s photographs into the other\'s gallery (on 2026-09-11 that is '
            '1,647 road-race frames into a football gallery). Re-run with '
            '--album "<title>" to scope to one event.%s'
            % (date, total, len(material), lines, '(in no album)', no_album,
               ('\n' + tool_hint) if tool_hint else ''))
    print('   event scope: %s is a single-event night (%d R7 frames, albums: %s)'
          % (date, total, counts or 'none'))


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


# ---------------------------------------------------------------------------
# season/t write boundary
# ---------------------------------------------------------------------------
# On 2026-09-27 the live site went OVER the 1GB GitHub Pages limit: 99 full-res
# originals (1,112,137,163 B) were sitting in season/t. Each one had a correct
# ~33.6KB twin already present and was named "<stem> 2.jpg" -- macOS's collision
# suffix. So the encoder never failed; an unintended SECOND write landed beside
# the good thumb and nothing checked it. 1.1GB shipped silently.
#
# These guards close that boundary. They are deliberately in make_thumb(), the
# one function every TDIR write funnels through (publish_day.py main() and
# extend_day.py both call it; grade_day.py and merge_player_tags.py only READ
# TDIR). They RAISE. A warn-and-continue is exactly how the 1.1GB got in.
COLLISION_SUFFIX_RE = re.compile(r'^(?P<stem>.+) (?P<n>\d+)\.jpg$', re.IGNORECASE)


class ThumbWriteError(RuntimeError):
    """A write into season/t broke a thumbnail invariant. Never caught in-tree."""


def image_dimensions(path):
    """(width, height) for a file on disk. Pillow reads the header only; sips is
    the fallback so the guard still works on a machine without Pillow.

    NOTE, deliberate: ImportError falls THROUGH to sips silently -- that is the
    no-Pillow machine, not a failure. Only a genuine decode error (corrupt file,
    not an image) raises ThumbWriteError. Do not "fix" the bare `except
    ImportError: pass` into a hard failure; it would break every Pillow-less
    machine, macOS ships sips by default and Pillow is not a dependency here.
    """
    try:
        from PIL import Image
        with Image.open(path) as im:
            return im.size
    except ImportError:
        pass
    except Exception as e:
        raise ThumbWriteError('cannot read dimensions of %s (%s)' % (path, e))
    r = subprocess.run(['sips', '-g', 'pixelWidth', '-g', 'pixelHeight', path],
                       capture_output=True, text=True)
    dims = {}
    for line in r.stdout.splitlines():
        key, _, val = line.strip().partition(':')
        if key in ('pixelWidth', 'pixelHeight'):
            try:
                dims[key] = int(val.strip())
            except ValueError:
                pass
    if 'pixelWidth' in dims and 'pixelHeight' in dims:
        return dims['pixelWidth'], dims['pixelHeight']
    raise ThumbWriteError('cannot read dimensions of %s (sips gave: %r)'
                          % (path, r.stdout.strip()))


def guard_thumb_name(dst):
    """Reject a collision-suffixed destination BEFORE any bytes are written.

    "<stem> 2.jpg" can only mean a duplicate write: real ids are
    <prefix>_<canon-stem> and never carry a space-digit tail.
    """
    base = os.path.basename(dst)
    m = COLLISION_SUFFIX_RE.match(base)
    if m:
        raise ThumbWriteError(
            'REFUSING to write %r into season/t: the name carries a macOS '
            'collision suffix (" %s.jpg"), which can only mean an unintended '
            'duplicate write beside the real thumbnail %r. This is the exact '
            'shape of the 99-stray / 1.1GB incident. Fix the id, do not rename '
            'around this check.' % (base, m.group('n'), m.group('stem') + '.jpg'))


def guard_thumb_file(dst):
    """Verify AFTER encoding that the file really is a thumbnail.

    sips running without error is not evidence that it shrank anything, so this
    measures the file that actually landed. An oversized file is DELETED before
    raising -- the invariant is that nothing oversized exists in season/t, and
    leaving the offender on disk for a later `git add -A` to sweep up would
    reproduce the original incident.
    """
    if not os.path.exists(dst):
        raise ThumbWriteError('make_thumb produced no file at %s' % dst)
    w, h = image_dimensions(dst)
    if max(w, h) > THUMB_W:
        size = os.path.getsize(dst)
        os.unlink(dst)
        raise ThumbWriteError(
            'REFUSING to leave %r in season/t: long edge %dpx exceeds THUMB_W=%d '
            '(%dx%d, %s bytes). The resize did not take effect, so this is a '
            'full-res original, not a thumbnail. Offending file deleted; fix the '
            'encode path and re-run.'
            % (os.path.basename(dst), max(w, h), THUMB_W, w, h, format(size, ',')))
    return w, h


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
    # WRITE BOUNDARY, part 1 of 2: reject the name before any bytes land.
    guard_thumb_name(dst)
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
    # WRITE BOUNDARY, part 2 of 2: verify what actually landed on disk. sips
    # exiting 0 is not evidence it resized anything.
    guard_thumb_file(dst)
    return o


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('date')
    ap.add_argument('--game-id', required=True)
    ap.add_argument('--label', required=True)
    ap.add_argument('--sub', default='')
    ap.add_argument('--prefix', required=True, help='id prefix, e.g. rock_')
    ap.add_argument('--album', default=None,
                    help='scope selection to one album (substring match). REQUIRED '
                         'on a mixed-event night, where a calendar date holds two '
                         'different events.')
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

    assert_single_event(a.date, a.album,
                        'e.g. python3 publish_day.py %s --album "FOOTBALL IG" ...' % a.date)
    assets, missing = local_assets(a.date, album=a.album)
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
