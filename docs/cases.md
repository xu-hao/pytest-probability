# Benchmark files

A benchmark file is an ordinary Python module whose name matches the
`prob_pattern` ini option (default `bench_*.py`). Inside it, **every
module-level function whose name starts with `bench_` is a benchmark**
— the same convention pytest applies to `test_*` functions in
`test_*.py` files. Point pytest at a directory or at the file itself.

## The module contract

`bench_*(**params)` functions
: Each is an **ordinary test body**: it takes its case's values as
  arguments, does the work, and `assert`s the outcome. Cases come from
  plain `@pytest.mark.parametrize` on the function — every parameter
  combination is one case. A function with no parametrize marks is a
  single case named after the function. See {doc}`parametrization`.

`setup()` / `teardown()` *(optional)*
: Called once per file: `setup()` before the file's first item runs,
  `teardown()` after its last — shared by all `bench_*` functions in
  the file. If `setup()` raises, the file's items error out
  individually with that exception.

A file matching the pattern with no `bench_*` functions is silently
skipped — zero items, not an error. A file that raises on import is
reported as a pytest collection error, with the traceback.

```{note}
Benchmark files are not test modules: pytest fixtures and
`conftest.py` fixtures do not apply (see {doc}`internals` for why). If
you need per-run resources, create them inside the bench function;
per-file resources belong in `setup()`/`teardown()`.
```

## Writing a benchmark

If you can write a pytest test, you already know the syntax:

```python
import pytest
from pytest_probability import record_cost

@pytest.mark.parametrize("text,expected", [
    pytest.param("my ssn is 078-05-1120", "pii", id="identify_pii",
                 marks=pytest.mark.fast),
    pytest.param("is this a question?", "question", id="is_question"),
])
def bench_classify(text, expected):
    answer = my_classifier(text)
    record_cost(0.0002)                      # optional, see Cost
    assert answer == expected, f"got '{answer}'"
```

- **Values are function arguments** — named, hintable, refactorable.
- **`pytest.param(id=...)`** names the case; without it, pytest's id
  rules apply (value text for scalars, `argnameN` for objects,
  `ids=` list/callable supported).
- **`pytest.param(marks=...)`** tags a single case for `-m` selection
  or skips/xfails it; marks on the function apply to every case.
- **Stacked decorators** cross-product with pytest's exact id layout —
  `refund-terse`, `refund-chain_of_thought`, bottom decorator leftmost.
- **No parametrize at all** → the function runs as one case named
  after itself (`bench_smoke` → case `smoke`), which makes
  `--prob-runs` a repeat-runner for plain functions.

Each case gets its own summary row, namespaced by the function's short
name so identical ids in different benchmarks never collide:

```text
  classify::identify_pii   7/10  [35%,  93%]  FLAKY
  classify::is_question   10/10  [69%, 100%]
```

Dataset-driven suites are a comprehension away — a ragged payload is
just a dict-valued parameter:

```python
@pytest.mark.parametrize("row", [
    pytest.param(row, id=row["id"]) for row in load_dataset()
])
def bench_extract(row):
    assert extract(row["input"]) == row["expected"]
```

## Run outcomes

A run's outcome is decided by how the function body ends — the three
classes fall straight out of Python:

| The body… | Outcome | Meaning |
|---|---|---|
| returns normally | **pass** | correct answer |
| raises `AssertionError` | **fail** | your code answered wrong |
| raises anything else | **error** | your harness broke (API down, timeout, bug) |

Two consequences worth internalizing:

- **Assert the *outcome*, let infrastructure raise.** A wrong
  classification is an `assert`; a `ConnectionError` from the client
  library propagates by itself and lands in the error class. You get
  the fail/error separation for free by writing idiomatic code.
- **`pytest.skip()` / `xfail` work** exactly as in pytest — and a
  skipped run is not a sample: it never enters any fraction.

The plugin measures each run's wall-clock time itself (`elapsed` in the
{doc}`JSON report <json-report>`) — no stopwatch code in your benches.

Failures are ordinary assertion failures with **pytest's full assertion
rewriting**: bench modules pass through the same rewriter as
`test_*.py` files, so a bare assert reports its operands and diffs —

```text
>       assert answer == expected
E       AssertionError: assert 'other' == 'pii'

bench_classify.py:41: AssertionError
```

The first line of that message (your explicit assert message, or the
rewritten `assert 'other' == 'pii'`) also lands in the JSON report as
the run's `message`, so wrong answers are analyzable in bulk.

Rewriting honors pytest's own escape hatches: `--assert=plain`
disables it session-wide, and a module docstring containing
`PYTEST_DONT_REWRITE` opts that file out.

Want several checks against one case? Either assert them in sequence
(the first failure ends the run — exactly like a test), or split them
into separate `bench_*` functions sharing a helper so each check gets
its own fraction row.

## Multiple benchmarks per file

A file holds as many `bench_*` functions as you like; they collect in
definition order:

```python
@pytest.mark.parametrize("row", CASES)
def bench_classify(row):
    assert classify(row["input"]) == row["expected"]

@pytest.mark.parametrize("row", CASES)
def bench_latency(row):
    t0 = time.perf_counter()
    classify(row["input"])
    assert time.perf_counter() - t0 < 2.0
```

Summary rows are namespaced by the function's short name
(`bench_classify` → `classify::`), so identical case ids in different
benchmarks aggregate separately, and `-k bench_latency` selects one
benchmark's items.
