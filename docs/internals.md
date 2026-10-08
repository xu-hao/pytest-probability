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
                 ├─ resolve the function's comparison (_compare_plan)
                 ├─ resolve each case's runs and gate (_case_plan)
                 ├─ warn about gates that can't pass (InfeasibleGateWarning)
                 └─ yield BenchItem per (case, run)
                                          (--prob-transpose reorders)

BenchItem.runtest()            (one (case, run) execution)
  ├─ optional inter-run delay
  ├─ run the bench_* body; classify by exception type
  ├─ publish ("probability", {..., "gate": {...}, "compare": {...},
  │                            "latency": {...}})
  │   onto item.user_properties
  └─ let exceptions propagate

pytest_runtest_makereport      (hookwrapper)
  └─ gated case, margin comparison, or baseline case under
     --prob-margin: report a failing run as xfailed

Curtailer                      (--prob-stop=curtail; registered in
  │                             pytest_configure where items run)
  └─ pytest_runtest_protocol:  tally each case's runs; once
                               Gate.settled() names a verdict, skip
                               the rest (skip mark + probability_stop)

ProbabilityAggregator          (registered in pytest_configure)
  ├─ pytest_runtest_logreport: rebuild stats from user_properties
  ├─ pytest_sessionfinish:     decide gates (rate and latency) and
  │                            margins (axis and baseline) → exit
  │                            status, then write the
  │                            JSON report (controller only)
  └─ pytest_terminal_summary:  render the fraction table, function-level
                               lines, metrics block, latency block,
                               comparisons block,
                               baseline block, gates block and
                               (--prob-explain) explain section

--prob-plan                    (instead of running)
  ├─ pytest_cmdline_main:      turn off xdist's -n
  └─ pytest_runtestloop:       plan_rows(session.items) → aggregator.plan,
                               rendered as the probability: plan table
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
`elapsed`, read through the module global `_clock`) and installs a `_RunRecorder` into a `ContextVar` —
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
no other way to learn a case's gate. The `compare` key, present only
for cases of a compared function, is the same idea for comparisons:
the function's `CompareSpec.to_record()` (axis, baseline, margin,
equivalence) plus the case's `input`, `arm` and `arm_index`. The
`latency` key, present only when a case's marks set `latency_quantile`
or `max_latency`, is its `LatencySpec.to_record()`.

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

## Metrics

pass^k and pass@k (`--prob-metric`) ride on the aggregates:

- `stats.pass_hat_k(c, n, k)` = C(c, k)/C(n, k) and
  `stats.pass_at_k(c, n, k)` = 1 − C(n − c, k)/C(n, k), with
  `math.comb` and one exact integer division each, so they are
  correctly rounded at any n. Both need 1 ≤ k ≤ n.
- `Metric(kind, k)` (`kind` `"^"` or `"@"`) is one requested metric:
  `name` is its spelling (`pass^3`), `value(passes, total)` the
  estimator and `of(CaseStats)` a row's value, `None` below k runs.
  `parse_metrics()` reads `--prob-metric` (append, each value split on
  commas and whitespace) or `prob_metric`, in first-seen order without
  duplicates, and `metrics_config(config)` keeps the result on
  `config.stash`, resolved and validated in `pytest_configure` like the
  other settings.
