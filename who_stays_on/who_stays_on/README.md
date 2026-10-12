# Who Stays On? Generator roster for a day of load shedding

A web app and an API that decide, for every outage hour, which shops stay powered and which sit in the dark, within the generator capacity, the diesel budget, the mandatory shops and the "never dark too long" rule, while serving as much value as possible.

## Start

```
pip install -r requirements.txt     # scipy + numpy only (needs to be installed once)
python app.py                       # serves the screen and the API on port 8000
```

Open http://localhost:8000. Use another port with `PORT=9000 python app.py`.
No internet is needed at runtime. The server uses only the Python standard library plus scipy.

## Where things are (decision logic in two minutes)

| File | What it does |
|---|---|
| `solver.py` | **The decision logic.** `run(data)` validates, solves, explains, and returns `(http_status, output)`. The top docstring explains the model. |
| `validate.py` | Input checks (rules 2, 4, 9). Fuel is converted to whole hundredths of a litre here. |
| `app.py` | HTTP server. `POST /api/roster` calls `solver.run`. Everything else serves `static/index.html`. |
| `static/index.html` | The screen. It sends the input text to `POST /api/roster` and draws the board from the response. It does no solving. |
| `checker.py` | Independent validator: recomputes every rule from an output file. `python checker.py input.json output.json` |
| `cli.py` | `python cli.py input.json [output.json]` runs the solver without the server. |
| `tests/run_tests.py` | Test suite. `tests/brute.py` is an exhaustive solver used to prove the main solver is optimal on random small inputs. |
| `evidence/` | See below. |
| `outputs/` | Our outputs for the public inputs. |

## How it decides

The whole day is one plan, solved exactly as a 0/1 optimisation (HiGHS via `scipy.optimize.milp`, optimality gap 0):

- `x[shop, hour]`: shop powered in that outage hour. `y[hour, level]`: which fuel-table level the hour is charged (exactly one).
- Powered kW is at most the level's kW and above the previous level's kW, so "round up to the next level" is exact, and fuel is charged once per hour on the combined load.
- Day's diesel (whole hundredths) is at most the budget. Mandatory shops are on in every outage hour.
- Dark-run rule: in every window of `max_consecutive_dark + 1` consecutive hours that are all outage hours, each normal shop is on at least once. A grid hour inside a window resets the run, so such windows need no constraint.
- Stage 1 maximises total value. Stage 2 keeps that value and uses the least diesel.

Every dark entry gets a one-line reason built from the final roster: over capacity, over the diesel budget, no value that hour, with the real numbers and the shop's current dark run. If no roster exists the answer is `infeasible` with the rule and the shops and hours in conflict, plus a labelled best-effort roster when one can be built.

## API

`POST /api/roster`, body = input JSON, response = output JSON. HTTP 200 for `ok` and `infeasible`, HTTP 400 for `invalid_input` (the message names the field). Unknown extra keys (such as `_story`) are ignored. CORS is open so other markets can call it.

Output fields: `status`, `roster[]` (`hour`, `time`, `powered`, `dark[]` with `id` and `reason`, `load_kw`, `fuel_litres`, `fuel_cumulative_litres`), `value_total`, `fuel_total_litres`, `dark_hours`, plus `capacity_kw`, `fuel_budget_litres`, `max_consecutive_dark`, `outage_hours`. `hour` is the slot 0-9 (10:00-19:00).
The organisers' `schema.md` was not available when this was built. If it differs, change only `build_result()` and the `run()` return values in `solver.py`; the solver itself does not depend on the output format.

## Tests and evidence

```
python tests/run_tests.py                       # 33 checks, no server needed
python app.py   (in another terminal)
python evidence/run_evidence.py http://localhost:8000
```

- `evidence/broke_v1_*`: an input where the hour-by-hour greedy baseline (`v1_greedy.py`) fails: its output breaks the diesel budget and the three-hours rule (`broke_v1_violations.txt`). `broke_v1_fixed_output.json` is the current app's valid answer.
- `evidence/boundary_*`: the 12-shop sample with the budget set to exactly the cheapest feasible roster (valid, uses every drop), and the same input one hundredth of a litre lower (infeasible, fuel conflict named).
- `tests/worked_example.json`: the brief's five-shop example. The solver reaches the stated optimum of 84 with exactly 8.00 L.
- Random small inputs are compared with an exhaustive search: same optimum, same feasibility.

## Public inputs

- `outputs/sample_output.json`: output for `examples/sample_input.json` (value 186, 16.40 L).
- Minute-150 update: when the file arrives, run
  `python cli.py update.json outputs/update_output.json` (or paste it in the screen) and commit the result.
- `examples/simulated_update_input.json` is our own test (16:00 becomes an outage); it is not the organisers' file. Its output is `outputs/simulated_update_output.json` (infeasible at 17.00 L, needs 17.60 L).
