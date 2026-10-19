# Triage evidence map — every way a channel can be lost, and what stops it

**Status: SKELETON (2026-10-20). The sampled-evidence column fills from the
triage study when the VM returns; rows already evidenced cite their runs.
One page, by design. "Triage" = the decision step that maps a day
directory in EqServer to the subset of files that gets converted.**

| # | How coverage gets lost | What stops it in v3 | Evidence so far | Study stratum |
|---|---|---|---|---|
| 1 | The copy with fewer channels wins the day (disk had only Z; telemetry had Z/N/E) | Choice is made PER CHANNEL: each orientation goes to the copy that carries it with the best day coverage | **PROVEN** on the worst known day: WLSH 2019-06-15 — N+E (23.7 h each) recovered, 64 M samples, 0 errors, loss 85,000 s → 0.5 s | multi_copy |
| 2 | Two time-shifted duplicate copies get alternated, dropping slivers at every switch | One population wins the whole day per channel; other copies fill only true gaps, never overlay | Mechanism built + unit-level pass; the named STBK day (406 × 1.6 s gaps in the old output) still to run | multi_copy |
| 3 | Oddly-named files are invisible (hourly bundles, unkeyable grammars) | Revived cohorts enter triage as candidates; bundles count as 60 minutes of coverage | WLSH recovery came FROM bundles (they carried the only N/E) | bundle_or_unparsed |
| 4 | A filename lies about its contents (single-channel name, multi-channel content) | Chooser trusts names only for single-channel files; multi-channel populations are content-probed monthly; auditor reads actual contents regardless | 300/300 sampled suffix-named files matched their contents exactly; study scales this up | suffixed |
| 5 | Junk converts: wrong-station files, triggered files, accelerometer events | Excluded at the index stage (name-vs-folder check; trigger markers; session-offset mismatch); accelerometer channels dropped per identity contract | Long-standing v1 behaviour, carried over; study controls confirm no regressions | all strata |
| 6 | Day boundary: the first seconds of a day live in the previous day's last file | Previous day's final minute is pulled into each day's conversion; output trimmed to the day | Proven in v1 (June fix) + carried into selection mode; Jan 1 handled via legacy tail query | clean + boundary days |
| 7 | Mixed recorder types in one day: the day is converted with one engine, the minority format's files ignored | **KNOWN GAP:** engine dispatch is per-day; a day with both SUDS and miniSEED converts only the majority format | Not yet defended — the mixed_rec stratum exists to measure how much data this loses; fix shape: per-population engine dispatch (triage already groups by population) | mixed_rec |
| 8 | Bogus-dated strays consume attention or convert garbage (1970/1900/rollover dates) | Stray-day rule (specced, not yet built): re-date rollover files, auto-exclude isolated epoch-default strays, escalate only ambiguous clusters | Design only (operator-approved); not in the current chooser | (follow-up) |
| 9 | A completed-looking run silently converted only part of the year | Completion equation: expected = converted + no-data + waived, checked per station before "done" | Design settled (review-adopted); build pending — the dress rehearsal exercises it | n/a (orchestration) |

**Honest summary at skeleton stage:** rows 1–6 are defended and partially
or fully evidenced; row 7 is an open gap the study will quantify before we
fix it; rows 8–9 are approved designs not yet built. The study's job is to
replace every "mechanism built" with a sampled pass-rate and to surface
any loss mode this table doesn't name.
