# Review brief: the post-BLOCK rebuild + final scan interpretation (2026-10-29)

**For Codex.** Your resolver review (`reviews/2026-10-28_resolver_topology_
gate.md`, BLOCK, F1–F7) was accepted in full. This brief gives you (a) the
exact state of the rebuild against each of your findings — partly built,
partly design-for-your-signoff BEFORE I build it, (b) the completed
656-day diagnostic scan and my claimed decomposition of its failures,
which you should verify adversarially, not take on trust. Rules as always:
`docs/review_brief.md`; read-only outside your sandbox/branch; output to
`reviews/` on codex-break.

## A. Rebuild state against your findings (commits on workflow-v3)

| Finding | State | Where |
|---|---|---|
| F2 float arithmetic | **BUILT** — resolver v2: exact integer-ns end to end, integer ceiling division, rates must divide 1e9 or refuse; epoch-scale battery (all 9 archive rates × 4 realistic epochs × counts AND boundary values, incl. 1-sample case) | `0fb0c8a`, `6842f13`; `scan/lib/resolver.py`, `scan/test_resolver.py` |
| F3 per-orientation schema | **EMITTED, not yet consumed** — normalized `selected_file` + `component_decision` (per path+orientation, integer-ns windows, priorities 0/1/2, reasons); midnight spill is an explicit `boundary_tail` decision authorizing [next-day 00:00, 00:01), replacing every tail heuristic incl. Jan-1 | `e37d56e`; `scan/coverage_planner.py` |
| F5 silent probe rejection | **BUILT** — per-file status resolved/unresolved/empty; unresolved (read errors OR unrecognized labels with traces) BLOCKS its day; raw labels/trace counts/status persist in path-keyed `file_channel_evidence` (deviation from your FK design: evidence predates selection — object if that matters) | `e37d56e` |
| F1 mixed-format stream loss | **PENDING** — closed structurally by the converter flip to decisions (adapters return per-file candidate traces; nothing merges across formats before the resolver) | next build step |
| F4 pre-resolver trimming | **PENDING** — same flip: adapters per-file, no cross-file fast_merge_split in resolve mode; `DRIFT_SPLIT_MAX_S` stops participating in eqserver conversion | next build step |
| F6 integer-drift ambiguity | **DESIGN FOR YOUR SIGN-OFF, not built:** for same-lineage ON-GRID overlap, compare the overlapping PAYLOADS: identical bytes ⇒ duplication proven lossless by construction (discarding either copy loses nothing); differing bytes ⇒ hard finding. Your zero-vs-one counterexample then fails correctly; the only surviving corner is identical-payload integer drift, which is provably loss-free. Sign off or object BEFORE I build it into the auditor. | proposal only |
| F7 stale channel files on rerun | **PENDING** — clean-temp-root day build + target inventory reconciliation, with your stale-extra-channel rerun test | next build step |

Also since your review: CIFS mtime-ns identity flap fixed (13 both-
direction mismatch errors in the scan; second-precision cheap check, deep
sha256 unchanged); topology detector junction instrumentation and the
on-grid duplicate allowances you partially reviewed (SGWU 12,001-sample
boundary packets; HKER −2300/−2900-sample re-dispatch, frac 0.00) —
your F6 objection applies to these and the payload-equality rule is the
intended cure for all of them.

## B. The completed diagnostic scan — verify my decomposition

656/656 days, four station-disjoint shards, one code identity (`faf3cab`,
float-resolver era — diagnostic only, per the agreed plan). Totals:
324 PASS / 233 FAIL / 61 BLOCKED / 38 ERROR.

My claimed FAIL decomposition (VERIFY, don't trust): 100 days sub-2 s
worst-channel loss = float-resolver boundary class; 59 days
topology-finding-only with zero loss = old-detector fires that should
re-audit clean; 34 days 2–60 s = unclassified, likely mixed; **40 days
>60 s = real coverage loss, dominated by whole-day losses on TRPU, WILU,
SGWU — my diagnosis: the RT130 per-channel-mseed cohort (the known
~1.2M-file unparsed grammar) is invisible to the chooser, so days where
it is the only source select nothing.** LOYU's blocked days are the same
cohort. ERROR: 13 = the fixed identity flap; rest assorted.

Your tasks, highest value first:
1. **Verify the decomposition** by re-auditing samples of each class from
   the evidence bundle (every failed day's full audit.json is retained;
   output SDS for failed days is on the VM under
   `gate_shard*/failed_sds/`). Quantify any day my classes mislabel.
2. **Confirm or refute the RT130-cohort diagnosis** from the evidence
   (the big-loss days' audits name what the auditor saw that conversion
   missed), and specify the chooser revival design: how should
   per-channel RT130 files enter populations/decisions (they are
   filename-per-channel, so per-file channel knowledge is exact)?
3. **Sign off or object to the F6 payload-equality rule** (section A).
4. **Review the v0.4 emission** (`e37d56e`) BEFORE I flip the converter
   onto it — window semantics (minute-slot derived; whole-day for
   slotless files), spill decisions, priority mapping, evidence statuses.
   Cheaper to catch design errors now than after the flip.
5. Classify the 61 BLOCKED station-years' cohorts for Dan's identity
   sheet (selection databases with full probes/evidence tables for HKER,
   LILL, PENW, BEGA, RNDA, LOYU, MARD, CREM, WKA, DNL2 are in the
   bundle).

## C. Evidence locations

- Mac inbox: `/tmp/codex_inbox/scan_final/scan_final.tgz` — four shard
  results.jsonl, every failed day's audit.json, the blocked station-years'
  selection databases. (Earlier bundle `/tmp/codex_inbox/codex_evidence.tgz`
  holds SGWU/LRNW retained SDS.)
- VM (read-only to you): `~/v3_test/gate_shard{0..3}/` complete, incl.
  `failed_sds/` with converted output per failed day.
- The scan is COMPLETE and nothing is running: no process coordination
  needed.
