# Internals

This page explains how the plugin works, in enough detail to modify it
confidently. It assumes familiarity with [pytest's hook
system](https://docs.pytest.org/en/stable/how-to/writing_plugins.html)
and custom collectors.

Everything lives in one module, `pytest_probability/plugin.py`,
registered under the `pytest11` entry point named `probability`.

## The big picture

```text
pytest_collect_file            (file name matches prob_pattern)
  └─ BenchFile.collect()
       ├─ import the module under a path-unique name
       ├─ read setup / teardown
       └─ yield BenchFunction per module-level bench_* callable
            └─ BenchFunction.collect()
                 ├─ expand parametrize marks into case variants
                 └─ yield BenchItem per (case, run)
                                          (--prob-transpose reorders)

BenchItem.runtest()            (one (case, run) execution)
  ├─ optional inter-run delay
  ├─ run the bench_* body; classify by exception type
  ├─ publish ("probability", {...}) onto item.user_properties
  └─ raise StepFailures / let exceptions propagate

ProbabilityAggregator          (registered in pytest_configure)
  ├─ pytest_runtest_logreport: rebuild stats from user_properties
  ├─ pytest_terminal_summary:  render the fraction table
  └─ pytest_sessionfinish:     write the JSON report (controller only)
```

The load-bearing design decision is in the middle: **execution
publishes plain data onto the test report; aggregation consumes
reports.** Everything else follows from it.

## Collection

`pytest_collect_file` claims any file whose *name* fnmatches
`prob_pattern` and returns a `BenchFile` (a `pytest.File` subclass).
Regular `test_*.py` files never match, so pytest's own Python collector
is untouched.

`BenchFile.collect()` **imports the module** with `importlib` under a
name derived from the file's stem plus a hash of its full path
(`pytest_probability_mods.bench_x_1a2b3c4d`), so two files with the
same stem in different directories cannot collide in `sys.modules`.
Import errors propagate — pytest turns them into collection errors
with a traceback, which beats silently collecting nothing. It then
yields a `BenchFunction` collector for every module-level `bench_*`
callable, in definition order — a file with none collects nothing,
silently, so a `bench_helpers.py` utility module is ignored rather
than reported as broken.

`BenchFunction.collect()` reads the function's marks straight off
`bench_fn.pytestmark` — the attribute `pytest.mark.*` decorators stash
on the function — rather than going through `Metafunc`, which only
exists for functions collected by pytest's Python collector:

- **Parametrize marks** are expanded by `_parametrize_variants()` into
  `(id, params, marks)` triples: the cartesian product across stacked
  decorators, processed in `pytestmark` order (bottom decorator
  leftmost in the composed id, matching pytest). Ids follow pytest's
  default rules (value text for scalars, `argnameN` for objects), with
  `ids=` and `pytest.param(id=...)` overrides. `ParameterSet` (what
  `pytest.param` returns) is detected by duck-typing to avoid
  importing private pytest modules. **No parametrize marks** yield the
  single empty variant — an unparametrized function is one case.
- **One `BenchItem` per (case, run)** is emitted; case-major order by
  default, `--prob-transpose` flips the loop nesting. Each item's
  `case` attribute is the function-qualified row id
  (`classify::identify_pii`, or just `classify` for the empty
  variant), so identical ids in different benchmarks aggregate
  separately.
- **Non-parametrize marks** on the function, plus per-value marks from
  `pytest.param(..., marks=...)`, are attached to every generated
  item. One subtlety: `Node.add_marker()` only accepts
  `MarkDecorator`, but `pytestmark` holds raw `Mark` objects, so the
  plugin appends to `item.own_markers` and `item.keywords` directly —
  the same two places `add_marker()` writes. pytest's skipping/xfail
  machinery and `-m` selection read from those, so they work on bench
  items unmodified.

Module-level `setup()`/`teardown()` are stored on the `BenchFile` and
exposed as the collector's own `setup()`/`teardown()`. pytest's
`SetupState` calls a collector's `setup()` before the first item in its
chain and `teardown()` when leaving it — which is precisely
once-per-file semantics, with no bookkeeping of our own.

`indirect=True` is rejected territory: it routes parameters through
fixtures, and `BenchItem` does not participate in the fixture system
(no `FuncFixtureInfo`, no request object). Wiring that up is the single
biggest possible extension to this plugin — it would also unlock
`tmp_path`-style fixtures inside bench functions — and should be done
deliberately, not accidentally.

## Execution

`BenchItem.runtest()` calls `bench_fn(**params)` — a plain test body —
inside a try that classifies the outcome:

- **Clean return** → pass. (A returned generator is rejected with a
  loud `TypeError`: the pre-0.2.0 yield-based style would otherwise
  silently pass without executing.)
- **`AssertionError`** → fail; the first line of the assert message is
  kept as the run's `message`, then the exception re-raises so pytest
  renders its normal traceback — pointing at the actual `assert`.
- **Any other `Exception`** → error; recorded as
  `"TypeError: …"`-style text, then re-raised.
- **`BaseException` control flow** (`pytest.skip`, `pytest.fail`,
  KeyboardInterrupt) is not caught: those runs are not samples and are
  never recorded.

Around the call, `runtest()` starts a `perf_counter` clock (the run's
`elapsed`) and installs a `_RunRecorder` into a `ContextVar` —
`record_usage()`/`record_cost()` resolve it and append. The
`finally` block publishes the record whatever the outcome, which is
why usage recorded before a failing assert survives. The ContextVar
(rather than a global) keeps concurrent in-process runs isolated.

**Assertion rewriting** is wired in at import: `_compile_bench_source`
parses the module to an AST, runs it through pytest's rewriter
(`_pytest.assertion.rewrite.rewrite_asserts` — one deliberate internal
import, guarded so the plugin degrades to plain asserts if it ever
moves), and compiles the result itself. The rewritten code is
self-contained (it imports its helpers at module top), and pytest's
own `pytest_runtest_protocol` wrapper installs the comparison hooks
around every item — including ours — so bare asserts report operands
and diffs exactly as in `test_*.py`. `--assert=plain` and a
`PYTEST_DONT_REWRITE` docstring opt out, matching pytest.

The inter-run delay also lives at the top of `runtest()`. A
`StashKey[bool]` on `config` marks "the first benchmark item already
ran", so the sleep happens strictly *between* executions. Being
config-stash-scoped makes it naturally per-process — each xdist worker
throttles its own stream.

## Publishing results: why `user_properties`

`runtest()` ends by appending one tuple to `item.user_properties`:

```python
("probability", {"case": case, "run": run_id, "outcome": "fail",
                 "message": ..., "error": None, "elapsed": 0.41,
                 "cost": 0.0002, "usage": [ ... ]})
```

Alternatives considered and rejected:

- *Record into a collector object during `runtest()`* — simplest, but
  wrong under pytest-xdist: items execute in worker processes, and the
  controller (which renders the summary) would aggregate nothing.
- *A custom xdist IPC channel* — needless coupling to xdist internals.

`user_properties` is the sanctioned escape hatch: pytest copies it onto
the `TestReport`, and xdist serializes reports from workers to the
controller. The constraint it imposes is that values must survive that
serialization — hence **plain dicts and scalars only** (usage entries
are normalized to dicts as they are recorded). If you extend the
payload, keep it JSON-shaped.

## Aggregation and output

`ProbabilityAggregator` is instantiated once per session in
`pytest_configure` and registered as a plugin, which is how its hook
methods get called with no global state.

- `pytest_runtest_logreport` filters for `when == "call"` reports,
  unpacks the `"probability"` user property, and folds each run into
  an `OrderedDict[case → CaseStats]` by its `outcome` class. Cost sums
  when present, and `usage` entries merge into a per-model aggregate
  on the row. Row order is therefore first-encounter order of results.
- The statistical settings (`prob_method`, `prob_confidence`,
  `prob_prior`, `prob_intervals`) are resolved and validated once in
  `pytest_configure` into a frozen `StatsConfig` kept on
  `config.stash`; `stats_config(config)` returns it. Intervals are
  computed from `CaseStats` at render time, never shipped from
  workers, so under xdist they come from the controller's own
  options. Anything that needs an interval goes through
  `StatsConfig.interval()`, so printed intervals and any later verdict
  share one method and level.
- `pytest_terminal_summary` renders the table with fractions
  right-aligned across rows, an optional interval column, colors by status (green/yellow/red), and
  appends the `Overall`/`Cost`/`Tokens`/`Report` footer lines (the
  per-model `Tokens:` block merges every row's usage). It renders
  nothing when no benchmark items ran.
- `pytest_sessionfinish` writes the JSON report. It returns early on
  xdist workers, detected by the `workerinput` attribute on the config
  — the standard idiom, used instead of importing xdist. Raw per-run
  records are only retained in memory when `--prob-json` was requested.

The row `status` property names the combination of the three run
classes (pass / fail / error): `pass` and `error` are the pure cases,
`fail` means no passes but real failures exist, `flaky` is reserved
for a genuine pass/fail mixture, and `errored` marks passes marred
only by errors. Fractions are `passes/total` — every run counts — and
the status word plus the `(N errored)` annotation say which class made
up the gap.

## Invariants worth preserving

If you change the plugin, these are the properties the test suite pins
down and users rely on:

1. Everything published to `user_properties` is plain-dict/scalar
   (xdist serialization).
2. Item ids are stable and predictable
   (`file::bench_fn::case-id[runN]`) — people script against them with
   `-k` and `--deselect`.
3. Parametrize ids and ordering match real pytest for the same
   decorators.
4. Outcome classes are exception-derived and exact: `AssertionError` →
   fail, other `Exception` → error, `pytest.skip`/control-flow → not a
   sample. Usage recorded before the exception is never lost.
5. Files matching the pattern but lacking `bench_*` functions collect
   nothing, silently.
6. The summary section appears only when benchmark items ran; regular
   pytest suites see zero output difference.
7. The JSON report is written once, by the process that has all the
   results.
