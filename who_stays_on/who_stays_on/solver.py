"""THE DECISION LOGIC LIVES HERE.

Public entry point: run(data) -> (http_status, output_dict)

How it decides (the whole day is ONE plan, never hour by hour):
  x[shop, hour] in {0,1}   shop is powered in that outage hour
  y[hour, level] in {0,1}  which fuel-table level that hour is charged (exactly one)
  - powered kW <= level's kW, and > previous level's kW  (so "round up to next level" is exact)
  - sum of litres (whole hundredths) over all outage hours <= budget
  - mandatory shops: x = 1 in every outage hour
  - dark-run rule: in every window of (max_consecutive_dark + 1) consecutive hours that are ALL
    outage hours, each normal shop is on at least once (a grid hour inside a window resets the run,
    so such windows need no constraint)
  Stage 1 maximises total value. Stage 2 keeps that value and minimises diesel (spare diesel stays
  in the tank for Monir bhai).
Solved exactly with HiGHS (scipy.optimize.milp, mip_rel_gap = 0). Works offline.
"""
import threading
import time

import numpy as np
from scipy.optimize import milp, LinearConstraint, Bounds

from validate import validate, InvalidInput, SLOTS

TIME_LIMIT = 3.0    # seconds per MILP stage
TOTAL_LIMIT = 8.0   # seconds for one whole request (brief allows 10)
_state = threading.local()


def _time_left():
    return max(0.3, getattr(_state, "deadline", time.time() + TOTAL_LIMIT) - time.time())


def label(h):
    return f"{10 + h:02d}:00"


def litres(hundredths):
    return hundredths / 100


def fmt_l(hundredths):
    return f"{hundredths / 100:.2f}"


def fuel_for(levels, load):
    """Litres (hundredths) for a combined load: first level >= load. Zero load costs nothing."""
    if load == 0:
        return 0
    for kw, cost in levels:
        if load <= kw:
            return cost
    return None  # above capacity


def outage_hours(m):
    return [h for h in range(SLOTS) if not m["grid"][h]]


def outage_runs(m):
    runs, cur = [], []
    for h in range(SLOTS):
        if m["grid"][h]:
            if cur:
                runs.append(cur)
            cur = []
        else:
            cur.append(h)
    if cur:
        runs.append(cur)
    return runs


# ---------------------------------------------------------------- exact bookkeeping
def hour_load(m, on_set):
    return sum(m["shops"][s]["kw"] for s in on_set)


def totals(m, on):
    """on: {hour: set(shop index)} -> (value, fuel_hundredths)."""
    value = fuel = 0
    for h, on_set in on.items():
        value += sum(m["shops"][s]["value"][h] for s in on_set)
        fuel += fuel_for(m["levels"], hour_load(m, on_set))
    return value, fuel


def violations(m, on):
    """Exact re-check of rules 3-6. Returns list of strings (empty = valid)."""
    bad = []
    for h in outage_hours(m):
        on_set = on[h]
        load = hour_load(m, on_set)
        if load > m["cap"]:
            bad.append(f"capacity exceeded at {label(h)}: {load} > {m['cap']}")
        for i, s in enumerate(m["shops"]):
            if s["type"] == "mandatory" and i not in on_set:
                bad.append(f"mandatory {s['id']} dark at {label(h)}")
    if not bad:
        _, fuel = totals(m, on)
        if fuel > m["budget"]:
            bad.append(f"fuel {fmt_l(fuel)} > budget {fmt_l(m['budget'])}")
    for i, s in enumerate(m["shops"]):
        if s["type"] != "normal":
            continue
        run = 0
        for h in range(SLOTS):
            if m["grid"][h] or i in on[h]:
                run = 0
            else:
                run += 1
                if run > m["D"]:
                    bad.append(f"{s['id']} dark more than {m['D']} h in a row (at {label(h)})")
                    break
    return bad


