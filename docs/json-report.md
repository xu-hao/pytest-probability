# JSON report

`--prob-json=PATH` writes a single machine-readable file at the end of
the session — think `--junitxml`, but fraction-shaped. It exists so the
numbers can leave the terminal: dashboards, regression tracking,
diffing two runs, CI policy gates.

```bash
pytest benchmarks/ --prob-runs=10 --prob-json=report.json
```

The terminal summary confirms the path:

```text
  Overall: 50/70 passed (71%)
  Cost:    $0.0150
  Report:  report.json
```

Parent directories are created as needed. The file is written in
`pytest_sessionfinish`, after all items have run; under pytest-xdist
only the controller writes it, with the full result set.

## Schema

```json
{
  "created": "2026-07-07T21:43:18",
  "runs": 10,
  "stats_config": {"method": "exact", "level": 0.95, "prior": [1.0, 1.0],
                   "resamples": 5000, "seed": 0, "min_inputs": 10},
  "exit_status": 1,
  "totals": {
    "passes": 35,
    "fails": 15,
    "errors": 0,
    "count": 50,
    "pass_rate": 70.0,
    "cost": 0.011,
    "usage": {
      "m-small": {"input_tokens": 1200, "output_tokens": 80,
                  "cached_input_tokens": 640, "cost": 0.001},
      "m-large": {"input_tokens": 4800, "output_tokens": 900,
                  "cached_input_tokens": 0, "cost": 0.004}
    }
  },
  "rows": [
    {
      "case": "triage::refund-terse",
      "passes": 8,
      "fails": 2,
      "errors": 0,
      "total": 10,
      "pass_rate": 80.0,
      "status": "flaky",
      "ci": {"method": "exact", "level": 0.95,
             "low": 0.4439045376923587, "high": 0.9747892736731666},
      "gate": {"rule": "rate", "min_rate": 0.9, "confidence": 0.95,
               "method": "exact", "prior": [1.0, 1.0], "errors": "count",
               "runs": 10, "passes": 8, "total": 10, "excluded": 0,
               "low": 0.4439045376923587, "high": 0.9747892736731666,
               "verdict": "undecided"},
      "latency": {"quantile": 0.95, "total": 10, "excluded": 0,
                  "estimate": 0.0123,
                  "ci": {"method": "order-statistic", "level": 0.95,
                         "low": 0.0101, "high": null, "ranks": [8, null],
                         "coverage": 0.9884964426207031},
                  "min_runs": 72, "max_latency": null, "verdict": null},
      "cost": 0.001,
      "usage": {
        "m-small": {"input_tokens": 1200, "output_tokens": 80,
                    "cached_input_tokens": 640, "cost": 0.001}
      },
      "metrics": {"pass^3": 0.4666666666666667, "pass@5": 1.0}
    }
  ],
  "aggregates": [
    {
      "scope": "function",
      "name": "classify",
      "inputs": 40,
      "runs": {"min": 10, "mean": 10.0, "max": 10},
      "estimate": 0.83,
      "ci": {"method": "bootstrap", "level": 0.95,
             "low": 0.755, "high": 0.897562499999999},
      "normal_ci": {"method": "normal", "level": 0.95,
                    "low": 0.7556643371916796, "high": 0.9043356628083206},
      "resamples": 5000,
      "seed": 0,
      "resampling_unit": "input",
      "note": "inputs treated as a sample",
      "suppressed": null,
      "icc": 0.6033,
      "width_factor": 0.8018541014424008,
      "metrics": {
        "pass^3": {"k": 3, "inputs": 40, "left_out": 0,
                   "estimate": 0.6905,
                   "ci": {"method": "bootstrap", "level": 0.95,
                          "low": 0.5762, "high": 0.7989},
                   "normal_ci": {"method": "normal", "level": 0.95,
                                 "low": 0.5770, "high": 0.8040},
                   "suppressed": null},
        "pass@5": {"k": 5, "inputs": 40, "left_out": 0,
                   "estimate": 0.9132,
                   "ci": {"method": "bootstrap", "level": 0.95,
                          "low": 0.8611, "high": 0.9583},
                   "normal_ci": {"method": "normal", "level": 0.95,
                                 "low": 0.8659, "high": 0.9605},
                   "suppressed": null}
      }
    }
  ],
  "comparisons": [
    {
      "function": "triage",
      "axis": "style",
      "baseline": "terse",
      "arm": "chain_of_thought",
      "pairs": 1,
      "unpaired": [],
      "difference": 0.19999999999999996,
      "ci": {"method": "newcombe", "level": 0.95,
             "low": -0.1123531026122217, "high": 0.5098375284633582},
      "p": 0.4736842105263155,
      "p_method": "fisher",
      "exact": true,
      "p_adjusted": 0.4736842105263155,
      "adjustment": "none",
      "family": 1,
      "exploratory": false,
      "margin": null,
      "equivalence": false,
      "verdict": null,
      "suppressed": null,
      "resamples": 5000,
      "seed": 0,
      "cost_ratio": 4.0,
      "inputs": [
        {"input": "refund",
         "baseline": {"passes": 8, "total": 10},
         "arm": {"passes": 10, "total": 10},
         "difference": 0.19999999999999996,
         "ci": {"method": "newcombe", "level": 0.95,
                "low": -0.1123531026122217, "high": 0.5098375284633582},
         "p": 0.4736842105263155,
         "p_method": "fisher"}
      ]
    }
  ],
  "baseline": null,
  "records": [
    {
      "case": "triage::refund-terse",
      "run": 4,
      "outcome": "fail",
      "message": "assert 'other' == 'billing'",
      "error": null,
      "elapsed": 0.0101,
      "cost": 0.0001,
      "usage": [
        {"model": "m-small", "input_tokens": 120, "output_tokens": 8,
         "cached_input_tokens": 64, "cost": 0.0001}
      ]
    }
  ]
}
```

