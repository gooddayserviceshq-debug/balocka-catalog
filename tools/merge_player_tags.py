#!/usr/bin/env python3
"""merge_player_tags.py — fold player-tags-<DATE>.json into season/data.js.

WHY THIS EXISTS
---------------
The Saturday sweep wrote 10 tag files (13,921 frames) and stopped there. A tag
file that is not in data.js is not a deliverable: the QR cards on the fence
deep-link to season/#g=<game>, and the jersey search behind them reads ONLY
window.SEASON_DATA.tags. Tags sitting on disk reach nobody. On 2026-09-27 that
gap was 4.4% searchable.

THE JOIN, AND WHY IT IS DATE-SCOPED
-----------------------------------
A published photo id is either a camera filename (IMGL4016, sg_IMGL4016) or a
Photos UUID (oak_<uuid>). Canon RECYCLES filenames across nights — 179 basenames
in this catalog appear in 2+ games, and IMGL4016 exists on 2026-08-21, 09-07,
09-11 and 2025-08-01. A filename-only join therefore attaches one night's jersey
tag to another night's photo, showing a parent the wrong kid. Every join here is
scoped to ONE shoot date:

  * UUID-keyed id -> the UUID dates the asset exactly (authoritative).
  * filename id   -> matched only inside the game whose shoot date equals the tag
                     file's date, and only when the library confirms a frame with
                     that filename exists on that date.

NOTHING IS HARDCODED (this runs weekly, unattended)
---------------------------------------------------
Earlier versions carried hand-maintained GAME_DATE and PREFIXES tables. On a
recurring job that is a silent-failure machine: a new game night is absent from
both tables, so its tags merge zero frames and the run still reports success.
Both are now DERIVED:

  * prefixes  — inferred from the published ids themselves (leading lowercase
                token before '_'), so a new 'rock_' gallery needs no edit.
  * game date — inferred by resolving each game's published ids against the
                library and taking the modal date, and only accepted when
                consensus is at least MIN_DATE_CONSENSUS. A game whose date
                cannot be established that confidently is SKIPPED LOUDLY rather
                than guessed at, because a wrong date is a mis-filed tag.

CHILD-SAFETY INVARIANT (non-negotiable)
---------------------------------------
Jersey NUMBERS travel; minors' names do not. rosterByNum was deliberately
blanked on 2026-09-25 after a commit silently restored 71 names into a live data
file. This script writes only tags.<side>[<number>] = [photo ids], never reads or
derives a name, rejects a non-numeric tag key (that is the shape a name arrives
in), and asserts the roster is still blank before AND after writing.

Off-roster numbers are opponent players, not OCR errors, and go to tags.opponent.

USAGE
    python3 merge_player_tags.py --dry-run     # report only, writes nothing
    python3 merge_player_tags.py --apply       # rewrite season/data.js in place
Exit non-zero on any safety or arithmetic failure, so a cron run cannot mistake a
refusal for success.
"""
import argparse
import json
import os
import re
import shutil
import sqlite3
import sys
import time
from collections import Counter, defaultdict

HOME = os.path.expanduser('~')
DB = os.path.join(HOME, 'Pictures/Photos Library.photoslibrary/database/Photos.sqlite')
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_JS = os.path.join(REPO, 'season', 'data.js')
TDIR = os.path.join(REPO, 'season', 't')
LIB = os.path.join(HOME, 'Pictures/balocka-library')
APPLE_EPOCH = 978307200
# A game's date is accepted only if this share of its resolvable published frames
# agree on it. Below that, the game is skipped instead of guessed.
MIN_DATE_CONSENSUS = 0.90


def load_data_js(path=DATA_JS):
    raw = open(path, encoding='utf-8').read()
    m = re.match(r'\s*window\.SEASON_DATA\s*=\s*', raw)
    if not m:
        sys.exit('FAIL: data.js does not begin with window.SEASON_DATA =')
    return json.loads(raw[m.end():].strip().rstrip(';'))


def backup_data_js(suffix='bak'):
    """Copy data.js to a rollback file OUTSIDE the served directory.

    Backups used to be written as season/data.js.<suffix>-<epoch>, i.e. inside
    season/, which GitHub Pages SERVES -- one was committed and publicly
    fetchable. A backup is a pre-edit snapshot, so one taken before a privacy fix
    preserves exactly what the fix removed, and verify_no_names.py only scans
    tracked files. .backups/ is neither served nor tracked.
    """
    d = os.path.join(REPO, '.backups')
    os.makedirs(d, exist_ok=True)
    dst = os.path.join(d, 'data.js.%s-%d' % (suffix, int(time.time())))
    shutil.copy2(DATA_JS, dst)
    return dst


