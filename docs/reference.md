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

## Python API

Everything importable lives in the top-level package:

```python
from pytest_probability import TokenUsage, record_cost, record_usage
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

## Terminal summary anatomy

```text
================================= probability ==================================
  classify::identify_pii   7/10  $0.0020  FLAKY
   \_ function::case-id  \_ fraction  \_ cost  \_ status (omitted when pass)

  Overall: 35/50 passed (70%), 3 errored   # errored count only when present
  Cost:    $0.0110                                 # only when cost was recorded
  Tokens:  m-small  1,200 in / 80 out / 640 cached  $0.0010   # per model,
           m-large  4,800 in / 900 out              $0.0040   # only with usage
  Report:  report.json                             # only with --prob-json
```

The section renders only when at least one benchmark item ran.

## Exit status

Standard pytest semantics: any failed or errored run makes the session
exit nonzero. There is no "allowed flakiness" threshold in the plugin
itself; build soft gates on the {doc}`JSON report <json-report>`.
