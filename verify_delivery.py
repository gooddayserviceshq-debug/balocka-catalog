#!/usr/bin/env python3
"""Block until a GitHub Pages gallery is ACTUALLY SERVING the commit you just pushed,
then prove every photo and the download zip are live.

The Amber failure mode: the commit with all 152 photos was pushed, Pages hadn't finished
building, the client opened the link and saw the old 60-photo index. Nothing checked.
This checks -- and refuses to say "delivered" until the live bytes agree with the repo.

Gates (all must pass; exit 1 on any failure):
  1. local    every <img>/href the index references exists on disk; report orphans
  2. git      working tree clean, local HEAD == origin HEAD (nothing unpushed)
  3. pages    poll /pages/builds/latest until status=built AND commit == local HEAD
  4. served   live index references the same photo set as the local index (count + names)
  5. photos   HEAD every live photo URL -> 200 + image content-type
  6. zip      live download zip HEADs 200, bytes match the local file, entry count matches
  7. invoice  (with --doc) the billed deliverable_count in the ledger equals the live index
              count AND the zip entry count -- so an invoice can never claim more photos
              than the client can actually see

Usage:
    python3 verify_delivery.py topgun/                  # verify one gallery dir
    python3 verify_delivery.py topgun/ --doc 0002       # also bind it to a ledger invoice
    python3 verify_delivery.py topgun/ --no-wait        # fail fast instead of polling Pages
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor

REPO_DIR = os.path.dirname(os.path.abspath(__file__))
LEDGER = os.path.expanduser("~/Desktop/balocka_ledger/ledger.json")
POLL_SECONDS = 10
POLL_TIMEOUT = 600  # Pages builds on this repo run ~4 min; 10 is a real ceiling
UA = {"User-Agent": "balocka-delivery-verifier"}

IMG_RE = re.compile(r'(?:src|href)=["\']([^"\']*?img/[^"\']+\.(?:jpe?g|png))["\']', re.I)
ZIP_RE = re.compile(r'href=["\']([^"\']+\.zip)["\']', re.I)


def sh(cmd, cwd=REPO_DIR):
    r = subprocess.run(cmd, cwd=cwd, shell=True, capture_output=True, text=True)
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def get(url, method="GET", binary=False):
    req = urllib.request.Request(url, headers=UA, method=method)
    with urllib.request.urlopen(req, timeout=30) as r:
        body = b"" if method == "HEAD" else r.read()
        return r, (body if binary else body.decode("utf-8", "replace"))


def site_base():
    _, remote, _ = sh("git remote get-url origin")
    m = re.search(r"github\.com[:/]([^/]+)/([^/.]+)", remote)
    if not m:
        sys.exit(f"cannot parse owner/repo from remote: {remote}")
    owner, repo = m.group(1), m.group(2)
    return owner, repo, f"https://{owner}.github.io/{repo}"


def main():
    argv = sys.argv[1:]
    doc_number = None
    if "--doc" in argv:
        i = argv.index("--doc")
        doc_number = argv[i + 1] if i + 1 < len(argv) else None
        del argv[i:i + 2]
    args = [a for a in argv if not a.startswith("--")]
    wait = "--no-wait" not in sys.argv
    gallery = (args[0] if args else "topgun").strip("/")
    owner, repo, base = site_base()
    index_path = os.path.join(REPO_DIR, gallery, "index.html")
    if not os.path.exists(index_path):
        sys.exit(f"no index at {index_path}")

    fails = []
    local_html = open(index_path).read()
    # Keep BOTH views: basenames (the deliverable set, what gets counted/billed) and the
    # literal relative paths the page requests. A grid that tiles img/thumb/x.jpg while the
    # lightbox opens img/x.jpg has two live URLs per photo; checking only basenames would
    # have shipped 160 broken tiles with every gate green.
    local_rel = sorted(set(IMG_RE.findall(local_html)))
    local_imgs = sorted({os.path.basename(p) for p in local_rel})
    derived = sorted({f"img/{f}" for f in local_imgs})  # lightbox + "Save this photo" target
    check_rel = sorted(set(local_rel) | set(derived))
    print(f"=== {base}/{gallery}/")
    print(f"index references {len(local_imgs)} unique photos over {len(check_rel)} URLs")

    # --- gate 1: local integrity
    gdir = os.path.join(REPO_DIR, gallery)
    img_dir = os.path.join(gdir, "img")
    on_disk = sorted(f for f in os.listdir(img_dir)
                     if f.lower().endswith((".jpg", ".jpeg", ".png")))
    missing = [f for f in local_imgs if f not in set(on_disk)]
    missing_rel = [p for p in check_rel if not os.path.exists(os.path.join(gdir, p))]
    orphans = [f for f in on_disk if f not in set(local_imgs)]
    print(f"  [1] local     {len(on_disk)} on disk, {len(missing)} referenced-but-missing, "
          f"{len(missing_rel)} missing paths (incl. derivatives), {len(orphans)} orphaned")
    if missing:
        fails.append(f"index references {len(missing)} files not on disk: {missing[:5]}")
    if missing_rel:
        fails.append(f"{len(missing_rel)} referenced paths absent on disk: {missing_rel[:5]}")

    # --- gate 2: nothing unpushed
    # Only this gallery's own files can make the delivery stale; untracked helper
    # scripts elsewhere in the repo are not a client-facing problem.
    _, dirty, _ = sh(f"git status --porcelain -- {gallery}")
    _, head, _ = sh("git rev-parse HEAD")
    sh("git fetch origin --quiet")
    _, remote_head, _ = sh("git rev-parse origin/HEAD 2>/dev/null || git rev-parse origin/main")
    pushed = head == remote_head
    print(f"  [2] git       HEAD {head[:7]} | origin {remote_head[:7]} | "
          f"{'clean' if not dirty else 'DIRTY'} | {'pushed' if pushed else 'NOT PUSHED'}")
    if not pushed:
        fails.append("local HEAD is not on origin -- push before verifying")
    if dirty:
        fails.append(f"uncommitted changes:\n{dirty}")

    # --- gate 3: Pages has BUILT that exact commit  (the gate that would have caught Amber)
    deadline = time.time() + (POLL_TIMEOUT if wait else 0)
    built = False
    while True:
        rc, out, err = sh(f"gh api repos/{owner}/{repo}/pages/builds/latest")
        if rc == 0:
            b = json.loads(out)
            status, commit = b.get("status"), (b.get("commit") or "")
            if status == "built" and commit == head:
                print(f"  [3] pages     built {commit[:7]} in {b.get('duration', 0)/1000:.0f}s")
                built = True
                break
            state = f"status={status} serving={commit[:7]} want={head[:7]}"
        else:
            state = f"pages API error: {err[:80]}"
        if time.time() >= deadline:
            print(f"  [3] pages     FAIL {state}")
            fails.append(f"Pages not serving this commit ({state})")
            break
        print(f"      waiting for Pages... {state}")
        time.sleep(POLL_SECONDS)

    # --- gate 4: what the client's browser actually gets
    live_url = f"{base}/{gallery}/"
    live_html = ""
    try:
        r, live_html = get(live_url)
        live_imgs = sorted({os.path.basename(p) for p in IMG_RE.findall(live_html)})
        same = live_imgs == local_imgs
        print(f"  [4] served    {r.status} live index references {len(live_imgs)} photos "
              f"({'matches repo' if same else 'MISMATCH vs repo'})")
        if not same:
            only_local = set(local_imgs) - set(live_imgs)
            fails.append(f"live index is stale: {len(only_local)} photos in repo but not served "
                         f"(e.g. {sorted(only_local)[:3]})")
    except Exception as e:
        live_imgs = []
        fails.append(f"live index unreachable: {e}")
        print(f"  [4] served    FAIL {e}")

    # --- gate 5: every photo really loads (tile derivative AND full-res, both are live URLs)
    checked = live_imgs or local_imgs
    live_rel = sorted(set(IMG_RE.findall(live_html))) if live_imgs else []
    check_urls = sorted(set(live_rel) | set(check_rel) | {f"img/{n}" for n in checked})

    def head_ok(rel):
        u = f"{base}/{gallery}/{rel}"
        try:
            r, _ = get(u, method="HEAD")
            ct = r.headers.get("Content-Type", "")
            if r.status != 200 or not ct.startswith("image/"):
                return f"{rel} {r.status} {ct}"
        except Exception as e:
            return f"{rel} {e}"
        return None

    with ThreadPoolExecutor(max_workers=12) as pool:
        bad = [b for b in pool.map(head_ok, check_urls) if b]
    print(f"  [5] photos    {len(check_urls) - len(bad)}/{len(check_urls)} URLs OK "
          f"({len(checked)} photos x tile+full)")
    if bad:
        fails.append(f"{len(bad)} photo URLs broken: {bad[:5]}")

    # --- gate 6: the download the client actually clicks
    zips = ZIP_RE.findall(local_html)
    zip_entry_counts = {}
    if not zips:
        print("  [6] zip       none linked (skipped)")
    for z in sorted(set(zips)):
        zname = os.path.basename(z)
        lpath = os.path.join(REPO_DIR, gallery, zname)
        zurl = f"{base}/{gallery}/{zname}"
        try:
            r, _ = get(zurl, method="HEAD")
            live_bytes = int(r.headers.get("Content-Length", 0))
        except Exception as e:
            fails.append(f"zip {zname} unreachable: {e}")
            print(f"  [6] zip       FAIL {e}")
            continue
        if not os.path.exists(lpath):
            print(f"  [6] zip       {zname} live {live_bytes/1e6:.1f}MB (no local copy to compare)")
            continue
        local_bytes = os.path.getsize(lpath)
        with zipfile.ZipFile(lpath) as zf:
            entries = len([n for n in zf.namelist() if not n.endswith("/")])
        zip_entry_counts[zname] = entries
        ok_size = live_bytes == local_bytes
        ok_count = entries == len(local_imgs)
        print(f"  [6] zip       {zname} live {live_bytes/1e6:.1f}MB vs local "
              f"{local_bytes/1e6:.1f}MB {'OK' if ok_size else 'STALE'} | "
              f"{entries} files vs {len(local_imgs)} in gallery {'OK' if ok_count else 'MISMATCH'}")
        if not ok_size:
            fails.append(f"served zip is a different build than the local one ({zname})")
        if not ok_count:
            fails.append(f"zip holds {entries} files but the gallery shows {len(local_imgs)}")

    # --- gate 7: the number on the invoice is the number the client can see
    # This is the gate that would have stopped "160 photos" going out for a 152-photo
    # gallery. The billed count must come from the ledger -- never from a typed email.
    billed = None
    if doc_number:
        try:
            ledger = json.load(open(LEDGER))
        except Exception as e:
            fails.append(f"cannot read ledger {LEDGER}: {e}")
            ledger = None
        doc = None
        if ledger:
            doc = next((d for d in ledger["documents"] if d["number"] == doc_number), None)
            if doc is None:
                fails.append(f"ledger has no document {doc_number}")
        if doc is not None:
            billed = doc.get("deliverable_count")
            live_count = len(checked)
            zip_counts = sorted(set(zip_entry_counts.values()))
            if billed is None:
                fails.append(f"doc {doc_number} has no deliverable_count -- the billed number "
                             f"is unverifiable (this is exactly how 160-vs-152 happened)")
                print(f"  [7] invoice   doc {doc_number} MISSING deliverable_count")
            else:
                ok_live = billed == live_count
                ok_zip = (not zip_counts) or zip_counts == [billed]
                print(f"  [7] invoice   doc {doc_number} bills {billed} | live index "
                      f"{live_count} {'OK' if ok_live else 'MISMATCH'} | zip "
                      f"{zip_counts or 'n/a'} {'OK' if ok_zip else 'MISMATCH'}")
                if not ok_live:
                    fails.append(f"invoice {doc_number} bills {billed} but the live gallery "
                                 f"serves {live_count} -- fix the ledger or the gallery "
                                 f"before this invoice is sent")
                if not ok_zip:
                    fails.append(f"invoice {doc_number} bills {billed} but the download zip "
                                 f"holds {zip_counts}")
            url = doc.get("gallery_url", "")
            expected = f"{base}/{gallery}/"
            if url and url.rstrip("/") != expected.rstrip("/"):
                fails.append(f"doc {doc_number} gallery_url ({url}) is not the gallery being "
                             f"verified ({expected})")

    print()
    if fails:
        print(f"DELIVERY NOT VERIFIED -- {len(fails)} failure(s). Do NOT send the link:")
        for f in fails:
            print(f"  - {f}")
        return 1
    print(f"DELIVERED: {len(checked)} photos live and loading, zip matches, "
          f"Pages serving {head[:7]}."
          + (f" Invoice {doc_number} bills {billed} \u2014 same number." if billed else "")
          + " Safe to send the link.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