def save_data_js(d, path=DATA_JS):
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        f.write('window.SEASON_DATA = ')
        json.dump(d, f, ensure_ascii=False, separators=(', ', ': '))
        f.write(';\n')
    os.replace(tmp, path)
    # Pages serves data.js with max-age=600; an unversioned <script src> leaves
    # returning visitors on the old catalog for ten minutes. Only stamp when we
    # wrote the real catalog (tests pass a temp path).
    if os.path.abspath(path) == os.path.abspath(DATA_JS):
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from stamp_data_version import stamp
        stamp(quiet=True)


def all_photo_ids(d):
    ids = [p['id'] for g in d['games'] for p in g['photos']]
    ids += [p['id'] for p in d.get('catchall', {}).get('photos', [])]
    return ids


def discover_prefixes(d):
    """Infer id prefixes from the data itself, longest first.

    A published id is either '<core>' or '<prefix>_<core>'. Sorting longest-first
    matters: 'sg_' must not eat the front of an 'sgx_' id.
    """
    pres = set()
    for pid in all_photo_ids(d):
        m = re.match(r'^([a-z0-9]{1,8})_', pid)
        if m:
            pres.add(m.group(1) + '_')
    return tuple(sorted(pres, key=len, reverse=True))


def strip_prefix(pid, prefixes):
    for p in prefixes:
        if pid.startswith(p):
            return pid[len(p):]
    return pid


def library_index():
    """Read-only library index.

    mode=ro (never immutable=1) so the WAL is included — immutable skips it and
    under-reports a full game night as a trivial shoot.
    """
    con = sqlite3.connect('file:%s?mode=ro' % DB, uri=True)
    fn_dates = defaultdict(set)
    uuid_date = {}
    by_date = defaultdict(dict)
    dupes = defaultdict(set)
    for u, fn, dt in con.execute("""
        select a.ZUUID, aaa.ZORIGINALFILENAME,
               date(datetime(a.ZDATECREATED+%d,'unixepoch','localtime'))
          from ZASSET a
          left join ZADDITIONALASSETATTRIBUTES aaa on aaa.ZASSET = a.Z_PK
         where a.ZTRASHEDSTATE = 0""" % APPLE_EPOCH):
        if fn:
            stem = os.path.splitext(fn)[0]
            fn_dates[stem].add(dt)
            if stem in by_date[dt] and by_date[dt][stem] != u:
                dupes[dt].add(stem)      # two frames, one filename, same night
            by_date[dt][stem] = u
        if u:
            uuid_date[u] = dt
    con.close()
    for dt, stems in dupes.items():
        for s in stems:
            by_date[dt].pop(s, None)
    return fn_dates, uuid_date, by_date


def is_uuid(core):
    return len(core) == 36 and core.count('-') == 4


