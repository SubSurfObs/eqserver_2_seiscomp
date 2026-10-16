---
name: feedback-use-station-dbs-first
description: "For any 'what's in EqServer?' question — counts, per-day coverage, channel-suffix transitions, deployment gaps — query the per-station SQLite DBs under /home/.../station_dbs/ first. Filesystem walks bypass work we already paid for."
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 66713a2d-7a0d-44f1-ae1a-e3a1e4de0f66
---

The per-station Level-1 SQLite manifests at
`/home/unimelb.edu.au/dsand/station_dbs/VW.<STA>.db` are the canonical
indexed snapshot of EqServer truth for each station. Schema (columns
on the `files` table):
`path, station, dir_year, dir_month, dir_day, recorder_type,
source_type, role, file_year, file_month, file_day, date_mismatch,
hhmm, ss, channel_suffix, filename_station, station_mismatch, flags,
size_bytes, mtime, exclude_reason`.

So for any of the following question shapes, the DB is the right tool:

- "How many files does STA have in year Y month M day D?"
- "What channel suffixes appear at STA on a given day / across a year?"
  (e.g. detecting filename-level transitions like
  DDBE 2019-12-15 = HH* → 2019-12-17 = CH*)
- "What's STA's actual deployment coverage in year Y?" (per-month
  distinct day counts; missing-days breakdown)
- "What recorder type / source type is STA producing?"
- "Are there station_mismatch or date_mismatch flags worth investigating?"

**Why:** Originally fell back to filesystem `os.walk` / `ls` for these
questions out of habit. Operator pushed back 2026-06-03 — the DB IS the
indexed authority for this; bypassing it is slower AND bypasses the
work we already paid for at Level-1 scan time. For immutable historical
years on the read-only NFS, the DB is also stable truth (no drift to
worry about).

**How to apply:**

- For station-EqServer questions: open `VW.<STA>.db` with sqlite3 and
  query the `files` table directly.
- The DBs hold Level-1 data only (filename-derived). They do NOT
  currently hold Level-2 data (mseed/SUDS header info — `sample_rates`,
  `channels`, `t_start`, `t_end`, `header_station` columns are
  unpopulated). So for header-level questions (e.g. "what sample rate
  is inside this file") still need a targeted file-read with ObsPy or
  sudspy.
- For SeisComP LT-side questions, no analogous DB exists yet — use
  `/mnt/seiscomp_archive` filesystem walks.

Related: [[project-overview]] documents the multi-level scan strategy
that produced these DBs; CLAUDE.md "Pre-scan and file manifest" section
defines the schema.
