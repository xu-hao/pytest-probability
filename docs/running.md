# Running suites

## Repeat counts

`--prob-runs=N` multiplies every case (and every parametrized
combination) into N items:

```bash
pytest benchmarks/ --prob-runs=10
```

Set a project default in your pytest configuration and override it from
the command line when needed:

```ini
# pytest.ini
[pytest]
prob_runs = 10
```

```toml
# pyproject.toml
[tool.pytest.ini_options]
prob_runs = "10"
```

With `N == 1`, item names are just the case id (`identify_pii`);
with `N > 1`, a run suffix is appended (`identify_pii[run3]`). Items
of an unparametrized benchmark are named `run1`…`runN` directly.

How many runs do you need? A rough rule: with 10 runs, the fraction is
only resolved to ±10 percentage points — enough to separate "solid"
from "coin flip", not enough to detect a 5% regression. Scale N to the
effect size you care about, and remember every run costs real tokens if
the code under test calls a paid API.

## Execution order

Default order is **case-major**: all runs of a case complete
before the next case starts.

```text
alpha[run1]  alpha[run2]  beta[run1]  beta[run2]
```

`--prob-transpose` (or `prob_transpose = true` in ini) switches to
**run-major**: run 1 of everything, then run 2, and so on.

```text
alpha[run1]  beta[run1]  alpha[run2]  beta[run2]
```

Transposing spreads each case's samples over time, which matters
when consecutive runs would hit the same transient state — a warm
cache, a rate-limit window, a provider hiccup — and correlate your
samples. Independent samples make the fraction meaningful.

## Throttling

`--prob-delay=SECONDS` (or `prob_delay` in ini) sleeps between case
executions — the standard fix for hitting a rate-limited API hundreds
of times in a row:

```bash
pytest benchmarks/ --prob-runs=50 --prob-delay=1.5
```

The delay applies *between* executions, never before the first, and
only to benchmark items — your ordinary tests in the same session are
not throttled.

## Selecting what runs

Every run is a pytest item with a predictable id
(`bench_file.py::bench_fn::case-id[runN]`), so pytest's whole selection
toolbox applies — the plugin adds no filters of its own:

```bash
pytest benchmarks/ -k identify_pii            # one case, all runs
pytest benchmarks/ -k bench_extract           # one benchmark function
pytest benchmarks/ -k "run1"                  # first run of everything
pytest benchmarks/ -k "large and not de"      # expression over combo ids
pytest benchmarks/ -m "fast and not eu"       # marks on cases/functions
pytest benchmarks/bench_extract.py            # one file
pytest "benchmarks/bench_extract.py::bench_extract::identify_pii[run3]"
```

`-k` and `-m` deselect items after collection; the summary table only
aggregates runs that actually executed. See {doc}`parametrization` for
how to mark cases.

Two flags deserve a caveat in fraction-land:

- `-x` / `--maxfail` stop the session at the first failing *run*, so
  fractions will be computed from a truncated sample.
- `--lf` reruns only previously-failed runs; the resulting fractions
  cover just those items. Both are occasionally useful for debugging a
  specific failing run — just don't read the summary of a partial
  session as a probability estimate.

## Parallelism with pytest-xdist

Because each run is an independent item, distribution is free:

```bash
pip install pytest-xdist
pytest benchmarks/ --prob-runs=20 -n 8
```

The plugin was built xdist-aware:

- Result aggregation is reconstructed from test reports, which xdist
  serializes from workers back to the controller — the summary table
  and the JSON report are complete regardless of how items were
  distributed.
- The JSON report is written by the controller only; workers never
  write partial files.
- `--prob-delay` throttles per worker (each worker sleeps between its
  own executions).
- Execution ordering (including `--prob-transpose`) is advisory under
  xdist: the scheduler assigns items to workers as they free up.
- `setup()`/`teardown()` run once per file *per worker that executes
  items from that file* — the same semantics xdist gives module-scoped
  fixtures. Keep them idempotent.

## Exit status

The session fails (exit code 1) if any run fails **or errors** — a
flaky case, and a case with harness trouble, both fail CI by design.
If you want a CI gate softer than "perfection" (say, alert only below
80%), keep the session green by policy-checking the
{doc}`JSON report <json-report>` in a follow-up step instead.
