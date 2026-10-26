# DU identity decision sheet — Dan's session (prepared 2026-10-26)

**How to use this:** each row is one ruling only you can make. For each,
the sheet says what the question is, where the evidence sits, and my
recommendation where I have one. Rulings get written into
`metadata/station_registry.yaml` and the per-station plans (then
`python3 metadata/check_registry.py` before commit). Nothing converts
until the plan carrying your ruling says `status: ok`.

## 0. Engine pin (blocks ALL production, by design)

Choose the `disk_to_sds` commit for Stage-4 production and record it in
`config/pinned_repo_shas.yaml`. Candidates: `20a483c` (last
engine-semantics commit) or current HEAD (now `d277741`, which adds the
first-write-wins merge ordering from the review — **recommended**, since
the dress rehearsal will run on it anyway). The launcher now refuses to
run until every pin is non-null.

## 1. The EchoPro cohort channel naming (the big one)

The DU EchoPro stations' intended codes (the loc-60 `EH*` question):
whether their SDS channels should carry short-period `EH*` names at
location 60 per the server's existing convention, versus rate-derived
`HH*` at 00. Evidence: the witness-agreement work (27 of 40 stations had
server inventory and archive evidence agreeing), FDSN snapshots in
`metadata/uploaded/DU/`, and the fdsnws server itself. The naming
OVERRIDE mechanism (plan code wins over rate-derived code) is the next
build — it implements whatever you rule, per station.

## 2. Stub stations — keep or drop (9 stations)

Stations whose EqServer presence is only fragmentary single-channel
telemetry stubs. Ruling per station: convert the stubs (convert-what's-
on-disc) or `include: false` with reason. Related: **PiesMo
`defer_conversion`** (decision 6 in `docs/du_pre_sweep_notes.md`) — my
recommendation: DROP the defer and convert the HHZ stubs; partial-but-
real data is not a skip signal by your own standing rule, and the stubs
are honest data with the bulk arriving via the other pipeline.

## 3. TPSO network (DU vs AB)

Decision 1 in `du_pre_sweep_notes.md`. Evidence at session time:
registry notes + FDSN. No recommendation from me — this is a pure
network-ownership fact you know better.

## 4. PENW

The carve-out flagged in the identity evidence review — rule its
intended codes/scope alongside item 1.

## 5. HML1 / RNDA multi-instrument

One registry row per instrument epoch, or one row with notes. My
recommendation: one plan epoch per instrument (matches the epoch model
everything else uses); registry stays one row per station code.

## 6. Multi-channel year-range disambiguation (11 stations)

Decimation duplicate vs real historical epoch, per station
(`du_pre_sweep_notes.md` decision 3). Evidence: LT channel inventory +
the station DBs' rate/channel epochs.

## 7. Zero-info stations (10)

No documentation at all. Recommendation: convert-and-revise — commit
best-evidence codes from the data itself (rates + components), flagged
low-confidence in the plan; renaming later is cheap (your 2026-10-18
ruling), losing the data to the decommission is not.

## 8. Pre-2017 year floor

Decision 5 in `du_pre_sweep_notes.md`: tighten `DEFAULT_MIN_DATA_YEAR`
for DU. Recommendation: per-network override in the plan (DU: 2017),
never a global change — VW/VX have real pre-2017 data.

## 9. NEW since the fix round: probe-blocked station-years

The stricter chooser now refuses any station-year where file probing
could not establish (or probes disagreed about) channel content. These
are identity questions by construction and belong in this session. Pull
the live list from the current study:
`grep -l probe_blocked` equivalents aside, the scoring query is in
STATUS; the dominant case so far is **DU HKER** (its "underscore4"
files probe to empty channel sets across years — likely the mixed-
separator RT130-style cohort). Each needs: real channels confirmed (or
the population ruled junk), recorded in the plan.

## 10. LT-overlap held-count policy (before DU launch, not identity)

Decision 7 in `du_pre_sweep_notes.md`: DU is ~70% already on the server,
so the override gate will fire a lot. Ruling needed: the held-queue
review workflow (sample-station estimate first). Can be settled at
dress-rehearsal time.
