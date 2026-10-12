"""Re-runs all evidence against the RUNNING API and compares with the committed outputs.

Start the app first (python app.py), then:  python evidence/run_evidence.py [http://localhost:8000]
"""
import json
import os
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
from checker import check  # noqa: E402

URL = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000").rstrip("/") + "/api/roster"


def post(path):
    body = open(path, "rb").read()
    req = urllib.request.Request(URL, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def load(name):
    return json.load(open(os.path.join(HERE, name)))


bad = 0


def show(ok, text):
    global bad
    bad += 0 if ok else 1
    print(("PASS " if ok else "FAIL ") + text)


# 1. input that broke v1 (hour-by-hour greedy)
inp = load("broke_v1_input.json")
v1 = load("broke_v1_output.json")
problems = check(inp, v1)
show(bool(problems), f"v1 greedy output is invalid, as recorded: {problems}")
code, fixed = post(os.path.join(HERE, "broke_v1_input.json"))
show(code == 200 and fixed == load("broke_v1_fixed_output.json") and not check(inp, fixed),
     f"fixed app: HTTP {code}, status {fixed['status']}, value {fixed.get('value_total')}, "
     f"fuel {fixed.get('fuel_total_litres')} L, checker clean, matches committed output")

# 2. boundary: diesel exactly enough, then one hundredth less
code, o = post(os.path.join(HERE, "boundary_input.json"))
show(code == 200 and o["status"] == "ok" and not check(load("boundary_input.json"), o)
     and o == load("boundary_output.json"),
     f"boundary (budget exactly enough): status {o['status']}, fuel {o.get('fuel_total_litres')} L")
code, o = post(os.path.join(HERE, "boundary_minus_001_input.json"))
show(code == 200 and o["status"] == "infeasible" and o == load("boundary_minus_001_output.json"),
     f"boundary (one hundredth less): status {o['status']} - {o.get('message')}")

# 3. HTTP contract for invalid input
req = urllib.request.Request(URL, data=b'{"grid_available": []}', headers={"Content-Type": "application/json"})
try:
    urllib.request.urlopen(req, timeout=15)
    show(False, "invalid input should return HTTP 400")
except urllib.error.HTTPError as e:
    body = json.loads(e.read())
    show(e.code == 400 and body["status"] == "invalid_input", f"invalid input: HTTP {e.code}, {body['message']}")

print("\nALL GOOD" if not bad else f"\n{bad} FAILED")
sys.exit(1 if bad else 0)
