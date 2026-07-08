# Changelog

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
