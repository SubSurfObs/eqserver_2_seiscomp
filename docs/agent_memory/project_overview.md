---
name: Project overview
description: Core design decisions and architecture for eqserver_2_seiscomp second-attempt pipeline
type: project
---

Python pipeline to convert a decade-scale (~2012–2025) EqServer waveform archive to SeisComP SDS format. Second attempt — legacy bash+Java pipeline in `legacy/` kept for reference only.

**Why second attempt:** Legacy pipeline had station contamination bugs, broken KEEP flag, EqConvert losing 3-component data in directory mode, JVM-per-file overhead (~1 min/day with 16 procs). Target: 10× faster using Python + sudspy (no JVM), in-memory merge, multiprocessing.Pool.

**Meta-principle (2026-03-27):** CLAUDE.md is a living document. Archive spans 2012–2025; conventions changed. Claude must test all structural assumptions (filenames, SS behaviour, channel names, etc.) against real archive data and update CLAUDE.md when findings differ. Legacy notes are starting hypotheses, not ground truth.

**Key design decisions added 2026-03-27:**
1. **Pre-scan first**: build a full SQLite manifest of the entire archive before any conversion; flow control decisions driven from manifest queries, not per-day filesystem scans.
2. **VM access**: Claude Code has direct connection to the EqServer VM (`seiscomp` user); archive at `/data/repository/archive/`; never modify files in place.
3. **Channel remapping config**: requires a versioned YAML/CSV station-channel mapping table covering all stations before conversion begins; target all seismometers → `CH?` band code.
4. **Test strategy**: semi-random stratified sampling from manifest + targeted edge-case days + golden-reference regression tests against legacy output.

**How to apply:** When suggesting implementation steps, prioritise manifest/pre-scan infrastructure before conversion code. Remind user that station-channel mapping table must be populated before any station can be processed end-to-end.