- `aggregate(..., metrics=...)` adds one `MetricAggregate` per metric
  to the `Aggregate`'s `metrics`: `aggregate(scope, name, eligible,
  cfg, value=metric.value)` over the cases with at least k runs — the
  same canonical order, seed, level and `min_inputs` rule, so the
  interval comes for free — or `None` when no case has k runs, plus
  `left_out`, the number of shorter cases. The inner aggregate's ρ is
  that of the eligible cases' pass/fail counts and is not shown.
  `ProbabilityAggregator.aggregates()` passes the session's metrics,
  so they are cached with the rest.
- `rows[].metrics` is `{name: Metric.of(row)}` and
  `aggregates[].metrics` is `{name: MetricAggregate.to_json()}`; both
  are `{}` without the option. `_shown_metrics()` picks the block's
  `(Aggregate, MetricAggregate)` pairs — every function, then Overall
  unless there is a single function, whatever their size — and
  `_metric_lines()` aligns them; its one-line form is also a reading's
  heading.
- Metrics never touch verdicts or the exit status. Comparisons and
  gates on a metric would reuse `Metric.value` with `compare_pairs` and
  `aggregate`; neither exists yet.

## Comparisons

Also in `plugin.py`, after the aggregates:

- **At collection**, `_parametrize_variants()` returns each variant's
  `parts` — `(value id, value index)` per decorator, the pieces its id
  is joined from — and `_compare_plan()` resolves the function's
  comparison from its own `probability` marks (`compare=` beats
  `--prob-compare`/`prob_compare`): the decorator holding the axis, the
  arm ids in value order, the baseline (default: the first) and the
  margin. Each case's arm is its piece from that decorator and its
  input the other pieces joined with `-`. A mark naming a missing axis,
  an axis with one value, duplicate arm ids or a bad
  baseline/margin is a `CollectError`; an axis from the command line
  that a function lacks just leaves it uncompared. Comparison
  arguments on a `pytest.param` mark are rejected: they describe the
  function.
- `CompareSpec` is the frozen spec; `verdict(interval)` applies
  `margin_verdict()`, which is `interval_verdict(low, high, −margin)`
  for non-inferiority and `equivalence_verdict()` — PASS strictly
  inside ±margin, FAIL entirely outside, else UNDECIDED — for
  equivalence. No interval is UNDECIDED.
- `compare_pairs(function, spec, arm, pairs, cfg, unpaired=, cost_ratio=)`
  is the pure core: `Pair(input, (passes, total), (passes, total))`s in,
  one frozen `Comparison` out. Pairs are sorted by input id (the
  bootstrap's draw order). One pair: that input's `stats.newcombe` and
  `stats.fisher_exact`. More: the mean difference and
  `stats.sign_flip_test`, plus — with at least `cfg.min_inputs` pairs
  — a percentile interval from `stats.bootstrap` over
  `(baseline, arm)` fraction pairs. Nothing in it depends on where
  the pairs came from, which is what a regression gate against a
  stored report can reuse.
- `comparisons_of(cases, cfg, adjust)` groups `CaseStats` with a
  `compare` dict by function (sorted by name), then by input and arm;
  for each arm in `arm_index` order it pairs inputs that have both
  arms (pooling duplicate ids), lists the rest as unpaired, prices
  `_cost_ratio()`, and finally `adjust_comparisons()` applies
  `stats.adjust_pvalues` over every comparison with a p-value — the
  session is one family.
- `ProbabilityAggregator.comparisons()` caches that list for the JSON
  report, the block (`_comparison_lines()`) and the readings.
  `pytest_sessionfinish` adds margin verdicts to the gate verdicts it
  passes through `GateConfig.fails()`.
- `BenchItem.judged` is true for a gated case or a case of a
  comparison with a margin; the makereport wrapper xfails failing runs
  of judged items. Errors are only excluded by a case gate.

## Baseline

Also in `plugin.py`, after the comparisons. A regression gate is a
comparison whose two arms are two reports, so it reuses
`compare_pairs()` whole:

- `load_baseline(text)` reads a report's `rows[]` — only `case`,
  `passes` and `total` are required, `errors` and `cost` are used when
  present, so 0.2.0 reports work — into `CaseStats` per case id
  (duplicate ids pooled, rows with no runs skipped), raising
  `ValueError` with what is wrong.
- `Baseline` is the loaded report plus `--prob-margin`.
  `baseline_of(config)` loads and validates it once, in
  `pytest_configure`, on **every** process: a missing or malformed
  file, a margin outside [0, 1), or a margin without a baseline is a
  `UsageError` before anything runs. Workers need it because the xfail
  decision is theirs: `Baseline.judges(case)` — there is a margin and
  the case is in the baseline — feeds `BenchItem.judged`. Nothing about
  the baseline travels in `user_properties`; the controller reads the
  same file.
- `compare_with_baseline(cases, baseline, cfg, adjust)` groups the
  union of both reports' case ids by `function_of`, sorted by name,
  and for each function with a paired case calls `compare_pairs()` with
  `CompareSpec(axis=None, baseline="baseline", margin=...)`, arm
  `"current"`, one `Pair` per case id in both (keyed by the id without
  its function, so `compare_pairs` sorts them), the rest as `unpaired`
  and `_cost_ratio()` over the paired rows. `adjust_comparisons()` then
  adjusts these comparisons as their own family. The `BaselineResult`
  also keeps the sorted ids found only in this run or only in the
  baseline. A function without a paired case gets no comparison, so a
  new or removed function never gets a verdict.
- `ProbabilityAggregator.baseline_result()` caches it for the JSON
  `baseline` block, the `probability: baseline` block
  (`_baseline_lines()`, which reuses `_comparison_lines()` with a
  `label` naming the line after the function) and the readings
  (`explain.baseline_summary_reading`, `explain.baseline_reading`).
  `pytest_sessionfinish` adds the baseline verdicts to the ones it
  passes through `GateConfig.fails()`.

## Latency

Also in `plugin.py`, next to the gates:

- **The clock.** `runtest()` times the body with the module global
  `_clock` (`time.perf_counter`), looked up at every run so tests can
  swap in a fake that only moves when a bench body advances it
  (`tests/test_latency.py` installs one from a conftest, so xdist
  workers get it too). The record's `elapsed` is the only input.
- **At collection**, `_latency_plan()` reads `latency_quantile` and
  `max_latency` from the case's merged `probability` marks (the same
  `_merge_probability_marks()` as `_case_plan()`, so a case mark
  overrides the function's key by key) and returns a frozen
  `LatencySpec` — quantile, level (the mark's `confidence` for a
  latency gate, else the session's), `max_latency`, the session's
  `prob_errors` and the planned runs — or `None` when the marks set
  neither. A spec travels in every run record as `latency`
  (`to_record()`), like `gate`; unmarked cases have no `latency` key
  and get the session default on the aggregating process
  (`prob_latency_quantile` from `LatencyConfig`, at `prob_confidence`).
- **Aggregation.** `pytest_runtest_logreport` appends each record's
  `elapsed` to `CaseStats.times`, or `error_times` for an errored run.
  `ProbabilityAggregator.latency_results()` calls
  `LatencySpec.evaluate()` for every row, once, and caches the dict.
  `evaluate()` takes every time, less `error_times` when the case is
  gated (a rate or a latency gate) under `prob_errors = exclude`,
  **sorts** them (arrival order differs under xdist), and computes
  `stats.sample_quantile` and `stats.quantile_ranks`: the interval is
  the order statistics at those ranks, with `None` for an end that
  doesn't exist yet. `stats.quantile_coverage` is the guarantee
  reported in JSON.
- **Verdicts.** `latency_verdict(low, high, max_latency)` is
  `interval_verdict(-high, -low, -max_latency)`, with a missing end
  mapped to ∓∞ so it can't PASS without an upper end or FAIL without a
  lower one. `pytest_sessionfinish` adds latency verdicts to those it
  passes through `GateConfig.fails()`, the `Gates:` tally counts them,
  and `_write_gates()` lists non-PASS ones after the rate gates
  through `_latency_lines()`, which also renders the `--prob-latency`
  block (`_write_latency()`) and each reading's heading, so the three
  can't disagree. `_secs()` formats durations.
- **Not judged.** A latency gate doesn't set `BenchItem.judged`: the
  makereport wrapper keeps reporting failing runs of a latency-only
  case as failures, because the gate says nothing about answers.
- `_warn_infeasible()` warns about latency gates whose planned runs
  are below `LatencySpec.min_runs()` (`stats.quantile_min_n`), keyed by
  spec like rate gates.
- `explain.latency_reading()` explains a `LatencyResult`;
  `_write_explained()` adds one for every latency-gated case, and with
  `--prob-latency` for a shown line missing an end, and the glossary
  gets `latency` (per level) and `latency-limit` entries.

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
  line — `gate_reading(GateResult, …)`, `row_reading(CaseStats, …)`,
  `aggregate_reading(Aggregate, min_inputs)` (which adds the ρ and
  runs-vs-inputs paragraphs when the aggregate has a ρ),
  `metric_reading(Aggregate, MetricAggregate, min_inputs)` and
  `comparison_reading(Comparison, …)` — and a new kind of
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

## Planning

`--prob-plan` lives in `plugin.py` after the comparisons, with its
column notes in `explain.py` (`plan_notes()`, `render_notes()`).

- **No run.** A `tryfirst` `pytest_runtestloop` returns `True` before
  pytest's own loop runs any item — the same short-circuit
  `--collect-only` uses, so selection (`-k`/`-m`, which deselect in
  `pytest_collection_modifyitems`) has already happened and
  `session.items` is exactly what would run. It first raises
  `Interrupted` on collection errors, as pytest's loop does. The
  aggregator gets `plan` (the rows) instead of results and its
  `pytest_terminal_summary` renders only the plan; its `_json_path` is
  `None` under `--prob-plan`, so no report is written.
- **No workers.** An xdist controller never collects, so it would have
  nothing to plan. A `pytest_cmdline_main` hookwrapper sets
  `config.option.numprocesses = 0` before xdist's own
  `pytest_cmdline_main` reads it (a wrapper runs before every plain
  implementation, whatever the plugin order), which makes xdist set
  `dist = "no"` and start nothing.
- `plan_rows(items, PlanConfig)` groups the `BenchItem`s by case,
  counting them (so a selected subset of runs is what's planned) and
  taking the case's `Gate` from its first item. Gate numbers don't
  depend on the planned run count, so they are cached per gate with
  `runs` cleared. A case's latency gate (its first item's
  `LatencySpec`, when `gated`) only raises `min_runs` to
  `LatencySpec.min_runs()` — `stats.quantile_min_n(quantile, level)`,
  the same number `_warn_infeasible()` uses; the power columns stay the
  rate gate's, marked `*` (`_PLAN_LATENCY_MARK`) when both apply.
- `Gate.critical_passes(n)` is the fewest passes of n with a PASS
  verdict (bisection: verdicts are monotone in the pass count).
  `Gate.power(n, rate)` is `stats.binom_sf(critical − 1, n, rate)`.
  `Gate.runs_for_power(rate, target, cap)` returns `None` for a rate
  gate whose bar is at or above `rate`, or when `power(cap)` falls
  short, and otherwise scans every n from `min_runs()`: power
  saw-tooths, so bisecting on n could miss the smallest. The critical
  count rises by at most one per extra run, so the scan tracks it with
  about one `verdict()` per n. Everything goes through `verdict()`, so
  a plan uses the gate's own method, level and prior.
- `stats.runs_to_see_failure(f, level)` is ⌈ln(1 − level)/ln(1 − f)⌉,
  nudged by whole runs against `stats.detection_chance` so rounding at
  a boundary can't make it one off. The plan passes `prob_confidence`
  as `level` — the session's one level.
- `PlanConfig` (`plan_config(config)`, resolved and validated in
  `pytest_configure`; `None` without `--prob-plan`) holds the assumed
  rate, the flake rates and the cost per run read from
  `--prob-plan-report` (`rows[].cost / rows[].total`).

## Early stopping

`--prob-stop=curtail` (issue #9) lives in `plugin.py`, in three parts:

- **The rule:** `Gate.cutoffs(total)` is `(pass_at, fail_at)` — the
  fewest passes that PASS at `total` judged runs (`critical_passes`,
  `total + 1` when none) and the most that FAIL (bisection, `-1` when
  none) — cached per gate and total by `functools.lru_cache`, since
  under `exclude` every error changes the total. `Gate.settled(passes,
  fails, errors, remaining)` returns the verdict when it can no longer
  change, else `None`. Bounds rise with passes and fall with
  non-passes, for every method and prior, so of all the ways the
  remaining runs could go (pass, fail, error, or no sample — under
  `exclude` an error is no sample too) the two extremes decide: PASS if
  `passes ≥ pass_at(total)` with `total` the judged runs so far plus
  `remaining` (every remaining run failing), FAIL if `passes +
  remaining ≤ fail_at(total)` (every one passing), UNDECIDED if neither
  is reachable. The same monotonicity makes the verdict on the runs so
  far already that verdict, which is why a stopped case can be judged
  on the runs it has. Two corners: nothing recorded yet never settles
  (stopping would leave no row), and with every run so far excluded the
  verdict on zero runs must agree too. `tests/test_stop.py` checks the
  rule against every completion for small run counts — every method,
  level, prior and error mode — including that it never waits longer
  than it must.
- **Deciding:** `stop_config(config)` (a frozen `StopConfig`, resolved
  in `pytest_configure`) says whether this process curtails: in-process
  yes; on an xdist worker only under `--dist loadgroup` (xdist turns it
  into `config.option.loadgroup` there); on the xdist controller never,
  with a `CurtailmentWarning` unless `--dist loadgroup`. Where it does,
  a `Curtailer` is registered. On the first item it plans from
  `session.items` (after `-k`/`-m`): the stoppable cases are those whose
  every item is `BenchItem.curtailable` — gated, and with no comparison
  margin, `--prob-margin` baseline or latency gate needing its runs —
  with one gate. Its `pytest_runtest_protocol` hookwrapper counts each
  finished item of such a case (the `"probability"` record it left on
  `user_properties`, or none) and asks `settled()` with the items left;
  once it answers, every later item of the case gets a `skip` mark
  (reason `probability gate: decided after 12/40 runs (FAIL)`, reported
  at the bench file) and a `("probability_stop", {case, run, after,
  planned, verdict})` property, so it is a skip, not a sample.
  `runtest()` skips too when `-p no:skipping` ignores the mark. Under
  loadgroup, collection gives each stoppable item without an
  `xdist_group` of its own one named after its case (`@` and `]`
  replaced, since xdist splits node ids on them), so one worker runs
  every run of the case. The decision only needs counts, so order —
  case-major, `--prob-transpose`, or xdist's — doesn't matter.
- **Reporting:** `ProbabilityAggregator` rebuilds a `Stop` per case
  from the skipped runs' setup (or call) reports, wherever it runs:
  rows and gates lines get `Stop.label()`, the footer `_stop_line()`,
  and the JSON report `rows[].stopped` and `stopping`.
  `suppress_stopped(agg, stops)` drops the intervals of any aggregate
  (and of each metric's inner aggregate) over a stopped case and sets
  `stopped`; `suppress_stopped_comparison()` drops a comparison's
  interval and p-value when its arm's or baseline's paired cases
  include a stopped one, before the p-values are adjusted, so the
  family is the comparisons that keep a p-value. The same goes for
  baseline comparisons, by function. `_shown_aggregates()` keeps a
  stopped aggregate that would have had an interval, and
  `_aggregate_lines()`, `_metric_lines()` and `_comparison_lines()`
  print a note in its place. The explain readings take the `Stop`
  (`gate_reading`, `row_reading`) or read `stopped` off the objects.

## Invariants worth preserving

If you change the plugin, these are the properties the test suite pins
down and users rely on:

1. Everything published to `user_properties` is plain-dict/scalar
   (xdist serialization).
2. Item ids are stable and predictable
   (`file::bench_fn::case-id[runN]`) — people script against them with
   `-k` and `--deselect`. (Under `--dist loadgroup`, xdist appends
   `@group` — with `--prob-stop=curtail`, the case's own group.)
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
12. Comparisons are a function of the aggregated counts, the `compare`
    specs in the records, the seed and the settings only: inputs are
    sorted by id, functions by name and arms by parametrize index, and
    the bootstrap and sign-flip test use private seeded generators.
    Suites without a compared function get no comparisons block, no
    `compare` key in records and the same exit status; a comparison
    without a margin never changes the exit status, and a margin only
    ever changes it from `OK` to `TESTS_FAILED`.
13. Metrics are a function of the aggregated counts, the requested
    metrics, the seed and the settings only, computed by `aggregate()`
    on the process that has every result, so they are the same with
    and without xdist. Without `--prob-metric`/`prob_metric` the
    terminal output is unchanged and `metrics` is `{}`; metrics never
    change the exit status.
14. `--prob-plan` runs no item, starts no worker and writes no report;
    its numbers are exact functions of the collected items, their
    gates and the plan options. Without `--prob-plan` the plan options
    change nothing.
15. A baseline comparison is a function of the aggregated counts, the
    baseline file, the seed and the settings only: functions are sorted
    by name and cases by id, through the same seeded `compare_pairs()`,
    so it is the same with and without xdist. Without
    `--prob-baseline` there is no baseline block, `baseline` is `null`
    in the JSON report, and nothing else changes; without
    `--prob-margin` it never changes the exit status or xfails a run.
    Only cases found in the baseline are judged, and a margin only
    ever changes the exit status from `OK` to `TESTS_FAILED`.
16. Latency results are a function of the records' `elapsed` values,
    the `latency` specs in the records and the settings only: times
    are sorted before anything is computed, on the process that has
    every result, so lines, JSON and verdicts are the same with and
    without xdist. Cases without `latency_quantile`/`max_latency` have
    no `latency` key in their records; without `--prob-latency` there
    is no latency block, and without `max_latency` nothing about the
    exit status, xfails or the gates block changes. A latency gate
    never xfails a run and only ever changes the exit status from `OK`
    to `TESTS_FAILED`.
17. Early stopping never changes a verdict: a case stops only when
    every way its remaining runs could go gives the verdict it has
    then, so every gate, margin and latency verdict — and the exit
    status — is the one all the runs would give. Only cases whose runs
    no other verdict needs are stopped, and only by the process that
    runs all of a case's runs (in-process, or one xdist worker under
    `--dist loadgroup`). Skipped runs are pytest skips, never samples
    (invariant 4), and the aggregator learns of them from their
    reports, so the output is the same with and without xdist.
    Intervals and p-values over a stopped case are suppressed, never
    shown. Without `--prob-stop=curtail` nothing changes: no marks, no
    skips, `stopped` is `null` in rows and 0 elsewhere.
