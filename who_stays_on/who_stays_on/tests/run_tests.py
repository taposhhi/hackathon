"""python tests/run_tests.py   (no pytest needed, no server needed)"""
import json
import os
import random
import sys
from decimal import Decimal

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

from solver import run          # noqa: E402
from checker import check       # noqa: E402
from validate import validate   # noqa: E402
from brute import brute         # noqa: E402

PASS = FAIL = 0


def load(path):
    with open(path) as f:
        return json.load(f, parse_float=Decimal)


def ok(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("PASS", name)
    else:
        FAIL += 1
        print("FAIL", name, extra)


def base():
    return json.loads(json.dumps(load(os.path.join(HERE, "worked_example.json")), default=float))


# 1. worked example from the brief: best value 84, exactly 8.00 L
st, out = run(load(os.path.join(HERE, "worked_example.json")))
ok("worked example status", st == 200 and out["status"] == "ok")
ok("worked example value 84", out["value_total"] == 84, out["value_total"])
ok("worked example fuel 8.00", out["fuel_total_litres"] == 8.0, out["fuel_total_litres"])
ok("worked example checker", not check(load(os.path.join(HERE, "worked_example.json")), out))

# 2. public sample passes the checker
sample = os.path.join(os.path.dirname(HERE), "examples", "sample_input.json")
st, out = run(load(sample))
ok("sample checker", st == 200 and not check(load(sample), out), check(load(sample), out))

# 3. no outage -> empty roster, zero totals
d = base(); d["grid_available"] = [True] * 10
st, out = run(d)
ok("no outage", st == 200 and out["status"] == "ok" and out["roster"] == [] and out["value_total"] == 0
   and out["fuel_total_litres"] == 0)

# 4. empty shop list
d = base(); d["shops"] = []
st, out = run(d)
ok("empty shops", st == 200 and out["status"] == "ok" and out["fuel_total_litres"] == 0
   and all(e["powered"] == [] for e in out["roster"]))

# 5. invalid inputs -> 400 naming the field
bad_cases = {
    "missing field": (lambda d: d.pop("fuel_table"), "fuel_table"),
    "grid length": (lambda d: d.__setitem__("grid_available", [True] * 9), "grid_available"),
    "grid type": (lambda d: d["grid_available"].__setitem__(0, 1), "grid_available[0]"),
    "dup id": (lambda d: d["shops"][1].__setitem__("id", "pharmacy"), "shops[1].id"),
    "blank id": (lambda d: d["shops"][1].__setitem__("id", "  "), "shops[1].id"),
    "neg value": (lambda d: d["shops"][1]["value_per_slot"].__setitem__(3, -1), "value_per_slot[3]"),
    "string kw": (lambda d: d["shops"][1].__setitem__("kw", "4"), "shops[1].kw"),
    "zero kw": (lambda d: d["shops"][1].__setitem__("kw", 0), "shops[1].kw"),
    "frac kw": (lambda d: d["shops"][1].__setitem__("kw", 2.5), "shops[1].kw"),
    "bad type": (lambda d: d["shops"][1].__setitem__("type", "vip"), "shops[1].type"),
    "3 decimals": (lambda d: d.__setitem__("fuel_budget_litres", 8.001), "fuel_budget_litres"),
    "neg budget": (lambda d: d.__setitem__("fuel_budget_litres", -1), "fuel_budget_litres"),
    "table order": (lambda d: d["fuel_table"].reverse(), "fuel_table"),
    "table last != cap": (lambda d: d.__setitem__("generator_capacity_kw", 12), "fuel_table"),
    "13 shops": (lambda d: d["shops"].extend(
        [{"id": f"x{i}", "kw": 1, "type": "normal", "value_per_slot": [1] * 10} for i in range(8)]), "shops"),
    "value len": (lambda d: d["shops"][1].__setitem__("value_per_slot", [1] * 9), "value_per_slot"),
    "body not object": (None, "(body)"),
}
for name, (mut, field) in bad_cases.items():
    d = base()
    if mut is None:
        d = [1, 2]
    else:
        mut(d)
    st, out = run(d)
    ok(f"invalid: {name}", st == 400 and out["status"] == "invalid_input" and field in out["message"],
       (st, out.get("message")))

# 6. extra keys are ignored
d = base(); d["_story"] = "hello"; d["whatever"] = 1
ok("extra keys ignored", run(d)[0] == 200)

# 7. infeasible cases carry a real conflict
d = base(); d["shops"][0]["kw"] = 9; d["shops"][1]["type"] = "mandatory"   # 9 + 4 mandatory > 10
st, out = run(d)
ok("infeasible: mandatory > capacity", st == 200 and out["status"] == "infeasible"
   and out["conflicts"][0]["rule"] == "mandatory_capacity" and out["best_effort"] is None, out.get("message"))
d = base(); d["fuel_budget_litres"] = 1.0
st, out = run(d)
ok("infeasible: mandatory fuel", st == 200 and out["status"] == "infeasible"
   and out["conflicts"][0]["rule"] == "mandatory_fuel", out.get("message"))
d = base(); d["shops"][1]["kw"] = 11                                        # 11 kW normal shop, 3-hour outage
d["grid_available"] = [True] * 7 + [False] * 3
st, out = run(d)
ok("infeasible: oversize shop dark 3 in a row", out["status"] == "infeasible"
   and out["conflicts"][0]["rule"] == "dark_run_oversize", out.get("message"))
d = base(); d["fuel_budget_litres"] = 3.0                                   # tight diesel, shops must rotate
st, out = run(d)
ok("infeasible: fuel vs dark-run has best effort",
   out["status"] == "infeasible" and out["conflicts"][0]["rule"] in ("dark_run_fuel", "mandatory_fuel"), out.get("message"))
ok("infeasible message names numbers", any(ch.isdigit() for ch in out["message"]))

# 8. boundaries: oversize normal shop is fine if outage runs are short enough
d = base(); d["shops"][1]["kw"] = 11
d["grid_available"] = [True, True, False, False, True, True, True, False, False, True]   # runs of 2
st, out = run(d)
ok("oversize normal shop with short outages is ok", st == 200 and out["status"] == "ok"
   and not check(d, out), out.get("message"))

# 9. max_consecutive_dark = 0 forces every normal shop on every outage hour
d = base(); d["max_consecutive_dark"] = 0
st, out = run(d)
ok("D=0 either infeasible or nobody dark", out["status"] == "infeasible"
   or all(not e["dark"] for e in out["roster"]))

# 10. random small inputs: MILP value == exhaustive optimum, feasibility agrees, checker passes
random.seed(7)
n_ok = n_inf = 0
for it in range(250):
    n = random.randint(1, 5)
    cap = random.randint(4, 12)
    k = random.randint(1, 3)
    cuts = sorted(random.sample(range(1, cap), min(k - 1, cap - 1))) + [cap]
    lit, levels = 0, []
    for c in cuts:
        lit += random.randint(40, 150)
        levels.append({"load_kw": c, "litres_per_hour": lit / 100})
    grid = [random.random() < 0.4 for _ in range(10)]
    if all(grid):
        grid[random.randrange(10)] = False
    shops = []
    for i in range(n):
        shops.append({"id": f"s{i}", "kw": random.randint(1, cap + 1),
                      "type": "mandatory" if random.random() < 0.25 else "normal",
                      "value_per_slot": [random.randint(0, 9) for _ in range(10)]})
    nout = grid.count(False)
    data = {"grid_available": grid, "generator_capacity_kw": cap,
            "fuel_budget_litres": random.randint(nout * 40, nout * 200) / 100,
            "fuel_table": levels, "max_consecutive_dark": random.randint(1, 3), "shops": shops}
    d = json.loads(json.dumps(data))
    st, out = run(json.loads(json.dumps(data), parse_float=Decimal))
    m = validate(json.loads(json.dumps(data), parse_float=Decimal))
    want = brute(m)
    if out["status"] == "ok":
        n_ok += 1
        good = want is not None and out["value_total"] == want and not check(d, out)
    else:
        n_inf += 1
        good = want is None and out["status"] == "infeasible" and out["message"]
    if not good:
        print("MISMATCH", it, out["status"], out.get("value_total"), "brute:", want, check(d, out) if out["status"] == "ok" else "")
        json.dump(data, open("/tmp/mismatch.json", "w"))
        FAIL += 1
        break
else:
    PASS += 1
    print(f"PASS random vs brute force ({n_ok} ok cases, {n_inf} infeasible cases)")

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
