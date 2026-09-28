#!/usr/bin/env python3
"""Live delivery verification for siegel-0911 after the 09-11 publish.

Gates, in the order a failure matters:
  1. Pages build is 'built' AND its commit == local HEAD
  2. working tree clean for the paths this gallery serves
  3. served data.js parses and carries the same siegel-0911 photo set as local
  4. the served index.html cache-bust tag matches the served data.js hash
     (a stale tag pins returning visitors to the previous catalog)
  5. every photo URL in the gallery -- BOTH the tn/ thumbnail the grid requests
     and the t/ tile the lightbox opens -- returns 200 + image/*

Gate 5 checks PATHS, not basenames: a tn/ file built locally and never
committed passes a basename check while serving broken tiles.
"""
import concurrent.futures as cf
import hashlib
import json
import re
import subprocess
import sys
import urllib.request

BASE = 'https://gooddayserviceshq-debug.github.io/balocka-catalog'
REPO = 'gooddayserviceshq-debug/balocka-catalog'
GAME = 'siegel-0911'
LOCAL = '/Users/blake/Desktop/_balocka/balocka-catalog-repo'

fails = []


def check(name, ok, detail=''):
    print('%-4s %s%s' % ('ok' if ok else 'FAIL', name,
                         '' if ok else '\n       <- ' + detail))
    if not ok:
        fails.append(name)


def get(url, timeout=30):
    req = urllib.request.Request(url, headers={'User-Agent': 'balocka-verify'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.headers, r.read()


def head(url, timeout=30):
    req = urllib.request.Request(url, method='HEAD',
                                 headers={'User-Agent': 'balocka-verify'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.headers.get('Content-Type', '')
    except Exception as e:
        return getattr(e, 'code', 0), str(e)[:60]


def sh(cmd):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True,
                          cwd=LOCAL).stdout.strip()


print('=== GATE 1: Pages build == local HEAD ===')
local_head = sh('git rev-parse HEAD')
b = json.loads(sh("gh api repos/%s/pages/builds/latest "
                  "--jq '{status:.status,commit:.commit}'" % REPO) or '{}')
check('pages status is built', b.get('status') == 'built', str(b))
check('pages commit == local HEAD', b.get('commit') == local_head,
      'pages %s vs local %s' % (b.get('commit'), local_head))

print()
print('=== GATE 2: tree clean for the paths this gallery SERVES ===')
# Scoped deliberately. A verifier that cries wolf gets ignored, and this repo is
# worked by more than one agent: unrelated in-flight tooling edits and test
# fixtures in season/tn that NOTHING references are not client-facing defects.
# What must be clean is the catalog, the page, and the image files this gallery
# actually references -- anything uncommitted there is a frame the live site
# cannot serve.
dirty_catalog = sh('git status --porcelain season/data.js season/index.html')
check('season/data.js + index.html committed', not dirty_catalog, dirty_catalog[:300])

local_txt0 = open(LOCAL + '/season/data.js', encoding='utf-8').read()
local0 = json.loads(local_txt0[local_txt0.index('{'):].rstrip().rstrip(';'))
sg0 = next((g for g in local0['games'] if g['id'] == GAME), None)
refs = sorted({'season/' + p[k] for p in (sg0['photos'] if sg0 else [])
               for k in ('thumb', 't') if p.get(k)})
untracked = set()
for chunk in range(0, len(refs), 200):
    out = sh('git status --porcelain -- ' + ' '.join(
        "'%s'" % r for r in refs[chunk:chunk + 200]))
    untracked |= {l for l in out.splitlines() if l.strip()}
check('every image %s references is committed (%d paths)' % (GAME, len(refs)),
      not untracked,
      '%d uncommitted: %s' % (len(untracked), sorted(untracked)[:4]))
check('local HEAD == origin/main', local_head == sh('git rev-parse origin/main'),
      'unpushed commits')

print()
print('=== GATE 3: served catalog matches local ===')
st, hdr, raw = get(BASE + '/season/data.js?cachebust=verify')
check('served data.js 200', st == 200, str(st))
served_txt = raw.decode('utf-8')
served = json.loads(served_txt[served_txt.index('{'):].rstrip().rstrip(';'))
local_txt = open(LOCAL + '/season/data.js', encoding='utf-8').read()
local = json.loads(local_txt[local_txt.index('{'):].rstrip().rstrip(';'))

sg_live = next((g for g in served['games'] if g['id'] == GAME), None)
sg_loc = next((g for g in local['games'] if g['id'] == GAME), None)
check('%s exists in the served catalog' % GAME, sg_live is not None)
if sg_live is None or sg_loc is None:
    print('\nFAIL: cannot verify a gallery that is not in the catalog.')
    print('Do NOT send the link.')
    sys.exit(1)
live_ids = {p['id'] for p in sg_live['photos']}
loc_ids = {p['id'] for p in sg_loc['photos']}
check('served photo set == local photo set',
      live_ids == loc_ids,
      'served %d, local %d, diff %s'
      % (len(live_ids), len(loc_ids), sorted(live_ids ^ loc_ids)[:5]))
print('       %s serves %d photos (was 174 before this publish)'
      % (GAME, len(live_ids)))

print()
print('=== GATE 4: cache-bust tag matches the served data.js ===')
st_i, _h, raw_i = get(BASE + '/season/index.html?cachebust=verify')
m = re.search(r'data\.js\?v=([0-9a-f]+)', raw_i.decode('utf-8'))
check('index.html carries a data.js version tag', bool(m))
if m:
    digest = hashlib.sha256(raw).hexdigest()[:8]
    check('tag %s matches served data.js hash' % m.group(1),
          m.group(1) == digest,
          'tag=%s hash=%s -- returning visitors keep the cached old catalog'
          % (m.group(1), digest))

print()
print('=== GATE 5: every referenced photo PATH is live (200 + image/*) ===')
urls = []
for p in sg_live['photos']:
    for key in ('thumb', 't'):
        v = p.get(key)
        if v:
            urls.append((p['id'], key, '%s/season/%s' % (BASE, v)))
uniq = sorted({u for _i, _k, u in urls})
print('       %d photos -> %d distinct URLs (grid tier + lightbox tile)'
      % (len(sg_live['photos']), len(uniq)))

bad = []
with cf.ThreadPoolExecutor(max_workers=16) as ex:
    for url, (code, ctype) in zip(uniq, ex.map(lambda u: head(u), uniq)):
        if code != 200 or not str(ctype).startswith('image/'):
            bad.append((url, code, ctype))
check('all %d photo URLs return 200 + image/*' % len(uniq), not bad,
      '%d bad, e.g. %s' % (len(bad), bad[:3]))

# The 148 newly published frames specifically.
new_ids = sorted(i for i in live_ids if i.startswith('sg_'))
print('       spot-check of newly published ids: %s ... %s'
      % (new_ids[:2], new_ids[-2:]))

print()
if fails:
    print('FAIL: %d gate(s) failed: %s' % (len(fails), fails))
    print('Do NOT send the link.')
    sys.exit(1)
print('ALL GATES PASS -- %s is live with %d photos.' % (GAME, len(live_ids)))
print('   %s/season/#%s' % (BASE, GAME))
