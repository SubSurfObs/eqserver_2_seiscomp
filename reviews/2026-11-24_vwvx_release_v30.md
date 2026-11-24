# Review: VW/VX re-sweep release v3.0  (2026-11-24, reviewer: Codex)

## Verdict

PROCEED.

Release v3.0 may begin bulk Stage-4 conversion. This verdict does not clear
any staged file for promotion: F1's content comparison remains a hard Stage-5
gate and should be built while conversion runs. No further proving-day
campaign is warranted.

## Findings

### F1 — blocker (promotion only) — bind exact content verdicts to immutable inputs

- Where: proposed staged-versus-LT comparison tool;
  `reviews/2026-11-18_vwvx_resweep_plan.md` F1/F2;
  `docs/vwvx_resweep_plan.md:18-23`.
- Status: verified.
- Scenario: the proposed comparison is the right replacement for sample-count
  decisions, but three verdicts alone are not yet a safe hand-off. First, a
  live or staged file can change after it is checked; an unbound `superset`
  verdict could then authorize bytes the checker never saw. Second, equal
  coverage and equal samples can still have the v1 fragmentation/topology
  defect the re-sweep is meant to repair. Calling that merely `identical` and
  skipping it would preserve the defect. Third, ObsPy whole-stream merging can
  move record starts, as the LKHRT case demonstrated, so a comparison built on
  merged streams can hide or invent timing differences.
- Smallest fix or check: build the tool now, in parallel with conversion, with
  these requirements:
  1. Decode records without joining or moving their stated starts; compare on
     integer-nanosecond sample times and exact integer sample values. Different
     MiniSEED encoding or record boundaries alone must not count as a conflict.
  2. Require every LT sample to exist with the same value before calling
     staging a strict superset. Equal-count/equal-time days also require exact
     value equality, not sampling.
  3. Distinguish at least: exact payload and topology (skip); exact payload but
     staged topology is cleaner (safe replacement); staging strict superset
     with identical overlap (safe replacement); LT superset (keep LT); and
     conflict/mutually unique coverage (hold). Conflicting duplicate values at
     one timestamp are always a conflict.
  4. Record the comparator code identity plus cryptographic hashes of both
     input day-files in every verdict. Immediately before applying it, Stage 5
     must verify those hashes and refuse or recompute a stale verdict. This is
     especially important for live SeedLink years.
  5. Keep live SeedLink LT authoritative unless staging passes the same strict-
     superset test. Intentional retirement of old DL*/SUM/CDO/HQ*/CQ* or blank-
     location paths remains an explicit rename/retirement action, never an
     inference from a missing stream.

  Regression cases should include equal counts with shifted times, equal
  times with one changed value, unique data on both sides, a clean strict
  superset, equivalent re-encoding, cleaner staged topology, conflicting
  duplicate timestamps, and an LT file changed after verdict creation.
  Update the still-stale sample-count wording in
  `docs/vwvx_resweep_plan.md:18-23` when this lands. Nothing in this finding
  requires delaying conversion into staging.

### F2 — nit — the `.wrno` uniqueness figure is an estimate, not a complete census

- Where: VM `~/v3_test/wrno_census.jsonl` and
  `~/v3_test/wrno_shift.py`; `docs/vwvx_resweep_plan.md:70-77`.
- Status: verified.
- Scenario: all 10,647 old-named files were header-scanned, and all really do
  carry the bogus 1989/1999 header year. The later duplicate check samples at
  most 40 files per station; it does not exhaustively prove that only about 90
  BEST/CLIF minutes lack a normal copy. Its sample also found unmatched cases
  at WPSH and NARR. Dan's explicit decision to exclude the entire cohort is
  unambiguous and remains a bounded accepted loss, but the stated uniqueness
  count is more precise than its evidence.
- Smallest fix or check: describe the unique-loss number as an estimate from a
  stratified sample, or conservatively state that all 4,432 files in the 1999
  cohort are excluded and many sampled HDDL/MARD/NARR/WPSH files have normal
  copies. Do not delay the sweep or run another census solely for this wording.

No conversion blocker or other should-fix finding was found.

## Checked and found OK

- **Release identity:** the VM release folder is clean. Strict conversion
  preflight passes with eqserver release pin `17f4fab` selecting conversion
  code `0506181`, `disk_to_sds` `84aee00`, and `sudspy` `753feaf`. The release
  imports its sibling engines rather than an ambient checkout.
- **Fresh manifests and plan coverage:** the share contains 59 fresh VW/VX
  station databases made with the same Level-1 parser as v3.0. Across
  152,931,888 non-excluded waveform rows, no post-2012 source date lies outside
  its station plan. The 58 regenerated plans all have `status: ok`, location
  `00`, supported recorder classes and `collision_precedence: earliest_start`.
  FBNK is the apparent 59th station: its fresh database contains only two
  unsupported `.ka2` files and no waveform row, so retaining its old empty plan
  cannot omit convertible data. The four v2.8 VW additions are intentionally
  outside this re-sweep.
- **Overlap rule:** the converter applies earliest-start precedence only after
  final identity mapping and only among traces with the same final identity
  and rate; different-rate conflicts remain loud. It preserves retained
  samples on their stated grids and records every displaced interval in a
  hashed sidecar. On WLSH 2018-07-20 it recorded four on-grid intervals and
  340 displaced samples; the independent audit compared 175,751 retained
  samples with zero bad values and zero missing or selection-loss seconds.
- **Fourth channel:** the plan drops only orientation `O` during WLSH/WDSD
  2018 and HOGN 2019, after naming and before collision resolution; the
  auditor applies the same date-bounded rule and counts the drops. Direct
  source checks at all three stations confirmed CDO is a near-flat, few-count
  signal while Z carries normal seismic amplitudes. The WLSH proof wrote only
  E/N/Z and recorded every dropped trace and sample count. This supports Dan's
  explicit unconnected-input ruling.
- **Decode serialization:** all threaded planner probes enter miniSEED and
  SUDS decoding through `qa_verify.read_source_file`, whose single shared lock
  covers both decoders. File reads and ZIP extraction remain outside the lock.
  The only other thread in the conversion driver is a watchdog and does not
  decode. Production day conversion uses separate processes, so it does not
  share the C callback state. WLSH selection compilation completed on the
  final code after the earlier concurrent-warning crash.
- **Targeted proof matrix:** the seven requested recorder/grammar cases cover
  RT130, Minimus, dash-around-station telemetry, 2012 Echo/VX, ordinary
  `.wrno`, a year absent from LT, and the WLSH fourth-channel/overlap case.
  Together they compared 337,038,251 decoded source/output samples with zero
  bad values, zero missing seconds and zero selection-loss seconds. WLSH's
  remaining `selection_loss_suspect` is the expected dropped `O` orientation,
  not missing E/N/Z data.
- Six proof days were run at plan commit `404fd94`; WLSH was rerun at final
  code `0506181`. The only intervening code change serializes decoding and
  cannot alter samples. The only other change adds earliest-start to the plan;
  the six earlier days had no identity collision (the pre-rule converter would
  otherwise have failed them loudly). Their evidence therefore remains valid
  for v3.0 without rerunning them.
- **Naming:** the plans consistently implement Dan's VW/VX rule—location 00,
  band from each trace's rate, instrument H, and source orientation including
  RT130 1/2/Z. The inventory mismatches are old names marked for retirement,
  sampled partial channel sets, the ruled-out fourth channel, or years absent
  from LT. They do not create an unreviewed alternative naming rule.
- The changes add no origin write, LT write, plan-gate bypass or unpinned
  production path.

## Response

