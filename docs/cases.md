# Benchmark files

A benchmark file is an ordinary Python module whose name matches the
`prob_pattern` ini option (default `bench_*.py`). Inside it, **every
module-level function whose name starts with `bench_` is a benchmark**
— the same convention pytest applies to `test_*` functions in
`test_*.py` files. Point pytest at a directory or at the file itself.

## The module contract

`bench_*(**params)` functions
: Each is a generator (any iterable-returning callable works) that
  exercises one case and yields one step result per check. Cases come
  from plain `@pytest.mark.parametrize` on the function — every
  parameter combination is one case, and its values arrive as
  arguments. A function with no parametrize marks is a single case
  named after the function. See {doc}`parametrization`.

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

## Declaring cases

Cases are stock pytest parametrization — nothing plugin-specific:

```python
import pytest
from pytest_probability import StepResult

@pytest.mark.parametrize("text,expected", [
    pytest.param("my ssn is 078-05-1120", "pii", id="identify_pii",
                 marks=pytest.mark.fast),
    pytest.param("is this a question?", "question", id="is_question"),
])
def bench_classify(text, expected):
    answer = my_classifier(text)
    yield StepResult(label="classify", passed=(answer == expected))
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
  [classify::identify_pii] classify   7/10  FLAKY
  [classify::is_question] classify   10/10
```

Dataset-driven suites are a comprehension away — and a ragged payload
is just a dict-valued parameter:

```python
@pytest.mark.parametrize("row", [
    pytest.param(row, id=row["id"]) for row in load_dataset()
])
def bench_extract(row):
    ...
```

## Step results

Bench functions yield one result per step:

```python
from pytest_probability import StepResult, TokenUsage

StepResult(
    label="classify",       # required — the row key in the summary
    passed=True,            # required — did this step pass this run?
    elapsed=0.42,           # optional — seconds, recorded in the JSON report
    error=None,             # optional — infrastructure failure text
    message="got 'other'",  # optional — shown in the failure summary
    details={},             # optional — free-form, for your own use
    cost=0.0002,            # optional — summed into the report
    usage=[TokenUsage(model="m-small", input_tokens=100, output_tokens=20)],
)
```

Field semantics:

| Field | Type | Effect |
|---|---|---|
| `label` | `str` | Aggregation key: fractions are computed per `(case, label)` pair |
| `passed` | `bool` | `False` fails this run's item and counts toward the row's `fails` |
| `elapsed` | `float` | Informational; preserved in `--prob-json` records |
| `error` | `str \| None` | Non-`None` counts the step as an **error**, not a fail |
| `message` | `str \| None` | Appended to the one-line failure report |
| `details` | `dict` | Not interpreted by the plugin |
| `cost` | `float \| None` | Summed per row and overall; see {doc}`cost` |
| `usage` | `list[TokenUsage]` | Per-model token accounting; see {doc}`cost` |

Steps are duck-typed: anything with a `label` and `passed` attribute
is accepted, and the remaining fields are read with `getattr(..., None)`
defaults — so bench functions may yield result types from other
frameworks as long as the attribute names line up.

## Multi-step runs

Yield as many steps as the case has checks. Each label aggregates into
its own summary row, and one failing step does not stop the following
steps from running:

```python
@pytest.mark.parametrize("text", CASES)
def bench_pipeline(text):
    t0 = time.time()
    answer = pipeline(text)
    yield StepResult(label="classify", passed=answer.kind == "billing",
                     elapsed=time.time() - t0)
    yield StepResult(label="latency", passed=(time.time() - t0) < 2.0)
    yield StepResult(label="citation", passed=answer.citation is not None)
```

```text
  [pipeline::refund_request] classify  9/10  FLAKY
  [pipeline::refund_request] latency   10/10
  [pipeline::refund_request] citation  4/10  FLAKY
```

## Failure and error semantics

Two distinct things can go wrong, and they are reported differently:

**A step fails** (`passed=False`)
: The run's item fails with a compact one-liner — no traceback, because
  there is no exception to trace:

  ```text
  ______________________ case: classify::identify_pii ______________________
  step 'classify' failed: got 'other'
  ```

  The text after the colon is the step's `error` or, failing that, its
  `message`.

**The bench function raises**
: The item fails with the ordinary pytest traceback, *and* the run is
  recorded as a failed step labeled `error` so it still counts in the
  aggregate:

  ```text
    [check::crashy] ok     1/1
    [check::crashy] error  0/1  ERROR
  ```

  Steps yielded before the exception are kept.

Runs are a three-class outcome, and the row status names the
combination: all errors shows **ERROR** (harness problem), passes
mixed with errors shows **N ERRORED** (`7/10  3 ERRORED` — the model
never answered wrong, the harness dropped runs), no passes with real
failures shows **FAIL**, and passes mixed with failures shows
**FLAKY** — the word reserved for genuine nondeterminism. The fraction
is always `passes/total runs`, so an errored run costs the row its
perfect score; the status word says which class of trouble it was.
All seven class combinations and their exact renderings are tabulated
in {doc}`reference`.
