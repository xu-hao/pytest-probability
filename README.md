# pytest-probability

[![Documentation](https://readthedocs.org/projects/pytest-probability/badge/?version=latest)](https://pytest-probability.readthedocs.io/en/latest/)
[![PyPI](https://img.shields.io/pypi/v/pytest-probability)](https://pypi.org/project/pytest-probability/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

**A test that passes 7 times out of 10 is not a passing test — it's a probability.**

A pytest plugin for nondeterministic code — LLM-driven functions, model
inference, integration points, anything flaky-prone. A single-shot test lies
to you: it samples a distribution once and calls the result truth.
pytest-probability runs every case N times and reports the empirical pass
fraction per case, so `7/10 FLAKY` stops hiding inside a green checkmark.

```
$ pytest benchmarks/ --prob-runs=10
...
================================= probability ==================================
  [classify::is_question] classify           10/10  $0.0020
  [classify::identify_pii] classify           7/10  $0.0020  FLAKY
  [classify::extract_amount] classify         0/10  $0.0020  FAIL
  [triage::refund-terse] triage              8/10  $0.0010  FLAKY
  [triage::refund-chain_of_thought] triage  10/10  $0.0040

  Overall: 35/50 passed (70%)
  Cost:    $0.0110
  Tokens:  m-small  1,200 in / 80 out / 640 cached  $0.0010
           m-large  4,800 in / 900 out              $0.0040
```

## Documentation

Full documentation at
**[pytest-probability.readthedocs.io](https://pytest-probability.readthedocs.io)** —
installation, a quickstart, guides for benchmark files, parametrization,
running, cost accounting and the JSON report, a complete options
reference, and a developer guide covering the plugin's internals.

To build locally: `pip install -r docs/requirements.txt && sphinx-build -W -b html docs docs/_build/html`.

## Install

```bash
pip install pytest-probability
```

No configuration needed — the plugin registers itself via the `pytest11`
entry point. The only dependency is pytest itself. Requires Python 3.11+
and pytest 7.4+.

## Usage

Benchmark files are named `bench_*.py`, and every `bench_*` function in
them is a benchmark — the same convention pytest applies to `test_*`.
There is no plugin syntax to learn: cases are stock
`@pytest.mark.parametrize`, values arrive as function arguments, and
the function yields one result per step instead of asserting:

```python
import time
import pytest
from pytest_probability import StepResult

@pytest.mark.parametrize("text,expected", [
    pytest.param("is this a question?", "question", id="is_question"),
    pytest.param("my ssn is 078-05-1120", "pii", id="identify_pii",
                 marks=pytest.mark.fast),
])
def bench_classify(text, expected):
    t0 = time.time()
    answer = my_classifier(text)
    yield StepResult(
        label="classify",
        passed=(answer == expected),
        elapsed=time.time() - t0,
        cost=0.0002,          # optional — summed into the report
    )
```

Every parameter combination is a case with its own fraction row,
namespaced by function (`classify::identify_pii`); items collect as
`bench_x.py::bench_classify::identify_pii[run3]`. `pytest.param`
names and marks individual cases; a function with no parametrize runs
as a single case. Dataset-driven suites are a comprehension away:
`[pytest.param(row, id=row["id"]) for row in load_dataset()]`.

```bash
pytest benchmarks/ --prob-runs=10       # every case ×10
pytest benchmarks/ -k identify_pii      # one case, by id
pytest benchmarks/ -m "fast and not eu" # marks, boolean expressions
pytest benchmarks/ -n 4                 # parallel via pytest-xdist
```

Module-level `setup()` / `teardown()` run once per benchmark file.

### Comparison axes

Stacked decorators cross-product with pytest's exact id layout and
ordering — put the configurations you're comparing (models, prompts,
thresholds) on their own axes and each combination gets its own row:

```python
import pytest
from pytest_probability import StepResult, TokenUsage

@pytest.mark.parametrize("style", ["terse", "chain_of_thought"])
@pytest.mark.parametrize("text", [
    pytest.param("my card was charged twice", id="refund"),
])
def bench_triage(text, style):
    response = my_classifier(text, prompt_style=style)
    yield StepResult(
        label="triage",
        passed=response.answer == "billing",
        usage=[TokenUsage(model=response.model,
                          input_tokens=response.input_tokens,
                          output_tokens=response.output_tokens,
                          cost=response.cost)],
    )
```

```
  [triage::refund-terse] triage              8/10  $0.0010  FLAKY
  [triage::refund-chain_of_thought] triage  10/10  $0.0040
```

## Options

| Option | Where | Default | Meaning |
|---|---|---|---|
| `--prob-runs=N` | CLI | ini or 1 | run every case N times |
| `--prob-json=PATH` | CLI | — | write a JSON report to PATH |
| `--prob-delay=SECONDS` | CLI | ini or 0 | sleep between case executions |
| `--prob-transpose` | CLI | ini or off | run-major order: run 1 of everything, then run 2, … |
| `prob_delay` | ini | 0 | default for `--prob-delay` |
| `prob_transpose` | ini | false | default for `--prob-transpose` |
| `prob_runs` | ini | 1 | default for `--prob-runs` |
| `prob_pattern` | ini | `bench_*.py` | glob for benchmark files |

## JSON report

`--prob-json=report.json` writes a single machine-readable file (think
`--junitxml`, but for fractions) — aggregate rows plus the raw per-run
step records for downstream analysis:

```json
{
  "created": "2026-07-07T21:43:18",
  "runs": 10,
  "exit_status": 1,
  "totals": {"passes": 35, "fails": 15, "errors": 0, "count": 50,
             "pass_rate": 70.0, "cost": 0.011,
             "usage": {"m-small": {"input_tokens": 1200, "output_tokens": 80,
                                   "cached_input_tokens": 640, "cost": 0.001}}},
  "rows": [
    {"case": "classify::identify_pii", "label": "classify", "passes": 7,
     "fails": 3, "errors": 0, "total": 10, "pass_rate": 70.0,
     "status": "flaky", "cost": 0.002, "usage": {}}
  ],
  "records": [
    {"case": "classify::identify_pii", "run": 3, "steps": [
      {"label": "classify", "passed": false, "elapsed": 0.01, "error": null,
       "message": "got 'other'", "cost": 0.0002, "usage": []}]}
  ]
}
```

Under pytest-xdist the controller writes the file with the full result
set; workers never write partial reports.

## Semantics

- One pytest item per **(function, case, run)** —
  `bench_x.py::bench_classify::identify_pii[run3]`. `-k`, `-x`, `--lf`,
  JUnit XML, and xdist all operate per run.
- A failing step fails that run's item with a compact message (no traceback
  noise); an exception in a bench function is a normal pytest failure *and*
  counts as an `error` step in the aggregate.
- Runs are a three-class outcome — pass, fail, error — and the row status
  names the combination: `FLAKY` is reserved for genuine pass/fail
  nondeterminism, while passes mixed with errors show `7/10  3 ERRORED`
  (the model never answered wrong; the harness dropped runs). Fractions
  are always over all runs.
- Selection is pure pytest: `-k` over composed case ids, `-m` over marks
  (with and/or/not), node ids for single runs. Note pytest's `-k` grammar
  rejects `=` — select on id substrings and mark names.
- Steps are consumed by duck-typing: anything with `label`, `passed`, and
  optionally `elapsed` / `error` / `message` / `cost` / `usage` works,
  including types from other frameworks. `usage` entries (objects or plain
  dicts with `model` and token fields) aggregate per model into the
  `Tokens:` block and the JSON report.
- The fraction summary is computed from `report.user_properties`, so it
  stays correct under pytest-xdist (workers serialize results back to the
  controller, which renders the table).
- Exit code follows pytest: any failed or errored run fails the session,
  so a flaky case exits nonzero. For softer CI gates (e.g. alert only
  below 80%), policy-check the JSON report in a follow-up step.
- `--prob-delay` throttles between executions and never sleeps before the
  first; under pytest-xdist each worker throttles its own stream.
- Default order is case-major (`alpha[run1]`, `alpha[run2]`, `beta[run1]`,
  …); `--prob-transpose` makes it run-major (`alpha[run1]`, `beta[run1]`,
  `alpha[run2]`, …), which spreads each case's runs over time — useful
  when back-to-back runs would hit the same transient state. Ordering is
  advisory under xdist, which schedules items across workers itself.
