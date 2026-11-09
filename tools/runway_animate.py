#!/usr/bin/env python3
"""Turn a face-free Balocka share card into a short motion clip with Runway Dev.

Use: @balocka_creative reels/stories and link-preview motion, made from the
og/ cards that make_og.py already designed to carry NO recognizable people.

PRIVACY RULE (the reason this file is shaped the way it is):
This repo is galleries of minors. No player photo may be sent to a third-party
generative AI -- not season/, not raw/, not enhanced/, not smyrna-topgun/, and
NOT hero_bg.jpg / bg_stadium.jpg either (darkened, but jersey numbers are
readable). So the input check is an ALLOWLIST, not a denylist: only files
directly inside og/ are accepted. A new directory of player photos added
next year is refused by default instead of leaking by default.

Runway facts below come from https://docs.dev.runwayml.com (read 2026-11-09):
  - POST /v1/image_to_video, model gen4_turbo: ratio one of 1280:720, 720:1280,
    1104:832, 832:1104, 960:960, 1584:672; duration integer 2-10.
  - Generation is async; the SDK's wait_for_task_output() polls with backoff.
  - Output URLs are temporary, so the clip is downloaded immediately.
  - Moderated generations FAIL as a task and still cost credits.
  - Credits: see https://docs.dev.runwayml.com/guides/pricing.md (single source).

Default is a DRY RUN: it validates and prints the request, spends nothing.
Add --submit to make the billable call. Needs RUNWAYML_API_SECRET in the env.

  python3 tools/runway_animate.py og/og-home.jpg
  python3 tools/runway_animate.py og/og-home.jpg --submit
  python3 tools/runway_animate.py og/og-topgun.jpg --ratio 720:1280 --duration 8 --submit

Output: .runway-out/<card>-<taskid>.mp4 (gitignored; the repo root is
Pages-served, so nothing lands there unless you deliberately move it).

Dependency: runwayml (pinned in tools/requirements-runway.txt), only
imported on --submit so the dry run and the tests need nothing installed.
"""

import argparse
import base64
import json
import os
import sys
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ALLOWED_DIR = os.path.join(REPO, "og")
OUT_DIR = os.path.join(REPO, ".runway-out")

MODEL = "gen4_turbo"
RATIOS = ("1280:720", "720:1280", "1104:832", "832:1104", "960:960", "1584:672")
DUR_MIN, DUR_MAX = 2, 10
PROMPT_MAX = 1000
DATA_URI_MAX = 5 * 1024 * 1024  # Runway's data-URI image limit
MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}

DEFAULT_PROMPT = (
    "Slow cinematic push-in on a branded title card. The background texture "
    "drifts gently like stadium light haze; the text and logo stay sharp, "
    "legible and unchanged. No people, no faces, no new text."
)


class Refused(ValueError):
    pass


def check_input(path):
    """Return the real path if it is a permitted card; raise Refused otherwise."""
    real = os.path.realpath(path)  # defeats ../ and symlinks pointing out of og/
    if os.path.dirname(real) != os.path.realpath(ALLOWED_DIR):
        raise Refused(
            f"refused: {path} is not directly inside og/. Only the face-free "
            "share cards may be sent to Runway; player photos never leave this repo."
        )
    ext = os.path.splitext(real)[1].lower()
    if ext not in MIME:
        raise Refused(f"refused: {path} is not a .jpg/.jpeg/.png image")
    if not os.path.isfile(real):
        raise Refused(f"refused: {path} does not exist")
    if os.path.getsize(real) * 4 / 3 > DATA_URI_MAX:
        raise Refused(f"refused: {path} is too large to send inline (5MB data-URI limit)")
    return real


def check_params(ratio, duration, prompt):
    if ratio not in RATIOS:
        raise Refused(f"ratio {ratio!r} not valid for {MODEL}; use one of {', '.join(RATIOS)}")
    if not (DUR_MIN <= duration <= DUR_MAX):
        raise Refused(f"duration {duration} out of range {DUR_MIN}-{DUR_MAX} for {MODEL}")
    if not prompt.strip() or len(prompt) > PROMPT_MAX:
        raise Refused(f"prompt must be 1-{PROMPT_MAX} characters")


def data_uri(real):
    with open(real, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")
    return f"data:{MIME[os.path.splitext(real)[1].lower()]};base64,{b64}"


def build_request(real, ratio, duration, prompt, seed=None):
    req = {
        "model": MODEL,
        "prompt_image": data_uri(real),
        "prompt_text": prompt,
        "ratio": ratio,
        "duration": duration,
    }
    if seed is not None:
        req["seed"] = seed
    return req


def submit(req, real):
    if not os.environ.get("RUNWAYML_API_SECRET"):
        sys.exit("RUNWAYML_API_SECRET is not set. Add it to the environment; never paste it into code or chat.")
    try:
        from runwayml import RunwayML, TaskFailedError
    except ImportError:
        sys.exit("runwayml not installed: pip install -r tools/requirements-runway.txt")

    client = RunwayML()
    try:
        task = client.image_to_video.create(**req).wait_for_task_output()
    except TaskFailedError as e:
        # Moderation lands here too, and is billed. Don't loop retries on it.
        sys.exit(f"Runway task FAILED (still billed if moderated): {e.task_details}")

    url = task.output[0]
    os.makedirs(OUT_DIR, exist_ok=True)
    stem = os.path.splitext(os.path.basename(real))[0]
    dest = os.path.join(OUT_DIR, f"{stem}-{task.id}.mp4")
    urllib.request.urlretrieve(url, dest)  # output URLs expire; keep a copy now
    size = os.path.getsize(dest)
    if size == 0:
        sys.exit(f"downloaded file is empty: {dest}")
    print(f"OK task {task.id}\n   saved {os.path.relpath(dest, REPO)} ({size:,} bytes)")
    return dest


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("card", help="an image directly inside og/, e.g. og/og-home.jpg")
    p.add_argument("--ratio", default="1280:720", choices=RATIOS)
    p.add_argument("--duration", type=int, default=5)
    p.add_argument("--prompt", default=DEFAULT_PROMPT)
    p.add_argument("--seed", type=int)
    p.add_argument("--submit", action="store_true", help="make the billable Runway call")
    a = p.parse_args(argv)

    try:
        real = check_input(a.card)
        check_params(a.ratio, a.duration, a.prompt)
    except Refused as e:
        sys.exit(str(e))

    req = build_request(real, a.ratio, a.duration, a.prompt, a.seed)
    shown = dict(req, prompt_image=f"<data URI, {len(req['prompt_image']):,} chars from {os.path.relpath(real, REPO)}>")
    print(json.dumps(shown, indent=2))

    if not a.submit:
        print("\nDRY RUN: nothing sent, no credits spent. Add --submit to generate.")
        return 0
    submit(req, real)
    return 0


if __name__ == "__main__":
    sys.exit(main())
