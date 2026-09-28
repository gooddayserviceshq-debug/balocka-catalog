#!/usr/bin/env python3
"""grade_backfill.py — measure every frame in data.js that carries no score.

WHY THIS EXISTS
---------------
grade_day.py grades ONE gallery. It ran to completion on 09-27 (7,196 of 7,210
frames, 99.8%), and then two later publish commits appended 3,096 more frames
with `score: 0, tier: ""`. Those frames were never submitted to a grader. The
hub reads a missing score as "the gallery is graded, this frame just isn't a
keeper": `gameIsGraded()` is true if ANY photo in the tab carries a tier, so an
unmeasured frame renders badgeless, sorts to the back of a gallery headed
"best first", and is excluded from the A+/A/B+ filter entirely.

So the defect is not in the grader. It is that the publish path can add frames
without enqueueing them for grading. This tool is the backfill; the invariant
that stops it recurring is tools/check_grade_coverage.py.

SCORE 0 IS TWO DIFFERENT STATES, AND data.js COULD NOT TELL THEM APART
----------------------------------------------------------------------
grade_array() legitimately returns 0.0 when mean < 40 or contrast < 12 -- a
black frame or a blown scoreboard shot is not a keeper at any sharpness. That
is a MEASUREMENT. A frame that no grader ever opened is ABSENT DATA. Both were
stored as `score: 0`, which is what let index.html carry a comment asserting
every score-0 frame was "too dark or too flat to measure" while 3,096 of them
had simply never been looked at. (Measured: the score-0 population was BRIGHTER
than the graded one in 3 of 5 tabs.)

This tool writes `"gated": 1` on a frame the metric measured at exactly 0.0.
The key is absent everywhere else, so:

    measured  <=>  score > 0 or gated == 1

is checkable by a script instead of asserted by a comment. index.html tests
that per frame now, so an unmeasured frame is labelled unmeasured.

RESUMABLE, IDEMPOTENT, AND LOUD WHEN PARTIAL
--------------------------------------------
Every measurement is appended to .grade-journal/backfill.jsonl before data.js
is touched, so a kill mid-pass loses nothing and a re-run skips what is already
done. Re-running after a complete pass is a no-op: the same original yields the
same score. The pass exits NON-ZERO whenever `scored != submitted`, so a partial
run can never look like a clean one -- the failure mode that produced this card.

    python3 tools/grade_backfill.py --dry-run
    python3 tools/grade_backfill.py --apply
    python3 tools/grade_backfill.py --apply --game-id jv-0824   # one tab
"""
import argparse
import csv
import json
import multiprocessing as mp
import os
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, 'tools'))

from grade_day import (DATA_JS, FLOOR, MANIFEST, backup_data_js,  # noqa: E402
                       date_from_game_id, load_data_js, manifest_originals,
                       photos_on, save_data_js, score_path, tier_for, verify)

JOURNAL_DIR = os.path.join(REPO, '.grade-journal')
JOURNAL = os.path.join(JOURNAL_DIR, 'backfill.jsonl')
TIERS = ('A+', 'A', 'B+')


# ------------------------------------------------------------------ helpers
def unscored(p):
    """A frame with no measurement. `gated` marks a real 0.0 measurement."""
    return not float(p.get('score', 0) or 0) and not p.get('gated')


def resolve_originals(d, only=None):
    """[(frame_id, game_id, original_path)] for every unscored frame.

    Two lookups, in grade_day.py's order: the manifest's `original` column,
    then a DATE-SCOPED Photos query. Never a bare basename join -- 179
    basenames recur across nights and IMGL4016 exists on four dates, so a
    global join would grade one night's frame against another night's pixels.
    """
    man = manifest_originals()
    out, unreachable = [], []
    for g in d['games']:
        if only and g['id'] != only:
            continue
        targets = [p['id'] for p in g['photos'] if unscored(p)]
        if not targets:
            continue
        by_date = None
        for pid in targets:
            o = man.get(pid)
            if not (o and os.path.exists(o)):
                if by_date is None:
                    date = date_from_game_id(g['id'])
                    by_date = photos_on(date) if date else {}
                o = by_date.get(pid.split('_', 1)[-1])
            if o and os.path.exists(o):
                out.append((pid, g['id'], o))
            else:
                unreachable.append((pid, g['id']))
    return out, unreachable


