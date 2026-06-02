# DU channel-code coverage — 2026-06-02 snapshot

Snapshot of which sources have channel/location info for each of the 53
DU `include:true` stations, taken at the start of DU-sweep planning. The
question this answers: **for how many DU stations do we have ZERO
channel-code information available across all sources, and which ones?**

Reproduce with `scripts/du_channel_coverage.py` (run on the staging VM).

## Sources checked

| Tag | Source | Path / endpoint | What it tells us |
|---|---|---|---|
| **VIP** | Live upstream Seismosphere snapshot | `https://objects.storage.unimelb.edu.au/6700-realtime-seismology-assets/system-health/vip_status.json` | Streams currently published by the upstream Seismosphere server (auto-generated; effectively a live "what's flowing through right now") |
| **FDSN snap** | UoM SeisComP FDSN snapshot | `metadata/uploaded/DU/du.xml` | Current-cohort channels on the UoM SeisComP server (subsurface.science.unimelb.edu.au) — derived downstream from VIP, captured 2026-02-03 |
| **LT** | LT SeisComP archive | `/mnt/seiscomp_archive/<YEAR>/DU/<STA>/` | All channels ever archived, with location codes inferred from SDS filenames. Note: includes decimation-duplicate channels from the upstream operator (EHZ as a duplicate of HHZ etc.) — should be filtered against VIP/FDSN truth when interpreting. |
| **xlsx** | SAA operator spreadsheets | `metadata/uploaded/DU/*.xlsx` (6 files) | Operator's per-station notes. Coverage column shows files whose rows for this station contain text matching a SEED channel-code pattern. |

Source 3 (upstream EchoPro→seedlink mapping file) was excluded by the
operator — it's managed by the SRC and the operator confirmed every fact
in it should be derivable from VIP + LT.

## Headline numbers

| Source | Coverage |
|---|---|
| VIP (live upstream) | 29 / 53 |
| FDSN snapshot | 29 / 53 — exact match to VIP |
| LT archive | 32 / 53 |
| Spreadsheet with channel-code hint | 31 / 53 |
| **At least one source** | **43 / 53** |
| **ZERO channel info in ANY source** | **10 / 53** |

### The 10 zero-info stations

These have nothing to plan from in any source — no current upstream
stream, no FDSN entry, no LT history, no spreadsheet row that names a
channel code:

| Station | Spreadsheet hints (no channel) |
|---|---|
| **ARKL** | "SA, Radio + netwk, Epro, Freewave, Out for 12 months" (GoingToEqserver2025) |
| **PLMR** | "SA, WAP + satellite, Epro, old Netgear WAPS, presently out" (GoingToEqserver2025) |
| **LKHRT** | "NSW, ? Telstra 4G, Gecko, ?" (GoingToEqserver2025) — recorder type known (Gecko); rate unknown |
| **JMS2 / JMS3 / JMS4 / JMS5** | "DL" (operator initials only) — no recorder type, no rate, no channel |
| **S88M / S88U** | "GG" (operator initials only) |
| **TPSOP** | "DL" only. NOTE: `TPSO` (without the P) IS live in VIP but under network **AB**, not DU. |

For these 10, either the registry's `include:true` was aspirational
(stations the operator wants to convert when they next come online) or
they need explicit operator input on channel codes before they can be
planned. Suggested split:
- **3 with recorder-type known** (ARKL = Epro, PLMR = Epro, LKHRT = Gecko):
  the recorder-type alone narrows possibilities. ARKL/PLMR look like
  short-period EchoPros at 100 sps based on neighbouring SA stations
  (BRTS/ROBE/STR2 etc. all 60/EHE/N/Z), so a default of EHE/N/Z at
  loc 60 is a defensible per-station registry override pending operator
  confirmation. LKHRT (Gecko, no rate) is harder.
- **7 with zero structural info** (JMS2-5, S88M/U, TPSOP): operator
  needs to nominate or these stay out of first-pass DU sweep.

## Cross-source verification (VIP vs FDSN vs LT)

VIP and FDSN snapshot agreed exactly on count (29 each). Station-level
overlap is near-perfect with small expected differences:
- BEGA, BRTS, CLV2, DNL, DNL2, HMV1, MRAT, ROBE, STR2, SUND, TPSO,
  UTT, WALR, WKA — in VIP, NOT in FDSN snapshot. These are stations
  on the upstream but not in the static FDSN copy from 2026-02-03 (the
  upstream churns; FDSN snapshot is a point-in-time).
- ALEX, BAMB, CLIL, DAMM, HKER, LEU, LGMA, NNP1, NNP2, PENW, PLYP,
  RNDA, S88P, USYD, WAH — in FDSN snapshot, NOT in VIP. Recent FDSN-
  registered stations that aren't actively telemetering, OR inactive
  in current upstream cycle. Worth a re-pull of FDSN to refresh.

