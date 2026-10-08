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

@pytest.mark.parametrize("text,expected", [
    pytest.param("hello", "greeting", id="english"),
    pytest.param("hallo", "greeting", id="german"),
])
def bench_classify(text, expected):
    answer = my_classifier(text)          # your code under test
    assert answer == expected, f"got '{answer}'"
```

That's a pytest test in everything but name — the `bench_` prefix is
the entire opt-in. The body asserts; a wrong answer fails the run, an
exception (API down, timeout) errors it, and those are tracked as
different classes.

## 2. Run it

```text
$ pytest benchmarks/
========================= test session starts ==========================
plugins: probability-0.2.0
collected 2 items

benchmarks/bench_greeting.py ..                                   [100%]

============================= probability ==============================
  classify::english  1/1
  classify::german   1/1

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
  classify::english  10/10  [69%, 100%]
  classify::german    8/10  [44%,  97%]  FLAKY

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

- {doc}`bdd` — the development loop: reproduce a production prompt
  failure as a fraction, fix it on an axis, guard it forever.
- {doc}`cases` — the full benchmark-file contract, run outcomes,
  lifecycle hooks, error semantics.
- {doc}`parametrization` — axes, ids, marks, and selection.
- {doc}`running` — repeat counts, ordering, throttling, parallelism.
- {doc}`statistics` — what intervals, gates and p-values mean, what they
  assume, and how to decide with them.
- {doc}`cost` — per-model token usage and spend, next to the fractions
  it bought.
- {doc}`json-report` — machine-readable output for CI and dashboards.
