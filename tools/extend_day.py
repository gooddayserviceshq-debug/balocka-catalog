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
extended without an explicit override. Measured 2026-09-27, the galleries split
in two:

  game-0831, game-0914, therock-0917, siegel-0911   published == every local frame
  brentwood-0821, jv-0824, oakland-0904, ...        CURATED, 9-53% of local frames

For the first group, "some frames are missing because they were in iCloud" is a
defect and appending repairs it. For the second, the missing frames are missing
because a human left them out; appending would silently overrule that curation
and put rejected frames in front of families.

The gate detects curation by COVERAGE, not by grading. An earlier version fired
only on tier/score, which missed siegel-0911: it published exactly its 174
jersey-tagged frames and nothing else (published set == tagged set, symmetric
difference empty) while carrying no grades, so a routine extend would have
appended 1,782 frames, 1,621 of them untagged, with no warning at all.

--only-tagged
-------------
A frame is findable because it carries a jersey tag; an untagged frame adds bytes
and no searchability. Without this flag the tool appends EVERY local frame, which
on the curated nights is 10,129 frames rather than the 3,037 tagged ones -- 4,713
untagged frames and ~160MB nobody asked for. Pass --only-tagged to append just
the tagged set. The flag refuses to run if the night has no tag file rather than
silently falling back to "everything".

USAGE
    python3 extend_day.py 2026-09-17 --game-id therock-0917 --prefix rock_ --dry-run
    python3 extend_day.py 2026-08-21 --game-id brentwood-0821 --prefix bw_ \
        --only-tagged --force-curated --dry-run
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
                         assert_single_event, backup_data_js, load_data_js,
                         local_assets, make_thumb, orientations, save_data_js)

# Where merge_player_tags.py reads its jersey tags from; --only-tagged uses the
# same source so the two tools cannot disagree about what "tagged" means.
TAGDIR = os.path.join(os.path.expanduser('~'), 'Pictures/balocka-library')


def published_identity(game, manifest_path):
    """Every way an already-published frame can be identified, as a single set.

    WHY THIS IS NOT `prefix + filename`
    -----------------------------------
    The original version deduped on `prefix + splitext(ZORIGINALFILENAME)[0]`.
    That key only equals a published id when the gallery's ids are literally
    prefix+camera-filename. Measured 2026-09-27, three id schemes are in use:

        sg_IMGL4012            prefix + camera filename   key matches
        g0831__MGL1192         prefix + camera filename   key matches
        IMGL4456               camera filename, NO prefix key NEVER matches
        oak_434E7E8B-BB7C-...  prefix + Photos UUID       key NEVER matches

    So the key matched on exactly the four galleries that already hold every local
    frame and therefore have nothing to append, and mismatched on all six curated
    galleries -- the only ones anyone would ever extend. A run on those would have
    re-thumbed and re-appended 3,353 already-published frames under second ids:
    ~114MB of duplicate thumbnails, permanent once committed, and every duplicated
    frame shown to families twice.

    The manifest is authoritative: it maps published id -> original absolute path
    for every published frame. Identity is therefore taken from the ORIGINAL PATH,
    which is the same thing local_assets() returns, so the comparison is
    path-to-path and cannot be defeated by an id scheme. Camera filename and
    Photos UUID are added as secondary keys for robustness when a gallery predates
    the manifest.
    """
    ident = set()
    for p in game['photos']:
        ident.add(('id', p['id']))
    if os.path.exists(manifest_path):
        with open(manifest_path, newline='', encoding='utf-8') as f:
            for r in csv.DictReader(f):
                if r.get('game_id') != game['id']:
                    continue
                o = (r.get('original') or '').strip()
                if o:
                    ident.add(('path', os.path.realpath(o)))
                    stem = os.path.splitext(os.path.basename(o))[0]
                    if stem:
                        ident.add(('stem', stem.upper()))
    return ident


def already_published(ident, uuid, filename, path, prefix):
    """Is this local asset already in the gallery, under ANY id scheme?"""
    if ('path', os.path.realpath(path)) in ident:
        return True
    stem = os.path.splitext(os.path.basename(path))[0]
    if ('stem', stem.upper()) in ident:
        return True
    fstem = os.path.splitext(filename)[0]
    if ('stem', fstem.upper()) in ident:
        return True
    # id-shaped fallbacks: with prefix, and bare (unprefixed galleries)
    for cand in (prefix + fstem, fstem, prefix + uuid, uuid):
        if ('id', cand) in ident:
            return True
    return False


