# DU channel-code coverage — 2026-06-02 snapshot

Snapshot of which sources have channel/location info for each of the 53
DU `include:true` stations, taken at the start of DU-sweep planning. The
question this answers: *"for how many DU stations do we already have
channel-code information, and from where?"*

Reproduce with `scripts/du_channel_coverage.py` (run on the staging VM).

## Sources checked

| Tag | Source | Path | What it tells us |
|---|---|---|---|
| **S1a** | FDSN snapshot | `metadata/uploaded/DU/du.xml` | Current-cohort streams on the UoM SeisComP server (subsurface.science.unimelb.edu.au) |
| **S1b** | LT SeisComP archive | `/mnt/seiscomp_archive/<YEAR>/DU/<STA>/` | All channels that have ever made it to LT, with location codes inferred from SDS filenames |
| **S2** | SAA operator spreadsheets | `metadata/uploaded/DU/*.xlsx` (6 files) | Operator's per-station notes; coverage is which files mention the station, NOT necessarily what channel info those files contain |
| **S3** | upstream EchoPro→seedlink mapping file | *location TBD* | Should be on the UoM SeisComP server itself; haven't located yet |

## Headline numbers

| Source | Coverage |
|---|---|
| S1a (FDSN) | 29 / 53 |
| S1b (LT archive) | 32 / 53 |
| S2 (any spreadsheet mention) | 53 / 53 |
| **ANY source** | **53 / 53** |
| **No waveform-source coverage** (S1a + S1b both absent) | **11 / 53** |

The "ANY" number is misleading on its own — every station appears in at
least one spreadsheet, but spreadsheet *presence* doesn't equal usable
channel info. The load-bearing number is **53 − 11 = 42 stations with
direct waveform-source evidence of channel codes**.

## Five categories

Grouping by what kind of evidence each station has:

### A — FDSN + LT agree (clean, 6 stations)

ABRY, ALEX, BRON, KBRI, LGMA, PFLO.

Both sources show the same code, typically `00/HHZ@200`. These are
the modern PiesMo cohort, freshly telemetered and well-represented.
Plan generation = FDSN-verbatim, no manual input needed.

### B — FDSN + LT, multiple epochs visible (11 stations)

DJO, ERIKA, HAZO, HELEN, KENT, LEU, NSTM, OAT, USYD, WAH, WEPH.

FDSN says current PiesMo (`00/HHZ@200`); LT also has older
`00/EHZ` entries from when the station was on EchoPro. Two valid
codes for two different epochs. Plan needs to be epoch-aware: pre-PiesMo
years → EHZ, PiesMo era → HHZ. The transition date per station is the
question for the plan generator.

### C — FDSN-only, not in LT yet (5 stations)

BAMB, CLIL, DAMM, NNP1, NNP2.

Modern PiesMo deployments registered with FDSN but no telemetered
bytes in LT yet (or telemetered very recently). Plan = FDSN-verbatim,
recovery for EchoPro era (if any) would still need S1b/S3.

### D — LT-only (no FDSN entry, 14 stations)

BEGA, BRTS, CLV2, DNL, DNL2, HMV1, MRAT, ROBE, STR2, SUND, TPSO,
UTT, WALR, WKA.

These are the **EchoPro-era cohort** that haven't been put onto the
new SeisComP server. They DO have LT data — almost all show
`60/EHZ` (short-period at loc 60), confirming the EHE/EHN/EHZ codes
the operator hand-wrote in registry notes for BRTS/ROBE/STR2/SUND/WILM.

This is the most important finding: **for the DU EchoPro stations,
LT (S1b) is the de facto source of truth for channel codes**, not
FDSN. Plan generator must consult LT before falling through to BLOCKED.

Two exceptions to flag inside this group:
- **BEGA** shows `00/HHE/HHN/HHZ` in LT (PiesMo-shape) despite
  being absent from FDSN. Either a recent addition the FDSN snapshot
  predates, or a misread — investigate.
