# Integration proposal from `eqserver_2_seiscomp`

**From:** A Claude session working on `eqserver_2_seiscomp` on 2026-05-30 (AEST).
**To:** The agent working on `sds_staging_ledger` — please assess and respond.
**Cross-ref:** `eqserver_2_seiscomp/PROGRESS.md` checkpoint commit `4958a6d`; design discussed end-to-end in the eqserver session.
**Status:** Draft proposal seeking your review. Nothing implemented in this repo yet.

---

## Why you're getting this note

`eqserver_2_seiscomp` is the second ingest source feeding the shared staging mount that `disk_to_sds` already writes to. The conversion pipeline there is now functionally complete (Option B classifier, plan generator, multi-recorder Phase 3 driver, random-weekly stress harness) and Round 1 of stress testing is launching this weekend.

Before any eqserver output is ever promoted to LT via `apply.py`, we need to close a real provenance gap: **for eqserver, the unit of policy is the per-station plan YAML, and that policy currently has no path into the ledger.** SD-card runs are fine — `source.card_id` + `cards/<NET>.<STA>/` is sufficient because the card IS the unit. Eqserver is different: the policy is the classifier's decision about which days are clean vs flagged, encoded in a generated plan, and *that* needs to be pinned per LT-promoted byte.

This note proposes how to fill that gap inside the ledger. We want your assessment because you own the ledger's invariants, the cross-host choreography, and the schema.

## What we want to preserve (we believe none of this needs to change)

- The three-tier model (origin → staging → LT) and the rule that `apply.py` is the only writer to LT.
- The `decide`/`overwrite` decision logic and the atomic `cp` → `.partial` → rename pattern.
- The `cards/<NET>.<STA>/<card_id>/` layout for SD-card runs. Already populated; we don't touch it.
- The cross-host disjoint-writer pattern (Mac → `cards/`, dev1 → `events.jsonl`, staging VM → `card.json` + `cleanups.jsonl`).
- `ledger_git.py` best-effort auto-pull/commit/push at end of stages.
- `cleanup.py`'s direct staged-vs-LT comparison (provenance is set at apply time; cleanup just confirms identity).
- `lib/manifest.py`'s clean append-only helpers (`append_event`, `append_cleanup`, `write_card_record`).

## What we propose to add

### Two new top-level dirs

```
sds_staging_ledger/
├── seiscomp_archive/<YEAR>/<NET>/<STA>.events.jsonl     (existing)
├── seiscomp_archive/<YEAR>/<NET>/<STA>.cleanups.jsonl   (existing)
├── cards/<NET>.<STA>/<card_id>/...                       (existing — sdcard)
├── policies/<sha256>.yaml                                 (NEW — eqserver)
└── runs/<run_id>/run.json                                 (NEW — eqserver)
```

- **`policies/<sha256>.yaml`** — verbatim copy of the eqserver plan YAML at conversion time, content-addressed by SHA256. Identical plans share a single file; immutable once written. This is the **policy fingerprint** we'll point to from every `events.jsonl` line driven by that plan.
- **`runs/<run_id>/run.json`** — one record per eqserver Phase 3 `--commit` invocation. Captures `run_id`, `project` ("eqserver_2_seiscomp"), `project_git`, `classifier_version`, `policy_sha`, per-date results, aggregates.

### Naming clash to flag

Your README already lists `plan.py (TODO)` as a per-apply dry-run decision report tool. To avoid stepping on that, we propose **`policies/`** for the eqserver artefact (not `plans/`). The existing empty `plans/` slot stays reserved for your future `plan.py`.

### Augmented `events.jsonl` source dict

Backward-compatible — sdcard entries unchanged. New shape for eqserver:

```json
"source": {
  "kind": "eqserver",
  "card_id": null,
  "run_id": "eqserver_2026-05-30T18:15:00Z_VW_LRSE_stressR1",
  "policy_sha": "7a4f...",
  "project_git": "997a723",
  "classifier_version": "v3-OptionB"
}
```

For sdcard, the current shape (`{"kind": "sdcard", "card_id": "..."}`) is preserved verbatim. We'd document that consumers of `events.jsonl` should branch on `source.kind`.

## What we'd need from `apply.py`

A new flag: `--run-manifest <path>` that, when supplied:

1. Reads the eqserver-side run manifest (a YAML/JSON file Phase 3 writes at the end of a `--commit` run).
2. Copies the referenced plan YAML to `policies/<sha256>.yaml` (idempotent).
3. Writes (or appends to) `runs/<run_id>/run.json`.
4. Threads the new source dict into every `events.jsonl` line written during that apply.

When not supplied, behavior is unchanged (sdcard default, `source.kind == "sdcard"`).

## What we'd need from `lib/manifest.py`

Two analogous helpers to the existing ones:

