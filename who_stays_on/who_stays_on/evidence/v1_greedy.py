"""v1: the hour-by-hour greedy approach the brief warns about (kept as the baseline).

For each outage hour in order: power mandatory shops, then add normal shops by highest value that
still fit the capacity and the diesel LEFT SO FAR. It never looks ahead, so it spends diesel at
noon and ignores the dark-run rule.

Usage: python evidence/v1_greedy.py input.json output.json
"""
import json
import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from validate import validate                                   # noqa: E402
from solver import build_result, outage_hours, fuel_for, hour_load  # noqa: E402


def greedy(m):
    left = m["budget"]
    on = {h: set() for h in range(10)}
    for h in outage_hours(m):
        chosen = {i for i, s in enumerate(m["shops"]) if s["type"] == "mandatory"}
        order = sorted((i for i, s in enumerate(m["shops"]) if s["type"] == "normal"),
                       key=lambda i: -m["shops"][i]["value"][h])
        for i in order:
            load = hour_load(m, chosen) + m["shops"][i]["kw"]
            if load <= m["cap"] and fuel_for(m["levels"], load) <= left:
                chosen.add(i)
        left -= fuel_for(m["levels"], hour_load(m, chosen))
        on[h] = chosen
    return on


if __name__ == "__main__":
    data = json.load(open(sys.argv[1]), parse_float=Decimal)
    m = validate(data)
    out = build_result(m, greedy(m))
    out["status"] = "ok"
    json.dump(out, open(sys.argv[2], "w"), indent=2)
