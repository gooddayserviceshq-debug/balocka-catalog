#!/usr/bin/env python3
"""stamp_data_version.py — make a data.js deploy take effect immediately.

season/index.html loads the catalog with a bare <script src="data.js">, and
GitHub Pages serves it with `cache-control: max-age=600`. So for ten minutes
after every publish or grade, a returning visitor keeps executing the PREVIOUS
data.js out of disk cache: measured on the live site right after a deploy,
transferSize was 0 and the page ran a 1,209,980-byte body while the server was
already serving 1,222,168. The tier filters stayed greyed out and the gallery
still said "not graded yet" even though the fix was live.

Fix: stamp the script tag with a short content hash of data.js.

    <script src="data.js?v=ab12cd34"></script>

The hash changes only when data.js changes, so the URL is stable between
deploys (families keep their cached 1.2MB copy) and changes the instant the
catalog does (they get the new one on the next load, not ten minutes later).
A timestamp would work too, but it would force every visitor to re-download
1.2MB on every visit -- the wrong trade on a phone in a stadium parking lot.

Idempotent: run it after any write to data.js. Prints whether it changed.
"""
import hashlib
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(REPO, 'season', 'data.js')
HTML = os.path.join(REPO, 'season', 'index.html')
PAT = re.compile(r'<script src="data\.js(?:\?v=[0-9a-f]+)?"></script>')


def short_hash(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()[:8]


def stamp(quiet=False):
    """Stamp index.html's data.js tag with data.js's content hash.

    Importable so every tool that writes data.js can call it as its last step:
        from stamp_data_version import stamp; stamp()
    Returns the version string. Raises on anything unexpected rather than
    silently leaving a stale tag behind.
    """
    for p in (DATA, HTML):
        if not os.path.exists(p):
            raise SystemExit('FAIL: missing %s' % p)

    ver = short_hash(DATA)
    html = open(HTML, encoding='utf-8').read()

    found = PAT.findall(html)
    if len(found) != 1:
        raise SystemExit('FAIL: expected exactly 1 data.js script tag, found %d '
                         '-- refusing to guess' % len(found))

    want = '<script src="data.js?v=%s"></script>' % ver
    if found[0] == want:
        if not quiet:
            print('unchanged: data.js?v=%s' % ver)
        return ver

    new = PAT.sub(want, html, count=1)
    tmp = HTML + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        f.write(new)
    os.replace(tmp, HTML)

    # prove it landed and that nothing else moved
    back = open(HTML, encoding='utf-8').read()
    assert want in back, 'stamp did not land'
    assert len(back) == len(html) + (len(want) - len(found[0])), \
        'unexpected extra edits to index.html'
    if not quiet:
        print('stamped %s -> data.js?v=%s' % (found[0], ver))
    return ver


def main():
    stamp()


if __name__ == '__main__':
    main()
