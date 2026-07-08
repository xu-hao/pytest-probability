# pytest-probability

**A test that passes 7 times out of 10 is not a passing test — it's a
probability.**

pytest-probability is a pytest plugin for testing nondeterministic code:
LLM-driven functions, model inference, integration points, anything
flaky-prone. A single-shot test samples a distribution once and calls the
result truth. This plugin runs every case N times and reports the
**empirical pass fraction** per case, so `7/10 FLAKY` stops hiding
inside a green checkmark.

```text
$ pytest benchmarks/ --prob-runs=10
...
================================= probability ==================================
  [classify::is_question] classify          10/10  $0.0020
  [classify::identify_pii] classify          7/10  $0.0020  FLAKY
  [classify::extract_amount] classify        0/10  $0.0020  FAIL
  [triage::refund-terse] triage              8/10  $0.0010  FLAKY
  [triage::refund-chain_of_thought] triage  10/10  $0.0040

  Overall: 35/50 passed (70%)
  Cost:    $0.0110
  Tokens:  m-small  1,200 in / 80 out / 640 cached  $0.0010
           m-large  4,800 in / 900 out              $0.0040
```

## Vocabulary

Six words, used consistently across these docs:

benchmark file
: A `bench_*.py` module, collected by the plugin.

benchmark
: One `bench_*` function inside it — a parametrized generator.

case
: One parameter combination of a benchmark, named by its composed
  pytest id (`identify_pii`, `refund-terse`). An unparametrized
  benchmark is a single case.

run
: One execution of a case — one pytest item
  (`bench_x.py::bench_classify::identify_pii[run3]`). Lands in exactly
  one of three classes: pass, fail, or error.

step
: One yielded `StepResult` within a run — a labeled check.

row
: The aggregate of all runs of one `(case, step label)` pair — a
  fraction, a status, and a cost in the summary table.

## Why fractions?

An LLM classifier that answers correctly 70% of the time will pass a
conventional test most days and fail it on the others. Both observations
are noise; the signal is the fraction. pytest-probability makes the
fraction the unit of reporting:

Every run lands in one of three classes — **pass**, **fail**, or
**error** — and the row status names the combination:

- **pass** — every run passed. Trustworthy.
- **flaky** — passes and fails mixed. This is the row that pays for
  the plugin: it looks green on a lucky day.
- **errored** — passes and errors mixed (`7/10  3 ERRORED`): the model
  never answered wrong, but the harness dropped runs. Different fix,
  different word.
- **fail** — no run passed, at least one real failure. Broken, at
  least deterministically so.
- **error** — every run errored. Your harness, not your model.

The fraction is always over all runs — `7/10` never dresses up as
`7/7` — while the status word tells you *which kind* of trouble made
up the gap.

## Highlights

- **One pytest item per (case, run)** — `-k`, `-m`, `-x`, `--lf`, JUnit
  XML, and [pytest-xdist](https://pypi.org/project/pytest-xdist/) all
  operate per run, no special casing.
- **Zero declaration surface** — every `bench_*` function in a
  `bench_*.py` module is a benchmark, exactly like `test_*` in
  `test_*.py`; cases are stock `@pytest.mark.parametrize`, named with
  `pytest.param(id=...)`, tagged with marks. No DSLs, no data files,
  no plugin classes to declare with.
- **Fractions per combination** — every parameter combination gets its
  own row and pass fraction, stacked axes cross-product with pytest's
  exact id rules, and an unparametrized function runs as a single case.
- **Cost and token accounting** — attach a `cost` or per-model
  `TokenUsage` entries to any step and get per-row spend plus a
  per-model input/output/cached token breakdown.
- **Machine-readable output** — `--prob-json` writes fractions,
  statuses, and raw per-run records to a single file.
- **One dependency: pytest.**

## Documentation

```{toctree}
:maxdepth: 2
:caption: User guide

installation
quickstart
cases
parametrization
running
cost
json-report
```

```{toctree}
:maxdepth: 1
:caption: Reference

reference
changelog
```

```{toctree}
:maxdepth: 2
:caption: Developer guide

internals
contributing
```
