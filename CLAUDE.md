# eqserver_2_seiscomp — Design Notes

## Goal

Replace the legacy bash + Java EqConvert pipeline (in `legacy/`) with a Python pipeline that converts a decade-scale EqServer waveform archive into a clean SeisComP SDS archive.

**Core dependency**: `sudspy` (`/Users/DSAND/projects/SubSurfObs/sudspy`) — provides PC-SUDS parsing and ObsPy bridge. The pipeline is built around Python and sudspy/ObsPy. SeisComP CLI tools (`scmssort`, `scart`) are available on the VM and were central to the legacy pipeline, but are not assumed to be required in the rewrite — see Toolchain section.

---

## Shared infrastructure & sibling project (added 2026-05)

This project no longer stands alone. It shares a VM, a staging SMB mount, the
staging SDS skeleton, and (ultimately) a manifest ledger with a sibling project,
**`disk_to_sds`** (`/Users/DSAND/projects/SubSurfObs/disk_to_sds`, formerly `sdcard_to_sds`), which
ingests Gecko SD cards into the same long-term SeisComP archive. EqServer
conversion and SD-card ingest are two **sources** feeding one destination.
**The "Storage architecture" and "VM access" sections further down predate this
and are partly superseded — this section is current.**

### The staging VM

A dedicated VM now hosts staging/analysis for both pipelines:

- Host `rs-l-0ezd3a.desktop.cloud.unimelb.edu.au` — **`172.26.144.41`**, user `dsand` (passwordless sudo).
- It is an **`rs-` type VM, but NFS access HAS been granted** to `research-nfs.unimelb.edu.au:/6000/6250-mei` (the rd-/rs- caveat in the VM-access section is resolved for this host).

Three mounts, all in `/etc/fstab` (reboot-robust):

| Mount | Source | Mode | Role |
|---|---|---|---|
| `/mnt/eqserver_archive` | `research-nfs.unimelb.edu.au:/6000/6250-mei` (NFS v4) | **ro** | origin EqServer archive — this project's input |
| `/mnt/seiscomp_staging` | `//mediaflux…/proj-6700_seiscomp_staging-1128.4.1649` (CIFS) | **rw** | shared staging SDS — both pipelines write here |
| `/mnt/seiscomp_archive` | `//mediaflux…/proj-6700_earth_sciences_seismology-1128.4.1237` (CIFS) | **ro** | long-term SeisComP archive — read for comparison/availability, never written from here |

Origin archive path: `/mnt/eqserver_archive/shared/data/repository/archive/<STATION>/continuous/<YEAR>/<MONTH>/<DAY>/`.
SMB creds for the mediaflux mounts are in `/etc/fstab-creds-mediaflux` (root-owned, central UoM password). NFS is `sec=sys` (IP-whitelisted, no creds).

**Mount status (verified 2026-05-25 on `rs-l-0ezd3a`):** all three mounts live
and in `/etc/fstab` with `nofail`/`_netdev` (reboot-safe). Origin NFS ≈ 16 TB,
**98% full** (~420 GB free), ro; 251 station dirs under the archive root. Staging
confirmed writable. LT holds real data — years 2012 + 2021–2026; nets
VW/VX/DU/**AU/Z1/OZ**; plus `Queries/`, `Restricted/`, and stale `sync_test*/`
dirs (leftover sync trials; do not rely on them).
**Caveat — `df` is unreliable on the two mediaflux CIFS mounts:** it reports
~1.9 GB total on staging and *0 bytes used* on LT, both wrong. Do **not** use
`df` to gauge staging headroom — size per-station output from the manifest and
confirm against the actual mediaflux share quota.

### Mediaflux service properties (verified with RCP 2026-06-01)

These properties of the UoM mediaflux platform meaningfully relax the safety
posture this project was originally designed against. They should be relied on
as a backstop but NOT as a substitute for the override gate or other
correctness checks.

- **Quota auto-grant up to 30 TB.** Allocations on `proj-6700_*` shares up to
  30 TB are granted automatically — no ticket required. This is enough to hold
  the full ~16 TB EqServer archive's converted SDS output (estimated ~17 TB
  for VW alone after channel-collapse) plus headroom.
- **Soft delete on local AND offsite.** Deleting a file from a mediaflux share
  doesn't actually remove the bytes — it's reclaimable for **up to 1 year**
  via the offsite copy. Even an `rm -rf` across the whole staging share is
  recoverable within that window. Recovery is operator-mediated via RCP.
- **Versioned overwrites.** When apply.py writes a new copy on top of an
  existing LT file, the previous bytes are kept as a prior version (not
  destroyed). This means an `--mode overwrite` mistake or a `--fast` typo is
  not catastrophic; the old version can be restored. However, **version count
  is not free** — routinely overwriting at scale pollutes version history and
  consumes quota, so the override gate (held.jsonl) remains the right
  primary defence, with versioning as backstop.
- **Offsite replication latency.** New files reach the offsite copy within
  seconds of being written locally. So even a window of catastrophic loss
  between write and offsite-sync is tiny.
- **Storage is single-IP backend.** Both the LT share (`proj-6700_earth_
  sciences_seismology-1128.4.1237`) and the staging share (`proj-6700_seis
  comp_staging-1128.4.1649`) — and any other `proj-6700_*` share — resolve to
  the same mediaflux backend at **`128.250.56.118`** (primary-mf1-qh2.storage.
  unimelb.edu.au). Per-share bandwidth caps and contention apply at this
  shared backend, not at a per-share network endpoint.

**Practical consequences for the sweep:**

- The pre-sweep LT-archive snapshot is **no longer urgent**; the soft-delete +
  versioning + offsite-replication chain is the backup. (We can still take a
  snapshot if it's cheap, but it's not gating.)
- The cleanup.py step that frees staging after a successful promote is
  **optional during the sweep** — we can leave staged copies in place as a
  redundant local-side backup, given the 30 TB allocation room. Run cleanup
  as a separate pass after the sweep is fully verified.
- The override gate (held.jsonl) is **still load-bearing**, not for "prevent
  data loss" (mediaflux's job now) but for "prevent silent provenance
  corruption + version-history pollution". A held entry is still a unit that
  needs human review before bytes flip.

### Shared staging SDS skeleton

Both pipelines write SDS into the **same** staging tree:
`/mnt/seiscomp_staging/seiscomp_archive/<YEAR>/<NET>/<STA>/<CHA>.D/…`. EqServer
conversion fills it station-by-station; SD-card ingest fills it card-by-card.
Long-term archive = common destination; staging = common intermediate.

### Shared ledger (manifest) — `sds_staging_ledger`

The repo **`sds_staging_ledger`** (cloned on the staging VM at
`~/projects/SubSurfObs/sds_staging_ledger` and on the Mac) is the system of
record for what reaches the long-term archive and how. It already tracks
SD-card uploads; **EqServer conversion should append to the same ledger** so one
provenance record covers both the old-archive conversion and SD-card ingests.
Reuse its model rather than re-implementing:

- `apply.py` — staging → LT writes, **atomic** (`cp` → `.partial` → size-verify
  → `rename`), **dry-run by default** (`--commit` to write), decision logic
  `write | skip | override`, never deletes. The EqServer write-to-LT step
  should follow this pattern.
- Manifests: `seiscomp_archive/<YYYY>/<NET>/<STA>.events.jsonl` (one line per
  write/override) + per-card/per-operation dirs. **NEVER delete from
  `/mnt/seiscomp_archive`** — only write/override; the local rolling buffer on
  the SeisComP servers is the only thing routinely deleted.

### Shared conversion core: `disk_to_sds/scripts/suds_convert.py`

**Engine pin: the SUDS→miniSEED+SDS engine the eqserver sweep imports lives
in `disk_to_sds/scripts/suds_convert.py` at `disk_to_sds` SHA `9a3b2ae`
(committed 2026-06-01).** This is the version that converted the
production-sweep bytes from 2026-06-01 onwards; prior to that commit the
file existed only as an untracked, side-loaded copy on the staging VM —
byte-identical to this SHA, but with no git record. See
`handoffs/disk_to_sds/2026-06-01_engine-provenance/` for the incident
write-up; see [[feedback-git-synced-across-hosts]] in agent memory for the
rule we're tightening to prevent recurrence.

The engine (built + tested on a real 2024 OUTU EchoPro day) — reuse it
here for Stage 3 instead of reimplementing:
- `convert_suds_files()` — read SUDS (sudspy) → remap to SEED ids: registry
  network, `c01→CHN / c02→CHE / c03→CHZ` (Kelunji manual), drop `c04+` aux/mic,
  loc `00`, band code by sample rate; per-file read-error capture for QC.
- `write_sds()` — atomic per-channel day-file write, **STEIM2** (int32 cast).
- `network_for_station()` — registry lookup.

`disk_to_sds/scripts/echopro_usb_to_sds.py` is the per-day **driver** to mirror:
discover days → date-window filter → convert → write SDS → QC-flag,
**resume-by-default** (skip a day only when all components are already in the SDS
— the SDS is the bookmark; survives hard kills). The legacy driver differs only
in source layout (day-dirs + the SQLite manifest) and the disk/telemetry
selection; the conversion core is identical.

### Shared diagnostic: duration ratio

`sds_staging_ledger/plot_card.py` computes a
**duration ratio** = (in-record sample-time) / 86400 — ~1.0 clean, ~2.0
duplicated, <1.0 gap. Same QC lens this project should use to validate converted
output; it's exactly what surfaced the upstream seedlink doubling in the live VW
feed. **File size is too noisy (±30–50% from compression) for completeness —
use the duration ratio.**

### Station registry = shared station→network truth

`metadata/station_registry.yaml` is the authoritative VW/VX/DU network
assignment per station. EchoPro/PC-SUDS files carry **no network code**, so the
registry is mandatory for network patching here. Several registry stations (e.g.
MARD, TRPU) ALSO arrive as Gecko SD cards in `disk_to_sds` — the registry is
the common reference for "which network does this station belong to," even
though Gecko miniSEED already carries correct codes.

---

## Station scope and target networks

**Not all 251 stations in the archive are targets for this pipeline.** Many have already been converted, or belong to other agencies with their own FDSN servers. The pipeline only processes stations explicitly listed as `include` in the station registry (see below).

### Target networks

Three SEED networks are the destination for stations in scope:

| Network | Description |
|---|---|
| `VW` | Permanent UoM network |
| `VX` | Temporary / aftershock deployment network |
| `DU` | Third network (passes through from source for DU-coded MiniSEED stations) |

Each station maps to exactly one target network. Determining the correct mapping is a prerequisite before any station can be processed.

### Network code rules

**PC-SUDS (EchoPro) files have no network code.** The SUDS format does not carry a network field. All three networks (VW, VX, DU) use EchoPros, so there is no way to infer target network from the file itself. The network assignment for every EchoPro station MUST come from `station_registry.yaml`.

**MiniSEED (Gecko, Piesmo, etc.) files carry a network code**, but it may not be the correct target:

| Source network code | Action |
|---|---|
| `DU` | Valid — pass through to `DU` unchanged |
| `UM` | Invalid placeholder — must be explicitly assigned to `VW` or `VX` in registry |
| Other | Review case by case; record in registry |

**Assignment heuristics** (use as a starting point; always confirm against metadata):

- `VX` — temporary and aftershock deployments:
  - Stations with a numbered-suffix naming pattern where multiple stations share a base name (e.g. `ABM1Y`–`ABM7Y`, `WPM1Y`–`WPM6Y`, `MOE3`–`MOE8`, `SYD01`–`SYD08`) — these indicate aftershock arrays
  - Woods Point stations: `WPM*` → `VX`
- `DU` — if source MiniSEED already carries `DU` network code
- `VW` — permanent single-station deployments not matching the above patterns

These heuristics narrow the problem but are not definitive. Explicit registry entries override any heuristic.

### Station registry

Station-level metadata is maintained in `station_registry.yaml` in this repository. This is a living document built up progressively as metadata is gathered. It is the authoritative source for:
- Whether a station is in scope (`include: true/false`)
- Target network (`VW`, `VX`, or `DU`)
- Recorder type(s) over time (may have changed — e.g. EchoPro → Gecko)
- Approximate coverage (first and last year with data)
- Notes (known anomalies, station renames, instrument changes)

The registry is hierarchical: high-level per-station facts are captured first, with day-level detail deferred to the SQLite manifest built during pre-scan.

**Schema** (one entry per station):
```yaml
ABM5Y:
  include: true
  target_network: VW               # VW, VX, or DU
  network_basis: registry          # how determined: 'registry' | 'passthrough' | 'heuristic'
  target_location: "00"            # SDS location code; default "00" if omitted (VW/VX convention).
                                   # Source mseed has empty location (Gecko/RT130/Minimus all write ''),
                                   # so the driver REPLACES the source value with this on every trace.
                                   # Set per-station to override (rare; mostly DU-network exceptions).
  recorder_types: [echopro]        # list; if changed over time, order chronologically
  source_network_code: null        # network code in source MiniSEED files (null for EchoPro/SUDS)
  coverage_start: 2016             # approximate year, from archive scan
  coverage_end: 2024
  notes: ""

