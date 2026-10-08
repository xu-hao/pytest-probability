# Changelog

## 0.3.0 (unreleased)

- **Visible output change:** with more than one run, every summary row
  now shows a 95% interval for the case's pass probability between the
  fraction and the cost — `classify::identify_pii   7/10  [35%,  93%]
  $0.0020  FLAKY`. Scripts that parse the terminal table need
  updating (the JSON report is the stable interface); single-run
  output and regular pytest suites are unchanged.
  `--prob-no-intervals` / `prob_intervals = false` restore the old
  layout. Choose the method with `--prob-method` / `prob_method`
  (`exact` Clopper-Pearson by default, `wilson`, or `bayes` with
  `prob_prior`, default `1,1`) and the level with
  `--prob-confidence` / `prob_confidence` (default 0.95).
- **JSON:** rows gain `ci: {method, level, low, high}` (unrounded),
  and a top-level `stats_config` records the method, level and prior.
- **Gates:** `@pytest.mark.probability(min_rate=0.9)` (or
  `--prob-min-rate` / `prob_min_rate` for every case) passes a case
  when its whole interval lies above the bar, fails it when the
  interval lies entirely below, and calls it UNDECIDED otherwise —
  failing by default, `--prob-undecided=pass` / `prob_undecided` to
  relax. `min_passes=` gates on a count instead; `runs=`,
  `confidence=`, `method=` and `prior=` set one function's (or, via
  `pytest.param` marks, one case's) run count and interval settings.
  In a gated case, failing runs are reported as xfailed (`-rx` lists
  them, `--xfail-tb` shows tracebacks, `-x` doesn't stop on them) and
  the verdict sets the exit status; a `probability: gates` block lists
  FAIL and UNDECIDED cases with their interval and bar, and the footer
  counts verdicts. `prob_errors = exclude` takes errored runs out of a
  gate's sample instead of counting them as non-passes. A
  collection-time `InfeasibleGateWarning` flags gates that can't pass
  with their run count. JSON rows gain `gate` (`null` when ungated),
  and `exit_status` includes gate failures. Ungated suites are
  unchanged. Known limitation: a failed gate has no failing item, so
  JUnit XML and `--lf` don't see it.
- **Internal:** new `pytest_probability.stats` module — the
  standard-library-only building blocks for the statistical-testing
  work: exact binomial tails, the regularized incomplete beta function
  and its inverse, Clopper-Pearson / Wilson / Beta-credible intervals
  for one proportion, the Newcombe interval and Fisher's exact test for
  two, and a seeded bootstrap. No user-visible change yet; pytest is
  still the only runtime dependency. The test suite cross-checks it
  against scipy and statsmodels, available as the `test` extra.

## 0.2.0 (2026-07-08)

Bench functions are now **plain test bodies that assert** — the
yield-a-result style is gone.

- **Breaking:** `StepResult` is removed. A bench function passes by
  returning, fails on `AssertionError`, and errors on any other
  exception — the three outcome classes fall straight out of Python.
  `pytest.skip`/`xfail` bypass recording, as in pytest. A generator
  bench (the old style) is rejected with a clear error.
- **Breaking:** rows aggregate per case (the step/label dimension is
  gone): terminal rows read `classify::identify_pii  7/10  FLAKY`, and
  the JSON `rows` lose `label` while `records` become flat per-run
  entries (`outcome`, `message`, `error`, `elapsed`, `cost`, `usage`).
- **Assertion rewriting:** bench modules pass through pytest's
  rewriter, so bare asserts report operands and diffs
  (`assert 'other' == 'pii'`); the first line lands in the JSON
  `message`. `--assert=plain` and `PYTEST_DONT_REWRITE` opt out.
- **New:** `record_usage(...)` and `record_cost(...)` attribute
  per-model token usage and plain cost to the current run from inside
  the body; usage recorded before a failing assert is kept. Run
  wall-clock time is measured by the plugin (`elapsed` in records).
- Multiple checks per case = multiple asserts (first failure ends the
  run) or multiple `bench_*` functions sharing a helper.

## 0.1.0 (2026-07-08)

Initial release.

- Collection of `bench_*.py` benchmark files the way pytest collects
  `test_*.py`: every module-level `bench_*` function is a benchmark —
  an ordinary parametrized generator yielding step results. Every
  parameter combination is a case with its own fraction row
  (`classify::identify_pii`); an unparametrized function runs as a
  single case. Optional once-per-file `setup()`/`teardown()`.
- Stock pytest declaration surface: `@pytest.mark.parametrize` with
  pytest's exact id layout and ordering, `pytest.param(id=...,
  marks=...)`, `ids=`; `skip`/`skipif`/`xfail` and custom marks apply
  per case or per function; selection via `-k`, `-m`, and node ids —
  no plugin-specific declaration or filtering options.
- One pytest item per (case, run) via `--prob-runs`/`prob_runs`, with
  compact one-line failure reports for failed steps and full
  tracebacks for exceptions (recorded as `error` steps in the
  aggregate).
- Pass-fraction terminal summary per `(case, label)` row, overall
  totals, cost, and a per-model token breakdown. Runs are a
  three-class outcome (pass/fail/error) and the row status names the
  combination — `pass`/`flaky`/`errored`/`fail`/`error` — with
  `flaky` reserved for genuine pass/fail nondeterminism and `errored`
  for passes marred only by infrastructure errors (`7/10  3 ERRORED`).
- Cost and token accounting: a currency-neutral `cost` per step and/or
  per-model `TokenUsage` entries (`model`, `input_tokens`,
  `output_tokens`, `cached_input_tokens`, `cost`), aggregated per row,
  per model, and in totals; duck-typed (objects or plain dicts).
- `--prob-json`: single-file JSON report with aggregate rows,
  per-model usage, and raw per-run step records; xdist-safe
  (controller writes).
- `--prob-delay`: throttling between executions (never before the
  first; per worker under xdist).
- `--prob-transpose`: run-major execution order.
- pytest-xdist compatibility throughout: aggregation is rebuilt from
  serialized test reports on the controller.
- Duck-typed step and usage objects.