def derive_game_dates(d, fn_dates, uuid_date, prefixes):
    """game id -> shoot date, derived from the game id and VERIFIED in the library.

    Two rejected approaches, recorded so they are not retried:

      1. Uniqueness voting (count only filenames shot on exactly one date) fails
         hardest on the games that most need dating: 8/31 shares 784 basenames
         with 9/04, so every frame is individually ambiguous and the game dates as
         unknown.
      2. Candidacy voting (every date in a frame's date set gets a vote) actively
         MIS-dates: it put siegel-0911 on 2026-08-21, because a pile of recycled
         filenames all list 08-21 among their dates. A confidently wrong date is
         worse than a skip -- it mis-files a jersey tag onto another night.

    What is actually authoritative is the gallery id itself: every game is named
    <opponent>-MMDD by the publisher. So take the date FROM the id, then require
    the library to agree: at least MIN_DATE_CONSENSUS of that game's resolvable
    frames must have that date in their date set. Derived, then verified -- and a
    game whose id carries no date, or whose frames contradict it, is skipped
    loudly rather than guessed at.
    """
    dates, skipped = {}, {}
    groups = [(g['id'], g['photos']) for g in d['games']]
    ca = d.get('catchall', {}).get('photos', [])
    if ca:
        groups.append(('catchall', ca))

    # Season year: inferred from the dates the library actually holds for these
    # frames, so nothing hardcodes 2026.
    years = Counter()
    for _, photos in groups:
        for p in photos:
            core = strip_prefix(p['id'], prefixes)
            for dt in ([uuid_date[core]] if is_uuid(core) and core in uuid_date
                       else fn_dates.get(core, ())):
                years[dt[:4]] += 1
    season_year = years.most_common(1)[0][0] if years else None

    for gid, photos in groups:
        m = re.search(r'-(\d{2})(\d{2})$', gid)
        if not m or not season_year:
            skipped[gid] = 'id carries no -MMDD date suffix, cannot derive a date'
            continue
        want = '%s-%s-%s' % (season_year, m.group(1), m.group(2))
        agree = resolvable = 0
        for p in photos:
            core = strip_prefix(p['id'], prefixes)
            if is_uuid(core):
                dt = uuid_date.get(core)
                if dt:
                    resolvable += 1
                    agree += (dt == want)
            else:
                ds = fn_dates.get(core)
                if ds:
                    resolvable += 1
                    agree += (want in ds)
        if not resolvable:
            skipped[gid] = 'no published frame could be resolved to a library date'
            continue
        share = agree / resolvable
        if share < MIN_DATE_CONSENSUS:
            skipped[gid] = ('library contradicts the id: only %.0f%% of %d resolvable '
                            'frames are consistent with %s' % (100 * share, resolvable, want))
            continue
        dates[gid] = want
    return dates, skipped


