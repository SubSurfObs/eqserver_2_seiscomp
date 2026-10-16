#!/usr/bin/env python3
"""registry_summary.py — one-screen view of conversion scope.

Reads metadata/station_registry.yaml (the stage-0 source of truth) and prints
counts and station lists by include-status and target network, plus the
grouped reasons for exclusions. Read-only.

    python3 scripts/registry_summary.py            # summary
    python3 scripts/registry_summary.py --net DU   # list one network's in-scope stations
    python3 scripts/registry_summary.py --unknown  # list include: unknown stations
"""
from __future__ import annotations
import argparse
import collections
from pathlib import Path

import yaml

REG = Path(__file__).resolve().parent.parent / "metadata" / "station_registry.yaml"


def classify_reason(reason: str) -> str:
    r = (reason or "").lower()
    if "artefact" in r or "artifact" in r:
        return "archive artefact dir"
    if "test" in r:
        return "test station / deployment"
    if "already converted" in r:
        return "already converted elsewhere"
    if "ga / au" in r or "geoscience australia" in r:
        return "external: GA / AU (ANSN)"
    if "s1 / ausis" in r:
        return "external: S1 / AUSiS"
    if "oz network" in r:
        return "external: OZ"
    if "raspberry" in r:
        return "external: Raspberry Shake"
    if "closed" in r or "shut" in r:
        return "closed"
    return "other / unspecified"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--registry", default=str(REG))
    ap.add_argument("--net", help="list include:true stations for this network")
    ap.add_argument("--unknown", action="store_true", help="list include:unknown stations")
    a = ap.parse_args()

    reg = yaml.safe_load(open(a.registry))
    by_net = collections.defaultdict(list)
    unknown, excluded = [], collections.defaultdict(list)
    for sta, e in reg.items():
        e = e or {}
        inc = e.get("include")
        if inc is True:
            by_net[e.get("target_network")].append(sta)
        elif inc is False:
            excluded[classify_reason(e.get("reason", ""))].append(sta)
        else:
            unknown.append((sta, e.get("target_network")))

    if a.net:
        print(" ".join(sorted(by_net.get(a.net, []))))
        return
    if a.unknown:
        for sta, net in sorted(unknown):
            print(f"{sta:8s} {net or '-'}")
        return

    print(f"registry: {a.registry}  ({len(reg)} entries)\n")
    print("IN SCOPE (include: true)")
    for net in sorted(by_net, key=str):
        print(f"  {str(net):5s} {len(by_net[net]):4d}")
    print(f"\nUNDECIDED (include: unknown)  {len(unknown):4d}")
    print(f"\nEXCLUDED (include: false)     {sum(map(len, excluded.values())):4d}")
    for k, v in sorted(excluded.items(), key=lambda kv: -len(kv[1])):
        print(f"  {len(v):4d}  {k}")


if __name__ == "__main__":
    main()