## Divergences worth resolving before DU sweep

These don't affect the headline count but they affect plan-generation
correctness:

1. **TPSO network mismatch.** Registry has `TPSO: target_network: DU`,
   but the live upstream publishes `AB.TPSO` (with `60/HH*`).
   Decision: reclassify TPSO to AB in registry, or accept DU.TPSO as
   the conversion target despite upstream code? If the latter, this
   is the only EqServer station that gets a registry network code
   different from its upstream code.
2. **HKER / WKA channel disagreement.** FDSN says `60/SHZ@100`; VIP
   says `00/HHZ` (HKER) and `60/EHZ` (WKA). Different bands, different
   eras. WKA's LT has both `60/EHZ` (matches VIP) and a historical
   different rate — sample-rate-aware epoch slicing needed.
3. **HML1 multi-instrument complexity.** FDSN exposes both `60/SH*@100`
   (seismometer) and `AB/HN*@100` (accelerometer); VIP says `60/EH*`;
   LT confirms `60/EH*`. Two location codes (60 + AB) and two
   instrument types in the same station.
4. **DNL band disagreement.** VIP says `60/EHE/N/Z`, spreadsheet rows
   say `SHE/SHN/SHZ`. The spreadsheet is older; VIP is the truth. But
   DNL2 says VIP `60/HH*` while LT shows `60/EH*` AND `60/HH*` —
   genuine multi-epoch.
5. **WILM live now.** Registry note said "NOT on UoM seedlink VIP
   2026-06-01" but VIP shows WILM as ACTIVE with `60/EHE/N/Z`. The
   note is stale (recovered today, or yesterday).

## Decimation-duplicate handling

The upstream operator started producing decimated copies of channels
which surface as extra codes (e.g. EHZ as a duplicate of HHZ at a
PiesMo station). The plan generator should:

- Trust **VIP / FDSN** as the truth for what's CURRENTLY meaningful.
- When LT has more channels than VIP/FDSN for the same epoch, treat
  the extras as decimation duplicates and drop them.
- When LT has channels in years BEFORE the VIP/FDSN epoch started,
  treat those as historical epoch and preserve them per-epoch.

This means the 11 stations I classified as "multi-epoch FDSN+LT" in
the v1 analysis (DJO/ERIKA/HAZO/HELEN/KENT/LEU/NSTM/OAT/USYD/WAH/WEPH —
all showing HHZ in VIP/FDSN + EHZ in LT) need a year-range check to
distinguish:

- **EHZ files in years contemporaneous with HHZ files** → decimation
  duplicate, drop.
- **EHZ files in years before HHZ first appeared** → real EchoPro
  history, preserve as epoch.

Code path TBD; the LT scan already captured per-year, just needs the
year-range query exposed in the analysis.

## Coverage matrix (all 53 stations)

Full table generated by `scripts/du_channel_coverage.py` — see the
script's output. Sample of categories:

| Category | Count | Examples |
|---|---|---|
| In VIP + FDSN + LT (clean) | ~17 | ABRY, BRON, KBRI, DJO, HAZO, NSTM, OAT, WEPH... |
| In VIP only (live, not in stale FDSN) | 14 | BEGA, BRTS, CLV2, DNL, DNL2, HMV1, MRAT, ROBE, STR2, SUND, TPSO, UTT, WALR, WKA |
| In FDSN only (FDSN-registered, not active in VIP right now) | 14 | ALEX, BAMB, CLIL, DAMM, LEU, LGMA, NNP1, NNP2, PENW, PLYP, S88P, USYD, WAH, RNDA |
| In LT only (historic, no longer live or never on new server) | ~3 | (LT-only with no spreadsheet channel hint) |
| Spreadsheet channel-hint only | 0 | (none — spreadsheets confirm where waveform sources do too, never standalone) |
| **ZERO INFO** | **10** | **ARKL, JMS2-5, LKHRT, PLMR, S88M, S88U, TPSOP** |

## Next steps

1. Resolve the 5 divergences above (TPSO network code, HKER/WKA band,
   HML1 multi-instrument, DNL/DNL2 multi-epoch, WILM stale note).
2. Per-station registry override for ARKL, PLMR (EHE/N/Z@60, EchoPros)
   and LKHRT (Gecko, rate TBD) pending operator confirmation.
3. Operator input needed for JMS2-5, S88M/U, TPSOP — or exclude from
   first-pass DU sweep.
4. Decimation-duplicate filter pass over LT for the 11 multi-epoch
   stations (year-range split).
5. Re-pull FDSN snapshot to refresh against 2026-02-03 vintage and
   add the 14 VIP-only stations.
