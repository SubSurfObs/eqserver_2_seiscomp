# eqserver_2_seiscomp — Design Notes

## Goal

Replace the legacy bash + Java EqConvert pipeline (in `legacy/`) with a Python pipeline that converts a decade-scale EqServer waveform archive into a clean SeisComP SDS archive.

**Core dependency**: `sudspy` (`/Users/DSAND/projects/SubSurfObs/sudspy`) — provides PC-SUDS parsing and ObsPy bridge. The pipeline is built around Python and sudspy/ObsPy. SeisComP CLI tools (`scmssort`, `scart`) are available on the VM and were central to the legacy pipeline, but are not assumed to be required in the rewrite — see Toolchain section.

---

## Shared infrastructure & sibling project (added 2026-05)

This project no longer stands alone. It shares a VM, a staging SMB mount, the
staging SDS skeleton, and (ultimately) a manifest ledger with a sibling project,
**`sdcard_to_sds`** (`/Users/DSAND/projects/SubSurfObs/sdcard_to_sds`), which
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

### Shared diagnostic: duration ratio

`sds_staging_ledger/plot_card.py` (and `sdcard_to_sds/plotting/`) compute a
**duration ratio** = (in-record sample-time) / 86400 — ~1.0 clean, ~2.0
duplicated, <1.0 gap. Same QC lens this project should use to validate converted
output; it's exactly what surfaced the upstream seedlink doubling in the live VW
feed. **File size is too noisy (±30–50% from compression) for completeness —
use the duration ratio.**

### Station registry = shared station→network truth

`metadata/station_registry.yaml` is the authoritative VW/VX/DU network
assignment per station. EchoPro/PC-SUDS files carry **no network code**, so the
registry is mandatory for network patching here. Several registry stations (e.g.
MARD, TRPU) ALSO arrive as Gecko SD cards in `sdcard_to_sds` — the registry is
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
- Triggered/exclude patterns: `.trig.dmx`, `.ss`, `_CHZ.mseed.zip`
- Note: older Gecko data (pre-~2018) uses different naming conventions — see Open Questions

**Guralp Radian** (MiniSEED — details unverified)
- Broadband recorder; data either written natively as MiniSEED or pre-converted before archive ingestion
- File naming conventions, extensions, and station associations: **to be confirmed by archive scan**

**Reftek** (MiniSEED — details unverified)
- Data either written natively as MiniSEED or pre-converted before archive ingestion
- File naming conventions, extensions, and station associations: **to be confirmed by archive scan**

**Piesmo** (MiniSEED — details unverified)
- Data either written natively as MiniSEED or pre-converted before archive ingestion
- File naming conventions, extensions, and station associations: **to be confirmed by archive scan**

### Recorder detection strategy

Recorder type is detected per day directory at Level 1 scan (filename/extension only):
- Presence of `.dmx` or `.dmx.gz` → EchoPro
- Presence of `.ms.zip` or `.ms` (without `.mseed`) → likely Gecko
- Presence of `.mseed.zip` or `.mseed` → likely Centaur/Guralp/Reftek/Piesmo
- Mixed extensions in one day directory → flag as `edge_case`; may indicate recorder transition or archive ingestion anomaly
- Unknown extension → flag as `unknown`; log for investigation

These heuristics should be validated against the archive before being treated as reliable.

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

- Parse all filenames: extract `HHMM`, `SS`, station code
- Discard: `.trig`, `.ss`, `.mseed.zip` patterns
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
- Write per-channel day MiniSEED: `Stream.write(path, format="MSEED", reclen=4096)`
- No gap filling — gaps preserved as separate traces within the day file (standard SDS)

### Channel remapping

After producing day MiniSEED, remap to target SEED codes:
- All seismometer channels → band code `CH` (consistent across instrument changes)
- Target network, location from config
- Example: `AB.ABM5Y.60.DLZ → VW.ABM5Y.00.CHZ`
- **Implementation open**: can be done via `scart --rename` (legacy approach) or in pure Python by editing ObsPy `Trace.stats` fields before writing. Python approach is preferred if it avoids a subprocess call per day; scart is acceptable as a fallback. See Toolchain section.

---

## Deduplication priority

1. Disk + continuous session (dominant SS) — preferred
2. Telemetry + continuous session — fallback if disk incomplete
3. Different SS (triggered/accelerometer session) — discard
4. Explicit triggered patterns (`.trig`, `_CHN.mseed.zip` etc.) — discard

---

## Performance and parallelism

