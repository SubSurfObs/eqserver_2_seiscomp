# Metadata

This directory holds station metadata relevant to the EqServer → SDS conversion pipeline.

## Contents

| File/Dir | Purpose |
|---|---|
| `links.md` | Links to external metadata sources (FDSN, SeisComP inventories, spreadsheets) |
| `station_registry.yaml` | Authoritative per-station mapping: scope, target network, recorder types, coverage |
| `uploaded/` | Any metadata files copied in directly (station XML, CSVs, etc.) |

## How to use

- Add external metadata URLs to `links.md` — Claude will read these and update `station_registry.yaml`
- Drop any local metadata files into `uploaded/`
- `station_registry.yaml` is the ground truth consumed by the pipeline; do not edit it without understanding the network code rules documented in `CLAUDE.md`
