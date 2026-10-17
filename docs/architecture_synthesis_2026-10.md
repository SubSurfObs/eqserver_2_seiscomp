# Architecture review — synthesis of two independent assessments

**Inputs:** `docs/architecture_review_2026-10.md` (Claude) and
`reviews/2026-10-17_codex_architecture_assessment.md` (Codex, written
without sight of Claude's). Both grounded in the audited archive reality.
**Status: options for the operator to arbitrate. Nothing here is decided.**
A third input — the adversarial review of the registry-absorbs-plan idea —
is still running and slots into Theme 2 when it lands.

Codex's one-sentence verdict, which Claude endorses: *keep the safety
shell; replace the decision core — "human-approved identity epochs plus
machine-generated, immutable, verified source selections."*

## Governing principle (operator, 2026-10-17 — overrides priority ordering below)

**The UoM EqServer instance is due to be decommissioned in ~2.5 months
(≈ early 2027-01). Conversion — capturing every byte — is the
irreversible, deadline-bound act. Channel naming is cheap and largely
independent afterwards:** once data is day-long SDS files, renaming is a
mechanical header-edit + file-rename pass, auditable, reversible, doable
any time. Consequences: (1) completeness and throughput outrank naming
elegance everywhere; (2) Theme 7 is demoted from "DU blocker" to "don't
LOSE data because of naming; approximate codes are acceptable, corrected
post-hoc"; (3) VW conventions are already right; DU's deltas are modest
and must not become a research project; (4) ANSWERED (operator,
2026-10-17): "Everything goes. The 16 TB archive goes." All repair work,
unknown-station triage, and future re-reads are deadline-bound; the
'origin is immutable' safety assumption expires at decommission.
PROPOSAL (pending operator decision): mirror the raw archive to a
mediaflux share (30 TB auto-grant, soft-delete, offsite-replicated) as
insurance priority zero, running in the background over the coming
weeks; optionally ask Research IT for a backend-level copy.

## Theme 1 — Source selection becomes a materialized, consumed artifact

**Agreement: total (both reviewers' #1).** Selection is where every
audited defect lived; today it is recomputed at runtime and the plan hash
proves only which *summary* accompanied a run, not which file produced
which output byte (Codex's concrete exhibit: WLSH's plan says 100% clean
with an empty source_priority).
**Options:** (a) per-run selection DATABASE (SQLite per station-year/run:
chosen source, usable channels, rule+version, rejected alternatives) —
Codex; (b) per-station selection jsonl bound to plan+manifest hashes —
Claude's earlier shape; (c) deterministic shared chooser library with
decision receipts, no stored table.
**Joint recommendation:** (a) — a selection table, not YAML ("do not put
millions of minute decisions in YAML"), with the converter REFUSING raw
manifest input unless a valid selection artifact accompanies it.
Claude's contract-binding requirements (manifest identity, generator SHA,
fail-closed) apply to it unchanged.

## Theme 2 — Where human judgment lives (the registry/plan question)

**Agreement on the principle, three options on the artifact.** Both
reviewers independently concluded: human judgment belongs on IDENTITY
(network, location, physical component → SEED channel epochs) with
exception-driven review; routine per-station plan approval should go.
**Options:** (a) status quo fat plans; (b) ONE living registry absorbing
dated identity epochs + mappings (operator's idea; adversarial review
pending); (c) small per-station identity CONTRACTS, schema-validated,
seeded from StationXML/registry, approval only on new/changed/conflicted
epochs (Codex).
**Trade-offs in brief:** (b) one document to rule them all, best
operator ergonomics at small scale; risks: 255-entry single-file growth,
merge/typo blast radius, provenance fingerprinting of a mutable file.
(c) keeps per-station provenance snapshots trivial and diffs small;
costs: 126+ small files instead of one view (tooling can give the
one-document VIEW over per-station storage — the options may converge).
**Decision needed from the operator after the pending stress-test lands.**

## Theme 3 — State: one event log + run-scoped workspaces

**Agreement on the disease** (ten loosely joined stores; unit state needs
a 4-way join; promotion walks a shared mutable staging tree that can
contain other runs' bytes — both pipelines write the same namespace).
**Complementary cures:** Claude: ONE typed append-only event log per
sweep + a single derivation tool. Codex (stronger): each run gets an
immutable workspace (inputs, identity, selection, output SDS, output
INVENTORY, verification, state) and promotion consumes only the recorded
inventory — never discovers files by walking staging.
**Joint recommendation:** both — run-scoped workspaces with output
inventories as the unit of promotion; queue events kept but as pointers
to runs. This also fixes Claude's H (shared-staging contamination)
more fundamentally than a namespace split.

## Theme 4 — Completion semantics (Codex-only finding; Claude endorses)

Today a unit can exit 0 on a partial run and get queued; promotion gates
only on overrides. The HOLS-716-days incident class lives here. Codex's
closure invariant, recommended jointly: promote verified day-channels
individually, but a station-year is COMPLETE only when
`expected = selected = converted + explicit_no_data = verified + explicit_waiver`,
with waivers durable, narrow, human-authored. This replaces ad-hoc
partial-completion guards with one checkable equation.

## Theme 5 — Schemas and the domain package

**Agreement** (implicit, unversioned schemas; taxonomies duplicated and
already diverging — Codex found size_stats missing `perchan_mseed` while
channel_epoch_scan knows it; recorder cohorts hard-coded in scripts).
**Joint recommendation:** one small internal domain package (versioned
models, filename grammars, recorder capabilities, validators) backed by
a versioned SQLite schema; YAML reserved for operator-owned identity;
every machine artifact carries schema_version + generator SHA + input
hashes. Registry validation grows types/required-fields/epoch-overlap
checks (today it only catches duplicate keys).

## Theme 6 — Code packaging and dangerous leftovers

**Agreement:** the path-import of the engine from a sibling checkout is
the architectural root of the pin/side-load problem; move the SUDS
reader + SDS writer into versioned installable packages (sudspy-centred),
keep the ledger as a separate privileged application. **Codex additions
Claude missed, verified-worthy:** obsolete-but-executable entrypoints
(`run_production.py` can promote AND delete staging in one process,
against the dev1-only boundary; `phase3_driver_v2.py` carries different
gate semantics) must be moved out of executable paths; preflight exists
but nothing production calls it yet — it belongs inside the one canonical
CLI so a run cannot start unpinned.

## Theme 7 — A concrete DU-blocking defect surfaced by the review

`phase3_driver._enforce_band_instrument` rewrites EVERY channel to
band+H+orientation unconditionally, and the engine's FDSN path is never
fed an inventory. Fine for VW/VX broadband; wrong for DU short-period
codes (SH*/EH*) which cannot be derived from rate. Whatever architecture
wins, DU cannot launch until naming enforcement is identity-driven.
(Claude: verified the unconditional call in both engine branches this
session; FDSN-unused and run_production.py claims to be spot-verified
during implementation.)

## What BOTH reviews keep unchanged

Read-only origin; staging/LT separation; dev1-only atomic LT writes;
never-delete; dry-run defaults; append-only ledger + content-addressed
records; SQLite as the raw inventory substrate; "convert what is on
disc"; the conversion/writer core itself; the independent sample-level
auditor — promoted from campaign tool to automatic completion gate; human
review retained for LT overrides and identity ambiguity only.

## Decisions this asks of the operator

1. Theme 2 artifact shape — after the pending adversarial review: one
   living registry vs per-station identity contracts (or contracts with
   a single-view tool).
2. Adopt the closure invariant as the completion rule (Theme 4)?
3. Adopt run-scoped workspaces + inventory-driven promotion (Theme 3)?
4. Library packaging direction (Theme 6) — affects disk_to_sds too.
5. Sequencing within the week: Themes 1+7 are DU-critical; 3–6 can land
   incrementally after.
