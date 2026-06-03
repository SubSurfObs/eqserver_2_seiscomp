# DU pre-sweep notes

Living register of **things we need to know on the input side** before
launching the DU network conversion sweep. Mirrors the recovery
register pattern (`docs/scan1_recovery_register.md`) but inverted: that
file tracks things to fix AFTER the VW sweep; this file tracks things
to settle BEFORE the DU sweep.

Open it the first time you come back to the DU planning thread.
Append, don't rewrite — the chronology of how we figured each thing
out is part of the value.

## Where each piece of DU work lives

| Artifact | What it holds |
|---|---|
| `metadata/station_registry.yaml` | Authoritative project-internal registry: include/target_network/recorder per station. Internal format. |
| `du_station_registry/du_stations.csv` (separate public repo) | **Operator-facing collaborative source** for DU station metadata. Operators edit via PR. CSV with tier-sorted rows, schema documented in that repo's README. |
| `metadata/uploaded/DU/*.xlsx` + `du.xml` | Raw operator deliveries and FDSN snapshot kept for provenance. |
| `metadata/derived/*.{xlsx,txt}` | Operator-returned spreadsheets from the 2026-05-27 round. |
| `docs/du_channel_coverage_2026-06-02.md` | Point-in-time analysis of channel-code coverage across sources. Not a living doc — re-run the script for current state. |
| `scripts/du_channel_coverage.py` + `scripts/build_du_csv.py` | Generators. The CSV in `du_station_registry/` is the canonical version; these are for bootstrap + occasional refresh. |
| **THIS FILE** | Cumulative DU-specific findings + decisions still owed. |

---

## Network cohort

Operator-confirmed (DL, 2026-06-02):

- **100% sure NO Minimus** in DU. The known Minimus cohort (DDBE, DDWB,
  SCM2) is entirely VW.
- **99% sure NO Reftek RT130** in DU. The known Reftek cohort (LOYU,
  MOSU, SGWU, TRPU, WILU) is entirely VW.
- **Many Piesmos** — the SAA cohort, most of them HHZ-only telemetry
  stubs on EqServer (full 3-comp lives on the upstream or SD card).
  Per CLAUDE.md "Piesmo" section: 15 SAA stations, 11 present in
  EqServer.
- **A few Geckos.**
- **The rest are Echo or EchoPro.** Echo is the predecessor; EchoPro
  is the current model. Both write PC-SUDS (`.dmx`), so file extension
  doesn't distinguish them — see "Recorder distinguishers" below.

So the realistic recorder taxonomy for DU is **{Echo, EchoPro, Gecko,
Piesmo}** — four classes, not six.

## Year cutoff for DU

DU was brought across to EqServer **approximately 2017** per operator
confirmation (2026-06-02). Pre-2017 year directories on EqServer for
DU stations are pre-GPS-lock WNRO artifacts — the same family of
artifacts the conversion pipeline drops at year 2012 via
`scan/phase3_driver.py:_filter_bogus_year_traces`.

**Implications:**

- `du_station_registry/du_stations.csv` already uses 2017 as the
  `eqserver_first_year` cutoff (presentation-only; the bytes still
  exist).
- For the DU sweep itself, consider **tightening
  `DEFAULT_MIN_DATA_YEAR` to 2017 specifically for DU stations**, OR
  adding a per-network override. This is a CHANGE compared to the VW
  sweep, which used 2012.
- The pre-2017 `1989/1999/2001/2004/2011` year-directories that show
  up on EqServer for stations like BRTS / DNL / HKER / HML1 / etc. are
  worth investigating once if we ever want to recover legacy data from
  before DU's official EqServer migration — but they're not part of the
  first-pass sweep.

## Recorder distinguishers (DU-only ruleset)

With Reftek and Minimus excluded, the classifier collapses to a 3-way
decision from the EqServer file layout, plus a 4th split inside the
SUDS family:

```
Files in continuous/<YEAR>/<MONTH>/<DAY>/:

  *.dmx / *.dmx.gz                     -> SUDS family
      └── read SUDS header, look at channel names:
          c01, c02, c03               -> EchoPro
          Up-T, East-T, North-T       -> Echo

  *_HHZ.mseed.zip                     -> Piesmo
      (~250-300 files/day, HHZ-only,
       no N/E components ever present)

  *.ms.zip / *.mseed.zip (no _XXX
       channel suffix)                -> Gecko
      (may also have .ss kelunjimeta
       sidecars under the same tree;
       sidecars are a Gecko tell)
```