ABM1Y:
  include: true
  target_network: VX               # aftershock array — numbered suffix pattern
  network_basis: heuristic
  recorder_types: [gecko]
  source_network_code: UM          # UM = invalid placeholder, overridden by registry
  coverage_start: 2018
  coverage_end: 2024
  notes: "Apollo Bay aftershock deployment"

SOMEOTHER:
  include: false
  reason: "Already converted / external agency"
```

Key fields:
- `network_basis: registry` — explicitly set from metadata; highest confidence
- `network_basis: passthrough` — source code is `DU`, passed through directly
- `network_basis: heuristic` — inferred from naming pattern; should be confirmed

### How the network mapping will be determined

1. User provides links to existing metadata sources (SeisComP inventories, FDSN station XML, spreadsheets)
2. Claude reads those and populates `station_registry.yaml` as far as possible
3. Remaining unknowns resolved by archive scan (recorder type from file extensions) and user questions
4. Registry is committed and treated as ground truth for all downstream pipeline decisions

---

## How to use this document

**This file is a living document, not a specification.** The archive spans more than a decade; file naming conventions, recorder firmware, telemetry pipelines, and station configurations have all changed over that time. Information recorded here — including anything from `legacy/` notes — reflects what was observed at a specific point in time on a subset of the data. It should be treated as a starting hypothesis, not ground truth.

### Claude's obligation when working on this project

- **Test assumptions against the real archive.** Before writing code that relies on a filename pattern, an SS convention, a channel name, or any other structural property, scan a representative sample of the actual archive data (ideally across multiple years and stations) to validate the assumption.
- **Update this file when discoveries contradict or refine it.** If a scan reveals a naming convention that differs from what is documented here, update the relevant section immediately — including adding a note about which years or stations the exception applies to.
- **Annotate uncertainty.** If a property is only confirmed for a subset of the archive, say so explicitly (e.g. "observed in 2021–2024 EchoPro data; unverified for pre-2018").
- **Track open questions actively.** The Open Questions section at the bottom is a queue of things to resolve by scanning the archive, not a permanent list of unknowns.

---

## Archive characteristics

### Directory structure
```
/data/repository/archive/<STATION>/continuous/<YEAR>/<MONTH>/<DAY>/
```
Station identity is known from the directory path. The pipeline always knows where it should be.

### Recorder types

Five recorder types are associated with the archive. **EchoPro is the only recorder that wrote in PC-SUDS format.** All others wrote MiniSEED natively, or had their data pre-converted to MiniSEED before being ingested into the EqServer archive. This is the primary branching point in the pipeline: EchoPro days require sudspy conversion; all other recorder days go through a MiniSEED-only path (deduplication → merge → scart).

The exact file naming conventions, extensions, and archiving quirks for the non-EchoPro recorders below are **not yet fully verified across the archive** and should be confirmed by scanning. See "How to use this document".

**EchoPro** (primary conversion target — PC-SUDS)
- Format: PC-SUDS, extension `.dmx` or `.dmx.gz` (gzip, usually compressed)
- 6 channels per recorder (3 seismometer + 3 accelerometer)
- Disk files (3 underscores): `2023-11-24_2057_02_ABM5Y.dmx[.gz]`
- Telemetry files (spaces): `2023-11-24 2057 02 ABM5Y.dmx[.gz]`
- Filename encodes: `DATE_HHMM_SS_STATION.dmx[.gz]`
  - `SS` = seconds offset, constant within a recording session; changes on recorder restart
  - Triggered accelerometer files have a *different* SS from the continuous session
- Requires sudspy for conversion to MiniSEED

**Gecko** (MiniSEED)
- Format: MiniSEED, extension `.ms.zip` (zipped) or `.ms`
- Disk files (2 underscores, no seconds): `20231029_0001_ABM1Y.ms.zip`
- Telemetry files (spaces): `2023-10-29 0001 00 ABM1Y.ms.zip`
- Local zip contains multiple files (MiniSEED + metadata); telemetry zip is single MiniSEED
- Waveform-exclude patterns: `.trig.dmx`, `_CHZ.mseed.zip`
- **`.ss` = metadata-INCLUDE, not a discard.** Gecko `kelunjimeta` sidecar
  (plain text) — the Gecko analog of the PC-SUDS header. Record it
  (`role=metadata`), don't treat it as waveform. Verified across 8+ stations
  (2026-05-26):
  - **Event-driven, NOT periodic.** Each `.ss` is a point-in-time configuration
    snapshot written on recorder boot / settings change. The filename `HHMM`
    is the recorder `settings_time` (e.g. STBK shows clusters like
    `2022-07-24 0200/0300/0400/0500/0600 STBK.ss` — a sequence of reboots that
    morning, not an hourly status ping). STBK = 4,831 `.ss` across 2018–2024.
  - **Always filed under `continuous/1900/01/01/`** (bogus path date) — the
    real date is in the filename. Real day dirs hold only `.ms*` waveform
    files. Scan derives true date from filename and flags `date_mismatch`.
  - **Fields per `.ss`:** `format=kelunjimeta`, `serial` (datalogger),
    `cpv` (counts/V — recorder sensitivity), `current_gain`, `sampling_rate`,
    `sitename`, `network_code` (often invalid `UM` — registry overrides),
    `location_id`, `storing_chan[0..3]`, `tele_chan[0..3]`, `gain_A_0..2`
    (per-channel cal), firmware/build. Older firmware (≤5.0) has occasional
    line-format quirks (e.g. a mashed `"gain"=…settings_time…"` line) —
    parser should be tolerant.
  - **Highly variable presence — not every Gecko station has `.ss`.** Of 8
    stations sampled, STBK / ABM1Y / ABM3Y / ABM4Y / ABM7Y have thousands;
    ABM2Y / ABM5Y / ABM6Y have **zero** despite being Gecko (the Gecko
    apparently wasn't configured to telemetry the `.ss` to EqServer — kept
    only on the SD card). MARD / OUTU / HOLS / JMS2 have zero because they
    are EchoPro-dominant (`.ss` only appears when Gecko is the actual data
    source). The Level-1 manifest's per-station metadata count IS the map
    of who has them.
  - **For metadata harvest:** dedup the snapshots per station — typical
    output is a small number of *distinct* configurations over the station's
    lifetime, and the **transitions between them are epoch-boundary
    candidates** for `uom_seismic_metadata` (serial change → new datalogger,
    sample_rate change → band code, gain change → recompute sensitivity,
    firmware bump → noted). Where `.ss` is absent, fall back to Gecko mseed
    blockettes + external sources.
- **Filename grammar may have evolved over the Gecko lifetime.** Geckos were
  introduced to the UoM network ~2016–2017; **early-era (pre-~2018) deployments
  may use different naming conventions** to the current one. The parser should
  be tolerant of variants and flag rather than misclassify anything that doesn't
  match the documented grammar. (User-confirmed 2026-05-26.)
- **`_CHAN.mseed.zip` (single-channel mseed) IS produced by Gecko stations** —
  verified 2026-05-26 across a Gecko mini-batch (BRTH / SGWU / STBK / TRPU /
  WDSD / WLSH × Q1 2020): 246 such files across 4 of 6 stations (WDSD 152,
  WLSH 65, STBK 28, BRTH 1), sporadic (often <5/day). Examples:
  `WDSD_CHZ.mseed.zip`, `BRTH_CHN.mseed.zip`. Look like emergency/fallback
  single-channel telemetry stubs when the full `.ms.zip` path wasn't available.
  Treating them as `exclude_reason=single_channel` is **correct for Gecko and
  EchoPro stations**.
- **For Guralp Radian/Minimus stations (DDBE / DDWB / SCM2 — see below), the
  same exclude rule is WRONG** — their per-channel mseed IS the data. A
  recorder-aware override is needed for that class (pending per-station Guralp
  annotation in the registry).

**Guralp Radian + Minimus** (MiniSEED, manual-upload — partly observed)
- Combined system: Radian = digital-output borehole sensor; Minimus = mseed
  passthrough recorder. Output is **one mseed file per channel per minute**
  (typically `Z/N/E`), so a complete day = ~4,320 files (3 × 1,440) — the "3.0×"
  pattern in the 2020-01-01 diagnostic.
- **Definitive Minimus cohort per `vw_reconciliation.yaml` historical reconciliation
  (sourced 2026-05-27): DDBE, DDWB, SCM2 — *and only these three.*** Serials in
  `station_registry.yaml`. Pattern matches in the archive scan: per-channel mseed,
  ~4,320 files/day, all three stations score 0% under the default classifier and
  flip to 97–99% clean with the Minimus per-station override (now implemented in
  `check_manifest.py`).
- **DDNE / SCMB are NOT Minimus** despite the cluster naming — the scan and the
  reconciliation agree. DDNE is dominated by Gecko-format files (97%+ clean by the
  default classifier); SCMB is `.dmx` EchoPro (Minimus was *stolen* — Radian
  recovered from the borehole per the 2024 handover PDF).
- **Source caveat:** the Layer-A reconciliation is a best-effort historical harvest
  from wiki + PDF + site visits (NOT field-verified). Treat as the most reliable
  current record, not ground truth.
- **NOT telemetered.** The Guralp Radian outputs too much data to be telemetered
  reliably, so Challenger (the operator) never set up telemetry for these
  stations. The space-separated filenames our parser would label "telemetry"
  are actually **manually-uploaded mseed files** with operator-inserted spaces
  in the names. **For Guralp/Minimus, treat `source_type=telemetry` as
  effectively disk** — there is only one source, no dedup decision to make.
  (User-confirmed 2026-05-26.)
- File naming convention: `YYYY-MM-DD HHMM 00 STA_CHAN.mseed.zip` (where
  `CHAN ∈ {DHZ, DHN, DHE}` or similar 3-letter channel code).
- The classifier's `exclude_reason=single_channel` rule must NOT fire for
  Guralp/Minimus stations — these per-channel mseed files ARE the data.

**Reftek RT130** (IESE-cluster — per `vw_reconciliation.yaml`, source-tagged)

> **Provenance note.** Recorder-cohort assignments and operational windows quoted
> below are sourced from `uom_seismic_metadata/reference/history/vw_reconciliation.yaml`
> (Layer A — a best-effort *historical reconciliation* harvested from wiki + 2024
> handover PDF + site-visit notes). They represent the most reliable centralised
> record currently available, **NOT field-verified ground truth**. Treat as the
> best-available starting hypothesis; revise per-station when the EqServer scan +
> mseed headers + operator confirmation say otherwise.

- **Cohort (5 stations per Layer A):** LOYU, MOSU, SGWU, TRPU, WILU. All Reftek RT130
  digitisers, 200 Hz, IESE S10g sensor (LOYU is the exception — OYO Geospace HS-1
  deep+surface). Per-station serials + operational windows in `station_registry.yaml`.
- **Filename grammar (`YYYY-MM-DD_HHMM_STA.ms.zip`)** is **indistinguishable from
  Gecko at the parser level** — both produce dashed-date `.ms.zip` files with the
  same token shape. The Level-1 manifest tags these as `recorder=gecko` because
  extension is the only signal; the *actual* recorder identity must come from the
  registry's `recorder_types` field (sourced from `uom_seismic_metadata` Layer A) or
  from an mseed header probe (RT130-specific blockettes).
- **Mini-batch validated (11.6 M files, 5 stations, all years):** 69% headline-clean,
  cleanest stations 73–86%; zero failing-recorder days. Day-classification is correct
  — only the *recorder label* is misleading without registry input.
