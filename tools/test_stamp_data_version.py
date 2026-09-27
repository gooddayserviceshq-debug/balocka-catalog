#!/usr/bin/env python3
"""Guards for the data.js cache-busting stamp.

The bug this protects against was observed live: GitHub Pages serves data.js
with `cache-control: max-age=600`, so with a bare <script src="data.js"> a
returning visitor executed a 1,209,980-byte cached body while the server was
serving 1,222,168 -- tier filters greyed out and "not graded yet" on a gallery
that had already been graded and deployed.

Run: python3 tools/test_stamp_data_version.py
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

TOOLS = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(TOOLS)
sys.path.insert(0, TOOLS)

import stamp_data_version as S  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    print('%-58s %s%s' % (name, 'PASS' if cond else 'FAIL',
                          '' if cond else '  <- ' + detail))


def with_fixture(html, data, fn):
    """Run fn() against a temp repo layout, restoring module globals after."""
    d = tempfile.mkdtemp(prefix='stamp-test-')
    try:
        season = os.path.join(d, 'season')
        os.makedirs(season)
        hp = os.path.join(season, 'index.html')
        dp = os.path.join(season, 'data.js')
        open(hp, 'w').write(html)
        open(dp, 'w').write(data)
        oh, od = S.HTML, S.DATA
        S.HTML, S.DATA = hp, dp
        try:
            return fn(hp, dp)
        finally:
            S.HTML, S.DATA = oh, od
    finally:
        shutil.rmtree(d, ignore_errors=True)


TAG = '<script src="data.js"></script>'
PAGE = '<html><body>x' + TAG + '</body></html>'


# 1. a bare tag gets stamped
def t1(hp, dp):
    S.stamp(quiet=True)
    out = open(hp).read()
    return re.search(r'<script src="data\.js\?v=[0-9a-f]{8}"></script>', out)


check('bare tag gains a content-hash version', bool(with_fixture(PAGE, 'A', t1)))


# 2. same data.js => same version (families keep their cached copy)
def t2(hp, dp):
    v1 = S.stamp(quiet=True)
    v2 = S.stamp(quiet=True)
    return v1 == v2


check('unchanged data.js keeps the same version', with_fixture(PAGE, 'A', t2))


# 3. changed data.js => new version (the actual bug)
def t3(hp, dp):
    v1 = S.stamp(quiet=True)
    open(dp, 'w').write('A DIFFERENT CATALOG')
    v2 = S.stamp(quiet=True)
    return v1 != v2 and ('?v=' + v2) in open(hp).read()


check('changed data.js forces a new version', with_fixture(PAGE, 'A', t3))


# 4. re-stamping replaces, never appends a second query string
def t4(hp, dp):
    S.stamp(quiet=True)
    open(dp, 'w').write('B')
    S.stamp(quiet=True)
    out = open(hp).read()
    return out.count('<script src="data.js') == 1 and '?v=' in out \
        and '?v=' not in out.split('?v=', 1)[1]


check('re-stamp replaces the version, no double query', with_fixture(PAGE, 'A', t4))


# 5. nothing else in the page is touched
def t5(hp, dp):
    before = open(hp).read()
    S.stamp(quiet=True)
    after = open(hp).read()
    return before.replace(TAG, '') == re.sub(
        r'<script src="data\.js\?v=[0-9a-f]{8}"></script>', '', after)


check('no collateral edits to index.html', with_fixture(PAGE, 'A', t5))


# 6. refuses an ambiguous page rather than guessing
def t6(hp, dp):
    try:
        S.stamp(quiet=True)
        return False
    except SystemExit:
        return True


check('refuses a page with two data.js tags',
      with_fixture('<html>' + TAG + TAG + '</html>', 'A', t6))


# 7. the real repo is currently stamped (the deployed fix stays in place)
real = open(os.path.join(REPO, 'season', 'index.html'), encoding='utf-8').read()
check('live index.html carries a versioned data.js tag',
      bool(re.search(r'<script src="data\.js\?v=[0-9a-f]{8}"></script>', real)))

# 8. and that version matches the real data.js content
real_ver = S.short_hash(os.path.join(REPO, 'season', 'data.js'))
check('live version matches data.js content hash',
      ('data.js?v=%s' % real_ver) in real,
      'run python3 tools/stamp_data_version.py')

# 9. every tool that writes data.js re-stamps
for tool in ('publish_day.py', 'grade_day.py', 'merge_player_tags.py'):
    src = open(os.path.join(TOOLS, tool), encoding='utf-8').read()
    check('%s re-stamps after writing data.js' % tool,
          'from stamp_data_version import stamp' in src)

# 10. the CLI is idempotent end to end
r1 = subprocess.run([sys.executable, os.path.join(TOOLS, 'stamp_data_version.py')],
                    capture_output=True, text=True)
r2 = subprocess.run([sys.executable, os.path.join(TOOLS, 'stamp_data_version.py')],
                    capture_output=True, text=True)
check('CLI is idempotent and reports unchanged',
      r1.returncode == 0 and r2.returncode == 0 and 'unchanged' in r2.stdout,
      r2.stdout.strip() + r2.stderr.strip())

print('\n%s — %d/%d checks passed'
      % ('PASS' if not FAIL else 'FAIL', len(PASS), len(PASS) + len(FAIL)))
sys.exit(1 if FAIL else 0)