Top level:

| Field | Type | Meaning |
|---|---|---|
| `created` | `str` | Local timestamp, `YYYY-MM-DDTHH:MM:SS` |
| `runs` | `int` | The configured `--prob-runs` value (a `probability(runs=...)` mark can change it for one function or case; see `rows[].total` and `rows[].gate.runs`) |
| `stats_config` | object | The session's statistical settings: `method` (`"exact"`, `"wilson"` or `"bayes"`), `level` (two-sided, e.g. `0.95`), `prior` (`[a, b]`, the configured `prob_prior`; used only by `bayes`), and the bootstrap's `resamples` (`--prob-bootstrap`), `seed` (`--prob-seed`) and `min_inputs` (`prob_min_inputs`) |
| `exit_status` | `int` | The session's exit code, including a failure from a gate |
| `totals` | object | Aggregates over every row: `passes`, `fails`, `errors`, `count`, `pass_rate`, `cost`, `usage` |
| `rows` | array | One entry per case, in encounter order |
| `aggregates` | array | One entry per bench function, by name, then one for Overall (below); empty when nothing ran |
| `comparisons` | array | One entry per compared arm (below): functions by name, arms in parametrize order; empty when nothing is compared |
| `baseline` | object \| `null` | This run against `--prob-baseline` (below); `null` without it |
| `records` | array | One entry per executed run |

`rows[]` — the aggregate view, mirroring the terminal table:

| Field | Type | Meaning |
|---|---|---|
| `case` | `str` | Function-qualified case id (`classify::identify_pii`) |
| `passes` / `fails` / `errors` | `int` | Run counts, one per outcome class |
| `total` | `int` | All three counts summed |
| `pass_rate` | `float` | `passes / total * 100` |
| `status` | `str` | `"pass"`, `"flaky"`, `"errored"`, `"fail"`, or `"error"` — see the status table in {doc}`reference` |
| `ci` | object | Interval on the pass probability: `method`, `level`, and unrounded `low`/`high` in [0, 1]. Always present, even for single-run rows and under `--prob-no-intervals`, which only affect the terminal. Always the session's method and level over every run, even for a gated row |
| `gate` | object \| `null` | The case's gate and its verdict (below); `null` for an ungated case |
| `latency` | object | A quantile of the case's run times, its interval and, with `max_latency`, the latency gate's verdict (below). Always present, with or without `--prob-latency` |
| `cost` | `float` | Summed run cost across runs |
| `usage` | object | Per-model token aggregate: `{model: {input_tokens, output_tokens, cached_input_tokens, cost}}` |
| `metrics` | object | One entry per `--prob-metric`, keyed by its name (`"pass^3"`, `"pass@5"`), in option order: the case's unbiased estimate in [0, 1], or `null` when the case has fewer than k runs. `{}` without `--prob-metric` |
| `explanation` | `str` | Only with `--prob-explain`: a plain-language reading of the row (its fraction and the `ci` interval), paragraphs separated by `\n`. A gated row's next step is in `gate.explanation` instead |

