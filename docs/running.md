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
  fractions will be computed from a truncated sample. In a
  [gated](#exit-status-and-gates) case, failing runs are xfailed and
  don't stop the session; errors still do, unless `prob_errors =
  exclude`.
- `--lf` reruns only previously-failed runs; the resulting fractions
  cover just those items. Both are occasionally useful for debugging a
  specific failing run — just don't read the summary of a partial
  session as a probability estimate. A failed gate leaves no failed
  item behind, so `--lf` doesn't see it.

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
- Gate verdicts are decided on the controller from the aggregated
  counts — each run's record carries its case's gate — so they are the
  same with and without `-n`. Comparisons likewise: each record
  carries its case's input and arm, and the controller pairs them.
- Collection-time warnings, such as the gate feasibility warning, are
  raised by every worker, so they show up once per worker.
- `setup()`/`teardown()` run once per file *per worker that executes
  items from that file* — the same semantics xdist gives module-scoped
  fixtures. Keep them idempotent.

## Exit status and gates

Without gates, the session fails (exit code 1) if any run fails **or
errors** — a flaky case, and a case with harness trouble, both fail CI
by design.

For a bar softer than "every run passes", gate the case on its pass
rate instead:

```python
@pytest.mark.probability(min_rate=0.9)
@pytest.mark.parametrize("text,expected", CASES)
def bench_classify(text, expected):
    assert my_classifier(text) == expected
```

```bash
pytest benchmarks/ --prob-runs=40                     # gates from marks
pytest benchmarks/ --prob-runs=40 --prob-min-rate=0.8 # or every case
```

A gated case passes when its whole interval lies above the bar, fails
when it lies entirely below, and is UNDECIDED in between. Its failing
runs are reported as xfailed, so `-x` doesn't stop on them and only the
verdict decides the exit status:

```text
================================= probability ==================================
  classify::is_question   40/40  [91%, 100%]
  classify::identify_pii  37/40  [80%,  98%]  FLAKY
  classify::never          3/40  [ 2%,  20%]  FLAKY

  Overall: 80/120 passed (67%)
  Gates:   1 passed, 1 failed, 1 undecided
============================== probability: gates ==============================
  classify::identify_pii  37/40  [80%, 98%]  ≥90%  UNDECIDED
  classify::never          3/40  [ 2%, 20%]  ≥90%  FAIL
======================== 80 passed, 40 xfailed in 2.91s ========================
```

That session exits 1: `never` failed its gate, and `identify_pii`
doesn't have enough runs to tell (37/40 is 92.5%, but the interval
reaches down to 80%). UNDECIDED fails by default;
`--prob-undecided=pass` lets it through. Run more to decide it —
the collection-time `InfeasibleGateWarning` tells you when a gate can't
pass at all with the runs it has (`min_rate=0.9` needs at least 36).

Errored runs still fail the session; set `prob_errors = exclude` to
take them out of the gate's sample instead. Ungated cases in the same
session behave exactly as before. See {doc}`reference` for the marker,
the count rule (`min_passes=`), per-gate `confidence=`/`method=`, and
the full exit-status table.

A comparison with a margin (`@pytest.mark.probability(compare="style",
margin=0.02)`) works the same way for a whole function: its failing
runs are xfailed, and the margin's verdict on each arm decides — see
Comparisons in {doc}`reference`. Without a margin a comparison only
reports.
