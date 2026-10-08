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
- **Explanations:** `--prob-explain` / `prob_explain = true` adds a
  `probability: explained` section that reads every gated case and
  every ungated row that didn't pass all its runs in plain language —
  what was measured, what the numbers mean, and a next step (for an
  UNDECIDED gate, about how many runs would settle it) — plus a short
  "Methods used" glossary naming the interval method and level in
  effect. Text wraps to the terminal width. With the flag, JSON rows
  and gates gain an `explanation` string. Without it, a session with a
  FAIL or UNDECIDED gate ends with a one-line hint,
  `Run with --prob-explain for a plain-language reading.`; nothing
  else changes.
- **Function-level intervals:** each bench function with at least
  `prob_min_inputs` cases (default 10) gets a line below the rows —
  `classify  N=40 inputs × k=10  83.0%  [75.5%, 89.8%]` — and an
  `Overall` line covers every case (left out when there is a single
  function). The estimate is the mean of the per-case pass fractions,
  so every input counts equally; the interval is a percentile interval
  from a cluster bootstrap that re-draws whole cases with all their
  runs, at `prob_confidence`. `--prob-bootstrap` / `prob_bootstrap`
  sets the resamples (default 5000) and `--prob-seed` / `prob_seed`
  the seed (default 0); the cases are drawn in case-id order, so the
  output is the same for the same results, with or without xdist.
  `--prob-no-intervals` hides the lines. Suites with fewer than 10
  cases see no change. **JSON:** a new top-level `aggregates[]` (every
  function and Overall: inputs, runs min/mean/max, estimate, `ci`, a
  normal-approximation `normal_ci` cross-check, resamples, seed,
  `resampling_unit: "input"`, the note "inputs treated as a sample",
  and `suppressed` when there are too few inputs), and `stats_config`
  gains `resamples`, `seed` and `min_inputs`. `--prob-explain` reads
  each line in plain words, including what treating the inputs as a
  sample means for a hand-picked suite.
- **Runs or inputs:** with more than one run per case, each
  function-level line also shows ρ, how alike the runs of one input are
  (intraclass correlation by one-way ANOVA on every run's pass/fail,
  adjusted for unequal run counts, clipped to [0, 1]), and an indented
  line projects how much doubling the runs or doubling the inputs
  would narrow its interval, priced at the recorded cost per run —
  `runs ×2 → interval −1%  ·  inputs ×2 → −29%  ·  each +$0.0400`. Low
  ρ: outputs vary from run to run, so more runs help; high ρ: each
  input is consistently right or wrong, so add inputs. Left out when
  every case ran once or every run had the same outcome, and shown
  only with its interval line. **JSON:** `aggregates[]` gain `icc` and
  `width_factor` (`null` when left out). `--prob-explain` explains ρ
  and the projection in words and bases its next step on them. New
  `stats.icc`, `stats.width_factor` and `stats.projected_width`.
- **Comparisons:** `@pytest.mark.probability(compare="style")` (or
  `--prob-compare=style` / `prob_compare` for every function with that
  parametrize argument) compares each value of the axis — each *arm* —
  with the baseline (the first value, or `baseline=`), paired by input
  (the case's other parameters). A `probability: comparisons` block
  prints one line per arm — `triage[style]  chain_of_thought − terse
  +20 pp [−11, +51]  p=0.47  1 paired  cost ×4.0` — with the mean
  difference in percentage points, its interval, a p-value, the pair
  count (`, N unpaired` for inputs missing an arm, which are left out)
  and the cost ratio per run. One input: that input's Newcombe interval
  and Fisher's exact p. With at least `prob_min_inputs` pairs: a paired
  cluster bootstrap over inputs (`prob_bootstrap`, `prob_seed`) and an
  exact-when-cheap, else seeded Monte Carlo, sign-flip permutation p
  (McNemar's exact test with one run per arm); in between, the
  average and p, plus one line per input with its own interval.
  `margin=0.02` makes it a non-inferiority gate (lower bound above −2
  points) and `equivalence=True` an equivalence gate (interval within
  ±2 points): the function's failing runs are xfailed and the verdicts
  set the exit status, with `--prob-undecided` as for gates.
  `--prob-adjust` / `prob_adjust` (`none`, `holm`, `bonferroni`, `bh`)
  adjusts the session's p-values for the number of comparisons; under
  the default `none`, several comparisons are labelled exploratory.
  **JSON:** a new top-level `comparisons[]` (function, axis, baseline,
  arm, pairs, unpaired, difference, ci, p, p_method, exact,
  p_adjusted, adjustment, family, exploratory, margin, equivalence,
  verdict, cost_ratio and per-input `inputs[]`). `--prob-explain`
  reads each comparison in plain words, including what its p-value
  means, and the hint also appears when comparisons are shown. Suites
  without a compared function are unchanged apart from the empty
  `comparisons` list. New `stats.sign_flip_test` and
  `stats.adjust_pvalues`.
- **pass^k and pass@k:** `--prob-metric=pass^3,pass@5` (repeatable,
  or `prob_metric` in the ini file) also reports pass^k, the chance
  that k runs of the same input all pass (reliability), and pass@k,
  the chance that at least one of k does (best-of-k). Per case they
  are the unbiased estimates C(c, k)/C(n, k) and
  1 − C(n − c, k)/C(n, k) from c passes in n runs (errored runs count
  as non-passes); cases with fewer than k runs are left out and
  counted. Each function and Overall gets the mean over its cases with
  the same cluster-bootstrap interval as its function-level line, in a
  new `probability: metrics` block —
  `classify  pass^3  N=12 inputs  34.3%  [17.2%,  53.8%]` — shown
  whatever the number of inputs (the interval still needs
  `prob_min_inputs`). **JSON:** `rows[]` gain `metrics`
  (`{"pass^3": 0.47, "pass@5": null}`, `null` below k runs) and
  `aggregates[]` gain `metrics` (`{"pass^3": {k, inputs, left_out,
  estimate, ci, normal_ci, suppressed}}`), both `{}` without the
  option. `--prob-explain` reads each line in plain words and the
  glossary explains both metrics. Without the option the output is
  unchanged. New `stats.pass_hat_k` and `stats.pass_at_k`.
- **Internal:** new `pytest_probability.stats` module — the
  standard-library-only building blocks for the statistical-testing
  work: exact binomial tails, the regularized incomplete beta function
  and its inverse, Clopper-Pearson / Wilson / Beta-credible intervals
  for one proportion, the Newcombe interval and Fisher's exact test for
  two, a seeded bootstrap, and a normal-approximation interval for a
  mean. No user-visible change yet; pytest is still the only runtime
  dependency. The test suite cross-checks it against scipy and
  statsmodels, available as the `test` extra.

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