# ---------------------------------------------------------------- the MILP
def _solve_milp(m, relax_dark=False, mode="value", min_value=None, ignore_budget=False):
    """Returns {hour: set(shop idx)} or None if infeasible / no solution found."""
    O = outage_hours(m)
    shops = m["shops"]
    nS, nH = len(shops), len(O)
    pos = {h: i for i, h in enumerate(O)}

    ext = [(0, 0, 0)]  # (level kW, litres, lowest load that maps to this level)
    prev = 0
    for kw, cost in m["levels"]:
        ext.append((kw, cost, prev + 1))
        prev = kw
    K1 = len(ext)

    nx, ny = nS * nH, nH * K1
    n = nx + ny

    def xi(s, hi):
        return s * nH + hi

    def yi(hi, l):
        return nx + hi * K1 + l

    rows, lo, up = [], [], []

    def add(coefs, lb, ub):
        r = np.zeros(n)
        for j, v in coefs:
            r[j] += v
        rows.append(r)
        lo.append(lb)
        up.append(ub)

    for hi in range(nH):
        add([(yi(hi, l), 1) for l in range(K1)], 1, 1)
        load = [(xi(s, hi), shops[s]["kw"]) for s in range(nS)]
        add(load + [(yi(hi, l), -ext[l][0]) for l in range(K1)], -np.inf, 0)   # load <= level kW
        add(load + [(yi(hi, l), -ext[l][2]) for l in range(K1)], 0, np.inf)    # load >= level's lower edge
    if not ignore_budget:
        add([(yi(hi, l), ext[l][1]) for hi in range(nH) for l in range(K1)], -np.inf, m["budget"])
    if not relax_dark:
        W = m["D"] + 1
        for s in range(nS):
            if shops[s]["type"] != "normal":
                continue
            for start in range(0, SLOTS - W + 1):
                window = range(start, start + W)
                if all(not m["grid"][h] for h in window):
                    add([(xi(s, pos[h]), 1) for h in window], 1, np.inf)
    if min_value is not None:
        add([(xi(s, hi), shops[s]["value"][O[hi]]) for s in range(nS) for hi in range(nH)], min_value, np.inf)

    c = np.zeros(n)
    if mode == "value":
        for s in range(nS):
            for hi in range(nH):
                c[xi(s, hi)] = -shops[s]["value"][O[hi]]
    else:  # minimise diesel
        for hi in range(nH):
            for l in range(K1):
                c[yi(hi, l)] = ext[l][1]

    lb = np.zeros(n)
    ub = np.ones(n)
    for s in range(nS):
        for hi in range(nH):
            if shops[s]["type"] == "mandatory":
                lb[xi(s, hi)] = 1
            elif shops[s]["kw"] > m["cap"]:
                ub[xi(s, hi)] = 0

    A = np.array(rows) if rows else np.zeros((0, n))
    res = milp(c, constraints=LinearConstraint(A, np.array(lo, float), np.array(up, float)),
               integrality=np.ones(n), bounds=Bounds(lb, ub),
               options={"mip_rel_gap": 0.0, "time_limit": min(TIME_LIMIT, _time_left()), "disp": False})
    if res.x is None:
        return None
    xs = np.rint(res.x[:nx]).astype(int)
    on = {h: set() for h in range(SLOTS)}
    for s in range(nS):
        for hi in range(nH):
            if xs[xi(s, hi)] == 1:
                on[O[hi]].add(s)
    return on


def _best_plan(m, relax_dark=False):
    on = _solve_milp(m, relax_dark=relax_dark)
    if on is None:
        return None
    value, _ = totals(m, on)
    on2 = _solve_milp(m, relax_dark=relax_dark, mode="fuel", min_value=value)
    if on2 is not None and totals(m, on2)[0] >= value:
        if relax_dark or not violations(m, on2):
            on = on2
    return on


# ---------------------------------------------------------------- reasons + output
def build_result(m, on):
    shops = m["shops"]
    O = outage_hours(m)
    nS = len(shops)
    value_total, fuel_total = totals(m, on)
    runs = [0] * nS
    dark_hours = [0] * nS
    roster = []
    running = 0
    for h in range(SLOTS):
        if m["grid"][h]:
            runs = [0] * nS
            continue
        on_set = on[h]
        load = hour_load(m, on_set)
        cost = fuel_for(m["levels"], load)
        running += cost
        powered = [shops[i]["id"] for i in range(nS) if i in on_set]
        dark = []
        for i in range(nS):
            if i in on_set:
                runs[i] = 0
                continue
            runs[i] += 1
            dark_hours[i] += 1
            dark.append({"id": shops[i]["id"],
                         "reason": _reason(m, i, h, load, cost, fuel_total, runs[i])})
        roster.append({"hour": h, "time": label(h), "powered": powered, "dark": dark,
                       "load_kw": load, "capacity_kw": m["cap"],
                       "fuel_litres": litres(cost), "fuel_cumulative_litres": litres(running)})
    return {
        "roster": roster,
        "value_total": value_total,
        "fuel_total_litres": litres(fuel_total),
        "dark_hours": {shops[i]["id"]: dark_hours[i] for i in range(nS)},
        "capacity_kw": m["cap"],
        "fuel_budget_litres": litres(m["budget"]),
        "max_consecutive_dark": m["D"],
        "outage_hours": O,
    }


