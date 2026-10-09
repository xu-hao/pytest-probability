# pytest-probability

[![Documentation](https://readthedocs.org/projects/pytest-probability/badge/?version=latest)](https://pytest-probability.readthedocs.io/en/latest/)
[![PyPI](https://img.shields.io/pypi/v/pytest-probability)](https://pypi.org/project/pytest-probability/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

**A semantic bug won't reproduce in one run — and can't hide from ten.**

A pytest plugin for nondeterministic code — LLM-driven functions, model
inference, integration points, anything flaky-prone. A single-shot test lies
to you: it samples a distribution once and calls the result truth.
pytest-probability runs every case N times and reports the empirical pass
fraction per case, so `7/10 FLAKY` stops hiding inside a green checkmark.

This enables **benchmark-driven development** for semantic functions —
TDD with the instrument swapped: red is a low fraction, green is a
fraction meeting your bar, refactor means beating the incumbent prompt
on a comparison axis. When a prompt fails in production, turn the
verbatim input into a case and sample it: the `6/10 FLAKY` row *is* the
reproduction that a single playground retry would have closed as
"cannot reproduce" — and the case stays in CI forever as a regression
sentinel. The worked loop:
[Benchmark-driven development](https://pytest-probability.readthedocs.io/en/latest/bdd.html).

```
$ pytest benchmarks/ --prob-runs=10
...
================================= probability ==================================
  classify::is_question            10/10  [69%, 100%]  $0.0020
  classify::identify_pii            7/10  [35%,  93%]  $0.0020  FLAKY
  classify::extract_amount          0/10  [ 0%,  31%]  $0.0020  FAIL
  triage::refund-terse              8/10  [44%,  97%]  $0.0010  FLAKY
  triage::refund-chain_of_thought  10/10  [69%, 100%]  $0.0040

  Overall: 35/50 passed (70%)
  Cost:    $0.0110
  Tokens:  m-small  1,200 in / 80 out / 640 cached  $0.0010
           m-large  4,800 in / 900 out              $0.0040
```

## Documentation

Full documentation at
**[pytest-probability.readthedocs.io](https://pytest-probability.readthedocs.io)** —
installation, a quickstart, guides for benchmark files, parametrization,
running, a statistics guide (what the intervals, gates and verdicts
mean and assume), cost accounting and the JSON report, a complete options
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
**A bench function is a pytest test body**: cases are stock
`@pytest.mark.parametrize`, values arrive as function arguments, and
the body asserts:

```python
import pytest
from pytest_probability import record_cost

@pytest.mark.parametrize("text,expected", [
    pytest.param("is this a question?", "question", id="is_question"),
    pytest.param("my ssn is 078-05-1120", "pii", id="identify_pii",
                 marks=pytest.mark.fast),
])
def bench_classify(text, expected):
    answer = my_classifier(text)
    record_cost(0.0002)                   # optional — summed into the report
    assert answer == expected
```

A run's class falls straight out of Python: a clean return **passes**,
an `AssertionError` **fails** (wrong answer — with pytest's full
assertion introspection, `assert 'other' == 'pii'`), any other
exception **errors** (broken harness). The plugin times each run
itself.

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

### A/B-testing prompts (comparison axes)

Stacked decorators cross-product with pytest's exact id layout and
ordering — put the prompts (or models, or thresholds) you're comparing
on their own axis and each combination gets its own fraction row, same
inputs, same sample size, cost attached:

```python
import pytest
from pytest_probability import record_usage

@pytest.mark.parametrize("style", ["terse", "chain_of_thought"])
@pytest.mark.parametrize("text", [
    pytest.param("my card was charged twice", id="refund"),
])
def bench_triage(text, style):
    response = my_classifier(text, prompt_style=style)
    record_usage(model=response.model,
                 input_tokens=response.input_tokens,
                 output_tokens=response.output_tokens,
                 cost=response.cost)      # survives a failing assert
    assert response.answer == "billing"
```

Name the axis and each arm is compared with the baseline (the first
value; `baseline=` picks another) on the inputs both ran:

```
$ pytest benchmarks/ --prob-runs=10 --prob-compare=style
...
  triage::refund-terse              8/10  [44%,  97%]  $0.0010  FLAKY
  triage::refund-chain_of_thought  10/10  [69%, 100%]  $0.0040
...
=========================== probability: comparisons ===========================
  triage[style]  chain_of_thought − terse  +20 pp [−11, +51]  p=0.47  1 paired  cost ×4.0
```

10/10 against 8/10 looks like a win, but it is one input: the
difference is +20 points with a 95% interval from −11 to +51, and
p = 0.47 — if the prompts were equally good, a gap this big would turn
up by chance about half the time. The data can't tell yet which prompt
is better; what it does show is that chain_of_thought costs four times
as much per run. Add inputs: with 10 or more pairs the interval comes
from a paired bootstrap over inputs and p from a sign-flip test on the
per-input differences. `@pytest.mark.probability(compare="style",
margin=0.02)` makes it a gate — the arm may be at most 2 points worse
(`equivalence=True`: within ±2 points) — and `--prob-adjust=holm`
corrects p for several arms.

The same comparison guards against regressions between runs:
`--prob-baseline=main.json --prob-margin=0.05` pairs every case with
its row in an earlier `--prob-json` report and fails a function whose
pass rate may have dropped by more than 5 points; new and removed
cases are listed, not compared.

### Gates: pass on a rate, not on every run

By default any failing run fails the session. To hold a case to a pass
rate instead, gate it:

```python
@pytest.mark.probability(min_rate=0.9)               # or --prob-min-rate=0.9 for every case
@pytest.mark.parametrize("text,expected", CASES)
def bench_classify(text, expected):
    assert my_classifier(text) == expected
```

A gated case **passes** when its whole interval lies above the bar,
**fails** when it lies entirely below, and is **undecided** (fails by
default; `--prob-undecided=pass` relaxes it) in between. Its failing
runs are reported as xfailed, so only the verdicts decide the exit
status:

```
$ pytest benchmarks/ --prob-runs=40
...
  Overall: 80/120 passed (67%)
  Gates:   1 passed, 1 failed, 1 undecided
============================== probability: gates ==============================
  classify::identify_pii  37/40  [80%, 98%]  ≥90%  UNDECIDED
  classify::never          3/40  [ 2%, 20%]  ≥90%  FAIL
```

Each gated case also gets a gate item after its last run,
`bench_classify.py::bench_classify::never[gate]`, which passes or fails
with the verdict: a failed gate shows in `-v`, is a failed testcase in
JUnit XML, and `--lf` reruns it with its runs. (Under pytest-xdist it
needs `--dist loadgroup`; `--prob-no-gate-items` leaves them out.)

`min_passes=19, runs=20` gates on a count instead; `confidence=`,
`method=` and `prior=` override the session's settings for one gate;
`pytest.param(..., marks=pytest.mark.probability(...))` gates one case.
A collection-time warning flags gates that can't pass with the runs
they have (`min_rate=0.9` needs at least 36 at 95%).

### Function-level intervals

A function with at least 10 cases also gets a line for the function as
a whole — the mean of its per-case pass fractions, with an interval
from a seeded bootstrap that re-draws whole cases, since runs of one
input are correlated — and the session gets an `Overall` line:

```
  classify  N=40 inputs × k=10    81.0%  [69.5%, 90.8%]  ρ=0.76
            runs ×2 → interval −1%  ·  inputs ×2 → −29%  ·  each +$0.0400
  triage    N=15 inputs × k=5–10  59.3%  [47.3%, 70.7%]  ρ=0.09
            runs ×2 → interval −15%  ·  inputs ×2 → −29%  ·  each +$0.0125
  Overall   N=55 inputs × k=5–10  75.1%  [66.0%, 83.8%]  ρ=0.56
            runs ×2 → interval −2%  ·  inputs ×2 → −29%  ·  each +$0.0525
```

With more than one run per case, ρ says how alike an input's runs are,
and the second line how much doubling the runs or the inputs would
narrow the interval, at the recorded cost. Low ρ (triage): outputs vary
from run to run, so more runs help. High ρ (classify): each input is
consistently right or wrong, so add inputs instead.

### pass^k and pass@k

How often does an input pass three runs in a row, or at least one of
five? `--prob-metric` reports either, per case (in the JSON report),
per function and overall, with the same bootstrap interval:

```
$ pytest benchmarks/ --prob-runs=10 --prob-metric='pass^3,pass@5'
...
============================= probability: metrics =============================
  classify  pass^3  N=12 inputs  34.3%  [17.2%,  53.8%]
            pass@5  N=12 inputs  99.4%  [98.8%, 100.0%]
  triage    pass^3  N=3 inputs   69.4%
            pass@5  N=2 inputs   99.8%                   1 left out (fewer than 5 runs)
  Overall   pass^3  N=15 inputs  41.3%  [22.9%,  61.2%]
            pass@5  N=14 inputs  99.5%  [98.9%,  99.9%]  1 left out (fewer than 5 runs)
```

pass^k is reliability — all k runs of the same input pass — and pass@k
is best-of-k — at least one does. Each case's value is the unbiased
estimate from its runs, and cases with fewer than k runs are left out.
Quote `^` in shells that treat it specially (zsh with `extendedglob`,
Windows `cmd`).

### Latency: a quantile with an interval, and a gate on it

Every run is timed. `max_latency=` gates a case on a quantile of its
run times (the 95th percentile unless `latency_quantile=` says
otherwise), and `--prob-latency` shows each case's quantile with a
distribution-free interval:

```python
@pytest.mark.probability(max_latency=2.0)            # p95 must be under 2s
@pytest.mark.parametrize("case", ["search", "summarize", "translate"])
def bench_api(case):
    call_service(case)
```

```
$ pytest benchmarks/ --prob-runs=100 --prob-latency
...
============================= probability: latency =============================
  api::search     100 runs  p95  769ms  [600ms, 933ms]  ≤2s  PASS
  api::summarize  100 runs  p95  2.00s  [1.71s, 4.84s]  ≤2s  UNDECIDED
  api::translate  100 runs  p95  3.00s  [2.60s, 3.25s]  ≤2s  FAIL
```

The verdict follows the same rule as a rate gate, with lower being
better: PASS when the whole interval is below the limit, FAIL when it
is entirely above. The interval needs enough runs to have an upper end
(72 for p95 at 95%; fewer leave it open, `[600ms, —]`, and the gate
can't pass yet). A latency gate judges speed only: failing asserts
still fail the session unless the case also has a rate gate.

### Stopping early: skip runs that can't change the verdict

Once `never` has failed 9 of its 40 runs, no remaining run can lift it
over a 90% bar. `--prob-stop=curtail` skips a gated case's remaining
runs as soon as its verdict can no longer change — so the verdicts,
and the exit status, are exactly those of running every run:

```
$ pytest benchmarks/ --prob-runs=40 --prob-stop=curtail
...
  classify::is_question   40/40  [91%, 100%]
  classify::identify_pii  37/38  [86%,  99%]  FLAKY  decided after 38/40
  classify::never          3/12  [ 5%,  57%]  FLAKY  decided after 12/40

  Overall: 80/90 passed (89%)
  Gates:   1 passed, 1 failed, 1 undecided
  Stopped: 2 cases early, saving 30 of 120 runs (25%)
```

The skipped runs are pytest skips, not samples. A stopped case's
fraction leans toward its verdict, so function-level, metric and
comparison intervals over it are hidden, with a note. Under xdist it
needs `--dist loadgroup`, so that one worker runs all of a case's runs.

`--prob-stop=sequential` goes further: it stops a pass-rate gate as
soon as the case is clearly above or below its bar, judged on an
interval that stays valid however often it is checked (marked `seq`),
and the run count becomes the most a case may use:

```
$ pytest benchmarks/ --prob-stop=sequential     # min_rate=0.9, runs=100
...
  classify::solid  53/53  [90%, 100%] seq  decided after 53/100
  classify::weak   45/60  [55%,  90%] seq  FLAKY  decided after 60/100
```

That interval is wider than a fixed-run one, so clear cases stop much
sooner (53 runs here, 96 under curtailment) while borderline ones take
longer; `--prob-plan` shows the trade-off before you spend anything.

## Options

| Option | Where | Default | Meaning |
|---|---|---|---|
| `--prob-runs=N` | CLI | ini or 1 | run every case N times |
| `--prob-json=PATH` | CLI | — | write a JSON report to PATH |
| `--prob-delay=SECONDS` | CLI | ini or 0 | sleep between case executions |
| `--prob-transpose` | CLI | ini or off | run-major order: run 1 of everything, then run 2, … |
| `--prob-method=M` | CLI | ini or `exact` | row interval method: `exact` (Clopper-Pearson), `wilson`, or `bayes` |
| `--prob-confidence=LEVEL` | CLI | ini or 0.95 | two-sided level for intervals |
| `--prob-no-intervals` | CLI | ini or shown | hide the interval column |
| `--prob-min-rate=RATE` | CLI | ini or none | gate every case on its pass rate |
| `--prob-undecided={fail,pass}` | CLI | ini or `fail` | whether an UNDECIDED gate fails the session |
| `--prob-explain` | CLI | ini or off | add a plain-language reading of the results (terminal and JSON) |
| `--prob-bootstrap=N` | CLI | ini or 5000 | bootstrap resamples for function-level and overall intervals |
| `--prob-seed=SEED` | CLI | ini or 0 | seed for every resampling procedure |
| `--prob-compare=AXIS` | CLI | ini or none | compare the values of parametrize argument AXIS, each against the first |
| `--prob-adjust=M` | CLI | ini or `none` | adjust comparison p-values: `none` (exploratory), `holm`, `bonferroni`, `bh` |
| `--prob-metric=M` | CLI | ini or none | also report `pass^K` (all K runs of an input pass: reliability) and/or `pass@K` (at least one of K passes: best-of-K); comma-separated, repeatable |
| `--prob-plan` | CLI | off | print a run budget per case (runs to pass a gate, to catch a flake, projected cost) and exit without running |
| `--prob-plan-assume=RATE` | CLI | 0.97 | true pass rate `--prob-plan` plans for |
| `--prob-plan-flake=RATES` | CLI | `0.1,0.01` | failure rates `--prob-plan` plans to catch |
| `--prob-plan-report=PATH` | CLI | — | previous JSON report to read cost per run from, for `--prob-plan` |
| `--prob-baseline=PATH` | CLI | none | compare every function with an earlier `--prob-json` report, pairing cases by id |
| `--prob-margin=MARGIN` | CLI | none (report only) | with `--prob-baseline`: fail a function whose pass rate may have dropped by more than MARGIN |
| `--prob-latency` | CLI | ini or off | show each case's latency quantile and its interval |
| `--prob-stop={off,curtail,sequential}` | CLI | ini or `off` | `curtail`: skip a gated case's remaining runs once its verdict can no longer change (same verdicts); `sequential`: judge rate gates on an anytime-valid interval and stop as soon as it clears or falls below the bar (`runs` becomes a maximum). Under xdist both need `--dist loadgroup` |
| `--prob-no-gate-items` | CLI | ini or added | don't add a `[gate]` item per gated case (and `[compare:ARM]`/`[baseline]` per margin) carrying its verdict into `-v`, JUnit XML and `--lf` |
| `prob_delay` | ini | 0 | default for `--prob-delay` |
| `prob_transpose` | ini | false | default for `--prob-transpose` |
| `prob_runs` | ini | 1 | default for `--prob-runs` |
| `prob_pattern` | ini | `bench_*.py` | glob for benchmark files |
| `prob_method` | ini | `exact` | default for `--prob-method` |
| `prob_confidence` | ini | 0.95 | default for `--prob-confidence` |
| `prob_prior` | ini | `1,1` | Beta prior `a,b` for `bayes` |
| `prob_intervals` | ini | true | show the interval column |
| `prob_min_rate` | ini | — | default for `--prob-min-rate` |
| `prob_undecided` | ini | `fail` | default for `--prob-undecided` |
| `prob_errors` | ini | `count` | errored runs in gated cases: `count` as non-passes, or `exclude` |
| `prob_explain` | ini | false | default for `--prob-explain` |
| `prob_bootstrap` | ini | 5000 | default for `--prob-bootstrap` |
| `prob_seed` | ini | 0 | default for `--prob-seed` |
| `prob_min_inputs` | ini | 10 | fewest cases a function needs for its function-level interval (and fewest paired inputs for a comparison's) |
| `prob_compare` | ini | — | default for `--prob-compare` |
| `prob_adjust` | ini | `none` | default for `--prob-adjust` |
| `prob_metric` | ini | — | default for `--prob-metric` (comma- or space-separated) |
| `prob_latency` | ini | false | default for `--prob-latency` |
| `prob_latency_quantile` | ini | 0.95 | latency quantile for cases whose mark sets none |
| `prob_stop` | ini | `off` | default for `--prob-stop` |
| `prob_gate_items` | ini | true | add gate items; `false` is `--prob-no-gate-items` |
| `@pytest.mark.probability(...)` | marker | — | per-function/case gate (`min_rate` or `min_passes`), `runs`, `confidence`, `method`, `prior`; per-function comparison (`compare`, `baseline`, `margin`, `equivalence`); latency (`latency_quantile`, `max_latency`) |

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
  JUnit XML, and xdist all operate per run. A gated case adds one
  `…::identify_pii[gate]` item carrying its verdict.
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
  below 80%), gate the case: `@pytest.mark.probability(min_rate=0.8)` or
  `--prob-min-rate=0.8`. Then the verdict, not each run, decides.
- `--prob-delay` throttles between executions and never sleeps before the
  first; under pytest-xdist each worker throttles its own stream.
- Default order is case-major (`alpha[run1]`, `alpha[run2]`, `beta[run1]`,
  …); `--prob-transpose` makes it run-major (`alpha[run1]`, `beta[run1]`,
  `alpha[run2]`, …), which spreads each case's runs over time — useful
  when back-to-back runs would hit the same transient state. Ordering is
  advisory under xdist, which schedules items across workers itself.
