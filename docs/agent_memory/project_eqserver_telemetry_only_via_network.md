---
name: project-eqserver-telemetry-only-via-network
description: "Telemetry files in the EqServer archive can ONLY have arrived via real-time network (FTP for EchoPro, serial for Gecko) into EqServer's inbox. There is no physical upload pathway. Archive mtimes reflect EqServer's dispatch time, NOT the original network arrival."
metadata: 
  node_type: memory
  type: project
  originSessionId: 66713a2d-7a0d-44f1-ae1a-e3a1e4de0f66
---

Operator-confirmed 2026-06-20: telemetry files (space-separated filenames in the EqServer archive) can only have arrived through one path:

1. Real-time network transmission from the field recorder into EqServer's inbox.
   - EchoPro: FTP
   - Gecko: serial stream
2. EqServer's own (operator-described as "glitchy") dispatch process moves files from the inbox into `archive/<STA>/continuous/<YYYY>/<MM>/<DD>/`.

**There is NO physical upload pathway for telemetry files.** No SD card upload route, no scp, no manual deposit. If a telemetry-named file exists in the archive, it came over the network.

**Archive mtime is the EqServer dispatch time, NOT the original arrival time.** A telemetry file with mtime Jan 2024 doesn't mean the data arrived in Jan 2024 — it means EqServer's dispatcher moved it from inbox to archive then. The original network arrival could be months or years earlier; the inbox may have been backlogged.

**Implications for understanding duplicate-telemetry patterns:**

Two telemetry populations covering the same minute (e.g. `2022-10-23 0001 STBK.ms.zip` and `2022-10-23 0001 00 STBK.ms.zip`) cannot both be independent real-time uploads. They are most likely:
- The same inbox content dispatched twice by EqServer's process, OR
- Different segmentation/naming applied by two passes of EqServer's dispatcher (different code versions / glitch modes), OR
- Real-time arrival + a re-processed copy from a backup of the inbox

Byte-size similarity (e.g. 54087 vs 54127 bytes for STBK 2022-10-23 0001) supports "same telemetry stream, slightly different framing" rather than "two different recordings."

**Disk-format files** (underscore-separated, no spaces) DO have a physical upload pathway — they were written by the local recorder's SD card and arrived at EqServer via card upload. These can co-exist with telemetry copies of the same minute and arrive at very different mtimes from telemetry.

**How to apply:**

When seeing duplicate or extra files in the EqServer archive:
- If they're telemetry-format: they cannot be from a separate upload mechanism. They're from EqServer's dispatcher acting on inbox content.
- mtimes don't tell you when telemetry "arrived" — they tell you when EqServer moved it.
- If the dispatcher misbehaved (glitchy software), duplicate file populations are plausible artifacts.
- For deduplication, treat near-identical telemetry copies (matching minute, close byte sizes) as the same data, not as alternate recordings.

Linked: [[project-class-a-vs-class-b-framing]], [[project-class-b-affects-echopro-too]].
