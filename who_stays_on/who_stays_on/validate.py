"""Input validation. Turns the raw JSON into a clean model dict, or raises InvalidInput.

Rules checked here: rule 2 (shops), rule 4 (fuel table, hundredths), rule 9 (invalid input).
Unknown extra keys (for example "_story") are ignored on purpose.
All fuel numbers become whole hundredths of a litre, so there is no float rounding anywhere.
"""
import math
from decimal import Decimal

SLOTS = 10
MAX_SHOPS = 12


class InvalidInput(Exception):
    def __init__(self, field, msg):
        self.field = field
        self.msg = msg
        super().__init__(f"{field}: {msg}")


def _num(v, field):
    if isinstance(v, bool):
        raise InvalidInput(field, "must be a number (got true/false)")
    if isinstance(v, int):
        return Decimal(v)
    if isinstance(v, float):
        if not math.isfinite(v):
            raise InvalidInput(field, "must be a finite number")
        return Decimal(repr(v))
    if isinstance(v, Decimal):
        if not v.is_finite():
            raise InvalidInput(field, "must be a finite number")
        return v
    raise InvalidInput(field, "must be a number")


def _whole(v, field, minimum=None):
    d = _num(v, field)
    if d != d.to_integral_value():
        raise InvalidInput(field, "must be a whole number")
    n = int(d)
    if minimum is not None and n < minimum:
        raise InvalidInput(field, f"must be at least {minimum}")
    return n


def _hundredths(v, field):
    d = _num(v, field)
    if d < 0:
        raise InvalidInput(field, "must not be negative")
    h = d * 100
    if h != h.to_integral_value():
        raise InvalidInput(field, "has more than two decimal places")
    return int(h)


def validate(data):
    if not isinstance(data, dict):
        raise InvalidInput("(body)", "must be a JSON object")
    for key in ("grid_available", "generator_capacity_kw", "fuel_budget_litres",
                "fuel_table", "max_consecutive_dark", "shops"):
        if key not in data:
            raise InvalidInput(key, "missing required field")

    grid = data["grid_available"]
    if not isinstance(grid, list) or len(grid) != SLOTS:
        raise InvalidInput("grid_available", f"must be a list of exactly {SLOTS} true/false values")
    for i, g in enumerate(grid):
        if not isinstance(g, bool):
            raise InvalidInput(f"grid_available[{i}]", "must be true or false")

    cap = _whole(data["generator_capacity_kw"], "generator_capacity_kw", 1)
    budget = _hundredths(data["fuel_budget_litres"], "fuel_budget_litres")
    dmax = _whole(data["max_consecutive_dark"], "max_consecutive_dark", 0)

    table = data["fuel_table"]
    if not isinstance(table, list) or not table:
        raise InvalidInput("fuel_table", "must be a non-empty list")
    levels = []
    prev = 0
    for i, row in enumerate(table):
        if not isinstance(row, dict):
            raise InvalidInput(f"fuel_table[{i}]", "must be an object with load_kw and litres_per_hour")
        for k in ("load_kw", "litres_per_hour"):
            if k not in row:
                raise InvalidInput(f"fuel_table[{i}].{k}", "missing required field")
        kw = _whole(row["load_kw"], f"fuel_table[{i}].load_kw", 1)
        lit = _hundredths(row["litres_per_hour"], f"fuel_table[{i}].litres_per_hour")
        if kw <= prev:
            raise InvalidInput(f"fuel_table[{i}].load_kw", "load levels must be strictly increasing")
        prev = kw
        levels.append((kw, lit))
    if levels[-1][0] != cap:
        raise InvalidInput("fuel_table", f"last load level ({levels[-1][0]} kW) must equal generator_capacity_kw ({cap} kW)")

    raw_shops = data["shops"]
    if not isinstance(raw_shops, list):
        raise InvalidInput("shops", "must be a list")
    if len(raw_shops) > MAX_SHOPS:
        raise InvalidInput("shops", f"at most {MAX_SHOPS} shops allowed, got {len(raw_shops)}")
    shops = []
    seen = set()
    for i, s in enumerate(raw_shops):
        p = f"shops[{i}]"
        if not isinstance(s, dict):
            raise InvalidInput(p, "must be an object")
        for k in ("id", "kw", "type", "value_per_slot"):
            if k not in s:
                raise InvalidInput(f"{p}.{k}", "missing required field")
        sid = s["id"]
        if not isinstance(sid, str) or not sid.strip():
            raise InvalidInput(f"{p}.id", "must be a non-blank string")
        if sid in seen:
            raise InvalidInput(f"{p}.id", f"duplicate id '{sid}'")
        seen.add(sid)
        kw = _whole(s["kw"], f"{p}.kw", 1)
        if s["type"] not in ("mandatory", "normal"):
            raise InvalidInput(f"{p}.type", "must be 'mandatory' or 'normal'")
        vals = s["value_per_slot"]
        if not isinstance(vals, list) or len(vals) != SLOTS:
            raise InvalidInput(f"{p}.value_per_slot", f"must be a list of exactly {SLOTS} numbers")
        value = [_whole(v, f"{p}.value_per_slot[{j}]", 0) for j, v in enumerate(vals)]
        shops.append({"id": sid, "kw": kw, "type": s["type"], "value": value})

    return {"grid": list(grid), "cap": cap, "budget": budget, "levels": levels,
            "D": dmax, "shops": shops}
