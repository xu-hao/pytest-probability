# Quickstart

This walkthrough goes from nothing to a flaky-detection report in about
five minutes.

## 1. Write a benchmark file

Benchmark files are named `bench_*.py` (configurable via the
`prob_pattern` ini option, see {doc}`reference`), and every `bench_*`
function in one is a benchmark: an ordinary parametrized generator.
Each parameter combination is a case:

```python
# benchmarks/bench_greeting.py
import pytest
from pytest_probability import StepResult

@pytest.mark.parametrize("text,expected", [
    pytest.param("hello", "greeting", id="english"),
    pytest.param("hallo", "greeting", id="german"),
])
def bench_classify(text, expected):
    answer = my_classifier(text)          # your code under test
    yield StepResult(
        label="classify",
        passed=(answer == expected),
    )
```

The function *yields* one `StepResult` per step instead of asserting.
A run can therefore report several labeled checks, and a failing step
doesn't abort the rest of the run.

## 2. Run it

```text
$ pytest benchmarks/
========================= test session starts ==========================
plugins: probability-0.1.0
collected 2 items

benchmarks/bench_greeting.py ..                                   [100%]

============================= probability ==============================
  [classify::english] classify  1/1
  [classify::german] classify   1/1

  Overall: 2/2 passed (100%)
========================== 2 passed in 0.02s ===========================
```

Each case became one pytest item
(`bench_greeting.py::bench_classify::english`), and the plugin appended
its summary after pytest's own output.

## 3. Turn up the sample size

One run of nondeterministic code proves nothing. Sample it:

```text
$ pytest benchmarks/ --prob-runs=10
...
collected 20 items

benchmarks/bench_greeting.py ...............F..F.               [100%]

============================= probability ==============================
  [classify::english] classify  10/10
  [classify::german] classify    8/10  FLAKY

  Overall: 18/20 passed (90%)
```

`german` passes a single-shot test 80% of the time — which means a
conventional test suite would have shipped it. The `FLAKY` row is the
entire point of this plugin.

Every run is an ordinary pytest item named `german[run7]`, so failures
appear individually in the short summary and rerun with `--lf` like any
other test.

## 4. Grow the file

More benchmarks are more `bench_*` functions; more cases are more
parametrize values — including generated ones:

```python
@pytest.mark.parametrize("row", [
    pytest.param(row, id=row["id"]) for row in load_dataset()
])
def bench_extraction(row):
    ...
```

Summary rows are namespaced by function (`classify::english`,
`extraction::case_017`), so nothing collides, and `-k bench_extraction`
runs one benchmark. Add a comparison axis
(`@pytest.mark.parametrize("model", ["small", "large"])`) and every
case splits into `-small` / `-large` rows.

## 5. Where to go next

- {doc}`cases` — the full benchmark-file contract, multi-step runs,
  lifecycle hooks, error semantics.
- {doc}`parametrization` — axes, ids, marks, and selection.
- {doc}`running` — repeat counts, ordering, throttling, parallelism.
- {doc}`cost` — per-model token usage and spend, next to the fractions
  it bought.
- {doc}`json-report` — machine-readable output for CI and dashboards.