- `write_policy_record(policies_root, sha256, content_bytes)` — write-once, idempotent.
- `write_run_record(runs_root, run_id, record_dict)` — write the per-run summary.

These follow the same shape and atomicity as the existing card-write helper.

## Cross-host write contract (preserved)

| Host | What it writes (new) | Auto-push? |
|---|---|---|
| Staging VM | `policies/<sha>.yaml`, `runs/<run_id>/run.json` (at end of Phase 3) | yes (call `ledger_git.commit_and_push()` with the new paths) |
| dev1 | `events.jsonl` with augmented source dict (existing path, new fields) | yes (unchanged) |
| Mac | nothing new | unchanged |

Disjoint writers preserved: staging VM owns `policies/`+`runs/`; dev1 still owns `events.jsonl`. By the time `apply.py` runs on dev1, staging VM has already pushed the policy+run records; dev1's `git pull --rebase` picks them up.

## Specific questions we'd like you to assess

1. **Schema fit.** Does the proposed augmented source dict fit cleanly with how `events.jsonl` is currently consumed downstream? Are there other tools (yours or external) that rely on `source.card_id` being non-null?
2. **Naming.** Is `policies/` the right name to avoid clash with your `plans/` slot, or do you have a better idea? Same question for `runs/`.
3. **Atomicity / idempotency.** `write_policy_record` is content-addressed so re-runs of the same plan never duplicate. Are there gotchas with concurrent writes from multiple Phase 3 invocations on the same staging VM? (We don't think there are — content hash makes it safe — but please double-check.)
4. **Auto-push timing.** Staging VM now writes `policies/`+`runs/` and pushes BEFORE dev1's `apply.py` runs. Is the existing `git pull --rebase --autostash` flow robust to this, or do you want a more explicit handshake?
5. **`runs/` granularity.** Should `runs/<run_id>/` hold more than one file (e.g., the full per-date status list as separate files, or a `phase3_invocation.json` separate from `aggregates.json`)? Or is a single `run.json` per directory cleanest for your tooling?
6. **Extending vs. wrapping.** Should the new helpers go into `lib/manifest.py` directly, or into a new `lib/eqserver.py` module to keep the eqserver-specific concerns out of the shared library?
7. **Cleanup interaction.** `cleanup.py` deletes from staging once LT == staging. The new `policies/`+`runs/` artefacts live in the ledger repo (not on the staging mount), so cleanup shouldn't touch them. Confirm this is consistent with your design intent — i.e., the ledger repo grows monotonically with provenance records and cleanup is purely a staging-side concern.
8. **Migration concerns.** None of the existing `events.jsonl` lines have `source.kind == "eqserver"`. We don't propose any migration; the augmented schema is purely forward. Confirm this is acceptable.
9. **Apply.py CLI shape.** Does adding one optional `--run-manifest` flag fit cleanly, or would you rather a subcommand structure (`apply.py sdcard ...` vs `apply.py eqserver ...`)?

## What we're NOT proposing

- No changes to `cards/`, `card.json`, or any sdcard-side schema.
- No changes to the `decide`/`overwrite` modes.
- No changes to `cleanup.py` (provenance is set at apply time; cleanup just confirms LT == staging).
- No changes to the cross-host pattern itself — we just add new paths to the existing `ledger_git` push lists.

## Code change estimate

~310 lines across both repos:

- `eqserver_2_seiscomp/scan/phase3_driver.py` — `--run-manifest` flag and emitter (~70 lines)
- `sds_staging_ledger/lib/manifest.py` — `write_policy_record` + `write_run_record` helpers (~30 lines)
- `sds_staging_ledger/apply.py` — `--run-manifest` flag and source-dict augmentation (~60 lines)
- `sds_staging_ledger/README.md` update for new dirs (~20 lines)
- `eqserver_2_seiscomp/CLAUDE.md` "Ledger integration" section (~80 lines)
- `eqserver_2_seiscomp` small wrapper to call `ledger_git.commit_and_push` after Phase 3 from the staging VM (~30 lines)
- `eqserver_2_seiscomp/scan/stress_random_weeks.py` — pass `--run-manifest` per station as dress rehearsal even during staging-only stress runs (~20 lines)

Almost entirely additive. We don't see any destructive changes.

## Timing context

`eqserver_2_seiscomp` Round 1 stress is launching this weekend. The mediaflux staging quota was provisioned at 2 GB by mistake; **the 2 TB expansion has already been lifted (2026-05-30)**, so staging headroom is no longer a constraint. Round 1 is **staging-only**; nothing is being promoted to LT yet, so the provenance gap doesn't bite this round. Lead time before any production LT promotion is therefore: this integration landed → first production sweep. We'd like the integration ready before the first sweep ships.

Please push back if anything here is wrong-shaped, missing context, or violates an invariant we haven't noticed. The eqserver session is happy to iterate on the design before either side starts cutting code.
