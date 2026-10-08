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
                 ├─ resolve each case's runs and gate (_case_plan)
                 ├─ warn about gates that can't pass (InfeasibleGateWarning)
                 └─ yield BenchItem per (case, run)
                                          (--prob-transpose reorders)

BenchItem.runtest()            (one (case, run) execution)
  ├─ optional inter-run delay
  ├─ run the bench_* body; classify by exception type
  ├─ publish ("probability", {..., "gate": {...}}) onto item.user_properties
  └─ let exceptions propagate

pytest_runtest_makereport      (hookwrapper)
  └─ gated case: report a failing run as xfailed

ProbabilityAggregator          (registered in pytest_configure)
  ├─ pytest_runtest_logreport: rebuild stats from user_properties
  ├─ pytest_sessionfinish:     decide gates → exit status, then write
  │                            the JSON report (controller only)
  └─ pytest_terminal_summary:  render the fraction table, function-level
                               lines, gates block and (--prob-explain)
                               explain section
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

`probability` marks are resolved per case by `_case_plan()`, from the
case's own marks (`pytest.param(marks=...)`) and then the function's,
closest first (`_merge_probability_marks()`): a closer mark wins
argument by argument, except that `min_rate`/`min_passes` are replaced
as one rule. The result is the case's run count (`--prob-runs` >
`runs=` > `prob_runs`) and a frozen `Gate` or `None`. Cases may
therefore have different run counts; the case-major and run-major
loops both handle that. Bad arguments raise `CollectError`, naming the
case. A gate whose `verdict(runs, runs)` isn't PASS gets one
`InfeasibleGateWarning` per distinct gate, issued with
`warnings.warn_explicit` at the function's line, like `Node.warn` does.

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

A gated case's failing run is a sample, not a verdict. The module-level
`pytest_runtest_makereport` hookwrapper (old-style `hookwrapper=True`,
for pytest 7.4) finds the run's record in `report.user_properties` and,
for a `"fail"` record — or an `"error"` one under `prob_errors =
exclude` — sets `report.outcome = "skipped"` and `report.wasxfail` to
a reason, which is exactly how pytest's own xfail reports look.
`longrepr` keeps the traceback (`--xfail-tb` prints it), `-rx` shows
the reason, and pytest's (and xdist's) `-x`/`--maxfail` accounting
skips reports that carry `wasxfail`. Control flow has no record and is
never converted, and `--runxfail` disables the conversion, as it does
for xfail marks.

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
                 "cost": 0.0002, "usage": [ ... ],
                 "gate": {"rule": "rate", "min_rate": 0.9, ...}})
