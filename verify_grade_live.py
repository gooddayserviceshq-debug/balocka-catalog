#!/usr/bin/env python3
"""verify_grade_live.py — gate the GRADING claim against the SERVED site.

A pushed commit is not a published gallery. GitHub Pages serves the PREVIOUS
build until its own build finishes, so `git push` plus "the grades are live" is
a lie the next visitor discovers for you. And this card's whole cost was a
rendering behaviour, so a repo-side check cannot close it: the question is what
the served data.js and index.html say.

Gates, all against the live origin:
  1. local HEAD == origin HEAD and the gallery paths are clean
  2. the Pages build reports `built` AND its commit == local HEAD
  3. the SERVED data.js parses and has ZERO unmeasured frames
  4. the served data.js is byte-identical to the local one (catches a stale page
     even when the API looks happy)
  5. the SERVED index.html no longer asserts score 0 means "too dark", and its
     isGraded() tests the frame
  6. the served index.html references a data.js?v= that actually resolves 200
     (a stale version query serves the old grades for the cache lifetime)
  7. a sample of newly-promoted keeper tiles HEADs 200 + image/*

Exit 0 only when every gate passes.

    python3 verify_grade_live.py
"""
import concurrent.futures as cf
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request

REPO = os.path.dirname(os.path.abspath(__file__))
OWNER_REPO = 'gooddayserviceshq-debug/balocka-catalog'
# The SERVED origin. Not balocka.com -- that host does not resolve; the live
# site is the Pages origin, which is what verify_tn_live.py gates too.
BASE = 'https://gooddayserviceshq-debug.github.io/balocka-catalog/season/'
UA = {'User-Agent': 'balocka-verify/1.0'}
RESULTS = []


def gate(label, ok, detail=''):
    RESULTS.append((label, bool(ok)))
    print('%s  %-56s %s' % ('PASS' if ok else 'FAIL', label, detail))
    return ok


def sh(*args):
    return subprocess.run(args, capture_output=True, text=True,
                          cwd=REPO).stdout.strip()


def fetch(url, tries=3):
    """GET with bounded retry. A verifier that cries wolf on a transient rate
    limit gets ignored, but 404/410 return immediately so a real absence still
    fails fast."""
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, r.read(), dict(r.headers)
        except urllib.error.HTTPError as e:
            if e.code in (404, 410):
                return e.code, b'', {}
            last = e
        except Exception as e:                       # noqa: BLE001
            last = e
        import time
        time.sleep(1.5 * (i + 1))
    return 0, b'', {'error': str(last)}


def head(url, tries=3):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA, method='HEAD')
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, r.headers.get('Content-Type', '')
        except urllib.error.HTTPError as e:
            if e.code in (404, 410):
                return e.code, ''
            last = e
        except Exception as e:                       # noqa: BLE001
            last = e
        import time
        time.sleep(1.5 * (i + 1))
    return 0, str(last)


def parse_data(raw):
    m = re.match(r'\s*window\.SEASON_DATA\s*=\s*', raw)
    if not m:
        return None
    return json.loads(raw[m.end():].strip().rstrip(';'))