`rows[].gate` — present when the case is gated (see Gates in
{doc}`reference`):

| Field | Type | Meaning |
|---|---|---|
| `rule` | `str` | `"rate"` (judge the interval against `min_rate`) or `"count"` (passes ≥ `min_passes`) |
| `min_rate` / `min_passes` | `float` / `int` | The bar; only the one for `rule` is present |
| `confidence` / `method` / `prior` | | The settings of the gate's interval: the session's, or the marker's overrides |
| `errors` | `str` | `prob_errors`: `"count"` or `"exclude"` |
| `runs` | `int` | The case's planned run count |
| `passes` / `total` | `int` | The gate's sample: errored runs are left out of `total` under `exclude` |
| `excluded` | `int` | Errored runs left out (0 under `count`) |
| `low` / `high` | `float \| null` | The gate's interval, unrounded; `null` when `total` is 0 |
| `verdict` | `str` | `"pass"`, `"fail"` or `"undecided"` |
| `explanation` | `str` | Only with `--prob-explain`: the verdict in plain language, ending with a `Next:` line when there is a next step — the same text as the terminal's explain section, unwrapped |

The gate's interval equals `ci` when the gate uses the session's
method and level and errors count; otherwise it is the one its verdict
was read from.

`rows[].latency` — a quantile of the case's run times (see Latency in
{doc}`reference`), for every row:

| Field | Type | Meaning |
|---|---|---|
| `quantile` | `float` | The quantile: the mark's `latency_quantile`, else `prob_latency_quantile` (`0.95` is p95) |
| `total` | `int` | Timed runs: every recorded run, less `excluded` |
| `excluded` | `int` | Errored runs left out: a gated case under `prob_errors = exclude`; else 0 |
| `estimate` | `float \| null` | The sample quantile in seconds — the ⌈q·n⌉-th fastest run time; `null` when `total` is 0 |
| `ci` | object \| `null` | `method` (`"order-statistic"`), `level`, unrounded `low`/`high` in seconds — each `null` while there are too few runs for that end — `ranks` (`[r, s]`: the interval is the r-th and s-th fastest run times, `null` for a missing end) and `coverage`, the probability the interval covers the true quantile for continuous run times (at least `level`; at least that with ties). `null` when `total` is 0 |
| `min_runs` | `int` | Fewest timed runs with both ends at this quantile and level (72 for p95 at 95%) |
| `max_latency` | `float \| null` | The latency gate's limit in seconds; `null` when the case has none |
| `verdict` | `str \| null` | `"pass"` (`high` below the limit), `"fail"` (`low` above it) or `"undecided"`, read off `ci`; `null` without `max_latency` |
| `explanation` | `str` | Only with `--prob-explain`: the line in plain language |

`records[].elapsed` holds every run's time, so other quantiles can be
worked out from the report itself.

`aggregates[]` — the function-level and overall intervals (see
Function-level intervals in {doc}`reference`). Every function is
listed, including those with too few cases for a terminal line, and
Overall always is, even when the terminal leaves it out:

| Field | Type | Meaning |
|---|---|---|
| `scope` | `str` | `"function"` or `"overall"` |
| `name` | `str` | The function's short name (`classify`), or `"Overall"` |
| `inputs` | `int` | Number of cases |
| `runs` | object | Runs per case: `min`, `mean`, `max` |
| `estimate` | `float` | Mean of the per-case pass fractions, in [0, 1]; every run counts, errors as non-passes |
| `ci` | object \| `null` | The percentile interval of the cluster bootstrap: `method` (`"bootstrap"`), `level`, unrounded `low`/`high`. `null` when `suppressed` |
| `normal_ci` | object \| `null` | Cross-check: the normal-approximation interval mean ± z·s/√N, with `method` `"normal"`. Not clipped to [0, 1] — a bound outside it means the approximation is poor there. `null` when `suppressed` |
| `resamples` / `seed` | `int` | The bootstrap's settings |
| `resampling_unit` | `str` | Always `"input"`: whole cases are resampled, keeping all their runs |
| `note` | `str` | Always `"inputs treated as a sample"`: the interval allows for a different set of inputs; for a hand-picked suite it measures the cases chosen, not a wider population |
| `suppressed` | `str \| null` | Why there is no interval, e.g. `"fewer than 10 inputs"`; `null` when there is one |
| `icc` | `float \| null` | ρ, the intraclass correlation of the runs' pass/fail outcomes (one-way ANOVA, adjusted for unequal run counts, clipped to [0, 1]): 0 when an input's runs vary as much as runs of different inputs, 1 when every run of an input gives the same result. `null` when every case ran once, or every run passed or every run failed. Computed even when `suppressed` |
| `width_factor` | `float \| null` | √((1 + (k − 1)ρ)/k), with k the harmonic mean of the run counts: how much one input's runs pin down its pass rate compared with a single run (1/√k at ρ = 0, 1 at ρ = 1). The interval's width scales with `width_factor`/√N, so doubling the runs changes it by `width_factor(2k)/width_factor(k)` and doubling the inputs by 1/√2. `null` with `icc` |
| `metrics` | object | One entry per `--prob-metric`, keyed by its name, in option order (below); `{}` without `--prob-metric` |
| `explanation` | `str` | Only with `--prob-explain`: the line in plain language, ending with a `Next:` line |

`aggregates[].metrics[name]` — a metric (see Metrics in {doc}`reference`) over
the function's (or the session's) cases that have at least k runs:

| Field | Type | Meaning |
|---|---|---|
| `k` | `int` | The k of `pass^k` or `pass@k`: how many runs the metric draws |
| `inputs` | `int` | Cases with at least k runs: the ones averaged |
| `left_out` | `int` | Cases with fewer than k runs, left out (their `rows[].metrics` value is `null`) |
| `estimate` | `float \| null` | Mean of the per-case values over `inputs`, in [0, 1]; `null` when `inputs` is 0 |
| `ci` / `normal_ci` | object \| `null` | As for the aggregate itself — the cluster bootstrap's percentile interval and the normal cross-check, at the same `level`, `resamples` and `seed` — over those `inputs` |
| `suppressed` | `str \| null` | Why there is no `ci`: `"fewer than 10 inputs"`, or `"no input with at least 5 runs"` |
| `explanation` | `str` | Only with `--prob-explain`: the line in plain language |

The bootstrap draws from the cases in case-id order with the recorded
`seed`, and entries are sorted by name, so the same results give the
same `aggregates` — with or without pytest-xdist.

`comparisons[]` — each arm against its function's baseline (see
Comparisons in {doc}`reference`):

| Field | Type | Meaning |
|---|---|---|
| `function` | `str` | The bench function's short name (`triage`) |
| `axis` | `str` | The compared parametrize argument (`style`) |
| `baseline` / `arm` | `str` | The two arms' ids; the difference is arm − baseline |
| `pairs` | `int` | Inputs that ran in both arms |
| `unpaired` | array | Ids of inputs that ran in only one of the two, sorted; left out |
| `difference` | `float \| null` | Mean over paired inputs of the arm's pass fraction minus the baseline's, in [−1, 1]; `null` with no pairs |
| `ci` | object \| `null` | `method` (`"newcombe"` for one pair, `"bootstrap"` for a paired bootstrap over inputs), `level`, unrounded `low`/`high`. `null` when `suppressed` |
| `p` | `float \| null` | Two-sided p-value, unadjusted; `null` with no pairs |
| `p_method` | `str \| null` | `"fisher"` (one pair) or `"sign-flip"` (permutation test on the per-input differences) |
| `exact` | `bool \| null` | Whether `p` is exact (`false`: a Monte Carlo estimate from `resamples` sign patterns) |
| `p_adjusted` | `float \| null` | `p` after `adjustment` over the session's `family`; equals `p` under `none` |
| `adjustment` | `str` | `--prob-adjust`: `"none"`, `"holm"`, `"bonferroni"` or `"bh"` |
| `family` | `int` | How many comparisons in the session have a p-value |
| `exploratory` | `bool` | `true` when `adjustment` is `"none"` and `family` > 1 |
| `margin` / `equivalence` | `float \| null` / `bool` | The comparison's margin (`null`: no verdict) and rule |
| `verdict` | `str \| null` | `"pass"`, `"fail"` or `"undecided"` with a margin, read off `ci`; `null` without one |
| `suppressed` | `str \| null` | Why there is no `ci`: `"fewer than 10 paired inputs"`, `"no paired inputs"` |
| `resamples` / `seed` | `int` | The bootstrap's and the Monte Carlo sign-flip test's settings |
| `cost_ratio` | `float \| null` | The arm's recorded cost per run over the baseline's, on the paired inputs; `null` unless both recorded cost |
| `inputs` | array | One entry per paired input, sorted by id: `input`, `baseline` and `arm` as `{passes, total}`, `difference`, a Newcombe `ci`, a Fisher `p` (never adjusted) |
| `explanation` | `str` | Only with `--prob-explain`: the comparison in plain language |