- **Transitions:** SGWU and TRPU transitioned to Gecko + s21g, 250 Hz on 2024-12-12
  (per Layer-B user_direct overlay in `vw_reconciliation.yaml`). LOYU / MOSU / WILU
  decommissioned before transition.
- **WNRO 1024-week timestamp correction — NOT active in the EqServer archive**
  (verified 2026-05-27, 5 stations × 4 years sampled: LOYU 2014/2016/2018,
  SGWU 2018, TRPU 2018). All header timestamps matched their path dates exactly.
  CLAUDE.md previously claimed WNRO was required for SGWU pre-2024-12 and LOYU
  2014-2024 — that claim referred to *raw* RT130 data before ingest. By the
  time data lands in EqServer, the correction has already been applied (the
  predecessor's ingest pipeline handled it; that's why files are sorted into
  correct year-dirs in the first place). **No Phase 3 WNRO converter needed.**
  If a future scan turns up RT130 data with mismatched header/path dates, then
  this assumption must be revisited.

**Piesmo** (DU-network mseed, telemetry-stub on EqServer)

> Source: SAA operator spreadsheet (`SAA-Stations/build/SAA_stations DanS Apr 2026.xlsx`,
> `datalogger="Peismo"`) + 2026-05-27 mini-batch scan against the EqServer archive.
> Spreadsheet is the operator's current-state record (single point in time), not a
> historical timeline. Treat as a starting hypothesis subject to per-station verification.

- **Cohort (15 SAA stations per spreadsheet; 11 present in EqServer archive):** ABRY, BRON,
  DJO, ERIKA, HAZO, HELEN, KENT, NSTM, OAT, USYD, WEPH (present); ALEX, LEU, LGMA,
  WAH (registered but no EqServer dir). All deployed Mar–Jul 2025.
- **Filename grammar on EqServer: `YYYY-MM-DD HHMM SS STA_HHZ.mseed.zip`** — single-
  channel telemetry-stub format, the *exact same shape* as Gecko single-channel
  emergency stubs. Default parser flags these as `exclude_reason=single_channel`.
- **HHZ only on EqServer — N and E components never land here.** ~250–300 files/day
  per station (one every ~5 min). Each file contains short snippets (~55 s + 240 s
  traces, NOT a full minute of continuous data).
- **Mseed header content: `DU.{STATION}.0.HHZ @ 200 Hz`** (location code is single-
  char `0`). WEPH's mseed header says `AU.WEPH.0.HHZ` — direct disagreement with the
  SAA spreadsheet (which lists WEPH as DU). Operator misconfig at deployment, worth
  flagging.
- **Implication: the bulk PiesMo data bypasses EqServer entirely.** Real 3-component
  data presumably reaches the SeisComP archive via direct seedlink or SD-card upload
  through the `disk_to_sds` path. The EqServer presence is a small sporadic dribble
  of single-channel telemetry stubs, ~hour or two of data per day per station.
- **For Phase 2b, PiesMo will need a recorder-aware override** similar to Minimus —
  for stations annotated `recorder_class: piesmo`, the `_HHZ.mseed.zip` exclude
  becomes "keep as Z-only data, flag missing N/E". Pending registry annotation pass
  for the 11 present PiesMo stations.

### Recorder detection strategy