```

The `gate` key is present only for gated cases. It is the case's
`Gate.to_record()`, fully resolved on the worker (marker overrides plus
the session's method, level, prior and `prob_errors`), because the
xdist controller that decides verdicts never collects items and so has
no other way to learn a case's gate.

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
  `prob_prior`, `prob_intervals`, and the bootstrap's
  `prob_bootstrap`, `prob_seed`, `prob_min_inputs`) are resolved and validated once in
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
- `pytest_sessionfinish` returns early on xdist workers, detected by
  the `workerinput` attribute on the config — the standard idiom, used
  instead of importing xdist. On the process with every result it
  decides the gates (below), then writes the JSON report. Raw per-run
  records are only retained in memory when `--prob-json` was requested
  (without their `gate` key; `rows[].gate` carries it once).

The row `status` property names the combination of the three run
classes (pass / fail / error): `pass` and `error` are the pure cases,
`fail` means no passes but real failures exist, `flaky` is reserved
for a genuine pass/fail mixture, and `errored` marks passes marred
only by errors. Fractions are `passes/total` — every run counts — and
the status word plus the `(N errored)` annotation say which class made
up the gap.

## Gates

The pieces, all in `plugin.py`:

- `interval_verdict(low, high, bar)` — the one verdict rule: PASS when
  `low > bar`, FAIL when `high < bar`, UNDECIDED otherwise. Later
  margin and latency gates are meant to reuse it.
- `GateConfig` / `gate_config(config)` — the session's global
  `min_rate`, `undecided` policy and `errors` mode, validated in
  `pytest_configure` and kept on `config.stash`.
  `GateConfig.fails(verdict)` says whether a verdict fails the session.
- `Gate` — one case's frozen gate: `rule` (`"rate"`/`"count"`), the
  bar, `stats` (a `StatsConfig`: the session's settings with the
  marker's `confidence`/`method`/`prior` applied through
  `dataclasses.replace`), `errors` and the planned `runs`.
  `judge(passes, total)` returns `(interval, verdict)`, computing the
  interval with `stats.interval()`, so nothing else can compute it
  differently. `evaluate(CaseStats)` picks the sample (errors out under
  `exclude`) and returns a `GateResult`. `min_runs()` is the smallest n
  with `verdict(n, n) == PASS`: n/n gets more convincing as n grows for
  every method, so a doubling search plus bisection finds it.
- `GateResult` — case, gate, sample (`passes`, `total`, `excluded`),
  interval and verdict; `to_json()` is `rows[].gate`.

`CaseStats.gate` is rebuilt from the first record of the case
(`Gate.from_record`). `ProbabilityAggregator.gate_results()` evaluates
every gated row from the aggregated counts, the same way in-process and
on the xdist controller. `pytest_sessionfinish` sets
`session.exitstatus = TESTS_FAILED` when a result fails and the status
is still `OK`; any other status is left alone. This works because the
plugin's hook runs inside the terminal reporter's
`pytest_sessionfinish` wrapper, before it renders the summary, and
`wrap_session` returns `session.exitstatus` as the process exit code.
The summary adds a `Gates:` tally to the footer and a `probability:
gates` section listing every non-PASS result with the gate's own
fraction, interval and bar. The main table keeps the session's interval
over every run, so its column means the same thing on every row.

## Function-level intervals

Also in `plugin.py`, after `CaseStats`:

- `function_of(case)` — the bench function a row belongs to: the case
  id up to its first `::` (a short name is an identifier, so a `::`
  inside a parametrize id can't confuse it).
- `aggregate(scope, name, cases, cfg, value=pass_fraction)` — one
  frozen `Aggregate` over some `CaseStats`. The estimate is the mean
  of `value(passes, total)` per case (each input counts equally). The
  interval is a cluster bootstrap: since the statistic is a mean of
  per-case values, resampling whole cases with all their runs is
  resampling their values, so it is `stats.bootstrap(values,
  statistics.fmean, resamples, seed)` then `stats.percentile_interval`
  at `cfg.level` — the one confidence level. `stats.normal_interval`
  is the JSON-only cross-check. With fewer than `cfg.min_inputs` cases
  both are `None` and `suppressed` says why. `value` is the hook for
  per-case metrics other than the pass fraction (pass^k).
- `Aggregate` keeps `cases` and `counts` (`(passes, total)` per case)
  in **canonical order — sorted by case id** — which is the order the
  bootstrap draws from. Result arrival order differs under xdist, so
  without the sort the same seed would resample a different list.
  `to_json()` is one `aggregates[]` entry.
- `ProbabilityAggregator.aggregates()` groups rows by `function_of`
  (sorted by function name — unlike rows, which follow arrival order,
  so the block and `aggregates[]` are identical under xdist), adds the
  `OVERALL` aggregate over every case, and caches the list: the JSON
  report and the summary both use it, and the bootstrap is the one
  costly step (about 0.3 s for 1,000 cases × 5,000 resamples).
  `_shown_aggregates()` is the terminal's subset: those with an
  interval, without Overall when there is a single function, and none
  under `--prob-no-intervals`.

- `Aggregate.icc` is `stats.icc(counts)` — ICC(1) by one-way ANOVA on
  the per-run 0/1 outcomes, computed from the `(passes, total)` counts
  alone (for binary data the sums of squares follow from them), with
  k₀ = (Σkᵢ − Σkᵢ²/Σkᵢ)/(N − 1) for unequal run counts, clipped to
  [0, 1]. It is `None` when undefined: every case run once (no
  within-input degrees of freedom) or every run with the same outcome
  (both mean squares 0). `width_factor` is `stats.width_factor` at
  `harmonic_runs` — the harmonic mean of the run counts, which makes
  √(ρ + (1 − ρ)/k) exact for an equally weighted mean of per-case
  fractions. `projection()` gives the relative width change for runs
  ×2 and inputs ×2 through `stats.projected_width`; `cost` (the cases'
  recorded cost, `math.fsum`, so arrival order can't change it) prices
  both, since each doubles the number of runs. ρ is always computed,
  suppressed or not, and goes to JSON as `icc`/`width_factor`;
  `_aggregate_lines()` shows it, with the continuation line, only on
  shown lines.

Errored runs count as non-passes in an aggregate even under
`prob_errors = exclude`: aggregates use the row's fraction, and the
exclusion is a gate setting. Aggregates never touch gate verdicts or
the exit status.

## Explanations

`--prob-explain` keeps its wording out of `plugin.py`: every sentence
lives in `explain.py`, as templates over numbers the plugin has
already computed. The plugin picks the lines to explain, supplies the
numbers, and colors the output; `explain.py` imports from `plugin.py`
and not the other way round (the plugin imports it lazily, only when
it needs it).

- A `Reading` is one explained line: `heading` (the line itself),
  `paragraphs` (the last one usually `Next: …`), a `tone` for coloring
  and the glossary `terms` it used. There is one function per kind of
  line — `gate_reading(GateResult, …)`, `row_reading(CaseStats, …)`
  and `aggregate_reading(Aggregate, min_inputs)` (which adds the ρ and
  runs-vs-inputs paragraphs when the aggregate has a ρ) — and a new kind of
  output line gets a new function beside them.
- The glossary is a registry: `@glossary_entry(key, label)` registers
  `fn(details) -> text`. A reading lists `(key, detail)` pairs, and
  each entry receives the distinct details — `("interval",
  stats_key(cfg))` makes the `[low, high]` entry name exactly the
  methods, levels and priors that appeared. Entries print in
  registration order, and only when some reading used them.
- `render_section()` lays it out for the terminal (wrapped with
  `textwrap` to the terminal writer's width); `Reading.text()` is the
  unwrapped form in the JSON report.
- The one estimate it prints, "about N runs would settle it", is math,
  so it lives on the gate: `Gate.runs_to_settle(passes, total, cap)`
  bisects for the smallest n whose verdict on round(p̂·n)/n is not
  UNDECIDED, through `verdict()` — the gate's own interval.

The terminal section and the JSON strings are both built on the
process with every result, from aggregated counts, so they are the
same under xdist.

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
8. Ungated cases are untouched by gates: no `gate` key in their
   records, no xfail conversion, and the same terminal output and exit
   status as without the gate options.
9. Gate verdicts come only from aggregated counts and the gate spec in
   the records, on the process that has every result, so they are the
   same with and without xdist. Gates only ever change the exit status
   from `OK` to `TESTS_FAILED`.
10. A printed verdict always sits next to the interval it was computed
    from: `Gate.judge()` is the only place a gate's interval is made,
    and the gates block prints that interval.
11. Function-level and overall intervals are a function of the
    aggregated counts, the seed and the settings only: `aggregate()`
    draws from cases sorted by id, with `stats.bootstrap`'s private
    seeded generator, on the process that has every result. The same
    results give the same intervals, with and without xdist, and the
    global `random` state is never touched. ρ and its projection are
    exact functions of the same counts.
