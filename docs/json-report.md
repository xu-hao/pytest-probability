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
  "stats_config": {"method": "exact", "level": 0.95, "prior": [1.0, 1.0]},
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
      "cost": 0.001,
      "usage": {
        "m-small": {"input_tokens": 1200, "output_tokens": 80,
                    "cached_input_tokens": 640, "cost": 0.001}
      }
    }
  ],
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
| `stats_config` | object | The session's statistical settings: `method` (`"exact"`, `"wilson"` or `"bayes"`), `level` (two-sided, e.g. `0.95`), `prior` (`[a, b]`, the configured `prob_prior`; used only by `bayes`) |
| `exit_status` | `int` | The session's exit code, including a failure from a gate |
| `totals` | object | Aggregates over every row: `passes`, `fails`, `errors`, `count`, `pass_rate`, `cost`, `usage` |
| `rows` | array | One entry per case, in encounter order |
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
| `cost` | `float` | Summed run cost across runs |
| `usage` | object | Per-model token aggregate: `{model: {input_tokens, output_tokens, cached_input_tokens, cost}}` |
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

`records[]` — the raw view, one per (case, run) execution:

| Field | Type | Meaning |
|---|---|---|
| `case` | `str` | Case id (matches `rows[].case`) |
| `run` | `int` | 1-based run number |
| `outcome` | `str` | `"pass"`, `"fail"`, or `"error"` — the run's class (a gated case's failing runs are xfailed in pytest's output, but stay `"fail"` here) |
| `message` | `str \| null` | First line of the assert's message on failure |
| `error` | `str \| null` | `"ExceptionType: text"` on error |
| `elapsed` | `float` | Wall-clock seconds, measured by the plugin |
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

Compare two reports case-by-case:

```bash
jq -n --slurpfile a old.json --slurpfile b new.json '
  [$a[0].rows[] as $r
   | ($b[0].rows[] | select(.case == $r.case)) as $n
   | select($n.pass_rate < $r.pass_rate)
   | {case: $r.case, was: $r.pass_rate, now: $n.pass_rate}]'
```

Archive the report from GitHub Actions:

```yaml
- run: pytest benchmarks/ --prob-runs=10 --prob-json=report.json
  continue-on-error: true
- uses: actions/upload-artifact@v4
  with:
    name: probability-report
    path: report.json
```