- **WALR** has six channel codes in LT (`60/EHE/EHN/EHZ`,
  `60/ENE/ENN/ENZ`, `60/HHE/HHN/HHZ`). Almost certainly multi-epoch
  / multi-sensor; needs careful epoch slicing.

### E — Spreadsheet-only (no FDSN, no LT, 11 stations)

ARKL, JMS2, JMS3, JMS4, JMS5, LKHRT, PLMR, S88M, S88U, TPSOP, WILM.

These are the genuine first-pass risk: we have zero waveform-source
evidence of channel codes for them. Three are the operator's "out for 12
months" / "presently out" / "temporarily offline" stations from the
recent promotion notes (ARKL, PLMR, WILM). The JMS group + S88 group +
LKHRT + TPSOP are the others.

This is exactly the cohort where **Source 3 (upstream EchoPro→seedlink
mapping)** would be most valuable, IF it has historical entries for
stations no longer telemetering. Otherwise: per-station operator input
or skip.

### F — Multi-sensor station, structural complexity (4 stations)

HKER, PENW, HML1, RNDA.

- **HKER, PENW**: `60/SHZ@100` in FDSN — short-period single-channel
  at loc 60. Simple but non-default.
- **HML1**: FDSN has `60/SH*@100` (broadband 3-comp) + `AB/HN*@100`
  (accelerometer at separate location code AB). LT shows only `60/EH*`
  — disagreement to resolve.
- **RNDA**: FDSN has `60/HH*@100` + `AB/HN*@100`. Note `HH` at 100 sps
  here uses the SEED full-convention (broadband at 80-250 sps → H), not
  the Gecko-subset table.

These need per-station per-instrument-code epoch handling and explicit
location-code resolution (`AB` vs `60` per channel).

## Location-code finding (critical)

The registry default `target_location: "00"` is wrong for the
EchoPro cohort. **EHE/EHN/EHZ channels in DU consistently use location
code `"60"`** in both FDSN (where present) and LT. Cohort breakdown:

| Cohort | Typical loc | Typical channel |
|---|---|---|
| Modern PiesMo (HH@200) | `00` | `HHZ/HHN/HHE` |
| EchoPro short-period (EH@100) | `60` | `EHZ/EHN/EHE` |
| Multi-sensor (accel) | `AB` | `HN*` |

The plan generator must pull location from FDSN/LT per station-epoch,
not from a single registry default. The registry's `target_location`
field should either become **per-epoch** or be removed in favor of
source-derivation.

## The "0" vs "00" location-code split

LT shows both `0/HHZ` and `00/HHZ` for some stations (e.g. ALEX, DJO,
ERIKA, KENT, LEU). This is the PiesMo-era ingest where the single-char
"0" location was used (CLAUDE.md "PiesMo cohort" section documents this
on the EqServer-archive side). Promotion to LT preserves whatever was
written. The plan generator should normalize both to a single canonical
loc code per epoch — almost certainly `"00"`.

## What still needs the user

1. **Source 3 location.** Where is the upstream EchoPro→seedlink mapping
   file? Best guess is on the SeisComP server itself — probably under
   `/etc/seiscomp/` or wherever the seedlink module's station bindings
   live. If you can point me at it (path on dev1), I'll add it to the
   coverage matrix. It's most useful for Category E (the 11 stations
   with no FDSN + no LT).
2. **Spreadsheet drill-in.** The 53/53 spreadsheet coverage is just
   "station name appears in the file." None of the 11 Category E
   stations have waveform-source channel info, but they might have
   channel info inside one of the spreadsheets I haven't yet parsed
   the contents of. If S3 is unavailable, I'll deep-read each xlsx for
   the Category E stations specifically.
3. **Multi-epoch transition dates.** For Category B (11 stations), the
   transition from EchoPro to PiesMo defines the epoch boundary
   between EHZ and HHZ. We'll need either a per-station transition date
   from the operator OR derivation from when LT files transition from
   `EHZ` to `HHZ`. I can run the LT-derivation pass.
