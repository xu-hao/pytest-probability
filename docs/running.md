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
  session as a probability estimate. A failed gate is the exception:
  its [gate item](#gate-items) failed, and `--lf`
  reruns it with all of its case's runs, so the verdict is judged on
  a full sample again.

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
- `--prob-plan` runs nothing, so it turns `-n` off and plans in one
  process; its output is the same with and without `-n`.
- Gate items judge under `--dist loadgroup` only: the plugin puts each
  gated case's runs and its gate item in an `xdist_group` of their own
  (`…::never[gate]@classify::never`), so the worker that ran the runs
  judges them. With another `--dist` a worker may hold only some of a
  case's runs, so gate items skip (`-rs` says why) and the verdicts are
  judged at session end, as without them; the exit status is the same.

  ```bash
  pytest benchmarks/ --prob-runs=40 -n 8 --dist loadgroup
  ```
- `--prob-stop` (`curtail` or `sequential`) needs `--dist loadgroup`:
  a case can only stop early in the process that runs all of its runs,
  so the plugin puts each stoppable case in an `xdist_group` of its
  own (xdist appends it to the node id:
  `…::never[run3]@classify::never`). With another `--dist` it warns
  (`CurtailmentWarning`) and every run runs — under `sequential`,
  judged by the usual fixed-run intervals, since nothing stopped. The
  controller rebuilds the stops from the reports, so the output
  matches a serial run.

  ```bash
  pytest benchmarks/ --prob-runs=40 --prob-stop=curtail -n 8 --dist loadgroup
  ```
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
=================== 2 failed, 81 passed, 40 xfailed in 2.91s ===================
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
the full exit-status table. {doc}`statistics` explains how to read the
verdicts and when UNDECIDED is the right answer.

A comparison with a margin (`@pytest.mark.probability(compare="style",
margin=0.02)`) works the same way for a whole function: its failing
runs are xfailed, and the margin's verdict on each arm decides — see
Comparisons in {doc}`reference`. Without a margin a comparison only
reports.

### Gate items

The `2 failed` above are the two failing verdicts. Each gated case gets
one more item after its last run, its *gate item*, which passes or
fails with the verdict:

```text
$ pytest benchmarks/ --prob-runs=40 -v
...
bench_classify.py::bench_classify::never[run40] XFAIL (probability gate: wrong...)
bench_classify.py::bench_classify::never[gate] FAILED
...
____________________________ gate: classify::never _____________________________
classify::never  3/40  [2%, 20%]  ≥90%  FAIL
3 of 40 runs passed. The true pass rate is probably between 2% and 20% (95% confidence). Your bar is 90%, and that whole range is below it, so this case falls short of the bar.
Next: look at the failing runs: -rx lists them with their assert messages, and --xfail-tb shows their tracebacks. If a lower pass rate is acceptable for this case, lower the bar.
```

- **JUnit XML** (`--junitxml`) records it as a failed testcase,
  `never[gate]`, with the gates block's line as its message.
- **`--lf`** reruns a failed gate item together with its case's runs,
  so the verdict is judged again on all of them; once it passes, there
  is nothing left to rerun.
- **`-k gate`** runs only the gated cases (a gate item brings its runs
  with it); `-k "not gate"` leaves gate items out, and the verdicts
  still decide the exit status at session end.
- A comparison or baseline margin gets the same, per function:
  `bench_triage[compare:few_shot]`, `bench_triage[baseline]`.
- `--prob-no-gate-items` (or `prob_gate_items = false`) leaves them
  out; then a failed gate fails only the exit status.

See Gate items in {doc}`reference` for the id format and details.

## Stopping early

Runs cost money, and a gated case often settles long before its last
one: once `never` above has 9 failures, no 31 remaining runs could
lift it over 90%. `--prob-stop=curtail` (or `prob_stop = curtail`)
skips a gated case's remaining runs as soon as its verdict can no
longer change, whatever they would do:

```bash
pytest benchmarks/ --prob-runs=40 --prob-stop=curtail
```

```text
================================= probability ==================================
  classify::is_question   40/40  [91%, 100%]
  classify::identify_pii  37/38  [86%,  99%]  FLAKY  decided after 38/40
  classify::never          3/12  [ 5%,  57%]  FLAKY  decided after 12/40

  Overall: 80/90 passed (89%)
  Gates:   1 passed, 1 failed, 1 undecided
  Stopped: 2 cases early, saving 30 of 120 runs (25%)
============================== probability: gates ==============================
  classify::identify_pii  37/38  [86%, 99%]  ≥90%  UNDECIDED  decided after 38/40
  classify::never          3/12  [ 5%, 57%]  ≥90%  FAIL  decided after 12/40

  Run with --prob-explain for a plain-language reading.
============= 2 failed, 81 passed, 30 skipped, 10 xfailed in 0.88s =============
```

- **The verdicts are exact:** the same as running every run, so the
  exit status is too. A case stops only when every way its remaining
  runs could go — pass, fail, error or skip — gives the same verdict.
- The skipped runs are pytest skips (`-rs` shows `probability gate:
  decided after 12/40 runs (FAIL)`), not samples, and the `Stopped:`
  line counts the runs and, when cost was recorded, the cost saved.
- A stopped case's fraction comes from fewer runs and leans toward its
  verdict (3/12 above, against 3/40 for all the runs), so
  function-level, metric, comparison and baseline intervals over a
  stopped case are hidden, with a note.
- Ungated cases always run every run, and so do gated cases whose runs
  another verdict needs: a comparison `margin=`, `--prob-margin`
  against a baseline, or a latency gate.
- Under pytest-xdist it needs `--dist loadgroup` (below).

### Stopping as soon as a case is clear

Curtailment waits until no remaining run could change the verdict,
which for a case that always passes is close to its last run.
`--prob-stop=sequential` (or `prob_stop = sequential`) stops a
pass-rate gate as soon as the case is clearly above or below its bar,
judged on an interval built for exactly that: one that stays valid
however often it is checked. The run count becomes the most a case may
use, so give it room:

```bash
pytest benchmarks/ --prob-stop=sequential     # classify: min_rate=0.9, runs=100
```

```text
================================= probability ==================================
  classify::solid  53/53  [90%, 100%] seq  $0.0530  decided after 53/100
  classify::close  79/83  [83%,  99%] seq  $0.0830  FLAKY  decided after 83/100
  classify::weak   45/60  [55%,  90%] seq  $0.0600  FLAKY  decided after 60/100
  smoke::steady    19/19  [82%, 100%]      $0.0190  decided after 19/20
  smoke::broken      2/4  [ 7%,  93%]      $0.0040  FLAKY  decided after 4/20

  Overall: 198/219 passed (90%)
  Gates:   2 passed, 2 failed, 1 undecided
  Stopped: 5 cases early, saving 121 of 340 runs (36%) and about $0.1210
  Cost:    $0.2190
============================== probability: gates ==============================
  classify::close  79/83  [83%, 99%] seq  ≥90%        UNDECIDED  decided after 83/100
  classify::weak   45/60  [55%, 90%] seq  ≥90%        FAIL  decided after 60/100
  smoke::broken      2/4  [ 7%, 93%]      ≥19 passes  FAIL  decided after 4/20

  Run with --prob-explain for a plain-language reading.
============ 3 failed, 200 passed, 121 skipped, 21 xfailed in 0.18s ============
```

- `seq` marks the interval a case was judged on: a *confidence
  sequence*. An ordinary interval checked after every run would be
  fooled by luck far more often than its level says (a 95% one leaves
  out the true rate at some point in 2,000 runs nearly half the time);
  this one, at most 5% of the time, however often it is checked.
- It is wider than an ordinary interval (1.6 times at 100 runs), so a
  90% bar takes 53 straight passes instead of 36. Cases far from their
  bar stop much sooner — `solid` above, 53 runs against 96 under
  curtailment — and cases near it take longer, or end UNDECIDED.
  `--prob-plan --prob-stop=sequential` shows the chance of passing
  within the budget and the runs a case would use on average.
- A case that reaches its budget is judged on `seq` too: UNDECIDED if it
  still straddles the bar.
- Count gates (`min_passes`) are curtailed, as above. Everything else
  — skips, hidden averages, cases that never stop, xdist — works as for
  curtailment.

See Early stopping and Sequential stopping in {doc}`reference` for the
details, and {doc}`statistics` for what `seq` promises.

## Budgeting runs before running them

How many runs does a gate need, and what will they cost? `--prob-plan`
answers without running anything: it collects, prints one row per
selected case, and exits 0, like `--collect-only`.

```bash
pytest benchmarks/ --prob-runs=40 --prob-plan
pytest benchmarks/ --prob-runs=40 --prob-plan --prob-plan-report=last.json
pytest benchmarks/ --prob-plan -k classify --prob-plan-assume=0.95
```

```text
============================== probability: plan ===============================
  case               runs  min runs  runs for 80%  chance now  catch 10%  catch 1%
  classify::refund     40        36           100         30%        99%       33%
  smoke                20        19            20         88%        88%       18%
```

For each gated case: the fewest runs with which the gate can pass at
all (`min runs`), the runs that give it an 80% chance to pass if the
case really passes 97% of its runs (`--prob-plan-assume`), and that
chance with the planned runs. For every case: the chance the planned
runs catch a flake that fails 10% or 1% of runs (`--prob-plan-flake`
sets the rates; 29 and 299 runs make it 95%). With
`--prob-plan-report=last.json`, a previous `--prob-json` report, it
also projects each case's cost. A one-line note under the table
explains every column; {doc}`reference` has the details.
