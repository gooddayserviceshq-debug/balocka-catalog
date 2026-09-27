#!/usr/bin/env python3
"""Regression harness for tools/brand_rule_audit.py.

A brand audit that prints PASS is only worth the confidence you have that
its detector still fires. A typo in one regex turns the whole check into a
green light that inspects nothing, which is the exact failure mode that let
the 2026-09-16 audit grade a violating site PASS.

So: MUST_FIRE cases are real violations that have to be caught, and
MUST_NOT_FIRE cases are legitimate copy that must never be flagged (a
detector that cries wolf on photo descriptions gets ignored, then disabled).

Run:  python3 tools/test_brand_rule_audit.py
Exit 0 = detector behaves, exit 1 = detector is broken. Run it after ANY
edit to the rules or the allowlist.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import brand_rule_audit as audit  # noqa: E402

# (description, line, expected_rule_id) — must produce a hit for that rule.
MUST_FIRE = [
    ("the original /photos/ line 92 violation",
     "<li>I shoot from the sideline in public view, with the school's knowledge.</li>",
     "access-position"),
    ("same line also trips the school-knowledge rule",
     "<li>I shoot from the sideline in public view, with the school's knowledge.</li>",
     "school-knowledge"),
    ("takedown routed through school staff",
     "Tell any Smyrna coach or athletic staff member and it's gone that day.",
     "staff-routing"),
    ("print request routed through a coach",
     "<p>ask your coach or team parent to pass along the request</p>",
     "staff-routing"),
    ("DM page staff routing",
     "Special requests route through <b>coaches and team parents</b>, not your DMs",
     "staff-routing"),
    ("athletic director routing",
     "Contact the athletic director to request a photo.",
     "staff-routing"),
    ("booster club routing",
     "Send print requests via the booster club table.",
     "staff-routing"),
    ("bare school-affiliation signature (memorial/town original)",
     "Volunteer photographer, Smyrna High School athletics<br>",
     "school-affiliation"),
    ("media credential claim",
     "<p>I shoot with a media pass from the school.</p>",
     "access-credential"),
    ("press box access claim",
     "Shot from the press box above the 50.",
     "access-credential"),
    ("field level access claim",
     "<p>Field level access lets me get tighter frames.</p>",
     "access-credential"),
    ("Blake's own camera note placing him at the sideline",
     "Stay on the 50mm at f/1.8 near the sideline.",
     "access-position"),
    ("endzone position claim",
     "I set up in the end zone for the second half.",
     "access-position"),
    ("claimed school permission",
     "<p>I'm out there with the school's permission.</p>",
     "school-knowledge"),
    ("claimed coach approval",
     "The coach's approval is why I can be down there.",
     "school-knowledge"),
    # The bug this harness was written to lock down: a disclaimer on the line
    # must NOT launder an access / permission / routing claim beside it.
    ("disclaimer must not excuse a sideline claim on the same line",
     "Not affiliated with Smyrna HS. I shoot from the sideline.",
     "access-position"),
    ("disclaimer must not excuse a coach-routing claim on the same line",
     "Not affiliated with Smyrna HS, but ask any coach to pass it along.",
     "staff-routing"),
    ("disclaimer must not excuse a school-permission claim on the same line",
     "Not affiliated with Smyrna HS, though I have the school's permission.",
     "school-knowledge"),
]

# Legitimate copy that must stay clean.
MUST_NOT_FIRE = [
    ("the approved fan-vantage line",
     "<li>I shoot from the stands, in public view, same seats you sit in.</li>"),
    ("season/ disclaimer copy",
     "Not affiliated with Smyrna High School or its athletic department. "
     "Fan photos, taken from general admission seating."),
    ("index.html:640 explicit disclaimer — the model to imitate",
     "Shot by Blake McConnell &middot; Canon EOS R7 &middot; Volunteer "
     "photographer, not affiliated with Smyrna HS or RCS Athletics."),
    ("memorial/town fixed signature",
     "Sports and event photographer, Smyrna TN &mdash; not affiliated with "
     "Smyrna HS or RCS Athletics<br>"),
    ("photo description mentioning sideline as SUBJECT matter",
     '  ["CD32C5BA","#9","Rear 3/4 portrait — dreadlocks, gold helmet, sideline"],'),
    ("graded gallery data row with sideline subject",
     '  ["_MGL0629","#12, #21, #8, #3","Sideline huddle — #12 adjusting helmet","huddle","B+"],'),
    ("photo description of players on the field",
     '  ["_MGL0628","#12, #21","Team group walking on field","huddle","B+"],'),
    ("huddle/sideline UI filter button",
     "<button class=\"filter-btn\" onclick=\"filterCards('huddle')\">Huddle / Sideline (22)</button>"),
    ("team parent routing is family-direct and allowed",
     "message me or ask a team parent to pass along the request"),
    ("direct-to-Blake takedown routing",
     "Message me on Instagram (@balocka_creative) and it's gone that day."),
    ("families never pay copy",
     "No charge, ever. Players and families never pay. Organizations with budgets hire me."),
]


def main():
    failures = []

    for desc, line, expected in MUST_FIRE:
        hits = audit.scan("test", line)
        rule_ids = {h[2] for h in hits}
        if expected not in rule_ids:
            failures.append(
                f"MUST_FIRE missed [{expected}]: {desc}\n"
                f"    line: {line}\n"
                f"    fired instead: {sorted(rule_ids) or 'nothing'}"
            )

    for desc, line in MUST_NOT_FIRE:
        hits = audit.scan("test", line)
        if hits:
            fired = ", ".join(f"{h[2]}({h[5]!r})" for h in hits)
            failures.append(
                f"MUST_NOT_FIRE false positive: {desc}\n"
                f"    line: {line}\n"
                f"    fired: {fired}"
            )

    total = len(MUST_FIRE) + len(MUST_NOT_FIRE)

    # Enumeration guards. The detector being correct is worthless if the sweep
    # never hands it the file. The 2026-09-16 audit passed by scanning a
    # remembered list of 4 pages; these assert the sweep stays wide.
    enum_checks = 0
    files = [audit.rel(f) for f in audit.html_files()]

    enum_checks += 1
    if "season/data.js" not in files:
        failures.append(
            "ENUMERATION: season/data.js is served to every visitor and is "
            "generated — it must be scanned, not skipped as non-HTML."
        )

    enum_checks += 1
    if not any(f.endswith(".html") for f in files):
        failures.append("ENUMERATION: no .html files enumerated at all.")

    enum_checks += 1
    html_count = sum(1 for f in files if f.endswith(".html"))
    if html_count < 12:
        failures.append(
            f"ENUMERATION: only {html_count} .html files found, expected >= 12. "
            "A page was dropped from the sweep."
        )

    enum_checks += 1
    if not any(f.endswith(".js") for f in files):
        failures.append("ENUMERATION: .js served copy is not being scanned.")

    total += enum_checks

    print(f"brand_rule_audit detector regression — {total} cases "
          f"({len(MUST_FIRE)} must-fire, {len(MUST_NOT_FIRE)} must-not-fire, "
          f"{enum_checks} enumeration guards)")

    if failures:
        print(f"\nFAIL — {len(failures)} of {total} cases wrong:\n")
        for f in failures:
            print(f"  {f}\n")
        return 1

    print(f"PASS — {total}/{total} cases correct. Detector is trustworthy.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
