#!/usr/bin/env python3
"""verify_tn_live.py — the gates that matter for the thumb tier, on the SERVED site.

A pushed commit is not a delivered gallery: GitHub Pages serves the PREVIOUS
build until its own build finishes. So nothing here reads the working tree
except to compare against it.

Gates:
  1. Pages build status == built AND commit == local HEAD
  2. the served index.html is byte-identical to the local one
  3. the served data.js is byte-identical to the local one (it carries the
     10,478 rewritten thumb paths; a stale data.js means a stale tier)
  4. the served index really requests tn/ and keeps the tile fallback
  5. a sample of tn URLs return 200 + image/* (threaded)
  6. the SAME sample of t URLs still return 200 -- the lightbox source must
     not have been disturbed by the tier work
  7. Oakland's first 300 thumbs, measured by Content-Length on the live URLs,
     weigh under 5 MB

    python3 verify_tn_live.py            # 120-URL sample
    python3 verify_tn_live.py --full-page   # HEAD all 300 of Oakland's page 1
"""
import argparse
import concurrent.futures as cf
import json
import os
import random
import re
import subprocess
import sys
import time
import urllib.request

REPO = os.path.dirname(os.path.abspath(__file__))
SEASON = os.path.join(REPO, 'season')
BASE = 'https://gooddayserviceshq-debug.github.io/balocka-catalog'
OWNER_REPO = 'gooddayserviceshq-debug/balocka-catalog'

fails = []


def gate(label, ok, detail=''):
    print('%-58s %s%s' % (label, 'OK' if ok else 'FAIL',
                          '  ' + detail if detail else ''))
    if not ok:
        fails.append(label)


def fetch(url, method='GET', timeout=30):
    req = urllib.request.Request(url, method=method)
    req.add_header('Cache-Control', 'no-cache')
    req.add_header('Pragma', 'no-cache')
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read() if method == 'GET' else b''
        return r.status, dict(r.headers), body


def head(url):
    try:
        st, h, _ = fetch(url, 'HEAD', timeout=25)
        return url, st, h.get('Content-Type', ''), int(h.get('Content-Length') or 0)
    except Exception as e:
        return url, 0, 'ERR %s' % type(e).__name__, 0


def local_head():
    return subprocess.run(['git', '-C', REPO, 'rev-parse', 'HEAD'],
                          capture_output=True, text=True).stdout.strip()


