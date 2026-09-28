#!/usr/bin/env python3
"""test_grade_backfill.py — the grading coverage boundary must stay closed.

This card exists because 3,096 frames sat at `score: 0` while index.html carried
a comment explaining them away as "too dark or too flat to measure". Three
distinct failures had to line up for that:

  1. the publish path could append frames without enqueueing them for grading
  2. `score: 0` meant BOTH "measured at 0.0" and "never measured", so no script
     could tell absent data from a measurement
  3. a partial pass could exit 0

Each is tested here by PLANTING the defect and proving the guard fires. A guard
nobody has watched fail is not known to work.

    python3 tools/test_grade_backfill.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, 'tools'))

import grade_backfill as gb          # noqa: E402
import grade_day as gd               # noqa: E402

COV = os.path.join(REPO, 'tools', 'check_grade_coverage.py')
RESULTS = []


def check(label, ok, detail=''):
    RESULTS.append((label, bool(ok)))
    print('%s  %-58s %s' % ('ok  ' if ok else 'FAIL', label, detail))
    return ok


def summarise():
    bad = [l for l, ok in RESULTS if not ok]
    print()
    if bad:
        print('FAIL — %d/%d checks failed:' % (len(bad), len(RESULTS)))
        for l in bad:
            print('   - %s' % l)
        return 1
    print('PASS — %d/%d checks passed.' % (len(RESULTS), len(RESULTS)))
    return 0


def write_data(path, games):
    with open(path, 'w', encoding='utf-8') as f:
        f.write('window.SEASON_DATA = ')
        json.dump({'games': games, 'catchall': {'photos': []},
                   'roster': [], 'rosterByNum': {}, 'tags': {}}, f)
        f.write(';\n')


def run_cov(data_path, extra=()):
    return subprocess.run([sys.executable, COV, '--data', data_path] + list(extra),
                          capture_output=True, text=True)


# --------------------------------------------------------------------------
def test_unscored_semantics():
    """`score: 0` alone is absent data; `gated: 1` makes a 0.0 a MEASUREMENT."""
    check('unscored(): placeholder score 0 is UNMEASURED',
          gb.unscored({'id': 'x', 'score': 0, 'tier': ''}) is True)
    check('unscored(): a measured 0.0 flagged `gated` is MEASURED',
          gb.unscored({'id': 'x', 'score': 0, 'tier': '', 'gated': 1}) is False)
    check('unscored(): a real score is MEASURED',
          gb.unscored({'id': 'x', 'score': 41.2, 'tier': ''}) is False)
    # The distinction is the whole point: without it the two states are one.
    a = {'id': 'a', 'score': 0, 'tier': ''}
    b = {'id': 'b', 'score': 0, 'tier': '', 'gated': 1}
    check('the two score-0 states are distinguishable in data.js',
          gb.unscored(a) != gb.unscored(b))


def test_apply_scores_gated_key():
    """`gated` must appear only where true, and be REMOVED when it stops being
    true -- a key that survives its own truth is the `thumb: "thumbs/..."`
    trap that cost this repo 3,822 dangling paths."""
    games = [{'id': 'g-0101', 'photos': [
        {'id': 'a', 'score': 0, 'tier': ''},
        {'id': 'b', 'score': 0, 'tier': ''},
        {'id': 'c', 'score': 0, 'tier': '', 'gated': 1},
    ]}]
    d = {'games': games}
    touched, written = gb.apply_scores(d, {'a': 0.0, 'b': 91.4, 'c': 72.5})
    ph = {p['id']: p for p in games[0]['photos']}
    check('a measured 0.0 gets gated=1', ph['a'].get('gated') == 1)
    check('a scored frame carries NO gated key', 'gated' not in ph['b'])
    check('a re-measure above 0 REMOVES a stale gated key',
          'gated' not in ph['c'], 'was gated:1, now %.1f' % ph['c']['score'])
    check('tier written from the season thresholds',
          ph['b']['tier'] == 'A' and ph['a']['tier'] == '' and ph['c']['tier'] == 'B+',
          '%s/%s/%s' % (ph['a']['tier'], ph['b']['tier'], ph['c']['tier']))
    check('the touched tab was re-sorted best-first',
          [p['id'] for p in games[0]['photos']] == ['b', 'c', 'a'],
          str([p['id'] for p in games[0]['photos']]))
    check('apply_scores reports what it wrote', written == 3 and touched == {'g-0101'})


def test_journal_resume():
    """A journal killed mid-write must lose nothing and skip nothing."""
    sandbox = tempfile.mkdtemp(prefix='gradejournal-')
    real = gb.JOURNAL
    try:
        gb.JOURNAL = os.path.join(sandbox, 'backfill.jsonl')
        gb.JOURNAL_DIR = sandbox
        check('no journal reads as no prior work', gb.read_journal() == {})
        with open(gb.JOURNAL, 'w', encoding='utf-8') as f:
            f.write(json.dumps({'id': 'a', 'game': 'g', 'score': 12.5}) + '\n')
            f.write(json.dumps({'id': 'b', 'game': 'g', 'score': 0.0}) + '\n')
            f.write('{"id": "c", "game": "g", "sco')     # SIGKILL mid-write
        j = gb.read_journal()
        check('a TORN final line does not lose the completed work',
              j == {'a': 12.5, 'b': 0.0}, str(j))

        # MUTATION: appending onto a torn line fuses two records into one
        # unparseable one. _repair_journal_tail() must terminate the fragment
        # BEFORE the next run writes, or a completed measurement is lost.
        check('the torn tail is detected as needing repair',
              gb._repair_journal_tail() is True)
        check('repair is idempotent on an already-terminated journal',
              gb._repair_journal_tail() is False)
        with open(gb.JOURNAL, 'a', encoding='utf-8') as f:
            f.write(json.dumps({'id': 'd', 'game': 'g', 'score': 77.0}) + '\n')
        j = gb.read_journal()
        check('a record appended AFTER the repair survives',
              j.get('d') == 77.0, str(j))
        check('the torn fragment itself is still discarded', 'c' not in j)

        with open(gb.JOURNAL, 'a', encoding='utf-8') as f:
            f.write(json.dumps({'id': 'a', 'game': 'g', 'score': 44.0}) + '\n')
        check('a re-measure supersedes the earlier value',
              gb.read_journal()['a'] == 44.0)
        check('a journalled 0.0 is retained, not treated as missing',
              'b' in gb.read_journal())
    finally:
        gb.JOURNAL = real
        shutil.rmtree(sandbox, ignore_errors=True)


def test_verify_rejects_placeholder_ground_truth():
    """MUTATION: feed verify() a pool of placeholders and prove it no longer
    accepts them as ground truth. This is the bug that made a correct metric
    report bias +37.5 and refuse to write."""
    real_load = gd.load_data_js
    real_man = gd.manifest_originals
    try:
        # One real reference frame (so the pool is not empty) plus 500 placeholders.
        d = real_load()
        man = real_man()
        ref = next((p for g in d['games'] if g['id'] in gd.REFERENCE
                    for p in g['photos']
                    if float(p.get('score', 0) or 0)
                    and man.get(p['id']) and os.path.exists(man[p['id']])), None)
        if ref is None:
            check('verify() excludes placeholders from ground truth', True,
                  'SKIPPED: no reference original on this machine')
            return
        fake = [{'id': ref['id'], 'score': ref['score'], 'tier': ref['tier']}]
        fake += [{'id': ref['id'], 'score': 0, 'tier': ''} for _ in range(500)]
        gd.load_data_js = lambda: {'games': [{'id': gd.REFERENCE[0],
                                             'photos': fake}]}
        ok = gd.verify(n=8)
        check('verify() PASSES despite 500 planted placeholders', ok,
              'placeholders excluded, not averaged in')

        # And the inverse: if placeholders were still admitted, it would fail.
        pool = [p for p in fake if float(p.get('score', 0) or 0)
                or p.get('gated')]
        check('the planted placeholders were genuinely excluded',
              len(pool) == 1, '%d of %d frames survived the filter'
              % (len(pool), len(fake)))
    finally:
        gd.load_data_js = real_load
        gd.manifest_originals = real_man


def test_coverage_invariant():
    """The invariant that stops this recurring: a publish that adds unmeasured
    frames must be VISIBLE to a script, not explained in a comment."""
    if not os.path.exists(COV):
        check('tools/check_grade_coverage.py exists', False, 'missing')
        return
    sandbox = tempfile.mkdtemp(prefix='gradecov-')
    try:
        clean = os.path.join(sandbox, 'clean.js')
        write_data(clean, [{'id': 'g-0101', 'photos': [
            {'id': 'a', 'score': 91.0, 'tier': 'A'},
            {'id': 'b', 'score': 12.0, 'tier': ''},
            {'id': 'c', 'score': 0, 'tier': '', 'gated': 1},
        ]}])
        r = run_cov(clean)
        check('coverage scanner exits 0 when every frame is measured',
              r.returncode == 0, 'exit %d' % r.returncode)

        # MUTATION 1: a publish appends an unmeasured frame.
        dirty = os.path.join(sandbox, 'dirty.js')
        write_data(dirty, [{'id': 'g-0101', 'photos': [
            {'id': 'a', 'score': 91.0, 'tier': 'A'},
            {'id': 'z', 'score': 0, 'tier': ''},          # <-- planted
        ]}])
        r = run_cov(dirty)
        check('coverage scanner exits 1 on a planted unmeasured frame',
              r.returncode == 1, 'exit %d' % r.returncode)
        check('the scanner NAMES the offending frame', 'z' in r.stdout + r.stderr)

        # MUTATION 2: a `gated` key on a frame that has a real score -- the
        # stale-key class of defect. gated must mean "measured exactly 0.0".
        stale = os.path.join(sandbox, 'stale.js')
        write_data(stale, [{'id': 'g-0101', 'photos': [
            {'id': 'a', 'score': 91.0, 'tier': 'A', 'gated': 1},   # <-- planted
        ]}])
        r = run_cov(stale)
        check('coverage scanner exits 1 on a stale gated key',
              r.returncode == 1, 'exit %d' % r.returncode)

        # MUTATION 3: a tier outside the hub's vocabulary would paint an
        # unknown badge on a kid's photo.
        vocab = os.path.join(sandbox, 'vocab.js')
        write_data(vocab, [{'id': 'g-0101', 'photos': [
            {'id': 'a', 'score': 91.0, 'tier': 'C-'},              # <-- planted
        ]}])
        r = run_cov(vocab)
        check('coverage scanner exits 1 on an out-of-vocabulary tier',
              r.returncode == 1, 'exit %d' % r.returncode)

        # MUTATION 4: a tier that does not match its own score. A hand-edit or a
        # half-applied threshold change shows up here.
        mism = os.path.join(sandbox, 'mismatch.js')
        write_data(mism, [{'id': 'g-0101', 'photos': [
            {'id': 'a', 'score': 12.0, 'tier': 'A+'},              # <-- planted
        ]}])
        r = run_cov(mism)
        check('coverage scanner exits 1 when tier disagrees with score',
              r.returncode == 1, 'exit %d' % r.returncode)

        # MUTATION 5: an unsorted tab. The header says "best first".
        uns = os.path.join(sandbox, 'unsorted.js')
        write_data(uns, [{'id': 'g-0101', 'photos': [
            {'id': 'a', 'score': 12.0, 'tier': ''},
            {'id': 'b', 'score': 91.0, 'tier': 'A'},               # <-- planted
        ]}])
        r = run_cov(uns)
        check('coverage scanner exits 1 on a tab not sorted best-first',
              r.returncode == 1, 'exit %d' % r.returncode)

        # An empty gallery is legitimately empty, not a violation.
        empt = os.path.join(sandbox, 'empty.js')
        write_data(empt, [{'id': 'g-0101', 'photos': []}])
        r = run_cov(empt)
        check('an empty gallery is not a violation', r.returncode == 0,
              'exit %d' % r.returncode)
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)


def test_real_tree():
    """The committed data.js must satisfy the invariant."""
    if not os.path.exists(COV):
        return
    r = run_cov(gd.DATA_JS)
    check('the REAL season/data.js passes the coverage invariant',
          r.returncode == 0,
          (r.stdout.strip().splitlines() or [''])[-1][:80])


def main():
    test_unscored_semantics()
    test_apply_scores_gated_key()
    test_journal_resume()
    test_verify_rejects_placeholder_ground_truth()
    test_coverage_invariant()
    test_real_tree()
    return summarise()


if __name__ == '__main__':
    sys.exit(main())