def _reason(m, i, h, load, cost, fuel_total, run):
    """One line, real rule + numbers, for shop i dark at hour h."""
    s = m["shops"][i]
    cap, D = m["cap"], m["D"]
    t = label(h)
    tail = f" [dark {run} h in a row, limit {D}]"
    v = s["value"][h]
    if s["kw"] > cap:
        return (f"Capacity: needs {s['kw']} kW but the generator carries at most {cap} kW, "
                f"so it can never run on the generator.{tail}")
    new_load = load + s["kw"]
    if new_load > cap:
        return (f"Capacity: {load} of {cap} kW already used at {t}; adding its {s['kw']} kW gives "
                f"{new_load} kW > {cap} kW. Its value now is {v}; the plan keeps the shops that "
                f"give more total value.{tail}")
    new_cost = fuel_for(m["levels"], new_load)
    delta = new_cost - cost
    if fuel_total + delta > m["budget"]:
        return (f"Fuel: adding it at {t} raises the hour from {fmt_l(cost)} L to {fmt_l(new_cost)} L "
                f"(+{fmt_l(delta)} L), taking the day to {fmt_l(fuel_total + delta)} L of "
                f"{fmt_l(m['budget'])} L budget. That diesel is worth more in other hours "
                f"(its value now: {v}).{tail}")
    if v == 0:
        return (f"No benefit: worth 0 at {t}; keeping it dark saves {fmt_l(delta)} L "
                f"for hours where shops earn value.{tail}")
    return (f"Trade-off: powering it at {t} is allowed but the optimal plan scores the same or "
            f"higher without it (value {v}, +{fmt_l(delta)} L).{tail}")


# ---------------------------------------------------------------- infeasible diagnosis
def _conflict(rule, shops, hours, message):
    return {"rule": rule, "shops": shops, "hours": hours,
            "times": [label(h) for h in hours], "message": message}


