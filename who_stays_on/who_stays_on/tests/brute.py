"""Tiny exhaustive solver used ONLY to prove the MILP is optimal on small random inputs."""
from functools import lru_cache
from itertools import product

NEG = float("-inf")


def brute(m):
    shops, D, cap, levels, budget = m["shops"], m["D"], m["cap"], m["levels"], m["budget"]
    n = len(shops)
    mand = [i for i, s in enumerate(shops) if s["type"] == "mandatory"]
    masks = []
    for bits in product([0, 1], repeat=n):
        if all(bits[i] for i in mand):
            load = sum(shops[i]["kw"] for i in range(n) if bits[i])
            if load <= cap:
                cost = 0 if load == 0 else next(c for kw, c in levels if load <= kw)
                masks.append((bits, cost))

    @lru_cache(maxsize=None)
    def go(h, runs, fuel):
        if h == 10:
            return 0
        if m["grid"][h]:
            return go(h + 1, (0,) * n, fuel)
        best = NEG
        for bits, cost in masks:
            if fuel + cost > budget:
                continue
            nr, ok = [], True
            for i in range(n):
                r = 0 if bits[i] else runs[i] + 1
                if shops[i]["type"] == "normal" and r > D:
                    ok = False
                    break
                nr.append(min(r, D + 1))
            if not ok:
                continue
            v = sum(shops[i]["value"][h] for i in range(n) if bits[i])
            sub = go(h + 1, tuple(nr), fuel + cost)
            if sub != NEG:
                best = max(best, v + sub)
        return best

    r = go(0, (0,) * n, 0)
    return None if r == NEG else r
