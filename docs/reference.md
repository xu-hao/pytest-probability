# Reference

## Command-line options

All options live in the `probability` group of `pytest --help`.

`--prob-runs=N`
: Run every case N times. Values below 1 are clamped to 1.
  **Default:** the `prob_runs` ini value, else `1`.

`--prob-json=PATH`
: Write the {doc}`JSON report <json-report>` to PATH at session end.
  Parent directories are created. Under pytest-xdist only the
  controller writes.
  **Default:** no report.

`--prob-delay=SECONDS`
: Sleep between case executions; never before the first. Applies per
  process (per worker under xdist) and only to benchmark items.
  **Default:** the `prob_delay` ini value, else `0`.

`--prob-transpose`
: Collect in run-major order — run 1 of every case, then run 2, … —
  instead of the case-major default. Ordering is advisory under xdist.
  **Default:** the `prob_transpose` ini value, else off.

`--prob-method={exact,wilson,bayes}`
: How the per-row interval on the pass probability is computed:
  `exact` (Clopper-Pearson; never under-covers), `wilson` (Wilson
  score; narrower, close to nominal on average), or `bayes` (an
  equal-tailed credible interval under the Beta prior `prob_prior`).
  Any other value is a usage error.
  **Default:** the `prob_method` ini value, else `exact`.

`--prob-confidence=LEVEL`
: Two-sided confidence (or credible) level for intervals, strictly
  between 0 and 1 — `0.95`, not `95`. One level drives every interval
  the plugin prints.
  **Default:** the `prob_confidence` ini value, else `0.95`.

`--prob-no-intervals`
: Hide the interval column in the terminal summary. The JSON report
  still carries `rows[].ci`. The gates block always shows its
  intervals.
  **Default:** the `prob_intervals` ini value, else shown.

