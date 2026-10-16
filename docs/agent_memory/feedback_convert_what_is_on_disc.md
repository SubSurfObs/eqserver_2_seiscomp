---
name: feedback-convert-what-is-on-disc
description: "The pipeline's job is to convert the data that exists on disc. The ONLY legitimate reason to skip a day is a pathological file-level issue (e.g. unreadable bytes). Recorder restarts, partial days, low-power outages, multi-session days, telemetry-only days are NORMAL operational states of the archive — NOT pathological — and must NOT be pre-filtered out."
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 66713a2d-7a0d-44f1-ae1a-e3a1e4de0f66
---

**The rule:** the eqserver→SeisComP pipeline exists to convert what is on
disc into SDS. The only legitimate reason to skip a day is a **pathological
file-level issue** (file unreadable, header corrupt, can't be parsed).
Everything else gets converted — whatever data is there ends up in LT,
whatever isn't, isn't. No gatekeeping.

**Why:** the UoM VW stations have spent more than a decade going in and out
of action because of power issues, recorder swaps, telemetry outages, and
operator restarts. Partial-day captures, multi-session days, days with
telemetry-only data, days where disk and telemetry disagree on which minutes
they have — these are all **operational reality**, not data faults. The
point of the archive is to capture what was actually recorded. If a station
only got 3 hours of data on a given day, the SDS should contain 3 hours of
data for that day. Skipping the day because "it doesn't fit the clean
template" is exactly the opposite of what this project is for.

**How to apply:**

1. **No station-level BLOCKED / needs_review status that gates whole
   stations.** A station's plan should not refuse conversion of 13 years
   of legitimate data because one or two days don't classify cleanly.
2. **The classifier's job is description, not gatekeeping.** Categories
   like `failing_recorder_disk`, `partial_disk`, `partial_telemetry_only`,
   `partial_source_disagree`, `clean_disk_multi_ss` are useful *labels* for
   ops/QA. They are NOT skip signals.
3. **A day is skippable only when:**
   - `skip_empty` — there are literally no source files for that day
     (not "no clean files" — no files at all). Nothing to do, not a skip.
   - The file(s) cannot be read or parsed — phase3 hits an exception on
     every source file for the day and produces no traces.
4. **Cross-source dedup is part of the conversion job, not a precondition.**
   "Disk and telemetry disagree on which minutes they have" doesn't disqualify
   the day — it means the dedup logic produces the per-minute union as
   designed. If two source files cover the same minute, the existing
   precedence (disk > telemetry) applies. If only one source covers a minute,
   it wins. If neither covers a minute, that minute is a gap in the SDS.
   The pipeline already does this — it doesn't need the classifier to pre-filter.

**Concrete failure to learn from (2026-05-31 → 2026-06-01):**

`scan/plan_generator.py:station_status` was setting `status: BLOCKED` on a
station if any one epoch had `> 20% failing_recorder_disk` OR `>= 1 "other"`
day. By that rule, **one** unclassifiable day in 437 anywhere in BEST's
history blocked all of BEST's 2012–2025 archive from being converted. 7 of
41 VW plans (BEST, BRIG, HDDL, KRAN, LOCU, MARD, WPSH) were flagged BLOCKED
by this logic; 8 more `needs_review`. I added a gate to convert.py honoring
the BLOCKED label without asking what the rule actually meant. The user
called this out as "completely insane logic" and "the whole concept of
BLOCKED is unnecessary." They are right.

**Direction for the next session:**

1. Strip station-level BLOCKED / needs_review from `plan_generator.py`. The
   plan should describe what's there (epochs, classifications, channel
   mapping); it should not refuse to run.
2. Strip the BLOCKED/needs_review/`--include-needs-review` gate from
   `scan/run_production_convert.py` (added 2026-05-31 in commit `026917a`).
   Keep `defer_conversion` because that's a different, legitimate signal —
   "this recorder's EqServer presence is a misleading partial" (PiesMo
   sporadic-HHZ telemetry stub). That's about *what the source actually
   represents*, not about classifier confidence.
3. Strip the flagged-day-skipping inside phase3 except for the truly
   pathological cases. Verify phase3 doesn't pre-emptively refuse to read
   a day just because the classifier labelled it `failing_recorder_disk`
   or `other` — let it try, let it produce whatever traces it can, let
   the dedup do its job.
4. Regenerate the plans after the rule change; expect most/all to flip to
   a "no status field needed" shape.

See also: [[feedback-test-the-pipeline-not-just-the-unit]] (the multi-unit
race that surfaced today), [[project-first-sweep-attempt]] (the LT state
at session end, fixes pushed but not exercised).