### Echo vs EchoPro hypothesis (2026-06-02) — needs broader corroboration

**This is a working hypothesis, not a confirmed rule.** It's based on
one Echo sample (SDAN 2017 + 2018) and one EchoPro sample (BRTS 2024),
plus the operator (David Love) indicating SDAN 2017-2018 was Echo.

David Love provided SDAN's history. EqServer probe of three sample
files:

| File | Channel labels embedded in SUDS header |
|---|---|
| SDAN 2017-01-12 `.relay.dmx.gz` | `Up-T` (single, vertical only) |
| SDAN 2018-01-01 `.dmx.gz` | `East-T` (3-comp, with `North-T`/`Up-T` siblings) |
| BRTS 2024-01-01 `.dmx` *(known EchoPro)* | `c01`, `c02`, `c03` |

Working hypothesis: **Echo uses descriptive long-form channel names**
(Up-T / East-T / North-T); **EchoPro uses Kelunji `c0X` codes**. If the
hypothesis holds, the disk_to_sds engine's `c01→N / c02→E / c03→Z`
Kelunji mapping is EchoPro-specific — for Echo, the channel-to-
orientation mapping would have to come from the descriptive label
(`Up-T` → Z, `East-T` → E, `North-T` → N).

**To upgrade to confirmed**, Level 2's SUDS header probe should be run
across:
- the known-EchoPro VW cohort (BEST, BRIG, CLIF, OUTU, etc.) — expect
  `c0X` everywhere if the rule is right
- any other suspected-Echo dates the operator nominates — expect
  descriptive labels
- a sweep across DU EchoPros to see if any are actually Echo in
  disguise

Until that corroboration runs, treat the split as informative-but-
unverified.

### Side observations from the SDAN probe

- SDAN 2017 files carry a `.relay.` infix in the filename
  (`2017-01-12 0509 51 SDAN.relay.dmx.gz`) — telemetered through a
  relay/repeater. The infix disappears by 2018. Likely upstream
  deployment change.
