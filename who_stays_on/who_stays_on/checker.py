"""Independent checker: recomputes every rule from the OUTPUT JSON alone.

Usage:  python checker.py input.json output.json
It shares only the input validator with the solver; fuel, capacity, dark runs and totals are
recomputed here from scratch (whole hundredths of a litre, no tolerance).
"""
import json
import sys
from decimal import Decimal

from validate import validate, InvalidInput, SLOTS


def _h(x):
    """number -> whole hundredths, or None if it has more than 2 decimals."""
    d = Decimal(str(x)) * 100
    return int(d) if d == d.to_integral_value() else None


def check(raw_input, out):
    errs = []
    try:
        m = validate(raw_input)
    except InvalidInput as e:
        if out.get("status") != "invalid_input":
            errs.append(f"input is invalid ({e}) but status is {out.get('status')!r}")
        elif e.field.split("[")[0].split(".")[0] not in out.get("message", ""):
            errs.append("invalid_input message does not name the field")
        return errs

    status = out.get("status")
    if status == "infeasible":
        if not out.get("conflicts") or not all(c.get("message") for c in out["conflicts"]):
            errs.append("infeasible without a conflict message")
        return errs
    if status != "ok":
        return [f"unexpected status {status!r}"]

    shops = {s["id"]: s for s in m["shops"]}
    outage = [h for h in range(SLOTS) if not m["grid"][h]]
    roster = out.get("roster", [])
    if sorted(e["hour"] for e in roster) != outage:
        errs.append(f"roster hours {[e['hour'] for e in roster]} != outage hours {outage}")
    by_hour = {e["hour"]: e for e in roster}

    fuel = value = 0
    dark_count = {sid: 0 for sid in shops}
    run = {sid: 0 for sid in shops}
    for h in range(SLOTS):
        if m["grid"][h]:
            run = {sid: 0 for sid in shops}
            continue
        e = by_hour.get(h)
        if e is None:
            continue
        powered = e["powered"]
        dark_ids = [d["id"] for d in e["dark"]]
        if len(set(powered)) != len(powered) or len(set(dark_ids)) != len(dark_ids):
            errs.append(f"hour {h}: repeated id")
        if set(powered) & set(dark_ids):
            errs.append(f"hour {h}: id both powered and dark")
        if set(powered) | set(dark_ids) != set(shops):
            errs.append(f"hour {h}: powered+dark are not exactly the shop ids")
        for d in e["dark"]:
            if not str(d.get("reason", "")).strip():
                errs.append(f"hour {h}: {d.get('id')} dark without reason")
        load = sum(shops[i]["kw"] for i in powered if i in shops)
        if load > m["cap"]:
            errs.append(f"hour {h}: load {load} > capacity {m['cap']}")
        cost = 0 if load == 0 else next((c for kw, c in m["levels"] if load <= kw), None)
        if cost is None:
            cost = 0
        fuel += cost
        value += sum(shops[i]["value"][h] for i in powered if i in shops)
        for sid, s in shops.items():
            if sid in powered:
                run[sid] = 0
            else:
                dark_count[sid] += 1
                run[sid] += 1
                if s["type"] == "mandatory":
                    errs.append(f"hour {h}: mandatory {sid} is dark")
                elif run[sid] > m["D"]:
                    errs.append(f"hour {h}: {sid} dark {run[sid]} h in a row (limit {m['D']})")
    if fuel > m["budget"]:
        errs.append(f"fuel {fuel / 100:.2f} L > budget {m['budget'] / 100:.2f} L")
    if _h(out.get("fuel_total_litres", -1)) != fuel:
        errs.append(f"fuel_total_litres {out.get('fuel_total_litres')} != recomputed {fuel / 100:.2f}")
    if out.get("value_total") != value:
        errs.append(f"value_total {out.get('value_total')} != recomputed {value}")
    if out.get("dark_hours") != dark_count:
        errs.append(f"dark_hours {out.get('dark_hours')} != recomputed {dark_count}")
    return errs


if __name__ == "__main__":
    inp = json.load(open(sys.argv[1]))
    outp = json.load(open(sys.argv[2]))
    problems = check(inp, outp)
    if problems:
        print("INVALID")
        for p in problems:
            print(" -", p)
        sys.exit(1)
    print("VALID")
