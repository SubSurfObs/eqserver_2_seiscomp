#!/usr/bin/env python3
"""qa_report — roll up qa_driver results.jsonl into a triage summary.

Counts by finding category × stratum × recorder, worst station-years, and
exemplar records per category. Read-only.
"""

import argparse
import json
from collections import Counter, defaultdict


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("results")
    ap.add_argument("--exemplars", type=int, default=3)
    args = ap.parse_args()

    days = []
    with open(args.results) as f:
        for line in f:
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("t") == "day":
                days.append(r)

    n_ok = sum(1 for d in days
               if d["metrics"].get("verdict") in ("ok", "ok_empty"))
    print(f"days checked: {len(days)}  ok: {n_ok}  "
          f"with findings: {len(days) - n_ok}")

    by_cat = Counter()
    by_cat_stratum = Counter()
    by_cat_rec = Counter()
    unit_findings = Counter()
    exemplars = defaultdict(list)
    for d in days:
        recs = ",".join(d["metrics"].get("recorders", []) or ["?"])
        cats = {f["category"] for f in d.get("findings", [])}
        for c in cats:
            by_cat[c] += 1
            by_cat_stratum[(c, d.get("stratum", "?"))] += 1
            by_cat_rec[(c, recs)] += 1
            if len(exemplars[c]) < args.exemplars:
                exemplars[c].append(
                    f'{d["net"]}.{d["sta"]} {d["date"]} '
                    f'[{d.get("stratum")}] '
                    + "; ".join(str(f.get("detail", f.get("channel", "")))
                                for f in d["findings"]
                                if f["category"] == c)[:160])
        if cats:
            unit_findings[(d["net"], d["sta"], d["date"][:4])] += 1

    print("\n== findings by category (days affected) ==")
    for c, n in by_cat.most_common():
        print(f"  {c:32s} {n}")
        for (cc, s), m in sorted(by_cat_stratum.items()):
            if cc == c:
                print(f"      stratum {s:12s} {m}")
        for (cc, r), m in sorted(by_cat_rec.items()):
            if cc == c:
                print(f"      recorder {r:12s} {m}")

    print("\n== worst station-years ==")
    for (net, sta, yr), n in unit_findings.most_common(15):
        print(f"  {net}.{sta} {yr}: {n} finding-days")

    print("\n== exemplars ==")
    for c, exs in exemplars.items():
        print(f"  {c}:")
        for e in exs:
            print(f"    {e}")


if __name__ == "__main__":
    main()