**Parallelism is a first-class design requirement**, not an optimisation to add later. The VM has 24–32 cores confirmed, possibly up to 64 (exact spec TBC). All pipeline stages must be designed to exploit this from the outset.

**Legacy benchmark**: EqConvert file-by-file with 16 parallel procs ≈ 1 min/day (subset). Full 3000-file day ≈ 3 min. Target: 10× improvement.

**Python pipeline advantages over legacy**:
- No JVM startup overhead per file (dominant cost in EqConvert file-by-file mode)
- In-memory merge: no intermediate MiniSEED files written per minute
- Fast path skips all file opens for clean days

### Processing unit: one station at a time

**The pipeline processes one station at a time.** All parallelism is within that station's workload. Reasons:
- Matches the staging architecture: one station fills staging → verify → rsync → clear → next
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

### Pre-scan parallelism

Level 1 filename scan can itself be parallelised: split the station list across workers, each walking one station's directory tree independently. SQLite WAL mode handles concurrent inserts safely if each worker uses its own connection.

---

## Configuration

```yaml
# config.yaml

# Storage paths (current staging VM — see Shared infrastructure section)
archive_path: "/mnt/eqserver_archive/shared/data/repository/archive"
staging_sds_path: "/mnt/seiscomp_staging/seiscomp_archive"   # shared with sdcard_to_sds
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
target_network: "VW"
target_location: "00"
target_channel_base: "CH"        # seismometer band code
mseed_record_length: 4096

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

> **Superseded specifics — see "Shared infrastructure & sibling project" above
> for current mounts.** The three-tier model still holds; the concrete paths/
> shares below are updated there (origin = NFS `/mnt/eqserver_archive`, staging
> = shared CIFS `/mnt/seiscomp_staging/seiscomp_archive`, long-term = CIFS
> `/mnt/seiscomp_archive`). The staging mount is now **shared with
> `sdcard_to_sds`**, not a per-project share.

The pipeline operates across three tiers of storage, all accessed from the staging VM:

```
[Origin archive]               [Staging SDS]                    [Long-term SDS]
NFS /mnt/eqserver_archive  →   CIFS /mnt/seiscomp_staging   →   CIFS /mnt/seiscomp_archive
shared/data/repository/        seiscomp_archive/                 <YEAR>/<NET>/<STA>/...
archive/<STA>/...              <YEAR>/<NET>/<STA>/...
  READ ONLY                     WRITE (both pipelines)            WRITE via ledger apply (rsync/cp)
```

**Origin archive** (`/data/repository/archive/`)
- ~25 TB SMB mount on the EqServer VM
- Read-only for this pipeline — never modify files in place
- I/O is the dominant bottleneck for large scans; minimise redundant reads (manifest caching, fast paths)

**Staging / test SDS** (Mediaflux, CIFS/SMB)
- Mounted at `~/mnt` on the processing VM
- Share: `//mediaflux.researchsoftware.unimelb.edu.au/proj-6700_uom_seismic_data-1128.4.1143`
- Credentials in `/etc/cifs-mediaflux` (root-readable only); mount command requires credentials file — password contains special characters that break inline `-o password=` parsing
- Used as the write target while processing one station at a time; also used for day-to-day testing
- Pipeline writes complete per-station SDS output here first
- Allows verification before committing to the long-term archive
- Can be wiped and reused between stations to manage space

**Long-term archive** (Mediaflux, SMB)
- Final destination SeisComP SDS archive
- Populated via `rsync` from staging, not written directly by the pipeline
- rsync is run manually (or scripted) after per-station output is verified

### Implications for pipeline design

- **Processing unit is one station**: complete one station fully (all years) → verify → rsync → clear staging → next station. This is a deliberate design choice, not a constraint — see Parallelism section.
- **Staging space budget**: estimate one station's full SDS output size from the manifest before starting (total compressed source size × expansion factor); confirm staging has headroom
- **rsync**: `rsync -av --checksum` to avoid overwriting already-correct files; always `--dry-run` first
- **Manifest lives on VM local disk**, not on any SMB mount, to avoid I/O overhead on frequent reads/writes during scanning

---

## VM access and origin archive navigation

> **Current host is the shared staging VM `rs-l-0ezd3a` (`172.26.144.41`) — see
> "Shared infrastructure" above.** It is an `rs-` VM but NFS access to
> `research-nfs:/6000/6250-mei` HAS been granted and is mounted ro at
> `/mnt/eqserver_archive` (in fstab). The `rd-`/`rs-` guidance below is retained
> for context / future VM requests but is already resolved for this host.

The origin archive lives on a remote Linux VM (EqServer host). Claude Code has direct SSH/connection access to this machine. Key points:

### VM type matters for NFS access

UoM Research IT provisions two VM flavours with different network policies:

- **`rd-` prefix** (Research Desktop, e.g. `rd-l-y9d9pt`) — has NFS mount access to `research-nfs.unimelb.edu.au` granted by default
- **`rs-` prefix** (Research Server, e.g. `rs-l-pg2zyo`) — does **not** have NFS mount access by default; requires IT to explicitly grant it

The working VM for this project was `rd-l-y9d9pt` (now decommissioned). If setting up a new VM, request an `rd-` type or ask IT to grant NFS access to `research-nfs.unimelb.edu.au:/6000/6250-mei` for the new VM's IP.

### NFS mount command (once access is granted)

```bash
sudo mkdir -p /mnt/eqserver_archive
sudo mount -t nfs -o ro,noatime,nodiratime,vers=4.0,rsize=1048576,hard,proto=tcp,port=0,timeo=600,retrans=2,sec=sys,local_lock=none,actimeo=600 research-nfs.unimelb.edu.au:/6000/6250-mei /mnt/eqserver_archive
sudo ln -s /mnt/eqserver_archive/shared/data/repository /data/repository
```

Add to `/etc/fstab` for persistence:
```
research-nfs.unimelb.edu.au:/6000/6250-mei  /mnt/eqserver_archive  nfs  ro,noatime,nodiratime,vers=4.0,rsize=1048576,hard,proto=tcp,port=0,timeo=600,retrans=2,sec=sys,local_lock=none,actimeo=600  0  0
```

- Archive root: `/data/repository/archive/<STATION>/continuous/<YEAR>/<MONTH>/<DAY>/`
- The VM runs as user `seiscomp`; tools like `scart`, `scmssort` are available on PATH
- Legacy Java tool `eqconvert.jar` is at `~/software/eqconvert.jar` (or `/home/sysop/mnt/software/eqconvert.7/eqconvert.jar`)
- **Do not modify files in place** on the VM — the EqServer archive must remain intact; all reads are safe, all writes go to a separate output path or temp dir

When scanning or testing directly against the VM, navigate using absolute paths. File listing via `ls` or `find` is safe; writing test SDS output to a scratch directory on the VM (e.g. `~/sds_conversion_tests/`) is acceptable during development.

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
- Station, year, month, day (from directory path)
- Recorder type classification (EchoPro / Gecko / Centaur / unknown) — from file extension
- Source type: disk (underscore) vs telemetry (space)
- `HHMM` and `SS` fields parsed from filename
- Exclude flags: `.trig`, `.ss`, wrong extension, wrong station in filename
- File size and mtime (from `stat`)

Output: the `files` table fully populated. Enables all flow-control decisions that don't require opening files.

**Level 2 — Header scan (decompress headers, skip data payloads)**

Runs `scan_suds_file()` (sudspy) or reads MiniSEED fixed header on files that survived Level 1. Adds per-file:
- Channel names present in the file (e.g. `DLZ`, `DLE`, `DLN`, `DNZ`…)
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

Remapping from EqServer/EqConvert stream codes to target SEED codes is required for every station. This is a non-trivial mapping: different stations have different instrument histories, and the source codes (network, location, channel band/instrument codes) vary.

A **station/channel mapping resource** is required — likely a YAML or CSV config file — that specifies per-station:

- Source network code (e.g. `AB`)
- Source location code (e.g. `60`)
- Source channel patterns (e.g. `DL?`, `EL?`, `DH?`)
- Target network (e.g. `VW`)
- Target location (e.g. `00`)
- Target channel band (always `CH` for seismometers)
- Whether to discard accelerometer channels (`BN*`, `DN*`)
- Any station name remapping (e.g. old code → new code)

This mapping table must be populated before conversion of any station. It should be versioned alongside the pipeline code. The `generate_remap_string.sh` script in the legacy pipeline inspects the actual MiniSEED stream names to build these strings dynamically — the Python pipeline should do the same but driven by the config table rather than runtime introspection where possible.

**Key SEED band code context** (from legacy notes):

| Code | Instrument | Frequency |
|------|-----------|-----------|
| D/E  | EchoPro output (via EqConvert) | ≥80 Hz or 250 Hz |
| C    | Target for all seismometers | consistent across instrument epochs |
| H/L  | High/Low gain seismometer (instrument code) | — |
| N    | Accelerometer (instrument code) — exclude | — |

The plan is to convert all seismometer channels to band code `C` (`CH?`) regardless of source, to maintain consistency across instrument changes over the decade-scale archive.

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