Inputs are sorted by id and the bootstrap and sign-flip test are
seeded, so the same results give the same `comparisons`, with or
without pytest-xdist.

`baseline` — this run set against an earlier report (see Baseline in
{doc}`reference`); `null` without `--prob-baseline`:

```json
"baseline": {
  "path": "main.json",
  "created": "2026-10-08T19:24:03",
  "margin": 0.05,
  "paired": 52,
  "only_current": ["triage::new_case"],
  "only_baseline": [],
  "comparisons": [
    {
      "function": "triage",
      "axis": null,
      "baseline": "baseline",
      "arm": "current",
      "pairs": 12,
      "unpaired": ["new_case"],
      "difference": -0.15,
      "ci": {"method": "bootstrap", "level": 0.95,
             "low": -0.25, "high": -0.05833333333333333},
      "p": 0.03125,
      "p_method": "sign-flip",
      "exact": true,
      "p_adjusted": 0.03125,
      "adjustment": "none",
      "family": 2,
      "exploratory": true,
      "margin": 0.05,
      "equivalence": false,
      "verdict": "fail",
      "suppressed": null,
      "resamples": 5000,
      "seed": 0,
      "cost_ratio": null,
      "inputs": [
        {"input": "t00",
         "baseline": {"passes": 9, "total": 10},
         "arm": {"passes": 5, "total": 10},
         "difference": -0.4,
         "ci": {"method": "newcombe", "level": 0.95,
                "low": -0.6759121533048664, "high": 0.002356109392247785},
         "p": 0.1408668730650158,
         "p_method": "fisher"}
      ]
    }
  ]
}
```

| Field | Type | Meaning |
|---|---|---|
| `path` | `str` | `--prob-baseline` as given |
| `created` | `str \| null` | The baseline report's own `created`, when it has one |
| `margin` | `float \| null` | `--prob-margin`; `null`: report only, no verdicts |
| `paired` | `int` | Cases found in both reports |
| `only_current` / `only_baseline` | array | Ids of the cases found in only one report, sorted; never paired |
| `comparisons` | array | One entry per bench function with a paired case, by name, shaped like `comparisons[]` with `axis` `null`, `baseline` `"baseline"` and `arm` `"current"` (the difference is current − baseline); `inputs[].input` and `unpaired` are case ids without the function prefix (`""` for an unparametrized function). `p_adjusted` adjusts over these comparisons only |
| `explanation` | `str` | Only with `--prob-explain`: the header in plain language; each comparison carries its own `explanation` too |

`records[]` — the raw view, one per (case, run) execution:

| Field | Type | Meaning |
|---|---|---|
| `case` | `str` | Case id (matches `rows[].case`) |
| `run` | `int` | 1-based run number |
| `outcome` | `str` | `"pass"`, `"fail"`, or `"error"` — the run's class (a gated case's failing runs are xfailed in pytest's output, but stay `"fail"` here) |
| `message` | `str \| null` | First line of the assert's message on failure |
| `error` | `str \| null` | `"ExceptionType: text"` on error |
| `elapsed` | `float` | Wall-clock seconds of the bench body (not the delay between runs), measured by the plugin; the input of `rows[].latency` |
| `cost` | `float \| null` | The run's recorded cost |
| `usage` | array | Raw `record_usage` entries, in call order |

Cases deselected with `-k`/`-m`, and runs that never
executed (e.g. after `-x`), are absent — the report describes what
actually ran.

## Recipes

List the gates that did not pass, with the interval behind each
verdict:

```bash
jq '.rows[] | select(.gate and .gate.verdict != "pass")
    | {case, verdict: .gate.verdict, low: .gate.low, high: .gate.high}' report.json
```

Flag cases whose interval cannot rule out a pass rate below 80% (for a
CI gate on this, use `--prob-min-rate=0.8` instead):

```bash
jq '.rows[] | select(.ci.low < 0.8) | {case, low: .ci.low, high: .ci.high}' report.json
```

Post the plain-language reading of every gate that did not pass, for
example as a PR comment (needs `--prob-explain`):

```bash
jq -r '.rows[] | select(.gate and .gate.verdict != "pass")
    | "\(.case): \(.gate.verdict | ascii_upcase)\n\(.gate.explanation)\n"' report.json
```

List each case's p95 with its interval, and the latency gates that did
not pass:

```bash
jq '.rows[] | {case, p: .latency.quantile, est: .latency.estimate,
    low: .latency.ci.low, high: .latency.ci.high}' report.json
jq '.rows[] | select(.latency.verdict and .latency.verdict != "pass")
    | {case, verdict: .latency.verdict, max: .latency.max_latency}' report.json
```

List each function's ρ — high means add inputs rather than runs:

```bash
jq '.aggregates[] | select(.icc != null) | {name, icc, width_factor}' report.json
```

Print each function's pass^3 next to its pass rate (needs
`--prob-metric=pass^3`):

```bash
jq '.aggregates[] | {name, pass_rate: .estimate, "pass^3": .metrics["pass^3"].estimate,
    left_out: .metrics["pass^3"].left_out}' report.json
```

Print each function's average and interval, with the cross-check:

```bash
jq '.aggregates[] | select(.ci)
    | {name, inputs, estimate, low: .ci.low, high: .ci.high, normal: .normal_ci}' report.json
```

List each comparison with its interval and p-value:

```bash
jq '.comparisons[] | {function, arm, baseline, pairs, difference,
    low: .ci.low, high: .ci.high, p: .p_adjusted, verdict}' report.json
```

Pull the flaky rows with `jq`:

```bash
jq '.rows[] | select(.status == "flaky") | {case, pass_rate}' report.json
```

To fail CI only below a pass-rate floor, instead of on any flaky run,
use a gate: `--prob-min-rate=0.8` (or `@pytest.mark.probability(...)`
per case) judges each case's interval against the bar and sets the
exit status itself — see {doc}`running`. A floor on the pooled overall
rate is still a `jq` one-liner (pair it with `continue-on-error` on an
ungated pytest step, since a flaky run exits nonzero there):

```bash
jq -e '.totals.pass_rate >= 80' report.json > /dev/null \
  || { echo "pass rate below 80%"; exit 1; }
```

To compare a run with an earlier report, use `--prob-baseline` rather
than diffing the files: it pairs the cases by id and puts an interval
on each function's change, so noise isn't mistaken for a regression
(see Baseline in {doc}`reference`). List what it found:

```bash
jq '.baseline.comparisons[] | {function, pairs, difference,
    low: .ci.low, high: .ci.high, verdict}' report.json
jq '.baseline | {only_current, only_baseline}' report.json
```

Gate pull requests on main's report from GitHub Actions: each push to
main saves its report to the cache, and a pull request restores the
latest one and runs against it (the run id in the key makes every
main run save a new entry; `restore-keys` picks the newest):

```yaml
- uses: actions/cache/restore@v4
  if: github.event_name == 'pull_request'
  with:
    path: main.json
    key: probability-main-${{ github.run_id }}
    restore-keys: probability-main-
- name: Benchmarks
  run: |
    if [ -f main.json ]; then
      baseline="--prob-baseline=main.json --prob-margin=0.05"
    fi
    pytest benchmarks/ --prob-runs=10 --prob-json=report.json $baseline
- if: github.ref == 'refs/heads/main' && always()
  run: cp report.json main.json
- uses: actions/cache/save@v4
  if: github.ref == 'refs/heads/main' && always()
  with:
    path: main.json
    key: probability-main-${{ github.run_id }}
```

On the first pull request, before main has saved a report, the step
runs without a baseline.

Archive the report from GitHub Actions:

```yaml
- run: pytest benchmarks/ --prob-runs=10 --prob-json=report.json
  continue-on-error: true
- uses: actions/upload-artifact@v4
  with:
    name: probability-report
    path: report.json
```