def main():
    # ---- gate 1: the tree and the remote agree ---------------------------
    local = sh('git', 'rev-parse', 'HEAD')
    sh('git', 'fetch', 'origin', '-q')
    remote = sh('git', 'rev-parse', 'origin/main')
    gate('local HEAD == origin/main', local == remote and bool(local),
         local[:8] + ' / ' + remote[:8])
    dirty = sh('git', 'status', '--porcelain', 'season/', 'tools/')
    gate('season/ and tools/ have no uncommitted changes', not dirty,
         dirty.splitlines()[0] if dirty else '')

    # ---- gate 2: Pages built THIS commit --------------------------------
    api = sh('gh', 'api', 'repos/%s/pages/builds/latest' % OWNER_REPO,
             '--jq', '{status:.status,commit:.commit}')
    try:
        b = json.loads(api) if api else {}
    except ValueError:
        b = {}
    gate('Pages build status == built', b.get('status') == 'built',
         str(b.get('status')))
    gate('Pages build commit == local HEAD', b.get('commit') == local,
         (b.get('commit') or '?')[:8])

    # ---- gate 3+4: the SERVED data.js ------------------------------------
    st, body, _ = fetch(BASE + 'data.js')
    gate('served data.js returns 200', st == 200, 'HTTP %s' % st)
    d = parse_data(body.decode('utf-8', 'replace')) if st == 200 else None
    gate('served data.js parses', d is not None)

    if d:
        unmeasured, total, gated = [], 0, 0
        per_tab = []
        for g in d.get('games', []):
            u = 0
            for p in g['photos']:
                total += 1
                sc = float(p.get('score', 0) or 0)
                if p.get('gated'):
                    gated += 1
                elif not sc:
                    unmeasured.append((g['id'], p['id']))
                    u += 1
            per_tab.append((g['id'], len(g['photos']), u))
        gate('ZERO unmeasured frames on the LIVE site', not unmeasured,
             '%s of %s  (%s gated by the quality floor)'
             % (format(len(unmeasured), ','), format(total, ','),
                format(gated, ',')))
        if unmeasured:
            for gid, pid in unmeasured[:6]:
                print('        %s/%s' % (gid, pid))

        localraw = open(os.path.join(REPO, 'season', 'data.js'),
                        encoding='utf-8').read()
        gate('served data.js is byte-identical to the local one',
             localraw.encode('utf-8') == body,
             '%s vs %s bytes' % (format(len(body), ','),
                                 format(len(localraw.encode()), ',')))
        print()
        print('        %-22s %8s %11s' % ('tab', 'frames', 'unmeasured'))
        for gid, n, u in per_tab:
            print('        %-22s %8s %11s' % (gid, format(n, ','), u))
        print()

    # ---- gate 5+6: the SERVED index.html --------------------------------
    st2, body2, _ = fetch(BASE)
    html = body2.decode('utf-8', 'replace') if st2 == 200 else ''
    gate('served index.html returns 200', st2 == 200, 'HTTP %s' % st2)
    if html:
        # The false claim may survive ONLY inside the note explaining it was false.
        stripped = re.sub(r'that stood here claimed[\s\S]*?had measured them\.',
                          '', html)
        gate('the false "too dark or too flat" claim is GONE from the live page',
             'too dark or too flat to measure' not in stripped)
        gate('live isGraded() tests the FRAME, not the gallery',
             bool(re.search(r'function isGraded\(p\)', html)))
        gate('live isGraded() honours the `gated` measurement',
             bool(re.search(r'function isGraded\(p\)[^\n]*p\.gated', html)))
        m = re.search(r'data\.js\?v=([0-9a-f]+)', html)
        gate('live index.html carries a data.js version query', bool(m),
             m.group(1) if m else 'none')
        if m:
            vst, vct = head(BASE + 'data.js?v=' + m.group(1))
            gate('the versioned data.js URL resolves 200', vst == 200,
                 'HTTP %s' % vst)

    # ---- gate 7: newly promoted keeper tiles are actually fetchable ------
    if d:
        promoted = [(float(p['score']), g['id'], p)
                    for g in d.get('games', []) for p in g['photos']
                    if p.get('tier') in ('A+', 'A')]
        # Sort on the score only: a tie would otherwise compare the dicts.
        promoted.sort(key=lambda x: -x[0])
        sample = promoted[:20]
        urls = []
        for _, _, p in sample:
            urls.append(BASE + p['t'])
            if p.get('thumb'):
                urls.append(BASE + p['thumb'])
        bad = []
        with cf.ThreadPoolExecutor(max_workers=8) as ex:
            for u, (s, ct) in zip(urls, ex.map(lambda u: head(u), urls)):
                if s != 200 or not ct.startswith('image/'):
                    bad.append((u, s, ct))
        gate('every sampled keeper tile+thumb HEADs 200 + image/*', not bad,
             '%d URLs, %d bad' % (len(urls), len(bad)))
        for u, s, ct in bad[:5]:
            print('        %s -> %s %s' % (u, s, ct))

    print()
    fails = [l for l, ok in RESULTS if not ok]
    if fails:
        print('DO NOT SEND THE LINK — %d of %d gates failed:'
              % (len(fails), len(RESULTS)))
        for l in fails:
            print('   - %s' % l)
        return 1
    print('ALL %d GATES PASS — the grading is live and verified on the served '
          'site.' % len(RESULTS))
    return 0


if __name__ == '__main__':
    sys.exit(main())
