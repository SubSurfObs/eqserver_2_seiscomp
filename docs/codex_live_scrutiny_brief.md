# Codex live-scrutiny brief — DU production rollout (from 2026-10-30)

**Your role changes.** v3 is in production: real station-years are
converting into the shared staging archive right now. You are the
independent scrutineer of that output *as it arrives* — not a code
reviewer this time, a data auditor working sample by sample. Claude runs
the conversion; Dan decides; you check the bytes. Be creative and
adversarial: the job is to find anything the pipeline lost, invented,
mistimed or misnamed — before Dan promotes it to the long-term archive.

## Ground rules (unchanged in spirit)

- **Read-only everywhere** except your own VM sandbox `~/codex_sandbox/`
  and your branch `codex-break` (commits prefixed `Codex: `).
- Never write to `/mnt/seiscomp_staging/` (staging SDS, queues),
  `/mnt/seiscomp_archive/` (long-term), or the origin
  `/mnt/eqserver_archive/`. Never stop or signal any process.
- Be gentle on the origin NFS mount: the converter is reading from it.
  Check one station-year at a time; no bulk scans; a few hundred file
  reads per check is fine.
- Timestamps are converted exactly as recorded — never "corrected". A
  timing difference vs the source is a finding; a timing difference vs
  the true time is not our business.

## Access

- VM: `ssh dsand@172.26.144.41` (key-based, from Dan's Mac). If your
  environment blocks SSH (it did in an earlier session), ask Dan to run
  you with network access — this task needs the VM.
- Python with obspy + sudspy: `/home/unimelb.edu.au/dsand/projects/
  SubSurfObs/disk_to_sds/.venv/bin/python3`. Code (read-only):
  `~/projects/SubSurfObs/eqserver_2_seiscomp_v3` (pinned, workflow-v3),
  `~/projects/SubSurfObs/sudspy`, `~/projects/SubSurfObs/disk_to_sds`.

## What is being produced, and where to watch

| What | Where |
|---|---|
| Finished station-years (the trigger) | `/mnt/seiscomp_staging/eqserver_sweep_v3/convert_done.jsonl` — one JSON line per unit (`net, sta, year, run_manifest_path, staging_root, items_succeeded`) |
| Converted output | `/mnt/seiscomp_staging/seiscomp_archive/<YEAR>/DU/<STA>/<CHAN>.D/DU.<STA>.<LOC>.<CHAN>.D.<YEAR>.<JDAY>` |
| Per-run manifest (per-day status + files written) | path in each convert_done line (`eqserver_sweep_v3/run_manifests/`) |
| The approved plan (names, years) | `plans/DU/DU.<STA>.plan.yaml` in the repo (mirrored in the queue `plans/DU/`) |
| The file list the converter obeyed | `~/v3_prod/selections/DU.<STA>.<YEAR>.selection.sqlite` (tables `selected_file`, `component_decision`: per file + orientation, integer-ns authorized windows, priority 0/1/2; `rejected`; `probes`; `file_channel_evidence`) |
| Source index (every EqServer file) | `~/station_dbs/DU.<STA>.db` (table `files`: path, source_type, hhmm, ss, size, exclude_reason…) — note: built 2026-05-28 |
| Origin day directories | `/mnt/eqserver_archive/shared/data/repository/archive/<STA>/continuous/<YYYY>/<MM>/<DD>/` |
| Naming rule + table | `docs/` not needed — rule: server codes verbatim if archived on SeisComP (`metadata/derived/du_lt_codes.json`), else miniSEED header names, else SEED short-period by rate. Wave status: VM `~/v3_prod/wave1.status` |

Wave 1 (running/finished): DAMM 2025, NSTM 2025, LKHRT 2023, CLV2 2021.
Wave 2 onward: full back catalogues, several stations in parallel.

## The core check (per finished station-year)

For each new `convert_done` line:

1. **Pick samples.** At least 5 random (day, minute) pairs per
   station-year, stratified: random minutes; minutes within 2 of
   midnight; minutes containing a trace boundary inside the converted
   day file (gaps, drift splits — read the file's trace list); and, if
   the selection shows multiple populations or formats that day, minutes
   where they hand over.
2. **Find every source file that could hold that minute** — not just the
   selected ones: all files in the day directory (and the previous day's
   final files for minutes near midnight) whose name or header time
   covers it, every population, every format, including excluded and
   "rejected" files. Use the index and a direct `ls` of the day
   directory — they should agree; if they don't, that is itself a
   finding (index staleness: the index predates 2026-05-28).
3. **Decode each source independently.** For miniSEED, obspy. For SUDS,
   go beyond sudspy where you can: the stdlib witness
   (`scan/suds_witness.py`) already walks PC-SUDS structures without
   sharing code with the converter — extend it in your sandbox to decode
   the sample payload itself (data type per DESCRIPTRACE) so the
   comparison is genuinely independent. Using sudspy too is fine, but
   say which reader produced each verdict.
4. **Compare sample by sample at stated times** (each sample's time =
   its source header start + n/rate). Classify every sample of the
   minute, per channel:
   - **match-selected** — equals a copy the selection authorized
     (expected);
   - **match-unselected-only** — equals only a copy the selection did
     NOT authorize (a choice to explain, not necessarily wrong);
   - **no-match** — no source copy has this value at this time
     (corruption or re-timing: serious);
   - **missing** — a source copy has data here but the converted file
     does not (loss: serious);
   - **phantom** — converted data where no source exists (serious);
   - **timing** — values match a source but offset by ≥ half a sample
     from its stated time (serious — weld or shift).
5. **Check identity:** network, station, location, channel exactly as
   the plan/naming rule says; no stray extra channels; sample rate
   matches the source.

## Be creative — suggested extra angles (pick, extend, invent)

- **Second independent witness: the live SeisComP archive.** Several DU
  stations are ALSO on the long-term archive via a different route
  (seedlink / SD card). Where a converted minute overlaps LT data for the
  same station and channel (e.g. NSTM 2025 — LT holds HHZ@0 for 2025),
  compare them: two pipelines, same ground truth.
- **Day-level accounting:** per converted day file, total samples vs the
  union of authorized source coverage; trace count vs expected drift
  splits.
- **Selection audit:** spot-check `component_decision` windows against
  the day directory — any file with data for a channel that no decision
  covers, on a day with a gap in that channel, is a candidate loss.
- **Quarantine audit:** `/mnt/seiscomp_staging/seiscomp_archive/
  .stale_quarantine/` should be empty or explicable for these
  station-days.
- **Glitch days:** files written as INT32 instead of STEIM2 (the
  glitch-sample fallback) — confirm values are bit-identical to source.
- Compare a few converted days against your own from-scratch conversion
  of the same minute files (in your sandbox), if you want a third
  opinion.

## Reporting

- Append findings per station-year to `reviews/live_scrutiny_
  2026-10-30.md` on `codex-break` (one section per unit: what you
  sampled, how, verdict counts, any findings with exact file/minute/
  sample reproduction). Commit as you go.
- **Escalate immediately** (tell Dan in your reply) on any `no-match`,
  `missing`, `phantom`, `timing` or naming defect — Dan will hold
  promotion of that station and Claude will fix. Everything else can
  wait for the running log.
- Keep a short running summary at the top: units checked, samples
  checked, findings by class.