Recorder type is detected per day directory at Level 1 scan (filename/extension only):
- Presence of `.dmx` or `.dmx.gz` → EchoPro
- Presence of `.ms.zip` or `.ms` (without `.mseed`) → labelled `gecko` in the manifest,
  but **NOT necessarily Gecko** — verified 2026-05-27 that **Reftek RT130 files also
  use the `.ms.zip` extension** with the same dashed-date filename grammar
  (`YYYY-MM-DD_HHMM_STA.ms.zip`). The extension alone CANNOT distinguish Reftek
  from Gecko; the only reliable disambiguation is the registry's `recorder_types`
  field (sourced from `vw_reconciliation.yaml` Layer A) or an mseed header probe
  (RT130 blockettes vs Gecko's signatures).
- Presence of `.mseed.zip` or `.mseed` → mixed bucket: Guralp/Minimus per-channel
  (DDBE/DDWB/SCM2 — annotated in registry), or Piesmo single-channel telemetry stub
  (`_HHZ.mseed.zip`), or Gecko emergency single-channel stub, or older single-channel
  exports. Default parser tags these `exclude_reason=single_channel`; recorder-aware
  override per station (registry `recorder_types: [minimus]` etc.) revives them as
  data when appropriate.
- Mixed extensions in one day directory → flag as `edge_case`; may indicate recorder transition or archive ingestion anomaly
- Unknown extension → flag as `unknown`; log for investigation

These heuristics should be validated against the archive before being treated as reliable;
where the registry `recorder_types` field is populated, prefer that over filename inference.

### File naming: EchoPro detail

| Pattern | Source | Notes |
|---|---|---|
| `DATE_HHMM_SS_STA.dmx[.gz]` | Disk (local) | 3 underscores, has seconds field |
| `DATE HHMM SS STA.dmx[.gz]` | Telemetry | spaces, has seconds field |
| `...trig.dmx[.gz]` | Triggered | usually identifiable by name |
| Accelerometer triggered | Triggered | no `trig` in name; different SS from continuous |

The `SS` (seconds offset) is the key discriminator for triggered files that lack `trig` in their name. All continuous files from a single recorder session share the same `SS`.

---

## Processing strategy (EchoPro days)

Three-stage pipeline per station-day. See `sudspy` CLAUDE.md for full detail.

### Stage 1 — Filename scan (no file opens)

- Parse all filenames: extract `HHMM`, `SS`, station code, **date (the filename
  date is authoritative — NOT the directory path; see scan findings below)**
- Discard for conversion: `.trig`, single-channel `*_CHAN.mseed.zip` patterns
  (but `.ss` is retained as `role=metadata`, not discarded)
- Discard: wrong station (filename station ≠ directory station)
- Classify: disk (underscore) vs telemetry (space)
- Group by `SS` value → identifies recording sessions and session boundaries
- Within each session: sort by `HHMM`, find missing slots → gap map

**Fast path**: if exactly 1440 disk files exist all sharing one `SS` → complete single-session day → skip Stage 2, go to Stage 3 directly.

**Near-complete fast path**: if disk file count ≥ (1440 − `THRESHOLD_MISSING_FILES`, default 60) with a single dominant `SS` → treat as complete disk day.

### Stage 2 — Metadata scan (decompress headers, skip data payloads)

- Call `scan_suds_file()` from sudspy on files surviving Stage 1
- Confirm header station identity matches directory (catches wrong-station files)
- Extract precise start/end times (needed at session boundaries where SS changes)
- Resolve deduplication: disk > telemetry for overlapping time windows
- Output: final ordered list of files to parse, contiguous group boundaries

### Stage 3 — Full parse + merge + write

- Call `read_suds_stream()` (sudspy) on selected files
- Group traces into contiguous segments: new group if `start[i] > end[i-1] + 0.5 * delta`
- Write per-channel day MiniSEED with **`encoding="STEIM2"`** and **cast samples to int32 first** (SUDS counts are integers): `Stream.write(path, format="MSEED", encoding="STEIM2", reclen=4096)`. obspy's default encoding writes **uncompressed INT32 (~3.4× bloat, verified)**; STEIM2 gives the ~23–26 MB/day-channel that matches Gecko output. Sanity-check `reclen` against the existing staging/LT SDS (Gecko/staging use 512).
- No gap filling — gaps preserved as separate traces within the day file (standard SDS)

### Channel remapping

After producing day MiniSEED, remap to target SEED codes per the decision tree
in "Network and channel code remapping" (FDSN inventory first; Gecko convention
by sample rate as fallback — NOT blanket `CH`):
- Network from `station_registry.yaml`, location from config (`00`)
- Channel: FDSN code verbatim when the station is live and the rate matches;
  otherwise band-by-sample-rate (`CH` @ 250, `FH` @ ≥1000, `HH` @ 100/200)
- Example (250 sps EchoPro velocity, VW): `AB.ABM5Y.60.c03 → VW.ABM5Y.00.CHZ`
  (orientation `Z` from the Kelunji `c03→Z` component, not Gecko ch-number)
- **Implementation open**: can be done via `scart --rename` (legacy approach) or in pure Python by editing ObsPy `Trace.stats` fields before writing. Python approach is preferred if it avoids a subprocess call per day; scart is acceptable as a fallback. See Toolchain section.

---

## Deduplication priority

1. Disk + continuous session (dominant SS) — preferred
2. Telemetry + continuous session — fallback if disk incomplete
3. Different SS (triggered/accelerometer session) — discard
4. Explicit triggered patterns (`.trig`, `_CHN.mseed.zip` etc.) — discard

---

## Performance and parallelism

**Measured numbers and pending experiments live in `PERFORMANCE.md`; the section
below captures design intent only.** When proposing a new perf experiment, check
`PERFORMANCE.md` first to see whether it (or a near variant) has already been run.

**Parallelism is a first-class design requirement**, not an optimisation to add later. The VM has 24–32 cores confirmed, possibly up to 64 (exact spec TBC). All pipeline stages must be designed to exploit this from the outset.

**Legacy benchmark**: EqConvert file-by-file with 16 parallel procs ≈ 1 min/day (subset). Full 3000-file day ≈ 3 min. Target: 10× improvement.

**Python pipeline advantages over legacy**:
- No JVM startup overhead per file (dominant cost in EqConvert file-by-file mode)
- In-memory merge: no intermediate MiniSEED files written per minute
- Fast path skips all file opens for clean days

### Processing unit: one station at a time

**The pipeline processes one station at a time.** All parallelism is within that station's workload. Reasons:
- Matches the staging architecture: one station fills staging → verify → apply via ledger → clear → next
- Simpler progress tracking, logging, and restart recovery
- Avoids SMB read contention across multiple stations' directory trees simultaneously
- Easier to reason about memory and disk space budgets

Cross-station parallelism would only be warranted if a single station's days cannot saturate all available cores — which is unlikely given a decade of minute-file data. Revisit only if profiling shows cores are idle during single-station runs.

### Parallelism model (within a station)

All cores are dedicated to one station at a time, across two levels:

**Level A — across days (outer pool)**
- Day jobs within the station are embarrassingly parallel: no shared state between days
- `multiprocessing.Pool(workers=N_OUTER)` dispatches all day jobs for the current station
- This is the primary parallelism lever; each worker: read files → parse/convert → write day SDS

**Level B — within a day (inner parallelism)**
- For EchoPro days: sudspy reads across minute files within the day
- For MiniSEED days: unzip + read across minute files
- Use `concurrent.futures.ThreadPoolExecutor` if I/O bound, `multiprocessing.Pool` if CPU bound
- Keep `N_OUTER × N_INNER ≤ total_cores`

**Worker count config** (in `config.yaml`):
```yaml
workers_outer: 16       # days-in-parallel within the current station
workers_inner: 2        # files-in-parallel within a single day
                        # workers_outer * workers_inner <= total cores
```
Conservative defaults; tune once bottleneck (I/O vs CPU) is identified for EchoPro vs MiniSEED days.

### Bottleneck analysis

With an NFS-mounted origin archive (see VM/infrastructure notes), the bottleneck will be one of:
- **NFS read I/O** (likely dominant for large days / many parallel workers hitting the same mount)
- **CPU** (sudspy decompression + SUDS parsing — EchoPro only)
- **NFS/SMB write I/O** (staging SDS writes)

Design mitigations:
- Pre-scan manifest avoids re-reading filenames; all dispatch decisions come from SQLite (local disk)
- Fast-path days skip file opens entirely — pure CPU-free dispatch
- If SMB read becomes the bottleneck, consider batching workers by station to improve locality (all workers reading the same station's mount path at once)
- SQLite in WAL mode supports concurrent reads from multiple workers without blocking; writers serialise per-table but writes are infrequent (progress updates only during conversion)

### Decompression is the dominant per-file cost

Most EchoPro files are `.gz`, so gzip inflate dominates per-file CPU:
- **No C/C++/Fortran rewrite is warranted** — the hotspots are already C (zlib for
  gzip, libmseed for the MiniSEED write, numpy for arrays); the pure-Python SUDS
  parse is negligible (a clean day *decodes* in ~0.4 s, measured on the OUTU day).
  The high-ROI change is **`python-isal` (igzip): a drop-in ~2–3× faster inflate**
  than stdlib zlib — route sudspy's `.gz` open through it.
- **`scan_suds_file(skip_data=True)` does NOT avoid decompression for `.gz`** — you
  can't seek a gzip stream, so it must inflate the whole file to walk the blocks (it
  only skips the numpy sample *decode*). So (a) isal speeds the scan too, and
  (b) **scan selectively**: trust disk/underscore files as the full `c01–c03`
  complement and header-scan only the *telemetry* files (where the single-channel
  legacy lives), rather than inflating every file just to count channels.

### Gecko/Minimus `.ms.zip` read: read-whole-file, seek-in-RAM (validated 2026-05-28)

For the MiniSEED cohorts (Gecko, RT130-via-gecko, Minimus) the per-file cost is
NOT decompression but **ZIP random-access seeks over NFS**. `zipfile.ZipFile(path)`
opened directly on an NFS file handle must seek to the End-Of-Central-Directory at
the tail, then back to the member — each seek is a network round-trip. The fix
(implemented in `scan/phase3_driver.py:_concat_zip_members`, shared by the gecko
and minimus branches): **read the whole (tiny, ~12–70 KB) `.ms.zip` in ONE
sequential NFS read into a `BytesIO`, then open the ZIP from memory** so all seeks
are in RAM. ~2× faster at workers=1 on cold NFS — matters most for exactly the
slow cohorts. Benchmark harness: `scan/gecko_read_benchmark.py` (old direct-open
vs new read-whole). No decode/recode; STEIM2 preserved end-to-end.

### Pre-scan parallelism

Level 1 filename scan can itself be parallelised: split the station list across workers, each walking one station's directory tree independently. SQLite WAL mode handles concurrent inserts safely if each worker uses its own connection.

---

## Configuration

```yaml
# config.yaml

# Storage paths (current staging VM — see Shared infrastructure section)
archive_path: "/mnt/eqserver_archive/shared/data/repository/archive"
staging_sds_path: "/mnt/seiscomp_staging/seiscomp_archive"   # shared with disk_to_sds
lt_archive_path: "/mnt/seiscomp_archive"                      # read-only; never delete
manifest_db: "/home/dsand/eqserver_manifest.db"  # local disk, not SMB

# Parallelism
workers_outer: 16       # station-day level; tune to available cores
workers_inner: 2        # within-day file level; set 1 to disable
                        # workers_outer * workers_inner <= total cores

# EchoPro processing
threshold_missing_files: 60      # fast path: disk files within this of 1440
min_file_threshold: 100          # min files before trusting source classification
channel_exclude: ["BN*"]         # accelerometer channels to discard

# SEED remapping
target_network: "VW"             # per-station from station_registry.yaml; this is only a default
target_location: "00"            # global default; per-station override via station_registry.yaml `target_location`
# NO fixed band code — channel is resolved per station-day (FDSN inventory first,
# else Gecko convention by sample rate: CH@250, FH@>=1000, HH@100/200). See
# "Network and channel code remapping".
fdsn_base_url: "https://subsurface.science.unimelb.edu.au"   # fdsnws-station at /fdsnws/station/1/query; source of truth for live stations
fdsn_inventory_dir: "metadata/uploaded"   # committed StationXML snapshots, per network
mseed_record_length: 4096        # sanity-check vs existing staging/LT SDS (Gecko used 512)
mseed_encoding: "STEIM2"         # NOT obspy's default (uncompressed int32, ~3.4x bloat)

station_map:                     # optional: rename stations
  OLD_STA: NEW_STA
```

---

## Toolchain

The legacy pipeline was built around **bash + Java (EqConvert) + SeisComP CLI** (`scmssort`, `scart`). The rewrite centres on **Python + sudspy + ObsPy**. SeisComP tools remain available on the VM but should not be assumed necessary.

### Primary tools (preferred)

| Task | Tool | Notes |
|---|---|---|
| PC-SUDS → MiniSEED | `sudspy` (`read_suds_stream`, `scan_suds_file`) | In-process, no subprocess overhead |
| MiniSEED read/merge/write | ObsPy `Stream` / `Trace` | Full control over headers; SDS write via `Stream.write(..., format="MSEED")` |
| SEED code remapping | ObsPy `Trace.stats` mutation | Edit network/station/location/channel before writing; no external process |
| SDS directory structure | Python (`pathlib`) | SDS layout is simple: `<year>/<net>/<sta>/<chan>.D/<net>.<sta>.<loc>.<chan>.D.<year>.<jday>` |
| Deduplication / sort | ObsPy `Stream.merge()`, `Stream.sort()` | Equivalent to `scmssort -u -E` |
| Parallelism | `multiprocessing.Pool` | Days within a station dispatched in parallel |
| Manifest / pre-scan | Python `sqlite3` | Local DB on VM disk |

### SeisComP tools (available, not required)

| Tool | Legacy use | Python equivalent |
|---|---|---|
| `scmssort -u -E` | Sort + deduplicate MiniSEED | `Stream.merge(method=1).sort()` |
| `scart --rename` | SEED code remapping + SDS import | `Trace.stats` mutation + `Stream.write()` |
| `scart --with-filecheck` | Avoid duplicate SDS writes | Track written days in manifest |

**When to use SeisComP tools**: if a specific operation proves difficult or buggy in pure Python/ObsPy, dropping down to a `subprocess` call to `scmssort` or `scart` is acceptable. This should be an explicit, documented exception rather than the default pattern.

### Not used in rewrite

- `eqconvert.jar` — replaced entirely by sudspy
- GNU Parallel — replaced by `multiprocessing.Pool`
- Bash wrappers — replaced by Python pipeline orchestration

## Legacy code (legacy/)

Kept for reference. Bash + Java EqConvert + scmssort + scart workflow. Known issues documented in `legacy/README.md`:
- Station contamination (errant files in wrong directory not always caught)
- KEEP flag for temp dirs broken in `process_day_echo`
- Verbose flag not propagated consistently
- EqConvert directory mode loses 3-component data when mixed with single-channel files

The new Python pipeline addresses all of these at the design level.

---

## Storage architecture

> **Mounts, shares, creds, and verified status are authoritative in "Shared
> infrastructure & sibling project" above** — this section keeps only the
> three-tier *model* and the per-stage *design implications*, not the concrete
> paths.

The pipeline operates across three tiers of storage, all from the staging VM:

```
[Origin archive]               [Staging SDS]                    [Long-term SDS]
NFS /mnt/eqserver_archive  →   CIFS /mnt/seiscomp_staging   →   CIFS /mnt/seiscomp_archive
shared/data/repository/        seiscomp_archive/                 <YEAR>/<NET>/<STA>/...
archive/<STA>/...              <YEAR>/<NET>/<STA>/...
  READ ONLY                     WRITE (both pipelines)            WRITE via ledger apply.py (atomic cp)
```

- **Origin** — read-only; never modify in place. I/O is the dominant scan
  bottleneck (minimise redundant reads via manifest caching + fast paths).
- **Staging** — per-station write/verify scratch, shared with `disk_to_sds`;
  wipe and reuse between stations.
- **Long-term** — written **only** through `sds_staging_ledger/apply.py`
  (dry-run by default, `--commit` to write; atomic `cp`→`.partial`→verify→
  `rename`; `write | skip | override`; never deletes), after per-station verify.

### Implications for pipeline design

- **EqServer→SeisComP is just another SDS source.** Conceptually identical to
  `disk_to_sds` (SD-card ingest) — both create SDS into the shared staging tree
  and promote to LT through the **same ledger**; this source just has a much
  larger, more heterogeneous origin disk. So this project's job at the LT
  boundary is simply to **write the correct records into the ledger**, mirroring
  what `disk_to_sds` does — not to invent its own promotion path.
- **Processing unit is one station**: complete one station fully (all years) →
  verify → **apply via ledger** → clear staging → next station. This is a
  deliberate design choice, not a constraint — see Parallelism section.
- **Staging space budget**: estimate one station's full SDS output size from the manifest before starting (total compressed source size × expansion factor); confirm staging has headroom — but **not via `df`** (unreliable on this CIFS mount, see verified note above); check the mediaflux share quota directly
- **LT promotion**: via `sds_staging_ledger/apply.py` (atomic, dry-run by
  default, write/skip/override, never deletes); always run the default dry-run
  first and review before `--commit`. See the "Ledger integration" section
  below for the full provenance flow.
- **Manifest lives on VM local disk**, not on any SMB mount, to avoid I/O overhead on frequent reads/writes during scanning

---

## Ledger integration

`sds_staging_ledger` is the system of record for the long-term SeisComP archive
— what's in it, and how each non-telemetered byte got there. This project
(`eqserver_2_seiscomp`) and the sibling `disk_to_sds` are the two source
projects that feed it. The integration is **load-bearing**: no eqserver byte
should ever reach LT without a complete provenance trail in the ledger.

### The ledger is the only writer to LT

```
origin (NFS, ro)              staging (CIFS, rw, shared)         long-term (CIFS)
/mnt/eqserver_archive    →    /mnt/seiscomp_staging         →    /mnt/seiscomp_archive
                              /seiscomp_archive                  (write only via apply.py)
        ↑                              ↑                                  ↑
        │                              │                                  │
   read-only origin              this project + disk_to_sds         sds_staging_ledger
                                 both write here                    apply.py (never deletes)
```

`apply.py` is the **single binary** that copies staged bytes into LT. It
appends one line per `(day, channel)` decision to
`seiscomp_archive/<YEAR>/<NET>/<STA>.events.jsonl`. That file is the canonical
history of "what reached LT and how."

### Two source kinds, one events.jsonl per (year, net, station)

The `events.jsonl` source dict disambiguates the two pipelines:

```json
// sdcard (disk_to_sds runs)
"source": {"kind": "sdcard", "card_id": "20250409-20250627_0487"}

// eqserver (this project)
"source": {
  "kind": "eqserver",
  "card_id": null,
  "run_id": "eqserver_VW_LRSE_20260530T081500Z",
  "policy_sha": "7a4f...",
  "project_git": "997a723",
  "classifier_version": "v3-OptionB"
}
```

Both kinds appear interleaved in the same per-station-year file. Consumers
that care about provenance branch on `source.kind`.

### Two new ledger top-level dirs for eqserver provenance

```
sds_staging_ledger/
├── seiscomp_archive/<YEAR>/<NET>/<STA>.events.jsonl     (existing — both kinds)
├── seiscomp_archive/<YEAR>/<NET>/<STA>.cleanups.jsonl   (existing — staging-side)
├── cards/<NET>.<STA>/<card_id>/...                       (existing — sdcard only)
├── policies/<sha256>.yaml                                 (NEW — eqserver only)
└── runs/<run_id>/run.json                                 (NEW — eqserver only)
```

- **`policies/<sha256>.yaml`** — verbatim copy of the per-station plan YAML
  at conversion time, content-addressed by SHA256. Same content → same path.
  Immutable once written: `apply.py` asserts byte-equality on duplicate-sha
  writes and aborts on mismatch rather than overwriting either copy. This is
  the **policy fingerprint** every `events.jsonl` eqserver line points back to
  via `source.policy_sha`.
- **`runs/<run_id>/run.json`** — per-conversion-run summary. One per phase3
  `--commit` invocation that gets promoted to LT. Schema: stable shared core
  (run_id, kind, project_git, host, started_at, finished_at, net, sta,
  target_root, policy_sha, classifier_version, aggregate counts,
  phase3_invocation) + a kind-namespaced extension under `eqserver:`
  (per_date_status, days_no_files, flagged_days_skipped, read_errors).
  See `sds_staging_ledger/README.md` for the full schema.

**Naming gotcha:** the ledger has a reserved `plans/` slot for a future
`plan.py` apply-dry-run tool. Eqserver plan YAMLs go to **`policies/`**, NOT
`plans/`. Same word, different layer of meaning.

### How phase3 produces the manifest

`scan/phase3_driver.py --run-manifest <path>` emits a JSON file at the
end of a station's conversion run. Schema is the same as `runs/<run_id>/run.json`
plus a transit-only `policy_yaml_path` field telling `apply.py` where to find
the plan YAML on disk so it can be hashed and copied to `policies/<sha>.yaml`.
The manifest is written **unconditionally** (dry-run or `--commit`) so the
schema can be exercised end-to-end during staging-only stress rehearsals.

Run ID format: `eqserver_<NET>_<STA>_<YYYYmmddTHHMMSSZ>` (path-safe, compact,
sortable). Unique per phase3 invocation.

### How apply.py consumes it

```
apply.py --staging-root /mnt/seiscomp_staging/stress_round1 \
         --lt-root /mnt/seiscomp_archive \
         --ledger-root /home/.../sds_staging_ledger/seiscomp_archive \
         --net VW --sta LRSE \
         --source-kind eqserver \
         --run-manifest /tmp/eqserver_runs/VW_LRSE.json \
         --mode decide --commit
```

On a `--commit` apply with `--run-manifest` set, `apply.py`:

1. Reads the manifest.
2. Reads the plan YAML at `manifest.policy_yaml_path`, hashes it, verifies
   the hash matches `manifest.policy_sha`. Aborts on mismatch.
3. Copies the plan to `<ledger-repo>/policies/<policy_sha>.yaml` via
   `lib/manifest.write_policy_record` (atomic, immutable, idempotent).
4. Writes `<ledger-repo>/runs/<run_id>/run.json` via
   `lib/manifest.write_run_record` (atomic).
5. Auto-injects `{run_id, policy_sha, project_git, classifier_version}` into
   the source dict for every events.jsonl line written during the apply.
6. Sweeps the new `policies/<sha>.yaml` and `runs/<run_id>/run.json` paths
   into the end-of-apply autocommit so they reach the Mac and dev1 via the
   existing `ledger_git.commit_and_push` push path.

There's also a generic `--source-extra-json '{...}'` flag for any writer
that wants to inject extra source fields without going through a manifest.
Reserved keys `kind` and `card_id` are rejected (the base shape stays stable).

### Cross-host write architecture

The ledger has a disjoint-writers invariant preserved through this integration:

| Host | What it writes to the ledger | Auto-push |
|---|---|---|
| Mac | `cards/<NET>.<STA>/<card_id>/` (sdcard prep) | yes |
| Staging VM | `card.json` + `cleanups.jsonl` (sdcard ingest); **eqserver phase3 does NOT write to the ledger directly — its manifest is a transit file consumed later by apply.py on dev1** | yes (for sdcard) |
| dev1 (SeisComp VM) | `events.jsonl` + `policies/<sha>.yaml` + `runs/<run_id>/run.json` (via apply.py) | yes |

The `policies/` and `runs/` entries always get written by `apply.py` on dev1.
That keeps the disjoint-writers rule clean: staging VM never touches the
ledger repo for eqserver work — it just hands the manifest file to dev1.

### Provenance contract for production runs

For any eqserver byte that reaches LT, there must exist:

1. A line in `seiscomp_archive/<YEAR>/<NET>/<STA>.events.jsonl` with
   `source.kind == "eqserver"` and a non-null `source.run_id` + `source.policy_sha`.
2. `policies/<source.policy_sha>.yaml` containing the verbatim plan that
   admitted the day.
3. `runs/<source.run_id>/run.json` summarising the conversion run.

If any of those three is missing for an LT-resident byte, the provenance
trail is broken. `sds_staging_ledger/verify_provenance.py` (post-MVP)
will be the walker that confirms this invariant; for now it's enforced by
construction (apply.py writes all three together or none at all, and the
autocommit ships them in the same push).

### Promotion flow (the operator-facing path)

1. **phase3 runs** with `--run-manifest <path>` on the staging VM. Writes
   SDS to staging; emits manifest JSON.
2. **Operator reviews** the manifest + staging output (dry-run apply, QA
   plots, sanity checks).
3. **apply.py runs** on dev1 with `--run-manifest <same-path> --mode decide
   --commit`. Copies bytes into LT, writes events.jsonl, copies plan to
   `policies/`, writes run.json, autocommits + pushes the ledger.
4. **cleanup.py runs** on the staging VM (sometime later) to clear the
   staged copy after verifying LT == staging. Independent of provenance.

Step 1 and 2 are eqserver responsibility; step 3 is operator-gated; step 4
is independent.

> **Host, mounts, and creds are authoritative in "Shared infrastructure" above**
> (current host: staging VM `rs-l-0ezd3a` / `172.26.144.41`, user `dsand`,
> passwordless sudo; origin NFS mounted ro via fstab). This section keeps only
> the reusable context for provisioning a *future* VM and the origin-safety rule.

- **VM type & NFS access (for a future VM):** UoM Research IT provisions `rd-`
  (Research Desktop — NFS to `research-nfs.unimelb.edu.au` granted by default)
  and `rs-` (Research Server — NFS *not* granted by default; must be requested).
  The current `rs-l-0ezd3a` is an `rs-` host that had NFS to
  `research-nfs:/6000/6250-mei` granted explicitly. If setting up a new VM,
  request `rd-`, or ask IT to grant NFS for the new VM's IP. The exact verified
  mount/fstab lines live in "Shared infrastructure".
- **Origin is sacrosanct:** read-only; never modify EqServer archive files in
  place. Reads (`ls`/`find`/open) are safe; all writes go to staging or a scratch
  dir (e.g. `~/sds_conversion_tests/` on the VM) during development.
- SeisComP CLI tools (`scart`, `scmssort`) and the legacy `eqconvert.jar` are
  **not required** by the rewrite — see Toolchain and the `legacy/` section.

---

## Pre-scan and file manifest

A key design difference from the legacy pipeline: **the new pipeline performs a full archive pre-scan before any conversion**, building a multi-level manifest that covers the entire origin archive. Scanning is progressive — each level adds richer metadata without repeating cheaper work already done.

### Storage backend: SQLite vs CSV/pandas

**SQLite is the default choice.** Reasons: persistent across sessions, queryable without loading everything into memory, supports incremental updates (add rows as scanning progresses or resumes), handles concurrent reads safely, and the multi-table schema (files / station_days / station_intervals) is a natural fit for relational queries.

**CSV + pandas is viable if:**
- The full `files` table fits comfortably in memory (~50–100 M rows would be marginal; the archive is likely in the low millions)
- Scanning always runs to completion in one session (no incremental resume needed)
- The workflow is exploratory/interactive (Jupyter, quick iteration) rather than production pipeline

**Tradeoffs:**

| | SQLite | CSV/pandas |
|---|---|---|
| Incremental resume | Yes (append rows, mark scan progress) | Awkward (re-scan or manual checkpointing) |
| Memory footprint | Low (query what you need) | Entire table in RAM |
| Query expressiveness | SQL joins across tables | pandas groupby/merge — fine for flat data |
| Portability | Single `.db` file | CSV files, easy to inspect in spreadsheet |
| Schema evolution | `ALTER TABLE` needed | Easy to add columns |
| Production pipeline use | Good | Fragile if archive grows |

**Decision**: implement SQLite. Keep a `to_dataframe()` helper on each table for interactive exploration. If early scanning reveals the archive is small enough that pandas is clearly simpler, revisit.

### Scan levels

**Level 1 — Filename scan (no file opens, fast)**

Walks the full directory tree. From path and filename alone:
- Station, year, month, day (from directory path) AND the date parsed from the
  filename — keep both and flag `date_mismatch` (the filename is authoritative)
- Recorder type classification (EchoPro / Gecko / mseed / unknown) — from file extension
- Source type: disk (underscore) vs telemetry (space)
- `HHMM` and `SS` fields parsed from filename; `channel_suffix` (e.g. `_DHZ`)
- `role`: waveform | metadata (`.ss`) | unknown
- Exclude flags (waveform): `.trig`, single-channel `*_CHAN.mseed.zip`, wrong
  extension, wrong station in filename (`.ss` is `role=metadata`, not excluded)
- File size and mtime (from `stat`)

Output: the `files` table fully populated. Enables all flow-control decisions that don't require opening files.

**Implemented:** `scan/level1.py` (stdlib only) — parallel across stations
(per-station part-DBs merged at end, no SQLite writer contention), `--no-db`
mode to isolate NFS walk cost.

**Verified scan findings (2026-05-26, current 16-proc VM):**
- **Throughput:** ~4,500 files/s per worker (NFS stat-walk bound). 7 workers run
  concurrently without NFS collapsing — per-worker rate holds (~3.1k–5.6k f/s).
- **Load-balancing matters more than raw concurrency:** with #stations==#workers
  the wall is set by the largest station (a 7-station run was capped at the 834k
  ABM3Y straggler → 16k f/s aggregate, not the ~31k the workers could sustain).
  → drive a worker POOL with stations QUEUED largest-first; consider splitting a
  giant 2-decade EchoPro station into per-year sub-units so the tail parallelises.
- **Scale:** aftershock Gecko stations are ~0.1–0.8 M files each; full 251-station
  archive likely ~10^8 files. Manifest must be indexed; batched inserts.
- **Date authority:** thousands of files per station have `date_mismatch=1` (esp.
  `.ss` under `1900/01/01`); trust the filename date, not the path.
- **Archive predates 2012:** real `2001` data (OUTU); `1900/1989/1999` dirs are
  bogus-date artifacts (some genuine corrupt timestamps, e.g. `…wrno.dmx.gz`).
- **`unknown`-extension and high per-station `excluded` counts** (e.g. ABM2Y 16k)
  remain to be characterised — surfaced by the scan, not yet explained.

**Phase 2b classifier validation — mini-batch results (2026-05-26):**

Built `scan/check_manifest.py` to verify the manifest can answer Phase 2b's
per-day classification questions and to measure the share of days that need no
human review. Categories starred ★ below are *first-class CLEAN*: the day-plan
can be emitted directly from the manifest with no further NFS reads and no
manual review.

- **EchoPro Jan 2020 (10 stations, 302 station-days):** strict-1440-1-SS = 49%;
  **all CLEAN = 92.6%**. Headline target met.
- **EchoPro full-year 2020 (10 stations, 3,140 station-days):** strict = 41.2%;
  **all CLEAN = 77.8%**. Drop from Q1 is *not* worse data — it surfaces two known
  classifier blind-spots at scale: (a) FORG transitioned EchoPro→Gecko on
  2020-03-19 and its Gecko-clean days fall through the EchoPro `ssd==1` test
  (FORG dropped 96.8% → 15%); (b) BRIG has *intermittent* failing-recorder days
  scattered Apr–Jul (~9 episodes, same signature as CRJN's Jan–Feb week but not
  contiguous). The Gecko-aware classifier branch alone should lift FORG back to
  ≥95%, restoring the headline.

**Classifier v2 (in `scan/check_manifest.py`, ready for next run):**

- ★ **Gecko-aware branch.** Gecko disk filenames carry no SS, so the SS test
  never fires for Gecko-clean days. New branch uses file count + HHMM coverage
  only when the dominant recorder is Gecko (`clean_gecko_disk` /
  `near_clean_gecko_disk` / `near_clean_gecko_threshold`).
- ★ **`clean_disk_multi_ss`** for EchoPro power-cycling days (recorder restarts
  → multiple SS → both sessions legit, recoverable as continuous data). Now
  first-class clean per operator's normal-running profile.
- ★ **`clean_telemetry_primary`** for "USB pending upload" pattern (tiny disk
  count, near-complete tele; NARR-style).
- ★ **`clean_cross_source_recovery`** for days where disk and tele each partial
  but their HHMM union ≥ 1,380 of 1,440 — disk fills primary, tele fills gaps.
- ★ **`clean_mseed_perchan`** provisional for Guralp/Minimus class
  (one file per channel per minute ≈ 4,320/day total).
- **Failing-recorder episode detector:** consecutive `ssd≥10` days (CRJN/BRIG
  signature) are collapsed into single *episodes*, so 21 days might condense to
  2–3 review items.
- **Parser fix:** trailing `.N` event-index on `*.N.trig.dmx` triggered files
  is now stripped before station-mismatch check (was producing false
  `filename_station=LOCU.1` etc.).

**Classifier v2.2 refinements (validated 2026-05-26 against EchoPro full-year 2020):**

- **HOLS calibration finding (load-bearing).** v2 had a `MAX_NORMAL_SS=10` cap on
  `clean_disk_multi_ss`, which threw HOLS from 99.7% → 44.2% (HOLS routinely
  power-cycles 30-60 times/day). Investigation showed HOLS's noisy days have
  `min_disk=1477` (i.e. `> 1440`) — the recorder writes a fresh session-boundary
  minute each restart, so high-ssd days actually capture the full 1,440 minutes
  with duplication, not less. **The right "failing recorder" signal is
  `ssd > MAX_NORMAL_SS` AND coverage loss (`hhd < 1440 - PARTIAL`)** — multi-SS
  alone is fine if `hhd` is full. v2.2 implements this.
- **Failing-recorder episodes after fix:** EchoPro 2020 has 24 episodes
  collapsing 137 days. Biggest by far: **BRIG had a 3-month failing recorder**
  (Apr 1 → Jul 9 2020, three contiguous-ish episodes; field intervention
  apparent around Jul 7 when telemetry returned and counts recovered). CRJN had
  two shorter episodes in Jan-Feb. LOCU has 17 small (1–2 day) episodes
  scattered through the year — borderline cases.
- **EchoPro full-year 2020 headline (v2.2): 87.1% CLEAN** across 3,140 station-
  days. Recovers from v2.1's over-correction (80.9%) once HOLS-style
  noisy-but-complete days are properly classified.
- **Gecko Q1 2020 headline: 100% CLEAN** across 546 station-days (6 stations:
  BRTH / SGWU / STBK / TRPU / WDSD / WLSH). **Zero failing days.** Confirms the
  operator hypothesis that Geckos are dramatically cleaner than EchoPros — no
  power-cycle pattern, no untagged-triggered, no degradation.

**Resolved caveats:**
- `_CHAN.mseed.zip` Gecko association — **VERIFIED** (246 files on 4 of 6 Gecko
  stations across Q1 2020). The exclude rule is correct for Gecko/EchoPro.

**Open caveats:**
- `clean_mseed_perchan` provisional pending per-station Guralp/Minimus
  annotation in the registry — the source_type split is meaningless for that
  class (operator-inserted spaces fake telemetry on manual mseed uploads).

**Classifier v3 — Option B / Pass-1 redefinition (2026-05-29):**

The v2.2 rule treated all `partial_*` categories as "flagged, skip in Pass 1."
Operator framing rejected this: **partial coverage is not pathological** —
the recorder captured what it captured; the resulting SDS just has fewer
minutes. Pathological is something else: (a) recorder thrashing AND coverage
loss together, or (b) disk and telemetry sources recording different minutes
of the same day with no consistent overlap pattern. v3 redraws the line.

**Three buckets, by what's actually possible:**

| Bucket | Outcome | Categories |
|---|---|---|
| **Convert (Pass 1)** | day-job runs | all `clean_*`, all `near_clean_*`, `clean_telemetry_primary`, `clean_cross_source_recovery`, and **all `partial_*`** (partial_disk, partial_gecko_disk, partial_telemetry_only, partial_minimus) |
| **Skip (no work)** | day-job not created | `skip_empty` — no files exist for this day; nothing to do, not a "review" item |
| **Pass 2 review** | flagged for operator | `failing_recorder_disk` (ssd > 10 AND hhd < 1380 — many sessions AND coverage loss together), `partial_source_disagree` (NEW, see below), `other` (genuinely unknown shape) |

**New category — `partial_source_disagree`** (`check_manifest.py:classify`):

Fires inside both gecko and echopro branches AFTER the clean detections and
BEFORE the partial returns. Defined by:
- both sources substantial: `n_hhmm_disk ≥ 60` AND `n_hhmm_tele ≥ 60`, AND
- low overlap: `n_overlap / min(n_hhmm_disk, n_hhmm_tele) < 0.5`,

where `n_overlap = n_hhmm_disk + n_hhmm_tele − n_hhmm_union`.

Captures the operator's "no consistent pattern between disk and tele" case —
the cross-source dedup logic has to make many independent per-minute
decisions instead of "disk wins for the whole window." Days with single-source
coverage cannot trigger this (nothing to disagree with) and stay in Pass 1
under their existing `partial_*` classification.

**Effect of v3 on the per-epoch status rule:** `MIN_OK_PCT = 80` still
applies, but with the partials counting as clean, far fewer epochs come in
under 80% and `status: ok` becomes the common case. Stations like BRTH
(previously `needs_review` because of a 1-day `skip_empty` epoch between
two gecko runs) flip to `ok` — the only remaining flag is the `skip_empty`
day, which Phase 3 was going to skip regardless. The status field now
reflects "operator review burden," not "data quality."

**Operational implication:** Phase 3 conversion behavior is unchanged in
code (it always honored `flagged_days`); the change is that `flagged_days`
lists shrink dramatically, so Pass 1 captures far more of the real data.

**Level 2 — Header scan (decompress headers, skip data payloads)**

Runs `scan_suds_file()` (sudspy) or reads MiniSEED fixed header on files that survived Level 1. Adds per-file:
- Channel names present in the file (e.g.  'c01', `DL*`,  Need to confirm what variable options might be. C0*, [123] definitely confirmed.)
- Sample rate(s)
- Precise start and end times
- Confirmed station identity from header (catches wrong-station files that passed filename check)

This is the same as the existing Stage 2 in the per-day processing strategy, but run as a batch sweep across the archive and stored persistently.

**Level 3 — Day-level aggregation (derived, no file opens)**

Computed from Level 1+2 rows grouped by `(station, year, month, day)`. Stored in a `station_days` summary table:
- Recorder type for the day
- File counts: total, disk, telemetry, excluded
- Dominant SS value and count; number of distinct SS values
- Channel names seen (union across all files for the day)
- Sample rates seen (union)
- Whether a fast-path is applicable
- Day quality flag: `clean`, `mixed`, `edge_case`, `skip`

**Level 4 — Station interval summary (derived)**

Computed from Level 3 rows grouped by `station`. Stored in a `station_intervals` table:
- First and last date with data
- Total days with data; total days in span
- Contiguous data intervals (gaps where no day directory exists)
- Recorder types encountered over time (transition dates if recorder changed)
- Channel names and sample rates seen over the station's lifetime
- Count of days by quality flag

This gives a high-level view of the entire archive: which stations are active when, what their channel complement is, and where instrument transitions occurred. It is the primary input for populating the station-channel remapping config.

### SQLite schema (sketch)

```sql
-- Level 1+2: one row per file
CREATE TABLE files (
    path TEXT PRIMARY KEY,
    station TEXT,
    year INT, month INT, day INT,
    recorder_type TEXT,       -- 'echopro', 'gecko', 'centaur', 'unknown'
    source_type TEXT,         -- 'disk', 'telemetry', 'unknown'
    hhmm TEXT,
    ss TEXT,
    size_bytes INT,
    mtime REAL,
    exclude_reason TEXT,      -- NULL if not excluded
    -- Level 2 (NULL until header scan run)
    channels TEXT,            -- JSON array, e.g. ["DLZ","DLE","DLN"]
    sample_rates TEXT,        -- JSON array, e.g. [200.0]
    t_start REAL,             -- epoch seconds
    t_end REAL,
    header_station TEXT       -- station from file header
);

-- Level 3: one row per station-day
CREATE TABLE station_days (
    station TEXT,
    year INT, month INT, day INT,
    recorder_type TEXT,
    n_files_total INT,
    n_files_disk INT,
    n_files_telemetry INT,
    n_files_excluded INT,
    dominant_ss TEXT,
    n_ss_values INT,
    channels TEXT,            -- JSON array (union across day)
    sample_rates TEXT,        -- JSON array (union)
    fast_path INT,            -- 1 if clean fast-path applicable
    day_quality TEXT,         -- 'clean', 'mixed', 'edge_case', 'skip'
    processed INT DEFAULT 0,
    PRIMARY KEY (station, year, month, day)
);

-- Level 4: one row per station
CREATE TABLE station_intervals (
    station TEXT PRIMARY KEY,
    first_date TEXT,          -- YYYY-MM-DD
    last_date TEXT,
    n_days_with_data INT,
    n_days_span INT,
    recorder_types TEXT,      -- JSON array of distinct types seen
    channel_names TEXT,       -- JSON array (union over all time)
    sample_rates TEXT,        -- JSON array (union)
    n_days_clean INT,
    n_days_mixed INT,
    n_days_edge_case INT,
    n_days_skip INT
);
```

### Flow control from manifest

After Level 1 (and progressively enriched by Levels 2–4):

1. **EchoPro fast path**: `station_days.fast_path = 1` → skip Stage 2 header scan at processing time
2. **Mixed days**: `day_quality = 'mixed'` → Stage 2 required
3. **Gecko days**: `recorder_type = 'gecko'` → no SUDS parsing
4. **Skip days**: `day_quality = 'skip'` → log and bypass
5. **Edge case targeting**: `day_quality = 'edge_case'` → prioritise for testing; inspect `n_ss_values`, channel list anomalies
6. **Remapping prep**: query `station_intervals` to see what channel names and sample rates actually exist per station → informs the remapping config before writing it by hand

---

## Network and channel code remapping

Every converted station needs its source stream codes mapped to target SEED
codes (network, location, and the 3-char band/instrument/orientation). The
decade-scale archive has varied instrument histories, so the mapping is resolved
**per station-day from a single decision tree**, not from a hand-maintained
per-station table.

### Source of truth: FDSN inventory first, Gecko convention as fallback

One rule for all networks. Resolve each station-day's channel codes in order:

1. **Station is in our FDSN/seedlink inventory AND the historical sample rate
   matches the FDSN channel's rate** → copy the FDSN code verbatim (band +
   instrument + orientation). Always **verify** the historical rate from the
   manifest/header scan — don't assume it matches.
