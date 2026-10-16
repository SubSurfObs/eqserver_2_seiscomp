---
name: project-sgwu-lt-findings-2026-06-21
description: "Direct LT verification 2026-06-21: SGWU channel naming consistent HH1/HH2/HHZ across the RT130 era (2014-2023); switches to CH?/DH?/FH? in 2025-2026 Gecko era. LT coverage shockingly low for several years: 2014 (23% of year), 2021 (2.5%!), 2022 (37%), 2023 (43%) — possibly correlates with the unparsed-cohort drop. Same pattern likely at LOYU, WILU, TRPU."
metadata:
  node_type: memory
  type: project
  originSessionId: 66713a2d-7a0d-44f1-ae1a-e3a1e4de0f66
---

Direct LT byte inspection 2026-06-21, before compaction. Saving the
findings explicitly because they revise an earlier (wrong) memory note.

## SGWU LT channel naming — direct verification

```
2014: HH1.D + HH2.D + HHZ.D   84 files per channel (= 23% of full year)
2015: HH1.D + HH2.D + HHZ.D  359 per chan (essentially full year)
2016: HH1.D + HH2.D + HHZ.D  356 per chan
2017: HH1.D + HH2.D + HHZ.D  362 per chan
2018: HH1.D + HH2.D + HHZ.D  363 per chan
2019: HH1.D + HH2.D + HHZ.D  201 per chan (55% of year)
2020: HH1.D + HH2.D + HHZ.D  296 per chan (81%)
2021: HH1.D + HH2.D + HHZ.D    9 per chan (2.5% !!)
2022: HH1.D + HH2.D + HHZ.D  137 per chan (37%)
2023: HH1.D + HH2.D + HHZ.D  157 per chan (43%)
2025: CHE.D + CHN.D + CHZ.D                      ← Gecko era starts
2026: CHE/N/Z + DHE/N/Z + FHE/N/Z                ← multi-rate Gecko
```

## Conclusions

1. **The LT channel naming for RT130-era SGWU is HH1/HH2/HHZ** — preserves
   the down-hole 1/2 convention. **Not** HHN/HHE/HHZ as my earlier (wrong)
   memory note claimed. The LT-vs-server channel-code mismatch is real
   (server gets E/N substitution; LT does not). Per
   [[project-rt130-borehole-z12-mapping]] this is operator-accepted.
2. **A clean SGWU 2018 day** has 1 trace × 3 channels at 100% sample
   coverage at 200 Hz — sample SGWU 2018 doy=196 verified directly.
   So scan 1's conversion of this station-day was perfect.
3. **But several SGWU years have shockingly low LT file counts:**
   - 2014 (23%) — could be start-of-deployment
   - 2019 (55%)
   - **2021 (2.5%! — only 9 day-files per channel)**
   - 2022 (37%)
   - 2023 (43%)
4. **The drop almost certainly correlates with the 995k unparsed-cohort
   underscore-tele files at SGWU** that scan 1 ignored. Quantitative
   correlation pending source-vs-LT audit per year.

## What to do

- Don't trust the previous LT scan's "definite=X" finding for these
  stations as the only signal of drop — also check **per-year LT file
  count** vs expected (365 per channel for full year).
- For LOYU, WILU, TRPU specifically: the SGWU pattern likely repeats.
  Add direct LT count checks to the Step 1 work of the scan 2 build order.
- Once the underscore-tele regex is added to `level1.py` (planned in Step 3
  of the scan 2 build order), re-attempt these stations and quantify
  recovered data.

Linked: [[project-rt130-borehole-z12-mapping]],
[[project-underscore-tele-files]] (now corrected to acknowledge RT130, not
Minimus, as the dominant carrier), [[reference-scan2-build-order]] (Step 3
where the regex extension happens).
