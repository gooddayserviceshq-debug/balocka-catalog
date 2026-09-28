#!/usr/bin/env python3
"""check_grade_coverage.py — assert every published frame carries a real grade.

WHY THIS EXISTS
---------------
3,096 frames reached the live hub at `score: 0, tier: ""` because the publish
path (publish_day.py / extend_day.py) can append frames without enqueueing them
for grading. Nothing checked, so nothing failed -- and the gap was then
explained away by a comment in index.html asserting score 0 meant the metric had
gated the frame out. It had not: those frames were measured for the first time
on 09-28 and 3 in 5 tabs' score-0 population was BRIGHTER than the graded one.

The lesson from the season/tn tier is that the guard belongs in the one place
every path must pass, and it must RAISE rather than warn. For grading there is
no single funnel (two publishers, one grader), so the invariant lives here and is
checked against the artifact itself: data.js.

WHAT IT ENFORCES
----------------
1. every frame carries a measurement -- score > 0, or score 0 flagged `gated`
2. `gated` appears ONLY on a frame measured at exactly 0.0 (a key that outlives
   its own truth is how `thumb: "thumbs/..."` came to dangle on 3,822 records)
3. tier vocabulary is exactly {A+, A, B+, ""} -- the hub has no other badge
4. tier agrees with score at the season thresholds (100 / 80 / 60)
5. every tab is sorted best-first, because the header promises it and the hub
   does no client-side sort

Exit 0 clean, 1 with every violation named. Run it after any publish or grade,
and before any push.

    python3 tools/check_grade_coverage.py
    python3 tools/check_grade_coverage.py --data /path/to/data.js --quiet
"""
import argparse
import json
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DATA = os.path.join(REPO, 'season', 'data.js')
TIERS = ('A+', 'A', 'B+')
FLOOR = 60.0
SHOW = 8


def load(path):
    raw = open(path, encoding='utf-8').read()
    m = re.match(r'\s*window\.SEASON_DATA\s*=\s*', raw)
    if not m:
        sys.exit('FAIL: unexpected data.js header in %s' % path)
    return json.loads(raw[m.end():].strip().rstrip(';'))


def tier_for(score):
    if score >= 100:
        return 'A+'
    if score >= 80:
        return 'A'
    if score >= FLOOR:
        return 'B+'
    return ''


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--data', default=DEFAULT_DATA)
    ap.add_argument('--quiet', action='store_true')
    a = ap.parse_args()

    d = load(a.data)
    unmeasured, stale_gated, bad_vocab, mismatch, unsorted_tabs = [], [], [], [], []
    total = measured = gated_n = 0
    per_tab = []

    for g in d.get('games', []):
        photos = g.get('photos', [])
        tab_unmeasured = 0
        scores = []
        for p in photos:
            total += 1
            sc = float(p.get('score', 0) or 0)
            tier = p.get('tier', '')
            scores.append(sc)
            is_gated = bool(p.get('gated'))

            if not sc and not is_gated:
                unmeasured.append((g['id'], p['id']))
                tab_unmeasured += 1
            else:
                measured += 1
            if is_gated:
                gated_n += 1
                if sc:
                    stale_gated.append((g['id'], p['id'], sc))
            if tier not in TIERS + ('',):
                bad_vocab.append((g['id'], p['id'], tier))
            elif tier != tier_for(sc):
                mismatch.append((g['id'], p['id'], sc, tier))
        if any(scores[i] < scores[i + 1] for i in range(len(scores) - 1)):
            unsorted_tabs.append(g['id'])
        per_tab.append((g['id'], len(photos), tab_unmeasured))

    if not a.quiet:
        print('%-22s %8s %11s' % ('tab', 'frames', 'unmeasured'))
        for gid, n, u in per_tab:
            print('%-22s %8s %11s%s' % (gid, format(n, ','), format(u, ','),
                                        '   <-- VIOLATION' if u else ''))
        print('-' * 44)
        print('%-22s %8s %11s' % ('TOTAL', format(total, ','),
                                  format(len(unmeasured), ',')))
        print('measured %s of %s  (%s gated to 0.0 by the quality floor)'
              % (format(measured, ','), format(total, ','), format(gated_n, ',')))

    problems = []
    if unmeasured:
        problems.append(
            '%s frames carry NO measurement (score 0 with no `gated` flag). A '
            'publish added them without enqueueing them for grading. Run '
            '`python3 tools/grade_backfill.py --apply`.\n   e.g. %s'
            % (format(len(unmeasured), ','),
               ', '.join('%s/%s' % (g, p) for g, p in unmeasured[:SHOW])))
    if stale_gated:
        problems.append(
            '%d frames carry a `gated` flag AND a nonzero score. `gated` must '
            'mean "measured at exactly 0.0"; a key that outlives its truth is a '
            'live trap.\n   e.g. %s'
            % (len(stale_gated),
               ', '.join('%s/%s=%.1f' % t for t in stale_gated[:SHOW])))
    if bad_vocab:
        problems.append(
            '%d frames carry a tier outside {A+, A, B+, ""} -- the hub would '
            'paint an unknown badge.\n   e.g. %s'
            % (len(bad_vocab),
               ', '.join('%s/%s=%r' % t for t in bad_vocab[:SHOW])))
    if mismatch:
        problems.append(
            '%d frames have a tier that disagrees with their own score at the '
            'season thresholds (100/80/%.0f).\n   e.g. %s'
            % (len(mismatch), FLOOR,
               ', '.join('%s/%s score=%.1f tier=%r' % t for t in mismatch[:SHOW])))
    if unsorted_tabs:
        problems.append(
            '%d tabs are not sorted best-first, but the gallery header promises '
            'it and the hub does no client-side sort: %s'
            % (len(unsorted_tabs), ', '.join(unsorted_tabs[:SHOW])))

    if problems:
        print()
        print('FAIL — grade coverage invariant violated:')
        for i, t in enumerate(problems, 1):
            print(' %d. %s' % (i, t))
        return 1
    if not a.quiet:
        print('\nOK — every published frame carries a real measurement, tiers '
              'match their scores, and every tab is sorted best-first.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