def tagged_stems(date):
    """Jersey-tagged frames for one night, as uppercase filename stems.

    Reads player-tags-<DATE>.json, the same source merge_player_tags.py uses. A
    frame counts as tagged only when its jersey list is NON-EMPTY -- the files
    also record scanned-but-untagged frames, and treating a present-but-empty
    entry as tagged would silently select the whole night.

    Jersey NUMBERS only. No name field is read, derived, or written.
    """
    p = os.path.join(TAGDIR, 'player-tags-%s.json' % date)
    if not os.path.exists(p):
        return None
    obj = json.load(open(p, encoding='utf-8'))
    out = set()
    for k, v in obj.items():
        if not (isinstance(v, list) and v):
            continue
        if not all(isinstance(x, int) or str(x).strip().isdigit() for x in v):
            sys.exit('FAIL: %s has a non-numeric tag value for %s -- that is the '
                     'shape a NAME arrives in, and names never travel.' % (p, k))
        out.add(os.path.splitext(k)[0].upper())
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('date')
    ap.add_argument('--game-id', required=True)
    ap.add_argument('--prefix', required=True)
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--only-tagged', action='store_true',
                    help='append ONLY jersey-tagged frames (what makes a frame '
                         'findable); without it every local frame is appended')
    ap.add_argument('--album', default=None,
                    help='scope selection to one album (substring match). REQUIRED '
                         'on a mixed-event night: 2026-09-11 holds a 5K road race '
                         'AND a football game, and a date-wide append puts one in '
                         "the other's gallery.")
    ap.add_argument('--force-curated', action='store_true',
                    help='append to a CURATED gallery, overriding human curation')
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
    ident = published_identity(game, MANIFEST)

    # EVENT SCOPE FIRST. This runs BEFORE the curation gate on purpose: the
    # curation gate can be waived with --force-curated, and on 2026-09-11 that
    # waiver was the whole disaster path -- it would have appended 1,795 frames
    # to siegel-0911, of which 1,647 are a community 5K. --force-curated says
    # "yes, overrule the human's selection"; it must never also mean "and pull in
    # a different event". There is no flag that waives this one.
    assert_single_event(a.date, a.album,
                        'e.g. python3 extend_day.py %s --game-id %s --prefix %s '
                        '--album "FOOTBALL IG" --dry-run'
                        % (a.date, a.game_id, a.prefix))
    assets, still_icloud = local_assets(a.date, album=a.album)

    tagged = tagged_stems(a.date)
    if a.only_tagged and tagged is None:
        sys.exit('FAIL: --only-tagged but no player-tags-%s.json in %s -- refusing '
                 'to fall back to "every local frame", which is a different and '
                 'much larger action.' % (a.date, TAGDIR))

    graded = sum(1 for p in game['photos']
                 if str(p.get('tier', '')).strip() or float(p.get('score', 0) or 0) > 0)
    coverage = 100.0 * len(game['photos']) / max(len(assets), 1)
    print('%s / %s: %d local frames tonight, %d already published (%.0f%% coverage), '
          '%d graded, %d still iCloud-only'
          % (a.date, a.game_id, len(assets), len(game['photos']), coverage, graded,
             still_icloud))

    # ---------------------------------------------------------------- curation gate
    # The gate used to fire on tier/score alone. That misses a gallery curated by a
    # rule OTHER than grading: siegel-0911 published exactly its 174 tagged frames
    # and nothing else (measured: published set == tagged set, symmetric difference
    # empty) while carrying no grades at all, so a routine extend would have
    # appended 1,782 frames -- 1,621 of them untagged -- with no warning.
    # Curation is therefore detected by COVERAGE, which catches every curated
    # gallery regardless of the rule used to curate it, with grading as a second
    # signal for the message.
    curated_by_coverage = coverage < 95.0
    if curated_by_coverage and not a.force_curated:
        why = ('%d of its frames are graded' % graded if graded
               else 'it publishes a selected subset (no grades, but only %.0f%% of '
                    'the night)' % coverage)
        sys.exit('REFUSING: %s publishes %d of %d local frames (%.0f%% coverage) and '
                 '%s, so its contents are a CURATED selection, not "every local '
                 'frame". Appending would put frames a human left out in front of '
                 'families. Re-run with --force-curated only if that is genuinely '
                 'intended.'
                 % (a.game_id, len(game['photos']), len(assets), coverage, why))

    # ---------------------------------------------------------------- selection
    new = [(u, fn, ts, p) for (u, fn, ts, p) in assets
           if not already_published(ident, u, fn, p, a.prefix)]
    print('   %d frames are local but NOT in the gallery' % len(new))

    if a.only_tagged:
        before = len(new)
        new = [(u, fn, ts, p) for (u, fn, ts, p) in new
               if os.path.splitext(fn)[0].upper() in tagged
               or os.path.splitext(os.path.basename(p))[0].upper() in tagged]
        print('   --only-tagged: %d of those %d carry a jersey tag (%d untagged '
              'frames NOT appended)' % (len(new), before, before - len(new)))
    elif tagged is not None:
        n_tag = sum(1 for (_u, fn, _ts, p) in new
                    if os.path.splitext(fn)[0].upper() in tagged
                    or os.path.splitext(os.path.basename(p))[0].upper() in tagged)
        print('   NOTE: %d of those %d are jersey-tagged. Without --only-tagged all '
              '%d will be appended, including %d untagged frames that add bytes '
              'but no searchability.' % (n_tag, len(new), len(new), len(new) - n_tag))

    if not new:
        print('nothing to do: gallery already holds every frame this run selects')
        return

    # A recycled Canon filename would collide with an id from ANOTHER night; the
    # per-game prefix prevents cross-game collisions, but two frames in the SAME
    # night sharing a filename would silently overwrite one thumbnail.
    stems = [os.path.splitext(fn)[0] for _, fn, _, _ in new]
    dupes = {s for s in stems if stems.count(s) > 1}
    if dupes:
        sys.exit('FAIL: %d filename(s) occur twice in this one night (%s) — one thumb '
                 'would overwrite the other' % (len(dupes), sorted(dupes)[:5]))

    # A new id must not collide with an id ALREADY in this gallery, or save_data_js
    # would write two photos with the same id and one thumbnail would overwrite the
    # other. The old prefix+filename dedup could not see this.
    collide = [a.prefix + s for s in stems if (a.prefix + s) in existing]
    if collide:
        sys.exit('FAIL: %d candidate id(s) already exist in %s (%s) — the dedup pass '
                 'should have excluded these; refusing to overwrite published frames'
                 % (len(collide), a.game_id, collide[:5]))

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

    bak = backup_data_js()
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