2. **In FDSN, but the historical sample rate differs** (e.g. old EchoPro at
   100/200 vs the station's current 250) → keep instrument + orientation,
   **recompute the band code from the historical sample rate** via the Gecko
   table below.
3. **Not on the server at all** (never telemetered — this conversion is the
   data's first appearance):
   - **VW / VX** → **Gecko conventions**: build the full 3-char code from the
     table below (band from sample rate, instrument from sensor type,
     orientation from the physical component).
   - **DU** → resolved by the **conversion-plan gate** (below). DU codes follow
     the full SEED naming convention (see SEED band-code note below), of which
     Gecko is only a simplified subset — so the pipeline must NOT guess a DU
     code from sample rate alone. A DU station-day with no FDSN entry and no
     metadata is written to the plan as `UNRESOLVED` / `status: BLOCKED`; the
     run stops until you confirm or override the code in the plan YAML. No DU
     data is ever converted with a guessed channel name.

Network comes from `station_registry.yaml`; location is `00` (config). The FDSN
inventory is **fetched by script and committed as a snapshot** under
`metadata/uploaded/<NET>/*.xml` (the existing `du.xml` is the DU slice — an
ObsPy-scripted FDSN pull) — re-run as stations migrate onto the server. **No
hand-maintained CSV**; the FDSN inventory IS the channel map, and the
conversion's channel codes must match it so fdsnws serves the streams.

### Gecko channel naming convention (authoritative for VW/VX)

The Gecko recorder builds the 3-char SEED channel code as a simplified subset of
the SEED standard. This is the table the VW/VX fallback and the band-code
recompute both use.

**1st letter — band code, by sample rate:**

| Letter | Sample rate (sps) |
|---|---|
| `B` | 50 |
| `H` | 100, 200 |
| `C` | 250, 400, 500, 800 |
| `F` | 1000, 2000, 4000 |

**2nd letter — instrument code, by sensor type:**

| Letter | Sensor |
|---|---|
| `H` | Velocity seismometer |
| `N` | Accelerometer |
| `D` | Pressure sensor (e.g. microphone) |
| `J` | Rotation sensor |
| `Y` | Displacement sensor |
| `Q` | Voltage |

**3rd letter — orientation, by channel number:**

| Letter | Channel | Typical use |
|---|---|---|
| `E` | 1 | East, Transverse, or X |
| `N` | 2 | North, Radial, or Y |
| `Z` | 3 | Up, Vertical, or Z |
| `O` | 4 | Outdoor microphone or extra vertical sensor |

So a 250 sps velocity seismometer → `CHZ/CHN/CHE` (the VW common case); 1000 sps
→ `FH*`; **100/200 sps → `HH*`** (this resolves the previously-open historical
row). Accelerometers carry instrument code `N` (`*N*`); discard per
`channel_exclude` unless explicitly kept.

> **⚠ Orientation caveat — do NOT apply the Gecko channel-number column to
> EchoPro blindly.** The 3rd-letter table is by *Gecko* channel number. EchoPro
> (Kelunji) numbers components differently: **`c01→N, c02→E, c03→Z`** (Kelunji
> manual, as implemented in `disk_to_sds/scripts/suds_convert.py`), and `c04+`
> are aux/mic. So when converting EchoPro, take the orientation from the source
> component's physical identity, not from Gecko's channel-number mapping. Band
> (sample rate) and instrument (sensor type) still come from the table above.

**Legacy source codes (inputs, for reference):** EqConvert emitted `D`/`E` band
with `H`/`L` gain instrument codes; these are *source* codes to be remapped, not
targets.

### Full SEED band code (the authority behind the Gecko subset)

DU operators name channels by the **full SEED convention**
(<https://ds.iris.edu/ds/nodes/dmc/data/formats/seed-channel-naming/>), where the
band code depends on **both sample rate AND the sensor corner period**
(broadband ≥10 s vs short-period <10 s):

| Band | Sample rate (sps) | Corner period |
|---|---|---|
| `F` / `G` | ≥1000, <5000 | ≥10 s / <10 s |
| `C` / `D` | ≥250, <1000  | ≥10 s / <10 s |
| `H` / `E` | ≥80, <250    | ≥10 s / <10 s |
| `B` / `S` | ≥10, <80     | ≥10 s / <10 s |

**Gecko = the broadband (≥10 s) column, indexed by sample rate** (`B`@50,
`H`@100/200, `C`@250–800, `F`@1000+). A *short-period* sensor at the same rate
takes the sibling code — which is why DU has `SHZ` (short-period at 10–80 sps,
where Gecko would say `B`). **Consequence:** band code cannot be derived from
sample rate alone for short-period instruments — so for DU (and any short-period
sensor) prefer the FDSN code, and never auto-guess. VW/VX are predominantly
broadband velocity seismometers, so the Gecko-by-rate fallback is safe there; the
plan gate catches any short-period exception.

### Conversion plan (per-station review gate)

Before converting a station, the pipeline materialises the decision tree above
into a **per-station YAML plan that you review and approve** — so you see the
exact intended channel mapping before any data is written, and never commit years
of data to a wrong code.

- **Generated, not hand-written.** Combines `station_registry.yaml` (network) +
  FDSN inventory (live/snapshot) + the **pre-scan manifest** (sample rates and
  components actually present in the data, per epoch) + the Gecko/SEED tables.
- **Keyed by sample-rate epoch**, since a rate change shifts the band code. These
  epochs are the same backbone as the `uom_seismic_metadata` epochs.
- Each channel records: `target` SEED code, `basis`
  (`fdsn_verbatim | fdsn_rate_recompute | gecko_fallback | UNRESOLVED`),
  `confidence`, and a note.
- **Hard gate:** top-level `status` is `ok | needs_review | BLOCKED`; the run
  refuses to proceed unless `ok`. Any `UNRESOLVED`/low-confidence line blocks it
  until you confirm or override the `target` in the YAML.
- Lives at `plans/<NET>.<STA>.plan.yaml`, versioned in-repo; the approved plan is
  also the per-run **provenance record** (pairs with the `sds_staging_ledger`).
- **Flow:** pre-scan manifest → generate plan → you approve → convert.

```yaml
station: NSTM
network: DU            # from registry
location: "00"
status: ok             # ok | needs_review | BLOCKED  <- run refuses unless 'ok'
epochs:
  - span: [2016, 2021]
    sample_rate: 100
    source: echopro
    channels:
      - component: c03            # Kelunji c03 = vertical
        target: DU.NSTM.00.HHZ
        basis: fdsn_verbatim      # matched live FDSN @ 100 sps
        confidence: high
# --- a blocked example ---
station: SOMEDU
network: DU
status: BLOCKED
epochs:
  - span: [2014, 2019]
    sample_rate: 100
    channels:
      - component: c03
        target: null
        basis: UNRESOLVED
        suggestion: DU.SOMEDU.00.HHZ   # guess only, NOT applied
        note: "no FDSN entry, no metadata — confirm or override before running"
```

---

## Station metadata epochs → uom_seismic_metadata (the metadata handoff)

This pipeline does two things in parallel: it converts *waveforms* (the main job), and as
a side-effect it harvests an **empirical record of station history** — what the recorders
themselves were emitting at every point in the archive. That empirical record is **one of
two complementary versions of the metadata history** that `uom_seismic_metadata` then
joins into a single canonical artifact.

### Two distinct versions of metadata history (joined at synthesis)

`uom_seismic_metadata` maintains two independent reconstructions of every station's
instrument timeline. They are deliberately separate during collection — joined only at the
synthesis step — so that disagreements stay visible rather than being silently merged.

| Version | Source material | Layer in `uom_seismic_metadata` |
|---|---|---|
| **Documentary** | wiki + PDF handover + 2021 / Januka StationXMLs (+ direct user statements as an overlay) | `reference/history/<net>_reconciliation.yaml`<br>`reference/user_direct/<net>_user_notes.yaml` |
| **Empirical** *(this project)* | PC-SUDS embedded headers + Gecko `.ss` (`kelunjimeta`) sidecars + SDS / MiniSEED inventory | future `reference/waveform_db/<net>_observations.yaml` |
| **Synthesis** | curated join of the above | `source/stations/<net>.yaml` (-> SMP / FDSN) |

Both versions speak the **same epoch schema** and are joined by station code + date. Where
they agree, the canonical is high-confidence. Where they disagree, the disagreement is
documented (not hidden); the **data archive itself is the ultimate arbiter** — which is
why the empirical version exists.

Open factual questions that no single layer can resolve are logged at
`uom_seismic_metadata/reference/user_direct/open_questions.md`. Several of these (LRNW
recorder type, DDBE data-end date, OUTU origin, a possible undocumented DDNE borehole)
will be resolved by this project's archive scan.

### Shared schema (single contract)

Single shared epoch contract: `uom_seismic_metadata/schema/station_metadata.draft.yaml`,
plus the layered architecture documented at `uom_seismic_metadata/reference/history/README.md`.
**Don't invent a parallel metadata format** — emit in this schema so the join is mechanical.

**Epoch boundary = a consequential change in recorder type, sensor type, OR sample rate.**
NOT serial numbers (those are annotation). Coordinates never change within a station code
(UoM always renames on a move, even a few hundred metres), so **coords live once at station
level — no per-epoch coordinate overrides.** The response is **DERIVABLE from
(recorder, sensor, sample_rate)** — constant sensitivity per MODEL.

Per station: identity (net, code, lat/lon/elev) + an ordered `observations:` list (per-source
captures, append-only) + a curated `reconciliation:` / `instruments:` list (the agreed
timeline). The synthesis step in `uom_seismic_metadata` then promotes confident reconciled
epochs into `source/stations/<net>.yaml`.

### Source tagging (what every observation must carry)

Each observation carries a `source:` string identifying both the version and the specific
record. Use these stable prefixes so cross-repo greps work:

| source prefix | version | meaning |
|---|---|---|
| `wiki/<page>` | documentary | DokuWiki page (incl. `site_visits/<sta>`) |
| `pdf/Handover_notes:<ref>` | documentary | The 2024 handover PDF |
| `xml21/<file>` | documentary | 2021 LatestXMLS StationXML |
| `xml/<file>` | documentary | Pre-2021 (Januka) StationXML |
| `user_direct/<YYYY-MM-DD>` | documentary (overlay) | Fact stated by the user in conversation |
| `pcsuds/<file_or_ref>` | **empirical (you emit this)** | PC-SUDS header — sample one per station-day |
| `gecko_ss/<ref>` | **empirical (you emit this)** | Gecko `.ss` kelunjimeta sidecar — one observation per dedup'd snapshot |
| `sds_scan/<ref>` | **empirical (you emit this)** | MiniSEED blockette / SDS-tree-derived fact |

### Authority: per-observation, with per-field overrides

Each observation has an `authority:` qualifier; per-field exceptions via
`authority_overrides:`. The **canonical use case for `authority_overrides:`** is the
operator-input fields inside an otherwise authoritative file:

| level | meaning |
|---|---|
| `authoritative` | machine-emitted by the recorder itself (PC-SUDS `recorder` / `gain` / `sample_rate`; `.ss` `recorder` / `cpv` / `firmware`) |
| `primary` | direct human record at the time (wiki site_visit; install log) |
| `reported` | secondary human description (PDF handover summary) |
| `derived` | read from a metadata file (XML) produced later |
| `operator_input` | picked from a list or typed by a human — **could be wrong**: PC-SUDS `sensor`, `.ss` `sensor_name` / `sens` / `sitename` |
| `placeholder` | known back-date or generic value |

Concrete: a PC-SUDS observation is `authority: authoritative` overall, with
`authority_overrides: {sensor: operator_input, sensor_sensitivity: operator_input}` because
the sensor was picked from a predefined list at deployment time and may be wrong.
Downstream curation must scepticise operator-input fields even when the parent observation
is "authoritative".

### Representing unknowns while keeping epochs

An epoch must exist for every time interval the station was recording, even when we don't
know what was in it. Distinguish three "no value" states — they are NOT interchangeable:

| value | semantic meaning |
|---|---|
| an actual value (`recorder: echopro`) | known and trusted |
| `null` *(or omit the key)* | not applicable — e.g. `end: null` because the epoch is open |
| `unknown` *(string sentinel)* | **we know there WAS a value but we can't determine it from this layer** — e.g. `recorder: unknown` when wiki + PDF disagree |

`unknown` is the right choice whenever this project sees the recording-tree extends across
some time interval but can't read the recorder/sensor identity from header bytes. Don't use
`null` for that case — `null` would imply "no recorder existed", which is wrong.

The synthesis-step generator must refuse to derive a response for an epoch whose core
fields are `unknown` (the math can't run), but the **epoch boundary still stands** —
downstream consumers know the time interval existed. The empirical-vs-documentary join is
typically the path to resolving `unknown`s.

### What this project emits (the empirical version)

**PC-SUDS (EchoPro era):**
- GPS **lat/lon/elev** — recorded accurately. Station-level; never per-epoch.
- **Recorder type** (EchoPro), **datalogger cpv** (counts/V), **sample rate**, **gain** — always present. `authority: authoritative`.
- **Sensor model + sensitivity** — present *when correctly entered*; `authority: operator_input`. When absent or implausible, emit `sensor: unknown` rather than guessing — let the documentary layer fill in.
- ⇒ overall sensitivity = datalogger × sensor when both known.

PC-SUDS embeds full metadata in **every** minute file — sampling one `.dmx` per station-day
(or one per `SS`-session) gives the full metadata picture. **Metadata-harvest is orders of
magnitude cheaper than waveform conversion.**

**Gecko (`.ss` kelunjimeta sidecar):**
The deduped sequence of `.ss` snapshots IS the epoch-boundary list — one observation per
distinct settings_time. Each `.ss` carries:
- Authoritative fields: recorder serial, recorder cpv (per unit!), firmware version,
  gain setting, sample rate, GPS, settings_time.
- Operator-input fields (via `authority_overrides`): sensor name, sens, sitename,
  network_code.

**Per-serial recorder calibration** — the `.ss` records cpv keyed by Gecko serial number.
These populate `uom_seismic_metadata/source/recorder_units.yaml`, keyed by serial, with
provenance back to the `.ss` file. The synthesis-step generator prefers per-serial cpv when
known and falls back to the per-model catalogue.

**SDS / MiniSEED scan:** sample rate + (gecko) band code (250 / 500 → `C`, 1000 → `F`)
can be read directly from headers; where blockettes carry datalogger / sensor descriptions,
record them as `source: sds_scan/...`, `authority: derived`.

### Most stations are 1–2 epochs

Most stations have ONE sensor for life — usually only the recorder (EchoPro→Gecko) or the
sample rate changes, so most stations are just 1–2 epochs. Don't overthink it. New epoch
only on a real recorder/sensor/rate change; serial swaps stay annotation on the existing
epoch.

### 2025 boundary

EQ Server supplies the **historical, CLOSED** epochs, which *prepend* to the **current
open** epoch already held in the canonical `source/stations/<net>.yaml` (the Gecko
migration era). Never duplicate or overwrite the current open epoch.

### Shared catalogue (one per recorder type / one per sensor type)

`uom_seismic_metadata/source/recorders.yaml` (counts/V by preamp gain) +
`uom_seismic_metadata/source/sensors.yaml` (V/unit, units, gain, NRL keys). overall
sensitivity = `sensor.v_per_unit × recorder.counts_per_volt[gain]`; *simple* output is that
sensitivity-only, *complex* is sensor NRL poles/zeros ⊗ recorder. Constant per MODEL (keep
VW CMG-6T-1 = 2400 V/m/s distinct from the SAA-sheet's ~1006). Historical universe ≈ current
+ a few — recorders {Gecko, EchoPro, PiesMo, Reftek RT130, Guralp Minimus}; sensors
{CMG-6T-1, CMG-3ESP, Trillium Compact 20 / 120, IESE S21g / S10g, OYO Geospace HS-1, Guralp
Radian (borehole, digital-output), Willmore MK II/III, Mark L-4, Guralp 5T, Sercel L4C-3D,
Kinemetrics SS-1, Sprengnether HSA3, Guralp Breve (OBS)}.

**Borehole architecture caveat** (Guralp Radian + Minimus at DDWB / DDBE / SCM2):
digitisation is in the sensor, the "recorder" is a passthrough. Doesn't fit the
`cpv × v_per_unit` decomposition cleanly; modelling decision is deferred until per-serial
Radian response is available (typically email Guralp with serial → poles/zeros).

### RT130 WNRO bug — historical context only, NOT active in EqServer

**The 1024-week GPS Week Number Rollover (WNRO) bug was a property of the *raw*
Reftek RT130 stream**, not of what's sitting in the EqServer archive. By the time
data lands under `archive/<STA>/continuous/<YEAR>/...`, the timestamps have
already been corrected (otherwise files literally couldn't be sorted into the
correct year dirs — the directory layout is itself a sanity check).

**Empirical verification 2026-05-27**: sampled headers across the RT130 cohort
(LOYU 2014/2016/2018, SGWU 2018, TRPU 2018) — all 5 files had mseed header
timestamps matching their path dates exactly. No correction needed at Phase 3.

Previous CLAUDE.md text claimed "SGWU pre-2024-12 and LOYU 2014-2024 are shifted
by 1024 weeks" — that claim originated from Layer A (`vw_reconciliation.yaml`,
best-effort historical reconciliation, not field-verified) and described the
raw RT130 source, not the EqServer-archived files. The predecessor pipeline
fixed it at ingest for all RT130 stations (TRPU was specifically called out,
but the same applies to the cohort).

**Caveat:** if a future scan turns up RT130 files with header/path mismatch,
this assumption must be revisited. Until then, treat WNRO as a closed concern.

### Granularities, one flow (not three competing truths)

`station_registry.yaml` (station-level scope/network) → SQLite manifest Level 4 (recorder
transitions, rates, spans — the raw material) → **empirical version** (per-station YAML,
sources tagged `pcsuds/` / `gecko_ss/` / `sds_scan/`, the artifact `uom_seismic_metadata`
ingests into its `reference/waveform_db/` layer) → joined with the documentary version
→ canonical `source/stations/<net>.yaml`.

---

## Testing strategy

### Semi-random and targeted test suite

Once Claude has VM access, a structured test suite should be built covering:

**Random sampling**
- Select N random station-days from the manifest, stratified by recorder type and year
- Run the full pipeline on each; compare output SDS against expected channel/day presence
- Automate: draw random sample → process → validate SDS structure → report pass/fail

**Targeted edge cases** (identified from legacy notes and archive characteristics)
- Days with both disk and telemetry files for the same minute
- Days with triggered accelerometer files sharing the dominant SS (hardest case)
- Days with multiple SS values (recorder restart mid-day)
- Days with < 1440 files (incomplete day)
- Days with > 1440 files (extra triggered or multi-channel files present)
- Gecko days with older filename formats (no seconds field, pre-2018 DDNE format)
- Mixed recorder type days (both EchoPro and Gecko files present)
- Days near station transitions (recorder swap, station rename)
- Early archive years (2012–2015) where conventions may differ

**Regression tests**
- Fix a small set of known-good station-days (verified against legacy pipeline output) as golden references
- Run new pipeline on these; diff output MiniSEED headers

**Test directory structure** (suggested on VM)
```
~/sds_conversion_tests/
    test_inputs/          # symlinks or copies of specific day dirs from archive
    test_sds/             # output SDS for test runs
    test_manifests/       # per-day pre-scan outputs
    reports/              # test pass/fail summaries
```

### Multi-unit pipeline test (the harness that catches orchestrator bugs)

Per-day correctness tests above don't catch failures in the ORCHESTRATOR
(convert.py + promote.py + cleanup.py + apply.py + ledger). That class of bug
is what bit the project on 2026-05-31 (the BEST 2024-under-BEST-2025-run_id
year-leak). Before any production sweep restart, exercise the orchestrator as
a system. Tested workflow on 2026-06-01 — see `agent memory:
project-pipeline-test-results` and `project-stress-test-results` for full
results.

**Infrastructure: proxy LT + local-bare ledger, NO real-target writes.**

The test infrastructure is deliberately distinct from production:
- **Proxy LT**: mount a fresh CIFS share (e.g. `proj-6700_sds_other_networks`)
  at a distinct mount point (`/mnt/test_lt`) on dev1 AND on the staging VM
  (ro on staging is enough; cleanup.py only reads LT there). Use a
  project-tied scratch subdirectory (e.g.
  `/mnt/test_lt/eqserver_pipeline_test_<YYYYmmdd>`) as the `--lt-root` so a
  one-typo `--lt-root /mnt/test_lt` still couldn't conflate with the real
  filesystem.
- **Local-bare ledger**: `git init --bare /var/tmp/.../test_ledger_bare.git`
  on dev1, then `git push` the real ledger main to it, then clone the bare
  into `test_ledger_clone`. The clone's `origin` URL **must** end in
  `.git` and **must NOT** contain `github|unimelb|gitlab|bitbucket` —
  verify in the same shell pipeline that creates it so a slip cannot leak.
  Autocommits push to the bare, not to real github.
- **Distinct staging + queue paths**: e.g.
  `/mnt/seiscomp_staging/test_round_<YYYYmmdd>/{seiscomp_archive,queue}`
  so a typo in `--staging-sds` or `--queue-dir` cannot collide with the
  real `/mnt/seiscomp_staging/seiscomp_archive` or
  `/mnt/seiscomp_staging/eqserver_sweep`.

**Pre-flight checks that ABORT before any test run if they fail.**

- `stat -c %d /mnt/test_lt` vs `stat -c %d /mnt/seiscomp_archive` —
  device IDs MUST differ.
- `mount | awk '/<test path>/'` vs `mount | awk '/<real path>/'` —
  source CIFS URLs MUST differ literally.
- `find /mnt/test_lt/<scratch> -maxdepth 2 -type d -regex '.*/20[12][0-9]'`
  MUST be zero (no year-dirs from prior runs).
- Test ledger clone `git remote -v` MUST be exactly one remote and MUST
  NOT contain external repo hosts.
- `git -C <real ledger> status --porcelain` MUST be empty (no working-tree
  dirt that could leak into autocommit).
- `pgrep -af "run_production_(convert|promote|cleanup)\.py" | grep -v
  <test path>` MUST be empty on BOTH hosts (no production watcher will
  race with the test on shared mounts).
- Capture real ledger HEAD + real github main SHA **into files** for
  post-test comparison.

**Scenarios that MUST run before a production sweep.**

1. **S0 — Falsifiability control.** Patch promote.py (or apply.py) with the
   bug REMOVED (e.g. `sed -i 's|"--year", str(...),||' promote_unfixed.py`),
   run the same multi-unit workload, assert the leak DOES appear. If S0
   cannot reproduce the bug, the subsequent year-scoping assertion is
   meaningless — ABORT the whole suite.
2. **S1 — Race + year scoping.** Convert two consecutive units sharing a
   station (different years). Open the race window by starting the second
   unit's phase3 in background. Assert that the first unit's events.jsonl
   contains only its own year's day strings and a single run_id matching
   that year.
3. **S2 — SKIP path (LT preserved).** Re-convert a unit whose LT data is
   already byte-equivalent. `apply.py` MUST report `skip` for every
   day-channel; LT bytes MUST be unchanged. This is the case operators
   most fear: "what if seedlink LT data gets overwritten by an eqserver
   sweep?" Answer: not if samples match.
4. **S2b — OVERRIDE path → held.jsonl.** Truncate one LT day-file (simulate
   partial pre-existing LT). Re-convert. `apply.py` dry-run MUST report
   `override > 0`. promote.py MUST refuse to call `--commit` and instead
   append to `held.jsonl`. No `.commit.log` created. LT bytes unchanged.
5. **S3 — Cleanup year-safety witness.** Plant a fake staged file in a
   year that was never promoted. Run cleanup. Assert the witness survives
   (cleanup.py is year-blind in *what it walks* but safe-by-construction
   via the LT-match-required precondition).
6. **S4 — Provenance triangle.** For every events.jsonl line, assert the
   referenced `policies/<sha>.yaml` and `runs/<run_id>/run.json` files
   exist. Assert real ledger HEAD and real github main are unchanged.
7. **S5 — setsid detach.** Launch each watcher via
   `setsid -f bash -c 'exec CMD > LOG 2>&1' < /dev/null`. Disconnect the
   launching SSH. From a fresh SSH session, verify each PID is still
   alive. Yesterday's `nohup ... &` failed this test.
8. **S6 — Process group kill.** Kill convert.py via
   `kill -KILL -<PGID>` (negative PID = process group) and verify the
   phase3 child also dies (no orphan).
9. **S7 — Resume after kill.** After S6, restart convert.py with the same
   args. Assert the killed (sta, year) is correctly re-queued and NOT
   listed in `convert_done.jsonl` until it actually completes.

**The detach recipe (record so it doesn't get lost).**

```bash
setsid -f bash -c 'exec PYTHON SCRIPT [args] > LOG 2>&1' < /dev/null
```

- `setsid` — new session/PGID, no controlling terminal, immune to SIGHUP
- `-f` — fork into background (launcher SSH can exit immediately)
- `bash -c 'exec ...'` — `exec` replaces bash with python, no wrapper
  process to confuse `pgrep`/`kill`
- `> LOG 2>&1` — capture all output
- `< /dev/null` — disconnect stdin so the process never blocks

To kill cleanly afterwards: `kill -KILL -<PGID>` catches the phase3 child too.

**Teardown.**

After sign-off, the teardown is mechanical:
- Kill any remaining watchers (`pgrep -af run_production_*test_round | xargs -r kill`)
- `rm -rf` all `/var/tmp/test_round_<DATE>*` and
  `/mnt/seiscomp_staging/test_round_<DATE>*` paths
- Remove project-named scratch dirs INSIDE the proxy mount, then
  `sudo umount /mnt/test_lt` on each host

Keep the local-bare and clone for forensic review until you're sure the
sweep is healthy, then `rm -rf` those too.

---

## Open questions

These should be resolved by archive scanning, not by assumption. Each item is a task to assign when VM access is available.

**EchoPro / PC-SUDS**
- Does `SS` stay consistent across all channels within a recording session (i.e. can we use SS per-station rather than per-channel)?
- Are there other file extensions beyond `.dmx` and `.dmx.gz` in EchoPro day directories?
- How far back do the EchoPro naming conventions (3-underscore disk / spaced telemetry) hold? Do pre-2016 directories use different patterns?

**Gecko**
- Is the absence of a seconds field in older filenames (`2020-11-08 0001 DDSW.ms.zip`) a reliable indicator of telemetry vs local, or was the convention different before a certain date?
- What is the earliest Gecko data in the archive, and what filename format does it use?

**Guralp Radian**
- Which stations used Guralp Radian recorders, and for what date ranges?
- What file extensions and naming conventions are present in their day directories?
- Was data pre-converted to MiniSEED before archiving, or does the raw format appear anywhere?

**Reftek**
- Which stations used Reftek recorders, and for what date ranges?
- What file extensions and naming conventions are present in their day directories?

**Piesmo**
- Which stations used Piesmo recorders, and for what date ranges?
- What file extensions and naming conventions are present in their day directories?

**General**
- What is the full set of file extensions present across the entire archive? (One `find` sweep to answer this)
- Are there day directories that contain files from more than one recorder type? If so, what is the cause (recorder transition mid-month, ingestion bug, etc.)?
- Are there station directories outside the `continuous/` subtree that need to be handled?