- SDAN 2017 / 2018 sample files are **truncated gzip streams** (~5.5KB
  / 15KB; sudspy's tolerant reader hits EOFError). The bytes that are
  readable show clean headers; the truncation is a telemetry-loss
  artifact, not on-disk corruption. Phase3's per-file fallback handles
  this gracefully.

## SDAN reference history (from operator David Love, 2026-06-02)

Keep this as the canonical test-case timeline for future epoch-handling
work:

| Period | Net.Sta.Loc | Channels | Rate | Recorder |
|---|---|---|---|---|
| 2017-01 to 2017-08 | AD.SDAN.(blank) | Up-T (vertical only) → EHZ | 100 Hz | Echo |
| 2017-09 to 2018-05 | AD.SDAN.(blank) | East-T / North-T / Up-T → EH* | 100 Hz | Echo |
| 2018-03 to 2018-06 (overlap) | AD.SDAN.5 (should be 50?) | EHE / EHN / EHZ | 100 Hz | ?? (Echo) |
| 2018-06-20 onwards | AU.SDAN.00 | BHE / BHN / BHZ | 40 Hz | mseed (Geoscience Australia took over) |

Note SDAN moved from **AD** network to **AU** at the GA takeover.
SDAN is not currently in our 80-station DU scope; if it should be
included for pre-2018-06 years (network AD), that's a scope decision
to make explicitly.

## Channel naming convention (DU-specific summary)

DU follows full SEED naming, NOT the Gecko-by-rate fallback that
VW/VX use. The cohort patterns observed in VIP / FDSN / LT:

| Cohort | Typical loc | Typical channel | Sample rate |
|---|---|---|---|
| Modern PiesMo | `00` | `HHZ` / `HHN` / `HHE` | 200 Hz |
| EchoPro short-period | `60` | `EHZ` / `EHN` / `EHE` | 100 Hz |
| Echo (older predecessor) | (blank or `60` historical) | descriptive in header → mapped to `EHZ/EHN/EHE` for SDS | 100 Hz |
| Multi-sensor accelerometer companion | `AB` | `HN*` | typically 100 Hz |

**The registry default `target_location: "00"` is wrong for the
EchoPro cohort** — they use `60`. The plan generator must read location
per-station from VIP/FDSN/LT/operator input, not from a single
network-wide default.

LT shows both `0/HHZ` and `00/HHZ` for some PiesMo stations
(single-char vs double-char loc); these should normalise to `00`.

## Known anomalies and conflicts

### TPSO network mismatch

Registry has `TPSO: target_network: DU`. Live VIP publishes as
`AB.TPSO` (loc 60, HH*). Operator decision needed before DU sweep:
reclassify TPSO to AB in registry, or accept DU.TPSO as the
conversion target despite upstream code.

### Multi-instrument stations

| Station | Instruments visible | Note |
|---|---|---|
| HML1 | FDSN `60/SH*` (seismometer) + `AB/HN*` (accelerometer); VIP/LT `60/EH*` | Multi-epoch + multi-instrument |
| RNDA | FDSN `60/HH*` + `AB/HN*` | Broadband + accelerometer pair |

CSV currently shows only the primary instrument per station; the
secondary lives in `notes`. **Decision before DU sweep**: do we convert
the accelerometer companion at `AB` location, or only the
seismometer? If we convert both, the CSV needs split rows.

### Decimation duplicates

Upstream operator produces decimated copies of some channels (EHZ as a
duplicate of HHZ etc.). Visible in LT as extra channel codes that
aren't in VIP/FDSN. The rule **(operator-confirmed 2026-06-02)**:

- VIP and FDSN are truth.
- Extras in LT beyond VIP/FDSN are most likely decimation duplicates
  and should be dropped at planning time.
- **EXCEPT** for the 11 stations where the extra LT codes could be
  real historical epoch data (e.g. EHZ pre-PiesMo era) rather than
  duplicates. Distinguish via year-range check: extras in years before
  the VIP/FDSN era started → real epoch; extras contemporaneous with
  VIP/FDSN era → duplicates.

The 11 candidates per the 2026-06-02 coverage analysis: DJO / ERIKA /
HAZO / HELEN / KENT / LEU / NSTM / OAT / USYD / WAH / WEPH. **TODO
before DU sweep**: run the year-range disambiguation pass.

### Zero-info cohort (10 stations)

No channel codes in VIP, FDSN, LT, or any spreadsheet:

| Station | Spreadsheet hints | Best-guess basis |
|---|---|---|
| **ARKL** | "SA, Epro, Out for 12 months" | Defensible default `60/EH*` (Echo/EchoPro family, SA EchoPro cohort uses this) |
| **PLMR** | "SA, Epro, presently out" | Same as ARKL |
| **LKHRT** | "NSW, Gecko, ?" | Recorder type known, rate unknown |
| **JMS2 / JMS3 / JMS4 / JMS5** | Operator initials only (DL) | Pure unknown. Probably aftershock cluster per operator (2026-06-02). Low priority. |
| **S88M / S88U** | "GG" only | Pure unknown |
| **TPSOP** | "DL" only | Pure unknown (related to TPSO?) |

Operator strategy (per 2026-06-02 discussion): convert all of these
into staging with best-guess channel codes, **leave them in staging
indefinitely** if the codes turn out wrong (the staging share has the
room, and channel-code corrections are a header rewrite, not a re-read).
This is a pragmatic "convert and revise" pattern, NOT a block-and-wait
pattern. Document the guess and the basis per station in
`du_station_registry/du_stations.csv`.

## Sources of truth for DU channel codes (in priority order)

1. **VIP** — live upstream Seismosphere snapshot, refreshed automatically:
   `https://objects.storage.unimelb.edu.au/6700-realtime-seismology-assets/system-health/vip_status.json`
2. **FDSN snapshot** — `metadata/uploaded/DU/du.xml`, captured 2026-02-03.
   Stale-ish; re-pull before DU sweep launch.
3. **SeisComP LT archive** — `/mnt/seiscomp_archive/<YEAR>/DU/<STA>/...`,
   walked at plan-generation time. Filter decimation duplicates against
   VIP/FDSN per the rule above.
4. **`du_station_registry/du_stations.csv`** — operator-edited
   collaborative source. Should be the FINAL word once operators have
   reviewed; pipeline reads this in preference to anything else where
   it disagrees.
5. **SAA operator spreadsheets** under `metadata/uploaded/DU/` — kept
   for provenance, no longer consulted directly by the pipeline once
   the CSV is the source of truth.

The pipeline-side resolution order at plan-generation:
**CSV → VIP → FDSN → LT → BLOCKED** (no Gecko-by-rate fallback for DU).

## Decisions still owed before DU sweep launch

In rough order of how blocking they are:

1. **TPSO network resolution** (DU vs AB).
2. **HML1 / RNDA multi-instrument approach** (one row per instrument,
   or one row per station with `notes` carrying the secondary).
3. **Year-range disambiguation pass** for the 11 multi-channel-in-LT
   stations (decimation duplicate vs real historical epoch).
4. **Zero-info 10**: confirm the convert-and-revise strategy; commit
   best-guess channel codes per station to the CSV.
5. **Pre-2017 year cutoff in phase3** — should `DEFAULT_MIN_DATA_YEAR`
   be tightened from 2012 to 2017 for DU-network plans, OR should we
   add a per-network override? The current 2012 floor will let
   pre-2017 DU artifact directories through if those directories
   contain any non-bogus files.
6. **PiesMo `defer_conversion` rationale re-examination**. CLAUDE.md
   currently flags PiesMo stations as `defer_conversion` (skip in
   plan_generator) because the EqServer presence is only HHZ-only
   telemetry stubs and the "real" bulk 3-comp data reaches LT via a
   different path. This was set per VW thinking; never fires for VW.
   Per `[[project-du-sweep-preconditions]]` agent memory: under the
   "convert what's on disc" rule, partial-but-real HHZ stubs are fine
   to convert. **Decision**: keep `defer_conversion` for PiesMo, or
   drop it? Tracked separately in agent memory.
7. **LT-overlap held count** for DU. The override gate
   (`apply.py:held.jsonl`) will fire on every (loc, chan) where staging
   ≠ LT, and DU has substantially more LT overlap than VW (70% migrated
   per the migration-status memory). Estimate held count from a sample
   station before launch; decide held.jsonl review workflow before
   launch, not after.

## Process notes

- **Registry duplicate-key incident** (2026-06-02): the 7 station
  promotions on 2026-06-01 (BRTS, ROBE, STR2, SUND, WILM, ARKL, PLMR)
  were appended above the existing skeleton entries' fields, leaving
  duplicate keys. YAML `safe_load` silently kept the LATER values, so
  all 7 parsed as `target_network: null` instead of `DU`. Fixed by
  `/tmp/fix_registry_dups.py`; guard added at
  `metadata/check_registry.py`. **Always run
  `python3 metadata/check_registry.py` before committing any registry
  edit**, especially when adding fields to an existing block. It's
  fast (255 blocks scanned in <1s) and catches exactly this class of
  bug.
- **The operator-spreadsheet "c" status** = closed/physically shut down
  (DL clarified 2026-06-02). Not "data stream closed". The 9 stations
  marked `c` in the 2026-05-27 ListToCheck round are demoted to
  `include: false` with reason captured in registry.

## Related design docs

- `CLAUDE.md` § "Pre-scan and file manifest" → **Level 2 — Header scan**
  is the metadata-building stage that should run AFTER VW sweep completes
  and BEFORE DU sweep launches. Recorder distinguishers, sensor authority
  tagging, epoch boundary detection (including the intra-band rate-change
  gap exposed by DDBE 2019-12-16), and skepticism rules for
  operator-input fields are all consolidated there as design notes.
  Implementation TBD; schema is already in place (the columns are NULL
  in the per-station DBs and just need populating).

## Related agent memories (cross-reference for future sessions)

- `[[project-du-sweep-preconditions]]` — what must be reviewed before
  DU sweep launches; the PiesMo `defer_conversion` re-examination and
  LT-overlap held count both originate there.
- `[[project-engine-provenance-incident-2026-06-01]]` — engine pin /
  side-load incident; pin is currently `disk_to_sds@2ee96f3`.
- `[[project-split-suds-convert-followup]]` — queued cross-repo rename
  of `suds_convert.py` → `sds_writer.py` after current sweep
  stabilises. Should land between VW sweep completion and DU sweep
  launch.
- `[[du-migration-status]]` — DU was ~70% migrated to new server as of
  2026-05-26. FDSN snapshot is the channel-code source of truth, no
  hand CSV (superseded by `du_station_registry` repo).