`--prob-min-rate=RATE`
: Gate every benchmark case on its pass rate: see [Gates](#gates).
  Strictly between 0 and 1. A `probability` mark that sets `min_rate`
  or `min_passes` takes precedence for its cases.
  **Default:** the `prob_min_rate` ini value, else no global gate.

`--prob-undecided={fail,pass}`
: Whether an UNDECIDED gate verdict fails the session. `pass` still
  lists the case in the gates block.
  **Default:** the `prob_undecided` ini value, else `fail`.

`--prob-explain`
: Add a [plain-language reading](#the-explain-section) of the results
  after the summary, and an `explanation` string to the JSON report's
  rows and gates.
  **Default:** the `prob_explain` ini value, else off.

Case selection has no plugin-specific options: use pytest's `-k`
(ids), `-m` (marks), and node ids.

## Ini options

Set these in `pytest.ini`, `pyproject.toml` (`[tool.pytest.ini_options]`),
`setup.cfg`, or `tox.ini`, exactly like any pytest ini option.

`prob_runs` *(string, default `"1"`)*
: Default repeat count; overridden by `--prob-runs`.

`prob_pattern` *(string, default `"bench_*.py"`)*
: `fnmatch` glob deciding which files the plugin collects. Matched
  against the **file name only**, not the path. Files matching
  `python_files` (`test_*.py`) stay with pytest's own collector, so
  keep the two patterns disjoint.

`prob_delay` *(string, default `"0"`)*
: Default seconds between executions; overridden by `--prob-delay`.

`prob_transpose` *(bool, default `false`)*
: Default execution order; overridden by `--prob-transpose`.

`prob_method` *(string, default `"exact"`)*
: Default interval method; overridden by `--prob-method`.

`prob_confidence` *(string, default `"0.95"`)*
: Default confidence level; overridden by `--prob-confidence`.

`prob_prior` *(string, default `"1,1"`)*
: The Beta(a, b) prior for `bayes`, as `a,b` with both positive:
  `1,1` is uniform, `0.5,0.5` is Jeffreys. Ignored by the other
  methods. Ini only.

`prob_intervals` *(bool, default `true`)*
: Show the interval column; `--prob-no-intervals` turns it off.

`prob_min_rate` *(string, default empty)*
: A global gate for every case; overridden by `--prob-min-rate`.
  Empty means no global gate.

`prob_undecided` *(string, default `"fail"`)*
: `fail` or `pass`; overridden by `--prob-undecided`.

`prob_errors` *(string, default `"count"`)*
: How errored runs of a gated case are treated: `count` or `exclude`.
  See [Errors in gated cases](#errors-in-gated-cases). Ini only; use
  `-o prob_errors=exclude` for a one-off.

`prob_explain` *(bool, default `false`)*
: Default for `--prob-explain`.

Invalid values for the statistical and gate options are reported as
pytest usage errors before anything runs.

## The `probability` marker

```python
@pytest.mark.probability(min_rate=0.9)                               # exact, at prob_confidence
@pytest.mark.probability(min_rate=0.9, method="bayes", prior=(1, 1))
@pytest.mark.probability(min_passes=19, runs=20)                     # count rule
@pytest.mark.probability(runs=40)                                    # run count only, no gate
```

| Argument | Meaning |
|---|---|
| `min_rate` | Gate on the pass rate: the case's interval must lie above it. Strictly between 0 and 1. |
| `min_passes` | Gate on a count instead: the case passes when passes ≥ `min_passes`. A positive integer. |
| `runs` | This function's (or case's) run count. A positive integer. |
| `confidence` | The level of this gate's interval, instead of `prob_confidence`. |
| `method` | `exact`, `wilson` or `bayes`, instead of `prob_method`. |
| `prior` | The Beta prior for `bayes`, as `(a, b)`, instead of `prob_prior`. |

- **Where it applies:** on a `bench_*` function, every case of it; on
  one case with `pytest.param(..., marks=pytest.mark.probability(...))`.
  A case's mark overrides the function's argument by argument, except
  that `min_rate` and `min_passes` are one setting: a case mark that
  names either replaces the function's rule.
- **One rule per mark:** `min_rate` and `min_passes` can't be used
  together.
- **Run count precedence:** `--prob-runs` beats `runs=`, which beats
  the `prob_runs` ini value.
- **Gated or not:** a case is gated when its mark sets `min_rate` or
  `min_passes`, or a global min rate is set. `runs=`, `confidence=`,
  `method=` and `prior=` alone don't create a gate.
- **Validation:** the marker is registered, so `--strict-markers`
  accepts it. A bad argument is a collection error that names the
  case, e.g. `classify::identify_pii: invalid probability mark: min_rate
  must be strictly between 0 and 1, got 90 (did you mean 0.9?)`.

## Gates

A gate decides whether a case passes from its estimated pass
probability, instead of requiring every run to pass.

**The verdict rule** is the same for every method. The interval is the
gate's (its `method`, `confidence` and `prior`) over the gate's sample,
compared with unrounded bounds:

| Interval vs `min_rate` | Verdict |
|---|---|
| Entirely above | PASS |
| Entirely below | FAIL |
| Straddles it | UNDECIDED |

UNDECIDED means there isn't enough data to tell yet: run more. It
fails the session by default; `--prob-undecided=pass` /
`prob_undecided = pass` lets it through. Under the count rule
(`min_passes`) there is no UNDECIDED: the case passes when passes ≥
`min_passes`, and fails otherwise.

**How runs are reported.** In a gated case, a run that fails an
`assert` is a sample, not a verdict, so it is reported as *xfailed*:

- `-rx` lists each one with its assert message as the reason
  (`XFAIL …::identify_pii[run3] - probability gate: assert 'other' ==
  'pii'`), and pytest 8's `--xfail-tb` prints the kept traceback.
- `-x` / `--maxfail` don't stop on it.
- `--runxfail` turns this off: gated failing runs are reported as
  ordinary failures again. The gate is still judged.

The verdicts are decided after every run is in, from the aggregated
counts — in-process, or on the pytest-xdist controller, so they are the
same with and without `-n`.

### Errors in gated cases

`prob_errors` decides what an errored run (any exception other than
`AssertionError`) means for a gate:

- `count` (default): errors fail the session as they always do, and
  count as non-passes in the gate's sample.
- `exclude`: errors leave the gate's sample (`7/10` with 3 errors is
  judged as `7/7`) and are reported as xfailed, so they don't fail the
  session. The summary row shows `(3 errored, excluded)`. A case whose
  every run errored has no sample left and is UNDECIDED.

Ungated cases are never affected.

### Feasibility warning

At collection, a gate that couldn't pass even if every run passed
raises an `InfeasibleGateWarning`:

```text
InfeasibleGateWarning: classify::identify_pii: min_rate=0.9 at 95% needs ≥36 runs; this case has 10
```

Cases of one function that share a gate get one warning
(`classify (3 cases): … each has 10`). With the exact method at 95%,
`min_rate` needs at least:

| `min_rate` | 0.8 | 0.9 | 0.95 | 0.99 |
|---|---|---|---|---|
| Runs | 17 | 36 | 72 | 368 |

A count gate needs `min_passes` runs. The warning is an ordinary pytest
warning: filter it with
`-W ignore::pytest_probability.InfeasibleGateWarning`, or make it an
error with `-W error::…`.

### Known limitation

A failed gate has no failing item: the runs passed or were xfailed. So
JUnit XML (`--junitxml`) records no failure for it, and `--lf` has no
failed items to rerun. The exit status and the JSON report do carry
the verdict. Gate items for JUnit XML and `--lf` are planned.

## Python API

Everything importable lives in the top-level package:

```python
from pytest_probability import (
    InfeasibleGateWarning, TokenUsage, record_cost, record_usage,
)
```

### `record_usage(usage=None, /, **fields)`

Attribute per-model token usage to the current bench run. Accepts a
`TokenUsage`, any object or dict with the same field names, or the
fields directly as keywords. Call once per model call; entries
aggregate per model. Usage recorded before a failing `assert` is kept
— spend is never lost to a wrong answer. Raises `RuntimeError` outside
a bench run.

### `record_cost(amount)`

Add a non-token cost to the current bench run. A run's total cost is
its `record_cost` amounts plus the `cost` of every usage entry. Raises
`RuntimeError` outside a bench run.

### `TokenUsage`

```python
@dataclass
class TokenUsage:
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    cost: float = 0.0
```

Token accounting for one model's calls within a run. See {doc}`cost`
for aggregation rules.

### `InfeasibleGateWarning`

A `pytest.PytestWarning` subclass, raised at collection for a gate that
cannot pass even if every run passes. See
[Feasibility warning](#feasibility-warning).

## Benchmark module contract

| Name | Required | Contract |
|---|---|---|
| `bench_*(**params)` | at least one | a benchmark per function; an ordinary test body that `assert`s; cases come from its `parametrize` marks (none → a single case named after the function) |
| `setup()` | no | called once per file, before its first item |
| `teardown()` | no | called once per file, after its last item |

## Item ids

```text
<file>::<bench-function>::<case-id>[run<N>]
```

- `<bench-function>` — the full function name (`bench_classify`).
- `<case-id>` — pytest's composed parametrize id: value text for
  scalars, `pytest.param(id=...)`/`ids=` overrides, stacked decorators
  joined with `-` (`refund-terse`). For an unparametrized function the
  item is named `run<N>` directly.
- `[run<N>]` — appended when `--prob-runs` > 1; 1-based.

Summary rows and JSON use the function-qualified case id:
`<function-short>::<case-id>`, where the short name strips the
`bench_` prefix (`classify::identify_pii`); an unparametrized
function's row is just the short name.

## Row statuses

Every run lands in one of **three classes**, decided by how the
function body ends — a clean return **passes**, an `AssertionError`
**fails**, any other exception **errors** — and the row status names
the combination, so model nondeterminism and infrastructure trouble
never blur into one word. Fractions render as `passes/total`: every
run counts.

| Status | Definition | Color |
|---|---|---|
| `pass` | every run passed | green |
| `flaky` | passes and fails mixed (real nondeterminism) | yellow |
| `errored` | passes and errors only — the model never answered wrong, the harness broke | yellow |
| `fail` | no run passed, at least one real failure | red |
| `error` | every run errored | red |

A run counts as *errored* when the bench function raised anything
other than `AssertionError`.

The status word is the verdict on your code, derived from the pass/fail
evidence; errors are an orthogonal fact about the harness and ride as
an annotation when they appear alongside real outcomes. Three classes
give seven possible combinations, and each renders distinctly:

| Classes present | Rendered | Example |
|---|---|---|
| pass | *(green, no word)* | `10/10` |
| fail | `FAIL` | `0/10  FAIL` |
| error | `ERROR` | `0/10  ERROR` |
| pass + fail | `FLAKY` | `5/10  FLAKY` |
| pass + error | `N ERRORED` | `7/10  3 ERRORED` |
| fail + error | `FAIL (N errored)` | `0/10  FAIL (2 errored)` |
| pass + fail + error | `FLAKY (N errored)` | `5/10  FLAKY (1 errored)` |

In the JSON report the question never arises: rows carry the three raw
class counts, so every combination is exactly recoverable regardless of
the status word.

In a gated case under `prob_errors = exclude`, the error annotation
reads `(N errored, excluded)` instead: `5/10  FLAKY (1 errored,
excluded)`, `7/10  (3 errored, excluded)` for passes and errors only,
`0/10  ERROR (10 errored, excluded)`. The fraction and interval on the
row still count every run; the gates block shows the gate's own
sample.

## Terminal summary anatomy

```text
================================= probability ==================================
  classify::identify_pii   7/10  [35%,  93%]  $0.0020  FLAKY
   \_ function::case-id  \_ fraction  \_ interval  \_ cost  \_ status (omitted when pass)

  Overall: 35/50 passed (70%), 3 errored   # errored count only when present
  Gates:   3 passed, 1 failed, 1 undecided     # only when a case is gated
  Cost:    $0.0110                                 # only when cost was recorded
  Tokens:  m-small  1,200 in / 80 out / 640 cached  $0.0010   # per model,
           m-large  4,800 in / 900 out              $0.0040   # only with usage
  Report:  report.json                             # only with --prob-json
```

The section renders only when at least one benchmark item ran.

### The gates block

When any gate is FAIL or UNDECIDED, a second section lists those cases
with the interval and bar each verdict came from:

```text
============================== probability: gates ==============================
  classify::identify_pii  37/40  [80%, 98%]  ≥90%        UNDECIDED
  classify::never          3/40  [ 2%, 20%]  ≥90%        FAIL
  triage::refund          17/20  [62%, 97%]  ≥19 passes  FAIL
```

- The fraction and interval are the gate's own: its `method`,
  `confidence` and `prior`, over its sample (errored runs left out
  under `prob_errors = exclude`, with a trailing `(N errored,
  excluded)`). So a printed verdict always sits next to the interval it
  was read from, even when the gate overrides the session settings.
- Intervals are shown even with `--prob-no-intervals` or a single run:
  the verdict depends on them.
- PASS cases are only counted, on the `Gates:` line. `(allowed)` marks
  UNDECIDED cases that `--prob-undecided=pass` lets through.

### The explain section

When any gate is FAIL or UNDECIDED, the summary ends with a hint:

```text
  Run with --prob-explain for a plain-language reading.
```

With `--prob-explain` (or `prob_explain = true`) a third section
replaces the hint. It reads every gated case, and every ungated row
that did not pass all its runs, in plain words: what was measured,
what the numbers mean, and a next step where there is one. Then a
short glossary explains each term those lines used, naming the
interval method and level actually in effect:

```text
============================ probability: explained ============================
  classify::close  37/40  [80%, 98%]  ≥90%  UNDECIDED
    37 of 40 runs passed. The true pass rate is probably between 80% and 98%
    (95% confidence). Your bar is 90%, and that range has values both above and
    below it, so there isn't enough data yet to tell whether this case meets the
    bar. Until that's settled, the case fails the test session
    (--prob-undecided=pass would let it through).
    Next: run more. If it keeps passing at today's rate (92.5%), about 570 runs
    in total would settle it.

  classify::weak  3/40  [2%, 20%]  ≥90%  FAIL
    3 of 40 runs passed. The true pass rate is probably between 2% and 20% (95%
    confidence). Your bar is 90%, and that whole range is below it, so this case
    falls short of the bar.
    Next: look at the failing runs: -rx lists them with their assert messages,
    and --xfail-tb shows their tracebacks. If a lower pass rate is acceptable
    for this case, lower the bar.

  Methods used
    [low, high]  The range the true pass rate probably falls in, given the runs
                 so far. More runs make it narrower. (Clopper-Pearson, 95%
                 confidence: errs on the side of a wider range.)
    Verdict      PASS: the whole range is above the bar. FAIL: the whole range
                 is below it. UNDECIDED: the range crosses the bar, so more runs
                 are needed.
  Full guide: https://pytest-probability.readthedocs.io/en/latest/reference.html#gates
```

- Text wraps to the terminal width; headings and the link don't.
- "About N runs" is the smallest total that would settle the verdict
  if the observed pass rate held, computed with the gate's own
  interval and rounded up to two significant figures. When the rate is
  too close to the bar to settle within 10,000 runs, the reading says
  so instead.
- Count gates (`min_passes`) are explained in counts; errored runs
  left out under `prob_errors = exclude` are called out.
- Under pytest-xdist the controller writes the section from the
  aggregated counts, so it reads the same as a serial run.

### The interval column

`[low, high]` is a two-sided interval for the case's true pass
probability, at `prob_confidence` (95% by default) by `prob_method`
(Clopper-Pearson by default). Every run counts toward it, exactly as
in the fraction, so errors count as non-passes.

- Bounds are whole percents, rounded half up; the JSON report keeps
  the unrounded values. `0%` and `100%` appear only for bounds that
  are exactly 0 or 1, so `9/10` reads `[55%,  99%]`, not
  `[55%, 100%]`.
- Low and high are right-aligned separately, so brackets, commas and
  the columns after them line up when widths vary.
- A row with a single run gets a blank cell; when no row has more than
  one run the column is omitted, so `--prob-runs=1` output looks as it
  did before intervals existed.
- `--prob-no-intervals` / `prob_intervals = false` hide the column.

## Exit status

Without gates, standard pytest semantics: any failed or errored run
makes the session exit nonzero.

With gates, a gated case's failing runs are xfailed, so they never fail
the session themselves; the gate's verdict does. After every run,
`pytest_sessionfinish` sets exit code 1 (`TESTS_FAILED`) when any gate
is FAIL, or UNDECIDED under `prob_undecided = fail`, and pytest would
otherwise have exited 0.

| Session | Exit code |
|---|---|
| Every gate PASS, nothing else failed | 0 |
| UNDECIDED gates only, `--prob-undecided=pass` | 0 |
| A gate FAIL, or UNDECIDED under the default `fail` | 1 |
| An ungated run failed or errored (whatever the gates) | 1 |
| An errored run in a gated case, `prob_errors = count` | 1 |
| Interrupted, usage error, no tests… | pytest's own code, unchanged |

pytest's last line counts runs, not gates, so a session can end
`137 passed, 43 xfailed` and still exit 1: the `Gates:` line and the
gates block say why. The JSON report's `exit_status` is the final code,
gates included.
