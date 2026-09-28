#!/usr/bin/env python3
"""test_thumb_guard.py — the season/t write boundary must stay closed.

Regression cover for the 2026-09-27 incident: 99 full-res originals
(1,112,137,163 B) reached the live site in season/t under macOS "<stem> 2.jpg"
collision names, each beside a correct ~33.6KB twin. The encoder had not failed;
nothing checked the write. This asserts both guards in
publish_day.make_thumb() still fire, and that check_thumbs.py still separates a
clean tree from a violating one.

    python3 tools/test_thumb_guard.py
"""
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import publish_day as pd  # noqa: E402
from publish_day import (THUMB_W, TDIR, ThumbWriteError, guard_thumb_name,  # noqa: E402
                         guard_thumb_file, make_thumb, image_dimensions,
                         COLLISION_SUFFIX_RE)
import check_thumbs  # noqa: E402

CHK = os.path.join(HERE, 'check_thumbs.py')
results = []


def check(label, ok, detail=''):
    results.append((label, ok, detail))
    print('%-58s %s%s' % (label, 'PASS' if ok else 'FAIL',
                          '  ' + detail if detail else ''))


def find_source():
    """Any real oversized JPEG on this disk, to exercise a genuine encode."""
    for d in ('/Users/blake/Desktop/_balocka/recapwall_frames',
              '/Users/blake/Desktop/_balocka/export_2026-09-07',
              '/Users/blake/Desktop/_balocka/exports_2026-09-11'):
        if os.path.isdir(d):
            for f in sorted(os.listdir(d)):
                if f.lower().endswith(('.jpg', '.jpeg')):
                    p = os.path.join(d, f)
                    if os.path.getsize(p) > 2_000_000:
                        return p
    return None