def load_local_data():
    raw = open(os.path.join(SEASON, 'data.js'), encoding='utf-8').read()
    m = re.match(r'\s*window\.SEASON_DATA\s*=\s*', raw)
    if not m:
        raise SystemExit('FAIL: unexpected data.js header')
    return json.loads(raw[m.end():].strip().rstrip(';')), raw


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sample', type=int, default=120)
    ap.add_argument('--full-page', action='store_true',
                    help="HEAD all 300 URLs of Oakland's first page")
    ap.add_argument('--wait', type=int, default=420,
                    help='seconds to wait for the Pages build (default 420)')
    a = ap.parse_args()

    want = local_head()
    print('local HEAD: %s\n' % want)

    # ---- gate 1: the build the site is actually serving ------------------
    deadline = time.time() + a.wait
    status = commit = None
    while time.time() < deadline:
        r = subprocess.run(['gh', 'api',
                            'repos/%s/pages/builds/latest' % OWNER_REPO],
                           capture_output=True, text=True)
        if r.returncode == 0:
            j = json.loads(r.stdout)
            status, commit = j.get('status'), j.get('commit')
            if status == 'built' and commit == want:
                break
            if status == 'errored':
                break
        print('  pages build: status=%s commit=%s ... waiting'
              % (status, (commit or '')[:8]))
        time.sleep(20)
    gate('Pages build is "built" at the local HEAD',
         status == 'built' and commit == want,
         'status=%s commit=%s' % (status, (commit or '')[:8]))

    # ---- gates 2-3: served bytes == local bytes --------------------------
    d, raw_data = load_local_data()
    local_index = open(os.path.join(SEASON, 'index.html'), 'rb').read()
    try:
        st, h, served_index = fetch(BASE + '/season/index.html')
        gate('served index.html is byte-identical to local',
             served_index == local_index,
             '%s served vs %s local' % (format(len(served_index), ','),
                                        format(len(local_index), ',')))
    except Exception as e:
        gate('served index.html is byte-identical to local', False, str(e)[:70])
        served_index = b''

    # data.js is fetched with the version query the index actually uses, since
    # that is the URL a visitor's browser requests.
    m = re.search(rb'data\.js\?v=([0-9a-f]+)', served_index or local_index)
    qs = ('?v=%s' % m.group(1).decode()) if m else ''
    try:
        st, h, served_data = fetch(BASE + '/season/data.js' + qs)
        # Compare and REPORT bytes on both sides. Printing len(raw_data) here
        # showed the character count (1,608,324) against the served byte count
        # (1,608,340) -- data.js carries non-ASCII labels, so the two differ by
        # 16 and the detail read as a contradiction next to an OK.
        local_bytes = raw_data.encode()
        gate('served data.js is byte-identical to local',
             served_data == local_bytes,
             '%s served vs %s local bytes (%s)'
             % (format(len(served_data), ','), format(len(local_bytes), ','),
                qs or 'no v='))
    except Exception as e:
        gate('served data.js is byte-identical to local', False, str(e)[:70])

    # ---- gate 4: the served HTML really wires the tier -------------------
    txt = (served_index or b'').decode('utf-8', 'replace')
    gate('served index requests p.thumb', 'p.thumb || p.t' in txt)
    gate('served index keeps the onerror tile fallback',
         'this.onerror=null;this.src=this.dataset.full' in txt)
    gate('served index lightbox still opens the full tile',
         "lightimg').src = p.t" in txt)

    # ---- gates 5-6: the URLs themselves ----------------------------------
    records = [p for g in d['games'] for p in g['photos']]
    if d.get('catchall'):
        records += d['catchall']['photos']
    gate('every record carries a thumb key locally',
         all(p.get('thumb') for p in records),
         '%s records' % format(len(records), ','))

    random.seed(20260928)
    sample = random.sample(records, min(a.sample, len(records)))
    urls = [BASE + '/season/' + p['thumb'] for p in sample]
    with cf.ThreadPoolExecutor(max_workers=16) as ex:
        res = list(ex.map(head, urls))
    bad = [(u, s, c) for u, s, c, _ in res if s != 200 or not c.startswith('image/')]
    gate('%d sampled tn URLs return 200 + image/*' % len(urls), not bad,
         'ok' if not bad else '%d bad, e.g. %s' % (len(bad), bad[:2]))

    turls = [BASE + '/season/' + p['t'] for p in sample[:40]]
    with cf.ThreadPoolExecutor(max_workers=16) as ex:
        tres = list(ex.map(head, turls))
    tbad = [(u, s, c) for u, s, c, _ in tres if s != 200 or not c.startswith('image/')]
    gate('40 sampled t (lightbox) URLs still return 200 + image/*', not tbad,
         'ok' if not tbad else '%d bad, e.g. %s' % (len(tbad), tbad[:2]))

    # ---- gate 7: the number this card is about, measured LIVE ------------
    oak = [g for g in d['games'] if g['id'] == 'oakland-0904'][0]
    page = oak['photos'][:300]
    probe = page if a.full_page else page[:80]
    purls = [BASE + '/season/' + p['thumb'] for p in probe]
    with cf.ThreadPoolExecutor(max_workers=16) as ex:
        pres = list(ex.map(head, purls))
    ok = [(s, n) for _, s, c, n in pres if s == 200 and n]
    if not ok:
        gate("Oakland first page under 5 MB (live)", False, 'no sizes read')
    else:
        mean = sum(n for _, n in ok) / len(ok)
        measured = sum(n for _, n in ok)
        total = measured if a.full_page else mean * 300
        gate("Oakland's 300-tile first page under 5 MB on the LIVE site",
             total / 1e6 < 5.0,
             '%.2f MB (%s URLs measured, mean %s B/tile%s)'
             % (total / 1e6, len(ok), format(int(mean), ','),
                '' if a.full_page else ', scaled to 300'))
        # and the before number, for the record
        tb = sum(os.path.getsize(os.path.join(SEASON, p['t'])) for p in page)
        print('    was %.2f MB of tiles -> %.1fx lighter' % (tb / 1e6, tb / total))

    print()
    if fails:
        print('FAIL: %d gate(s) failed: %s' % (len(fails), fails))
        print('Do NOT announce the gallery as fixed.')
        return 1
    print('PASS — the thumb tier is live and the grid is measurably lighter.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