def assert_no_names(d, where):
    bad = [n for n, v in (d.get('rosterByNum') or {}).items() if str(v).strip()]
    if bad:
        sys.exit('FAIL (%s): rosterByNum carries %d non-blank name(s): %s'
                 % (where, len(bad), bad[:5]))
    named = [r for r in d.get('roster', []) if str(r.get('name', '')).strip()]
    if named:
        sys.exit('FAIL (%s): roster carries %d non-blank name field(s)' % (where, len(named)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()
    if not (a.apply or a.dry_run):
        ap.error('pick --dry-run or --apply')

    d = load_data_js()
    assert_no_names(d, 'before merge')
    prefixes = discover_prefixes(d)
    fn_dates, uuid_date, by_date = library_index()
    game_date, skipped = derive_game_dates(d, fn_dates, uuid_date, prefixes)

    print('id prefixes discovered: %s' % (', '.join(prefixes) or '(none)'))
    for gid in sorted(game_date):
        print('   %-22s %s' % (gid, game_date[gid]))
    for gid, why in sorted(skipped.items()):
        print('   SKIP %-17s %s' % (gid, why))

    idx = {}
    for g in d['games']:
        idx[g['id']] = {strip_prefix(p['id'], prefixes): p['id'] for p in g['photos']}
    idx['catchall'] = {strip_prefix(p['id'], prefixes): p['id']
                       for p in d.get('catchall', {}).get('photos', [])}

    roster = {str(r['jersey']) for r in d['roster']}
    thumbs = {os.path.splitext(f)[0] for f in os.listdir(TDIR)
              if f.lower().endswith('.jpg')}

    tags = d.setdefault('tags', {})
    smyrna = tags.setdefault('smyrna', {})
    opponent = tags.setdefault('opponent', {})
    before_refs = sum(len(v) for v in smyrna.values()) + sum(len(v) for v in opponent.values())

    date_games = defaultdict(list)
    for gid, dt in game_date.items():
        date_games[dt].append(gid)

    new_s, new_o = defaultdict(set), defaultdict(set)
    for side, store in (('smyrna', new_s), ('opponent', new_o)):
        for num, ids in (smyrna if side == 'smyrna' else opponent).items():
            if not re.fullmatch(r'\d+', str(num)):
                sys.exit('FAIL: non-numeric tag key %r already in tags.%s — jersey '
                         'numbers only, names never travel' % (num, side))
            store[str(int(num))].update(ids)

    report = []
    for f in sorted(os.listdir(LIB)):
        m = re.match(r'player-tags-(\d{4}-\d{2}-\d{2})\.json$', f)
        if not m:
            continue
        date = m.group(1)
        t = json.load(open(os.path.join(LIB, f)))
        gids = date_games.get(date, [])
        st = dict(date=date, frames=len(t), tagged=0, merged=0, refs=0, no_game=0,
                  no_thumb=0, wrongdt=0, dupname=0, games=','.join(gids) or '-')
        stem_uuid = by_date.get(date, {})
        for fname, nums in t.items():
            if not nums:
                continue
            st['tagged'] += 1
            stem = os.path.splitext(fname)[0]
            uuid = stem_uuid.get(stem)
            pid = None
            for gid in gids:
                g = idx.get(gid, {})
                cand = g.get(stem) or (g.get(uuid) if uuid else None)
                if cand:
                    pid = cand
                    break
            if pid is None:
                if stem in fn_dates and date in fn_dates[stem] and not uuid:
                    st['dupname'] += 1
                else:
                    st['no_game'] += 1
                continue
            core = strip_prefix(pid, prefixes)
            ok = (uuid_date.get(core) == date) if is_uuid(core) else (date in fn_dates.get(core, set()))
            if not ok:
                st['wrongdt'] += 1
                continue
            if pid not in thumbs:
                st['no_thumb'] += 1
                continue
            st['merged'] += 1
            for n in nums:
                n = str(int(n))
                (new_s if n in roster else new_o)[n].add(pid)
                st['refs'] += 1
        report.append(st)

    tags['smyrna'] = {n: sorted(v) for n, v in sorted(new_s.items(), key=lambda kv: int(kv[0])) if v}
    tags['opponent'] = {n: sorted(v) for n, v in sorted(new_o.items(), key=lambda kv: int(kv[0])) if v}
    after_refs = sum(len(v) for v in tags['smyrna'].values()) + sum(len(v) for v in tags['opponent'].values())

    print('\n%-12s %7s %7s %7s %8s %8s %8s %8s %8s  %s'
          % ('date', 'frames', 'tagged', 'merged', 'refs', 'no_game', 'no_thumb',
             'wrongdt', 'dupname', 'games'))
    for s in report:
        print('%-12s %7d %7d %7d %8d %8d %8d %8d %8d  %s'
              % (s['date'], s['frames'], s['tagged'], s['merged'], s['refs'],
                 s['no_game'], s['no_thumb'], s['wrongdt'], s['dupname'], s['games']))

    searchable = len({p for v in tags['smyrna'].values() for p in v}
                     | {p for v in tags['opponent'].values() for p in v})
    published = sum(len(g['photos']) for g in d['games'])
    tagged_disk = sum(sum(1 for v in json.load(open(os.path.join(LIB, f))).values() if v)
                      for f in os.listdir(LIB) if re.match(r'player-tags-.*\.json$', f))
    print('\ntag refs in data.js: %d -> %d' % (before_refs, after_refs))
    print('TAGGED %d / PUBLISHED %d / SEARCHABLE %d (%.1f%% of published)'
          % (tagged_disk, published, searchable, 100.0 * searchable / max(published, 1)))

    allpub = set(all_photo_ids(d))
    for side in ('smyrna', 'opponent'):
        for n, ids in tags[side].items():
            if not re.fullmatch(r'\d+', n):
                sys.exit('FAIL: non-numeric tag key %r in %s' % (n, side))
            for i in ids:
                if i not in allpub:
                    sys.exit('FAIL: tag %s/%s references unpublished id %s' % (side, n, i))
                if i not in thumbs:
                    sys.exit('FAIL: tag %s/%s references missing thumb %s' % (side, n, i))
    assert_no_names(d, 'after merge')
    print('guards: all tag ids published + thumbed; keys numeric; no names present')

    if skipped:
        print('\nNOTE: %d game(s) skipped for want of a confident date — their tags '
              'merged nothing. Fix the cause, do not lower the gate.' % len(skipped))

    if a.apply:
        bak = backup_data_js()
        save_data_js(d)
        chk = load_data_js()
        assert_no_names(chk, 'reread')
        got = (sum(len(v) for v in chk['tags']['smyrna'].values())
               + sum(len(v) for v in chk['tags']['opponent'].values()))
        if got != after_refs:
            sys.exit('FAIL: reread got %d refs, expected %d' % (got, after_refs))
        print('wrote %s (backup %s), reread verified %d refs'
              % (DATA_JS, os.path.basename(bak), got))
    else:
        print('dry run: nothing written')


if __name__ == '__main__':
    main()