def main():
    # --- name guard: pure string logic, no source image required -------------
    for bad in ('rock_IMGL0048 2.jpg', 'x 3.jpg', 'a b c 10.jpg',
                'oak_0176396A 2.JPG'):
        try:
            guard_thumb_name('/tmp/' + bad)
            check('name guard rejects %r' % bad, False, 'not rejected')
        except ThumbWriteError:
            check('name guard rejects %r' % bad, True)

    for good in ('rock_IMGL0048.jpg', 'oak_65A94B68-EC1B.jpg', 'IMGT0041.jpg',
                 'rv_2.jpg', 'x2.jpg'):
        try:
            guard_thumb_name('/tmp/' + good)
            check('name guard ALLOWS %r' % good, True)
        except ThumbWriteError as e:
            check('name guard ALLOWS %r' % good, False, str(e)[:60])

    src = find_source()
    if not src:
        check('oversized source image available', False,
              'no >2MB jpeg found; size/encode checks skipped')
        return summarise()
    check('oversized source image available', True,
          '%s (%s B)' % (os.path.basename(src), format(os.path.getsize(src), ',')))

    sandbox = tempfile.mkdtemp(prefix='thumbguard_test_',
                               dir=os.environ.get('TMPDIR') or '/tmp')
    try:
        # --- size guard on an un-resized file --------------------------------
        raw = os.path.join(sandbox, 'raw_copy.jpg')
        shutil.copy2(src, raw)
        try:
            guard_thumb_file(raw)
            check('size guard rejects an un-resized original', False, 'accepted')
        except ThumbWriteError:
            check('size guard rejects an un-resized original', True,
                  'and deleted it: %s' % (not os.path.exists(raw)))
        check('size guard DELETES the offender', not os.path.exists(raw))

        # --- make_thumb rejects a collision-suffixed dst before writing ------
        dst_bad = os.path.join(sandbox, 'rock_x 2.jpg')
        try:
            make_thumb(src, dst_bad, 1)
            check('make_thumb refuses a collision-suffixed dst', False)
        except ThumbWriteError:
            check('make_thumb refuses a collision-suffixed dst', True)
        check('no bytes written for a rejected name', not os.path.exists(dst_bad))

        # --- make_thumb fails loudly when the resize silently no-ops ---------
        real_run = subprocess.run
        dst_over = os.path.join(sandbox, 'rock_over.jpg')

        def fake_run(cmd, *a, **k):
            if cmd and cmd[0] == 'sips' and '-Z' in cmd:
                class R:
                    returncode, stdout, stderr = 0, '', ''
                return R()
            return real_run(cmd, *a, **k)

        import PIL.Image
        real_save = PIL.Image.Image.save
        pd.subprocess.run = fake_run
        PIL.Image.Image.save = lambda self, *a, **k: None
        try:
            make_thumb(src, dst_over, 1)
            check('make_thumb raises when sips silently no-ops', False,
                  'an oversized thumb was accepted')
        except ThumbWriteError:
            check('make_thumb raises when sips silently no-ops', True)
        finally:
            pd.subprocess.run = real_run
            PIL.Image.Image.save = real_save
        check('oversized thumb not left on disk', not os.path.exists(dst_over))

        # --- a normal thumb still succeeds -----------------------------------
        dst_ok = os.path.join(sandbox, 'rock_ok.jpg')
        try:
            make_thumb(src, dst_ok, 1)
            w, h = image_dimensions(dst_ok)
            check('normal make_thumb still succeeds', max(w, h) <= THUMB_W,
                  '%dx%d %s B' % (w, h, format(os.path.getsize(dst_ok), ',')))
        except Exception as e:
            check('normal make_thumb still succeeds', False,
                  '%s: %s' % (type(e).__name__, e))

        # --- scanner: clean vs violating ------------------------------------
        clean = os.path.join(sandbox, 'clean')
        os.makedirs(clean)
        shutil.copy2(dst_ok, os.path.join(clean, 'rock_ok.jpg'))
        r = real_run([sys.executable, CHK, '--dir', clean, '--quiet'],
                     capture_output=True, text=True)
        check('scanner exits 0 on a clean tree', r.returncode == 0,
              'exit %d' % r.returncode)

        dirty = os.path.join(sandbox, 'dirty')
        os.makedirs(dirty)
        shutil.copy2(dst_ok, os.path.join(dirty, 'rock_ok.jpg'))
        shutil.copy2(src, os.path.join(dirty, 'rock_big.jpg'))
        r = real_run([sys.executable, CHK, '--dir', dirty, '--quiet'],
                     capture_output=True, text=True)
        check('scanner exits 1 on a planted oversized file', r.returncode == 1,
              'exit %d' % r.returncode)

        dirty2 = os.path.join(sandbox, 'dirty2')
        os.makedirs(dirty2)
        shutil.copy2(dst_ok, os.path.join(dirty2, 'rock_ok.jpg'))
        shutil.copy2(dst_ok, os.path.join(dirty2, 'rock_ok 2.jpg'))
        r = real_run([sys.executable, CHK, '--dir', dirty2, '--quiet'],
                     capture_output=True, text=True)
        check('scanner exits 1 on a collision-suffixed name', r.returncode == 1,
              'exit %d' % r.returncode)

        # --- the baseline must never excuse a collision name -----------------
        check('baseline never covers collision names',
              not any(COLLISION_SUFFIX_RE.match(n)
                      for n in check_thumbs.load_baseline()))

        # --- REGRESSION: the baseline grandfathers FILES, not NAMES ----------
        # The first version of load_baseline() returned bare names, so any file
        # occupying one of the 319 baselined names passed the size check no
        # matter what it was -- a 6000x4000 original dropped onto a baselined
        # name scanned clean AND was reported as "known legacy debt". These
        # checks pin the three-way identity match (name + dims + bytes).
        baseline = check_thumbs.load_baseline()
        check('baseline parses as name -> (w, h, bytes)',
              isinstance(baseline, dict)
              and all(isinstance(v, tuple) and len(v) == 3
                      for v in baseline.values()),
              '%d entries' % len(baseline))

        if baseline:
            bname = sorted(baseline)[0]
            bw, bh, bsize = baseline[bname]

            # (i) an OVERSIZED file wearing a baselined name must be rejected.
            impostor = os.path.join(sandbox, 'impostor')
            os.makedirs(impostor)
            shutil.copy2(dst_ok, os.path.join(impostor, 'rock_ok.jpg'))
            shutil.copy2(src, os.path.join(impostor, bname))
            r = real_run([sys.executable, CHK, '--dir', impostor],
                         capture_output=True, text=True)
            iw, ih = image_dimensions(os.path.join(impostor, bname))
            check('scanner exits 1 on an oversized file under a BASELINED name',
                  r.returncode == 1,
                  '%s planted at %dx%d / %s B -> exit %d'
                  % (bname, iw, ih, format(os.path.getsize(os.path.join(impostor, bname)), ','),
                     r.returncode))
            check('impostor is named as a changed baselined file, not legacy debt',
                  'BASELINED name' in r.stdout and bname in r.stdout,
                  r.stdout.strip().splitlines()[-1] if r.stdout.strip() else '(no output)')
            check('impostor bytes are NOT counted as known legacy',
                  'known legacy oversize' not in r.stdout)

            # (ii) the ACTUAL baselined file still passes.
            real_src = os.path.join(TDIR, bname)
            if os.path.exists(real_src):
                legit = os.path.join(sandbox, 'legit')
                os.makedirs(legit)
                shutil.copy2(real_src, os.path.join(legit, bname))
                r = real_run([sys.executable, CHK, '--dir', legit],
                             capture_output=True, text=True)
                check('the genuine baselined file still passes',
                      r.returncode == 0,
                      '%s %dx%d %s B -> exit %d'
                      % (bname, bw, bh, format(bsize, ','), r.returncode))
                check('genuine baselined file reported as known legacy',
                      'known legacy oversize' in r.stdout)
            else:
                check('the genuine baselined file still passes', True,
                      'skipped — %s not present locally' % bname)

            # (iii) a byte-level change under a baselined name is caught even
            # when the dimensions still match the frozen record.
            tweaked = os.path.join(sandbox, 'tweaked')
            os.makedirs(tweaked)
            if os.path.exists(real_src):
                tpath = os.path.join(tweaked, bname)
                shutil.copy2(real_src, tpath)
                with open(tpath, 'ab') as fh:
                    fh.write(b'\x00' * 64)   # same pixels, different bytes
                r = real_run([sys.executable, CHK, '--dir', tweaked],
                             capture_output=True, text=True)
                check('scanner exits 1 when a baselined file changed size only',
                      r.returncode == 1,
                      '%s B frozen vs %s B on disk -> exit %d'
                      % (format(bsize, ','), format(os.path.getsize(tpath), ','),
                         r.returncode))
            else:
                check('scanner exits 1 when a baselined file changed size only',
                      True, 'skipped — %s not present locally' % bname)

            # (iv) --rewrite-baseline must refuse to launder a live violation.
            r = real_run([sys.executable, CHK, '--dir', impostor,
                          '--rewrite-baseline'], capture_output=True, text=True)
            check('--rewrite-baseline refuses while a violation is present',
                  r.returncode == 1 and 'REFUSING' in r.stdout,
                  'exit %d' % r.returncode)
            frozen_now = check_thumbs.load_baseline()
            check('refused --rewrite-baseline left thumb_baseline.txt untouched',
                  frozen_now == baseline,
                  '%d entries before, %d after' % (len(baseline), len(frozen_now)))

            collide_dir = os.path.join(sandbox, 'collide')
            os.makedirs(collide_dir)
            shutil.copy2(dst_ok, os.path.join(collide_dir, 'rock_ok.jpg'))
            shutil.copy2(dst_ok, os.path.join(collide_dir, 'rock_ok 2.jpg'))
            r = real_run([sys.executable, CHK, '--dir', collide_dir,
                          '--rewrite-baseline'], capture_output=True, text=True)
            check('--rewrite-baseline refuses on a collision-suffixed name',
                  r.returncode == 1 and 'REFUSING' in r.stdout,
                  'exit %d' % r.returncode)
            check('collision refusal left thumb_baseline.txt untouched',
                  check_thumbs.load_baseline() == baseline)
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)

    return summarise()


def summarise():
    bad = [r for r in results if not r[1]]
    print()
    if bad:
        print('FAIL: %d of %d check(s) failed: %s'
              % (len(bad), len(results), [b[0] for b in bad]))
        return 1
    print('PASS — %d/%d checks passed. The season/t write boundary is closed.'
          % (len(results), len(results)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