def read_journal():
    """{frame_id: score}. Last line wins, so a re-measure supersedes."""
    done = {}
    if not os.path.exists(JOURNAL):
        return done
    with open(JOURNAL, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue          # a torn last line from a kill mid-write
            if 'id' in r and 'score' in r:
                done[r['id']] = float(r['score'])
    return done


def _measure(job):
    pid, gid, path = job
    try:
        return pid, gid, score_path(path), None
    except Exception as e:                       # noqa: BLE001
        return pid, gid, None, '%s: %s' % (type(e).__name__, e)


# -------------------------------------------------------------------- pass
def _repair_journal_tail():
    """Ensure the journal ends with a newline before anything appends to it.

    A SIGKILL mid-write leaves a torn final line with no newline. read_journal()
    survives that (it drops the fragment), but the NEXT run's first record would
    be appended onto the fragment -- fusing two lines into one unparseable one
    and silently losing a completed measurement. Terminate the fragment instead.
    Returns True if a repair was needed.
    """
    if not os.path.exists(JOURNAL) or os.path.getsize(JOURNAL) == 0:
        return False
    with open(JOURNAL, 'rb+') as f:
        f.seek(-1, os.SEEK_END)
        if f.read(1) == b'\n':
            return False
        f.write(b'\n')
    return True


def run_pass(jobs, procs, quiet=False):
    """Measure `jobs`, journalling each result as it lands.

    Returns (scored, errors). The journal is flushed per batch, so SIGKILL at
    any point loses at most the in-flight batch.
    """
    os.makedirs(JOURNAL_DIR, exist_ok=True)
    if _repair_journal_tail() and not quiet:
        print('   (repaired a torn journal line from an interrupted run)')
    scored, errors = {}, []
    t0 = time.time()
    with open(JOURNAL, 'a', encoding='utf-8') as jf:
        with mp.Pool(procs) as pool:
            for i, (pid, gid, sc, err) in enumerate(
                    pool.imap_unordered(_measure, jobs, chunksize=8), 1):
                if err:
                    errors.append((pid, err))
                    continue
                scored[pid] = sc
                jf.write(json.dumps({'id': pid, 'game': gid, 'score': sc,
                                     'at': int(time.time())}) + '\n')
                if i % 250 == 0:
                    jf.flush()
                    os.fsync(jf.fileno())
                    if not quiet:
                        rate = i / max(1e-9, time.time() - t0)
                        print('   ...%d/%d measured (%.0f/s)'
                              % (i, len(jobs), rate))
        jf.flush()
        os.fsync(jf.fileno())
    return scored, errors


def histogram(d, only=None):
    h = {}
    for g in d['games']:
        if only and g['id'] != only:
            continue
        for p in g['photos']:
            k = p.get('tier') or ('gated' if p.get('gated')
                                  else ('below floor' if float(p.get('score', 0) or 0)
                                        else 'UNMEASURED'))
            h[k] = h.get(k, 0) + 1
    return h


def print_hist(label, h):
    order = ['A+', 'A', 'B+', 'below floor', 'gated', 'UNMEASURED']
    keys = [k for k in order if k in h] + [k for k in h if k not in order]
    print('%s: %s' % (label, '  '.join('%s=%s' % (k, format(h[k], ',')) for k in keys)))


# ------------------------------------------------------------------- write
def apply_scores(d, scores):
    """Write score/tier/gated onto records and re-sort every touched tab."""
    touched, written = set(), 0
    for g in d['games']:
        hit = False
        for p in g['photos']:
            if p['id'] in scores:
                sc = scores[p['id']]
                p['score'] = sc
                p['tier'] = tier_for(sc)
                # `gated` distinguishes a measured 0.0 from absent data. Set it
                # only where true, and REMOVE it if a re-measure came back
                # above 0 -- a key that survives its own truth is the trap this
                # file exists to close.
                if sc == 0.0:
                    p['gated'] = 1
                else:
                    p.pop('gated', None)
                hit = True
                written += 1
        if hit:
            touched.add(g['id'])
            g['photos'].sort(key=lambda p: (-float(p.get('score', 0) or 0), p['id']))
    return touched, written


def update_manifest(scores):
    if not os.path.exists(MANIFEST):
        print('   (no manifest on this machine; skipped)')
        return 0
    with open(MANIFEST, newline='', encoding='utf-8') as f:
        rd = csv.DictReader(f)
        fields, rows = rd.fieldnames, list(rd)
    if not fields:
        sys.exit('FAIL: manifest has no header row')
    n = 0
    for row in rows:
        if row['id'] in scores:
            row['score'] = str(scores[row['id']])
            row['tier'] = tier_for(scores[row['id']])
            n += 1
    tmp = MANIFEST + '.tmp'
    with open(tmp, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, MANIFEST)
    return n


def postconditions(only=None):
    """Re-read from disk and assert what we claim to have produced."""
    d = load_data_js()
    bad_vocab, unmeasured, unsorted_tabs, bad_gated = [], [], [], []
    for g in d['games']:
        if only and g['id'] != only:
            continue
        sc = [float(p.get('score', 0) or 0) for p in g['photos']]
        if any(sc[i] < sc[i + 1] for i in range(len(sc) - 1)):
            unsorted_tabs.append(g['id'])
        for p in g['photos']:
            if p.get('tier') not in TIERS + ('',):
                bad_vocab.append(p['id'])
            if unscored(p):
                unmeasured.append(p['id'])
            if p.get('gated') and float(p.get('score', 0) or 0):
                bad_gated.append(p['id'])
    ok = True
    for label, bad in (('tier vocabulary is {A+,A,B+,""}', bad_vocab),
                       ('every tab sorted best-first', unsorted_tabs),
                       ('no `gated` key on a frame with a score', bad_gated)):
        print('   %-42s %s' % (label, 'OK' if not bad else 'FAIL %s' % bad[:5]))
        ok &= not bad
    print('   %-42s %s' % ('frames still unmeasured',
                           format(len(unmeasured), ',')))
    return ok, unmeasured


# -------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--game-id', help='restrict to one gallery')
    ap.add_argument('--limit', type=int, default=0, help='measure at most N')
    ap.add_argument('--procs', type=int,
                    default=max(1, (os.cpu_count() or 2) - 2))
    ap.add_argument('--fresh', action='store_true',
                    help='ignore the journal and re-measure from scratch')
    a = ap.parse_args()
    if not (a.apply or a.dry_run):
        ap.error('pick --dry-run or --apply')

    d = load_data_js()
    print_hist('BEFORE', histogram(d, a.game_id))

    jobs, unreachable = resolve_originals(d, a.game_id)
    print('unmeasured frames: %s reachable, %s with no original on this machine'
          % (format(len(jobs), ','), format(len(unreachable), ',')))
    if unreachable:
        print('   NOT gradable here (first 5): %s'
              % [p for p, _ in unreachable[:5]])

    done = {} if a.fresh else read_journal()
    if done:
        print('journal holds %s prior measurements at %s'
              % (format(len(done), ','), os.path.relpath(JOURNAL, REPO)))
    todo = [j for j in jobs if j[0] not in done]
    if a.limit:
        todo = todo[:a.limit]

    submitted = len(todo)
    print('submitting %s frames on %d processes' % (format(submitted, ','), a.procs))

    if not a.apply:
        print('\nDRY RUN — pass --apply to measure and write')
        return 0

    # The metric must still reproduce the season's published scale, or these
    # grades would be on a different scale than the 7,196 already published.
    if not verify():
        sys.exit('REFUSING to write: the metric no longer reproduces the '
                 'season scale.')

    t0 = time.time()
    fresh, errors = run_pass(todo, a.procs) if todo else ({}, [])
    if todo:
        print('measured %s frames in %.1fs'
              % (format(len(fresh), ','), time.time() - t0))
    for pid, err in errors[:10]:
        print('   ERROR %s: %s' % (pid, err))

    # Everything the journal knows that is in scope for this run.
    journal = read_journal()
    scores = {pid: journal[pid] for pid, _, _ in jobs if pid in journal}

    if scores:
        d = load_data_js()          # re-read: a concurrent publish may have run
        touched, written = apply_scores(d, scores)
        bak = backup_data_js('grade-backfill-bak')
        save_data_js(d)
        mrows = update_manifest(scores)
        print('wrote %s frames across %d tabs (manifest rows %s, backup %s)'
              % (format(written, ','), len(touched), format(mrows, ','),
                 os.path.basename(bak)))
        print_hist('AFTER ', histogram(load_data_js(), a.game_id))
        gated = sum(1 for v in scores.values() if v == 0.0)
        print('of the %s newly measured: %s gated to 0.0 by the quality floor '
              '(mean<40 or contrast<12), %s scored above it'
              % (format(len(scores), ','), format(gated, ','),
                 format(len(scores) - gated, ',')))
        keepers = sum(1 for v in scores.values() if v >= FLOOR)
        print('%s of them are keepers (>=%.0f) that the hub was hiding from the '
              'tier filter' % (format(keepers, ','), FLOOR))

    ok, unmeasured = postconditions(a.game_id)

    # THE GATE. A pass that measured fewer frames than it submitted is PARTIAL,
    # and must not be able to exit 0 -- that is exactly how 3,096 frames came to
    # sit at score 0 while a comment in index.html explained them away.
    if len(fresh) != submitted:
        print('\nPARTIAL: submitted %s, measured %s (%s errors). The journal at '
              '%s holds the completed work -- re-run to resume.'
              % (format(submitted, ','), format(len(fresh), ','),
                 format(len(errors), ','), os.path.relpath(JOURNAL, REPO)))
        return 1
    if not ok:
        print('\nFAIL: postconditions did not hold.')
        return 1
    if unmeasured and not a.limit:
        print('\nINCOMPLETE: %s frames still carry no measurement (no original '
              'reachable on this machine).' % format(len(unmeasured), ','))
        return 1
    print('\nOK: submitted == measured == %s; every in-scope frame carries a '
          'real measurement.' % format(submitted, ','))
    return 0


if __name__ == '__main__':
    sys.exit(main())
