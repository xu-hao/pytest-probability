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
      "label": "triage",
      "passes": 8,
      "fails": 2,
      "errors": 0,
      "total": 10,
      "pass_rate": 80.0,
      "status": "flaky",
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
      "steps": [
        {
          "label": "triage",
          "passed": false,
          "elapsed": 0.0101,
          "error": null,
          "message": null,
          "cost": 0.0001,
          "usage": [
            {"model": "m-small", "input_tokens": 120, "output_tokens": 8,
             "cached_input_tokens": 64, "cost": 0.0001}
          ]
        }
      ]
    }
  ]
}
```

Top level:

| Field | Type | Meaning |
|---|---|---|
| `created` | `str` | Local timestamp, `YYYY-MM-DDTHH:MM:SS` |
| `runs` | `int` | The configured `--prob-runs` value |
| `exit_status` | `int` | pytest's exit code for the session |
| `totals` | object | Aggregates over every row: `passes`, `fails`, `errors`, `count`, `pass_rate`, `cost`, `usage` |
| `rows` | array | One entry per `(case, label)` pair, in encounter order |
| `records` | array | One entry per executed run, with its raw steps |

`rows[]` — the aggregate view, mirroring the terminal table:

| Field | Type | Meaning |
|---|---|---|
| `case` | `str` | Function-qualified case id (`classify::identify_pii`), including any `[combo]` suffix |
| `label` | `str` | Step label |
| `passes` / `fails` / `errors` | `int` | Run counts, one per outcome class |
| `total` | `int` | All three counts summed |
| `pass_rate` | `float` | `passes / total * 100` |
| `status` | `str` | `"pass"`, `"flaky"`, `"errored"`, `"fail"`, or `"error"` — see the status table in {doc}`reference` |
| `cost` | `float` | Summed step cost across runs |
| `usage` | object | Per-model token aggregate: `{model: {input_tokens, output_tokens, cached_input_tokens, cost}}` |

`records[]` — the raw view, one per (case, run) execution:

| Field | Type | Meaning |
|---|---|---|
| `case` | `str` | Case id (matches `rows[].case`) |
| `run` | `int` | 1-based run number |
| `steps` | array | Steps in yield order: `label`, `passed`, `elapsed`, `error`, `message`, `cost`, `usage` (raw per-model entries) |

Cases deselected with `-k`/`-m`, and runs that never
executed (e.g. after `-x`), are absent — the report describes what
actually ran.

## Recipes

Pull the flaky rows with `jq`:

```bash
jq '.rows[] | select(.status == "flaky") | {case, pass_rate}' report.json
```

Fail CI only below a pass-rate floor, instead of on any flaky run
(pair this with `continue-on-error` on the pytest step, since a flaky
run still exits nonzero):

```bash
jq -e '.totals.pass_rate >= 80' report.json > /dev/null \
  || { echo "pass rate below 80%"; exit 1; }
```

Compare two reports case-by-case:

```bash
jq -n --slurpfile a old.json --slurpfile b new.json '
  [$a[0].rows[] as $r
   | ($b[0].rows[] | select(.case == $r.case and .label == $r.label)) as $n
   | select($n.pass_rate < $r.pass_rate)
   | {case: $r.case, label: $r.label, was: $r.pass_rate, now: $n.pass_rate}]'
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