def diagnose(m):
    O = outage_hours(m)
    cap, D, levels = m["cap"], m["D"], m["levels"]
    shops = m["shops"]
    mand = [s for s in shops if s["type"] == "mandatory"]
    normal = [s for s in shops if s["type"] == "normal"]
    mkw = sum(s["kw"] for s in mand)
    out = []
    if not O:
        return out
    mand_ids = [s["id"] for s in mand]

    if mkw > cap:
        names = " + ".join(f"{s['id']} {s['kw']} kW" for s in mand)
        out.append(_conflict("mandatory_capacity", mand_ids, O,
                             f"Capacity (rule 3) vs mandatory (rule 5): mandatory shops ({names}) need "
                             f"{mkw} kW in every outage hour, but the generator carries {cap} kW."))
    else:
        mand_cost = fuel_for(levels, mkw) * len(O)
        if mand_cost > m["budget"]:
            out.append(_conflict("mandatory_fuel", mand_ids, O,
                                 f"Fuel (rule 4) vs mandatory (rule 5): mandatory shops alone ({mkw} kW = "
                                 f"{fmt_l(fuel_for(levels, mkw))} L/h x {len(O)} outage hours) need "
                                 f"{fmt_l(mand_cost)} L but the budget is {fmt_l(m['budget'])} L."))
        free = cap - mkw
        for s in normal:
            if s["kw"] > free:
                for run in outage_runs(m):
                    if len(run) > D:
                        if s["kw"] > cap:
                            why = f"needs {s['kw']} kW, more than the generator's {cap} kW"
                        else:
                            why = (f"needs {s['kw']} kW but only {free} kW is left after mandatory shops "
                                   f"({mkw} of {cap} kW)")
                        out.append(_conflict("dark_run_oversize", [s["id"]], run,
                                             f"Dark-run (rule 6): {s['id']} {why}, so it is dark for the whole "
                                             f"outage {label(run[0])}-{label(run[-1] + 1)} "
                                             f"({len(run)} h in a row); the limit is {D}."))
    if out:
        return out

    W = D + 1
    need = sum(s["kw"] for s in normal)
    for start in range(0, SLOTS - W + 1):
        window = list(range(start, start + W))
        if all(not m["grid"][h] for h in window) and need > W * (cap - mkw):
            out.append(_conflict("dark_run_capacity", [s["id"] for s in normal], window,
                                 f"Dark-run (rule 6) vs capacity (rule 3): within {label(window[0])}-"
                                 f"{label(window[-1] + 1)} every normal shop must be on at least once, "
                                 f"needing {need} kW-hours, but only {W} x {cap - mkw} = {W * (cap - mkw)} "
                                 f"kW-hours are free after mandatory shops."))
            return out

    cheapest = _solve_milp(m, mode="fuel", ignore_budget=True)
    nids = [s["id"] for s in normal]
    if cheapest is None:
        out.append(_conflict("dark_run_capacity", nids, O,
                             f"Dark-run (rule 6) vs capacity (rule 3): no roster keeps mandatory shops on, "
                             f"stays within {cap} kW and leaves every normal shop dark at most {D} h in a row "
                             f"(even with unlimited diesel)."))
    else:
        _, mf = totals(m, cheapest)
        if mf > m["budget"]:
            out.append(_conflict("dark_run_fuel", nids, O,
                                 f"Fuel (rule 4) vs dark-run (rule 6): the cheapest roster that keeps mandatory "
                                 f"shops on and every normal shop dark at most {D} h in a row needs "
                                 f"{fmt_l(mf)} L, but the budget is {fmt_l(m['budget'])} L."))
        else:
            out.append(_conflict("unknown", [], O, "No roster satisfies all rules together."))
    return out


def _run_violations(m, on):
    """Dark runs longer than allowed in a (best-effort) roster."""
    res = []
    for i, s in enumerate(m["shops"]):
        if s["type"] != "normal":
            continue
        run, bad = 0, []
        for h in range(SLOTS):
            if m["grid"][h] or i in on[h]:
                run = 0
            else:
                run += 1
                if run > m["D"]:
                    bad.append(label(h))
        if bad:
            res.append({"id": s["id"], "hours_over_limit": bad})
    return res


# ---------------------------------------------------------------- public entry point
def run(data):
    _state.deadline = time.time() + TOTAL_LIMIT
    try:
        m = validate(data)
    except InvalidInput as e:
        return 400, {"status": "invalid_input", "field": e.field,
                     "message": f"{e.field}: {e.msg}"}

    O = outage_hours(m)
    if not O:
        out = build_result(m, {h: set() for h in range(SLOTS)})
        out["status"] = "ok"
        return 200, _ordered(out)

    on = _best_plan(m)
    if on is not None and not violations(m, on):
        out = build_result(m, on)
        out["status"] = "ok"
        return 200, _ordered(out)

    conflicts = diagnose(m)
    out = {"status": "infeasible",
           "message": conflicts[0]["message"] if conflicts else "No roster satisfies all rules together.",
           "conflicts": conflicts, "roster": [], "value_total": 0, "fuel_total_litres": 0.0,
           "dark_hours": {s["id"]: 0 for s in m["shops"]},
           "capacity_kw": m["cap"], "fuel_budget_litres": litres(m["budget"]),
           "max_consecutive_dark": m["D"], "outage_hours": O, "best_effort": None}
    if not any(c["rule"].startswith("mandatory") for c in conflicts):
        best = _best_plan(m, relax_dark=True)
        if best is not None and not [v for v in violations(m, best) if "dark more than" not in v]:
            be = build_result(m, best)
            be["label"] = ("BEST EFFORT, NOT VALID: capacity, diesel and mandatory shops are respected, "
                           "but the dark-run limit is broken")
            be["broken_rule"] = "max_consecutive_dark"
            be["dark_run_violations"] = _run_violations(m, best)
            out["best_effort"] = be
    return 200, _ordered(out)


def _ordered(out):
    first = ["status"]
    return {**{k: out[k] for k in first}, **{k: v for k, v in out.items() if k not in first}}
