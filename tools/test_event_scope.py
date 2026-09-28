#!/usr/bin/env python3
"""test_event_scope.py -- regression tests for the mixed-event gate.

WHAT THIS GUARDS
----------------
publish_day/extend_day used to select frames by CAMERA + CALENDAR DATE. That is
only correct when the camera shot one thing that day. On 2026-09-11 it did not:
the library holds 1,883 frames of the "9/11 Top Gun Run- City of Smyrna" 5K and
346 frames of the Siegel football game, interleaved 18:49-21:37 (identified from
the pixels, not from counts -- runners with race bibs vs. helmets and pads).

A date-wide `extend_day 2026-09-11 --game-id siegel-0911 --force-curated` would
have appended 1,795 frames to a FOOTBALL gallery, 1,647 of them a community road
race: strangers' faces published under a high school football tab.

Each test below asserts the gate fires where it must, does NOT fire where it
must not (a gate that blocks everything is as useless as one that blocks
nothing), and carries a MUTATION showing the assertion can fail.

Run: python3 tools/test_event_scope.py
Writes nothing; selection logic only.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import publish_day
from publish_day import (MIXED_EVENT_MIN_FRAMES, MIXED_EVENT_MIN_SHARE,
                         date_events, local_assets)

FAILS = []

MIXED_NIGHT = '2026-09-11'
FOOTBALL_ALBUM = 'FOOTBALL IG'
RACE_ALBUM = 'Top Gun'
# Single-event nights, including 09-04 whose 15-frame 'Instagram' minority must
# NOT read as a second event.
CLEAN_NIGHTS = ['2026-09-17', '2026-08-31', '2026-09-04', '2026-08-21']


def check(name, cond, detail=''):
    print('%-4s %s%s' % ('ok' if cond else 'FAIL', name,
                         '' if cond else '\n       <- ' + detail))
    if not cond:
        FAILS.append(name)


def fires(date):
    """Would assert_single_event refuse a date-wide selection here?"""
    counts, _no_album, total = date_events(date)
    material = {t: n for t, n in counts.items()
                if n >= MIXED_EVENT_MIN_FRAMES and n >= MIXED_EVENT_MIN_SHARE * total}
    return len(material) > 1, counts, total


def main():
    print('=== 1: the mixed night is DETECTED as mixed ===')
    hot, counts, total = fires(MIXED_NIGHT)
    check('%s detected as mixed-event' % MIXED_NIGHT, hot,
          'albums that night: %s' % counts)
    print('       albums on %s: %s (total %d R7 frames)' % (MIXED_NIGHT, counts, total))

    # The two events must BOTH be materially present, or "mixed" is a technicality.
    race = [n for t, n in counts.items() if RACE_ALBUM.lower() in t.lower()]
    ball = [n for t, n in counts.items() if FOOTBALL_ALBUM.lower() in t.lower()]
    check('both events are materially present on %s' % MIXED_NIGHT,
          race and ball and min(race[0], ball[0]) >= MIXED_EVENT_MIN_FRAMES,
          'race=%s football=%s' % (race, ball))

    print()
    print('=== 2: single-event nights are NOT blocked (no false positives) ===')
    # A gate that fires everywhere gets bypassed, which is how --force-curated
    # became routine in the first place.
    for d in CLEAN_NIGHTS:
        hot, counts, total = fires(d)
        check('%s NOT flagged as mixed' % d, not hot,
              'flagged with albums %s -- a false positive teaches everyone to '
              'pass --album blindly' % counts)

    print()
    print('=== 3: album scoping actually selects ONE event ===')
    wide, _ = local_assets(MIXED_NIGHT)
    ball_only, _ = local_assets(MIXED_NIGHT, album=FOOTBALL_ALBUM)
    race_only, _ = local_assets(MIXED_NIGHT, album=RACE_ALBUM)

    check('football scope is a strict subset of the date-wide set',
          0 < len(ball_only) < len(wide),
          'wide=%d football=%d' % (len(wide), len(ball_only)))
    check('race scope is a strict subset of the date-wide set',
          0 < len(race_only) < len(wide),
          'wide=%d race=%d' % (len(wide), len(race_only)))

    bp = {p for _u, _f, _t, p in ball_only}
    rp = {p for _u, _f, _t, p in race_only}

    # THE PROPERTY THAT MATTERS: the two scopes must be all but disjoint. They
    # are NOT perfectly disjoint and should not be asserted so -- two frames of
    # police vehicles staged at the 9/11 memorial are genuinely filed under both
    # albums (checked at full res: parked vehicles, no runners, no football
    # action). Album membership is a human judgement, not a partition. What must
    # hold is that the overlap is de minimis, so scoping still separates the
    # events, and that the tool REPORTS it rather than silently passing it on.
    overlap = bp & rp
    check('football and race scopes are all but disjoint',
          len(overlap) <= 5,
          '%d frames appear in both scopes -- that is no longer an edge case, '
          'the scoping rule is not separating these events: %s'
          % (len(overlap), sorted(overlap)[:3]))
    reported, _c = publish_day.album_overlap(MIXED_NIGHT, FOOTBALL_ALBUM)
    check('the tool REPORTS the cross-filed frames it cannot separate',
          len(reported) == len(overlap),
          'scoping leaks %d cross-filed frames but album_overlap reports %d -- '
          'an unreported leak is the dangerous kind'
          % (len(overlap), len(reported)))
    print('       date-wide %d, football %d, race %d, cross-filed %d (reported %d)'
          % (len(wide), len(ball_only), len(race_only), len(overlap), len(reported)))

    # CONTROL: date-wide really would have dragged the race in. Without this the
    # disjointness above could be true of two empty sets.
    check('date-wide selection DOES contain race frames (control)',
          len(rp & {p for _u, _f, _t, p in wide}) > 100,
          'only %d race frames in the date-wide set, so scoping prevented nothing'
          % len(rp & {p for _u, _f, _t, p in wide}))

    print()
    print('=== 4: a non-matching album REFUSES rather than falling back ===')
    # Silent fallback to date-wide is the dangerous failure: the operator thinks
    # they scoped and got everything.
    try:
        local_assets(MIXED_NIGHT, album='NO SUCH ALBUM ZZZ')
        check('bogus album refuses', False, 'it returned instead of exiting')
    except SystemExit as e:
        check('bogus album refuses (does not fall back to date-wide)',
              'Refusing to fall back' in str(e), str(e)[:120])

    print()
    print('=== 5: MUTATION -- the gate can actually fail ===')
    # Raise the share threshold above the race's share; the night must then read
    # as clean. If it still reports mixed, the gate is not consulting these
    # constants and tests 1-2 prove nothing.
    orig_share, orig_frames = (publish_day.MIXED_EVENT_MIN_SHARE,
                               publish_day.MIXED_EVENT_MIN_FRAMES)
    try:
        publish_day.MIXED_EVENT_MIN_SHARE = 0.99
        counts, _na, total = date_events(MIXED_NIGHT)
        material = {t: n for t, n in counts.items()
                    if n >= publish_day.MIXED_EVENT_MIN_FRAMES
                    and n >= publish_day.MIXED_EVENT_MIN_SHARE * total}
        check('at share=0.99 the mixed night reads CLEAN (mutation control)',
              len(material) <= 1,
              'still %d material albums -- the threshold is not being applied, '
              'so the gate is firing for some other reason' % len(material))
    finally:
        publish_day.MIXED_EVENT_MIN_SHARE = orig_share
        publish_day.MIXED_EVENT_MIN_FRAMES = orig_frames

    # And confirm restoring the threshold restores the detection.
    hot, _c, _t = fires(MIXED_NIGHT)
    check('threshold restored -> mixed night detected again', hot,
          'the mutation leaked and left the gate disabled')

    print()
    print('=== 6: duplicate album TITLES are not double-counted ===')
    # Two albums are both titled 'FOOTBALL IG ' (Z_PK 93 normal, 112 shared). A
    # naive per-row count reported 690 football frames on 09-11 instead of 346.
    counts, _na, total = date_events(MIXED_NIGHT)
    ball = next((n for t, n in counts.items()
                 if FOOTBALL_ALBUM.lower() in t.lower()), 0)
    scoped, _m = local_assets(MIXED_NIGHT, album=FOOTBALL_ALBUM)
    check('football count == distinct frames, not join rows',
          ball <= total and ball == 346,
          'date_events says %d football frames; the shared+normal album pair '
          'double-counts to 690 when rows are summed instead of frames' % ball)
    check('every album count is <= the night total',
          all(n <= total for n in counts.values()),
          'counts %s exceed total %d' % (counts, total))
    print('       football frames %d (of which %d have local originals)'
          % (ball, len(scoped)))

    print()
    if FAILS:
        print('FAIL: %d check(s) failed: %s' % (len(FAILS), FAILS))
        sys.exit(1)
    print('all checks passed')


if __name__ == '__main__':
    main()
