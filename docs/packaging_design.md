# eq2sds as a reusable package — design draft 1

**Status: DRAFT (2026-10-17), Claude ↔ Codex iteration track. Separate from
the v3 build; informs its boundaries so nothing new gets welded to UoM
specifics.**

## The idea

UoM's EqServer is not the only legacy EqServer holding a decade of PC-SUDS
and miniSEED minute-files. The conversion problem — walk the archive,
understand the file grammars, choose among duplicate sources, convert to a
clean SDS tree, and *prove* the output matches the input — is generic.
Package the pipeline so another operator can point it at their EqServer
archive and get a verified SDS archive out, without inheriting any UoM
infrastructure.

## What generalises (ships in the package)

| Piece | Why it's generic |
|---|---|
| Archive walker / manifest builder (Stage 1) | EqServer's `archive/<STA>/continuous/<Y>/<M>/<D>/` layout is the product's layout, not ours |
| File-grammar parsers | EchoPro/Echo (Kelunji), Gecko, per-channel mseed, telemetry variants — Kelunji/Gecko hardware is common across Australian networks; grammar table is extensible data, not code |
| Day profiling / classification (Stage 2) | Pure functions of the manifest |
| Plan + selection generation with human review gate (Stage 3) | The review-before-convert pattern is the design's core safety idea and is site-agnostic |
| Conversion engines (Stage 4) | SUDS→miniSEED via sudspy; mseed merge; SDS writer |
| **The QA comparator** | A converter that ships with an independent sample-level auditor ("prove the output against your originals") is the differentiating feature — no one trusts a black-box converter with irreplaceable data |
| Preflight / provenance recording | Run manifests + code-version stamping, generic |

## What stays out (site adapter layer)

- **Promotion/archival** (our ledger, dev1, mediaflux): the package's
  contract ENDS at "verified staging SDS tree + run manifests + QA
  report". Every site archives differently; ours remains a thin consumer.
- Station registry *contents*, network/channel policy, FDSN endpoints:
  the registry YAML schema ships; the data is the operator's. The
  registry remains the single user-facing source of truth — an operator's
  first and main task is filling it in.
- Hosts, mounts, worker counts: a single site config file.

## Proposed shape

- One installable Python package `eq2sds` (deps: obspy, numpy, pyyaml,
  sudspy). CLI = the locked stage vocabulary:
  `eq2sds manifest|profile|plan|convert|qa`, each stage resumable,
  preflight built in.
- **Blocker to resolve: the engine lives in a different repo.**
  `disk_to_sds/scripts/suds_convert.py` is the shared conversion core.
  Packaging forces the already-queued split: generic `sds_writer` +
  `suds_convert` move into the package (or into sudspy), and both UoM
  pipelines consume them from there. This retires the cross-repo pin
  problem at the same time.
- sudspy itself must be releasable (it already is a clean library).
- Site onboarding path = exactly our own DU path: fill registry → run
  manifest/profile → review generated plans → convert a pilot station →
  QA report → scale. We are user zero; DU is the acceptance test for the
  packaging boundaries.

## Performance is site-specific; correctness is universal (operator insight 2026-10-17)

UoM's slowness is a property of our storage (millions of minute-files
behind NFS), not of the conversion. A site with the archive on local disk
gets near-corpus speeds from the existing engine. Therefore: the package
bakes in NO NFS workarounds. Archive access is a plain directory path;
the staging prefetcher ("bulk-copy unit N+1 while converting unit N") and
mount-tuning guidance ship as an optional add-on plus a site-tuning doc,
off by default. Benchmarks published with the package must state the
storage topology they were measured on.

## Design rules for the v3 build (so packaging stays possible)

1. No absolute UoM paths outside one site config.
2. Recorder/grammar knowledge lives in data tables, not `if station ==`.
3. Every stage's inputs/outputs are files with documented schemas
   (registry, plan, selection, run manifest, QA report).
4. The ledger/promote integration imports the package, never the reverse.

## Open questions for review

- Package boundary: is "ends at verified staging SDS" right, or should a
  minimal reference promote step (atomic copy, no ledger) ship too?
- Where do the engines land: inside `eq2sds`, or in `sudspy` (keeping
  eq2sds pure orchestration)?
- Registry schema versioning and migration for external users.
- Licensing / repo home / name collision check.
- How much of the UoM-specific classifier lore (Minimus overrides, PiesMo
  defers, WNRO notes) is generic EqServer knowledge vs our fleet's?
