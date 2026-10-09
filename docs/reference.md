# Reference

This page lists every option and output. For what the statistics mean,
what they assume and how to decide with them, see the
{doc}`statistics guide <statistics>`.

## Command-line options

All options live in the `probability` group of `pytest --help`.

`--prob-runs=N`
: Run every case N times. Values below 1 are clamped to 1.
  **Default:** the `prob_runs` ini value, else `1`.

`--prob-json=PATH`
: Write the {doc}`JSON report <json-report>` to PATH at session end.
  Parent directories are created. Under pytest-xdist only the
  controller writes.
  **Default:** no report.

`--prob-delay=SECONDS`
: Sleep between case executions; never before the first. Applies per
  process (per worker under xdist) and only to benchmark items.
  **Default:** the `prob_delay` ini value, else `0`.

`--prob-transpose`
: Collect in run-major order — run 1 of every case, then run 2, … —
  instead of the case-major default. Ordering is advisory under xdist.
  **Default:** the `prob_transpose` ini value, else off.

`--prob-method={exact,wilson,bayes}`
: How the per-row interval on the pass probability is computed:
  `exact` (Clopper-Pearson; never under-covers), `wilson` (Wilson
  score; narrower, close to nominal on average), or `bayes` (an
  equal-tailed credible interval under the Beta prior `prob_prior`).
  Any other value is a usage error.
  **Default:** the `prob_method` ini value, else `exact`.

`--prob-confidence=LEVEL`
: Two-sided confidence (or credible) level for intervals, strictly
  between 0 and 1 — `0.95`, not `95`. One level drives every interval
  the plugin prints.
  **Default:** the `prob_confidence` ini value, else `0.95`.

`--prob-no-intervals`
: Hide the interval column in the terminal summary. The JSON report
  still carries `rows[].ci`. The gates block always shows its
  intervals.
  **Default:** the `prob_intervals` ini value, else shown.

`--prob-min-rate=RATE`
: Gate every benchmark case on its pass rate: see [Gates](#gates).
  Strictly between 0 and 1. A `probability` mark that sets `min_rate`
  or `min_passes` takes precedence for its cases.
  **Default:** the `prob_min_rate` ini value, else no global gate.

`--prob-undecided={fail,pass}`
: Whether an UNDECIDED gate verdict fails the session. `pass` still
  lists the case in the gates block.
  **Default:** the `prob_undecided` ini value, else `fail`.

`--prob-explain`
: Add a [plain-language reading](#the-explain-section) of the results
  after the summary, and an `explanation` string to the JSON report's
  rows, gates, aggregates, metrics and comparisons.
  **Default:** the `prob_explain` ini value, else off.

`--prob-bootstrap=N`
: How many times the bootstrap behind
  [function-level intervals](#function-level-intervals) re-draws the
  inputs. A positive integer; more resamples make the bounds less
  sensitive to the seed, at a cost of about 0.3 s per 5,000 resamples
  of 1,000 cases.
  **Default:** the `prob_bootstrap` ini value, else `5000`.

`--prob-seed=SEED`
: Seed for every resampling procedure, so the same results always
  print the same intervals. Any integer.
  **Default:** the `prob_seed` ini value, else `0`.

`--prob-compare=AXIS`
: [Compare](#comparisons) the values of the parametrize argument AXIS
  in every bench function that has one, each against the first value.
  Functions without it are not compared. A `probability(compare=...)`
  mark takes precedence for its function.
  **Default:** the `prob_compare` ini value, else no comparison.

`--prob-adjust={none,holm,bonferroni,bh}`
: Adjust the p-values of the session's comparisons for how many there
  are: Holm, Bonferroni, or Benjamini-Hochberg. `none` leaves them as
  they are and labels them exploratory when there is more than one.
  **Default:** the `prob_adjust` ini value, else `none`.

`--prob-metric=METRIC`
: Also report a [metric](#metrics) for each case, each function and
  the session: `pass^K`, the chance that K runs of an input all pass
  (reliability), or `pass@K`, the chance that at least one of K does
  (best-of-K). K is a whole number of at least 1. Comma-separated and
  repeatable — `--prob-metric=pass^3,pass@5` is `--prob-metric=pass^3
  --prob-metric=pass@5` — with duplicates dropped; any other spelling
  is a usage error. Given on the command line, it replaces
  `prob_metric`. `^` needs quoting in some shells: `'pass^3'` in zsh
  with `extendedglob`, `"pass^3"` in Windows `cmd`; bash, plain zsh,
  fish and PowerShell take it as is.
  **Default:** the `prob_metric` ini value, else none.

`--prob-plan`
: Collect, print a [run budget](#planning) for every selected case and
  exit without running anything — like `--collect-only`, exit code 0.
  Respects `-k`/`-m`; ignores `-n`; never writes `--prob-json`.

`--prob-plan-assume=RATE`
: The true pass rate `--prob-plan` plans for, strictly between 0 and 1.
  **Default:** `0.97`.

`--prob-plan-flake=RATES`
: Comma-separated failure rates `--prob-plan` plans to catch, each
  strictly between 0 and 1; one `catch` column each.
  **Default:** `0.1,0.01`.

`--prob-plan-report=PATH`
: A previous `--prob-json` report: `--prob-plan` reads each case's cost
  per run from it (its row's `cost` over `total`) and adds a projected
  cost column.

`--prob-baseline=PATH`
: Compare every bench function with an earlier `--prob-json` report:
  see [Baseline](#baseline). The file must exist and hold a report's
  `rows[]` (0.2.0 reports work); otherwise it is a usage error.
  **Default:** no baseline.

`--prob-margin=MARGIN`
: With `--prob-baseline`, turn the comparison into a regression gate:
  a function fails when its pass rate may have dropped by more than
  MARGIN (`0.02` is 2 points). At least 0 and below 1; a usage error
  without `--prob-baseline`.
  **Default:** none — the baseline comparison only reports.

`--prob-latency`
: Add a `probability: latency` block: every case's latency quantile
  (`prob_latency_quantile`, or the mark's `latency_quantile`) with its
  interval, and the verdict of a [latency gate](#latency). A flag, not
  `--prob-latency=0.99`, so it can't swallow a path that follows it;
  choose the quantile with `prob_latency_quantile` or the mark. Latency
  gates are judged, and listed in the gates block, without it.
  **Default:** the `prob_latency` ini value, else off.

`--prob-stop={off,curtail,sequential}`
: `curtail` skips a gated case's remaining runs as soon as its verdict
  can no longer change; the verdicts are the same as running every run.
  `sequential` judges each rate gate that can stop on an anytime-valid
  interval instead, and stops the case as soon as that interval is
  clear of the bar, so `runs` becomes the most a case may use: see
  [Early stopping](#early-stopping) and
  [Sequential stopping](#sequential-stopping). Under pytest-xdist both
  need `--dist loadgroup`; otherwise it warns and every run runs.
  **Default:** the `prob_stop` ini value, else `off`.

`--prob-no-gate-items`
: Don't collect [gate items](#gate-items): the `…::<case-id>[gate]`
  item after each gated case's runs, and the `[compare:ARM]` and
  `[baseline]` items after a function with a margin. Verdicts are then
  judged at session end only, as in the gates block and the exit
  status.
  **Default:** the `prob_gate_items` ini value, else collected.

Case selection has no plugin-specific options: use pytest's `-k`
(ids), `-m` (marks), and node ids.

## Ini options

Set these in `pytest.ini`, `pyproject.toml` (`[tool.pytest.ini_options]`),
`setup.cfg`, or `tox.ini`, exactly like any pytest ini option.

`prob_runs` *(string, default `"1"`)*
: Default repeat count; overridden by `--prob-runs`.

`prob_pattern` *(string, default `"bench_*.py"`)*
: `fnmatch` glob deciding which files the plugin collects. Matched
  against the **file name only**, not the path. Files matching
  `python_files` (`test_*.py`) stay with pytest's own collector, so
  keep the two patterns disjoint.

`prob_delay` *(string, default `"0"`)*
: Default seconds between executions; overridden by `--prob-delay`.

`prob_transpose` *(bool, default `false`)*
: Default execution order; overridden by `--prob-transpose`.

`prob_method` *(string, default `"exact"`)*
: Default interval method; overridden by `--prob-method`.

`prob_confidence` *(string, default `"0.95"`)*
: Default confidence level; overridden by `--prob-confidence`.

`prob_prior` *(string, default `"1,1"`)*
: The Beta(a, b) prior for `bayes`, as `a,b` with both positive:
  `1,1` is uniform, `0.5,0.5` is Jeffreys. Ignored by the other
  methods. Ini only.

`prob_intervals` *(bool, default `true`)*
: Show the interval column; `--prob-no-intervals` turns it off.

`prob_min_rate` *(string, default empty)*
: A global gate for every case; overridden by `--prob-min-rate`.
  Empty means no global gate.

`prob_undecided` *(string, default `"fail"`)*
: `fail` or `pass`; overridden by `--prob-undecided`.

`prob_errors` *(string, default `"count"`)*
: How errored runs of a gated case are treated: `count` or `exclude`.
  See [Errors in gated cases](#errors-in-gated-cases). Ini only; use
  `-o prob_errors=exclude` for a one-off.

`prob_explain` *(bool, default `false`)*
: Default for `--prob-explain`.

`prob_bootstrap` *(string, default `"5000"`)*
: Default for `--prob-bootstrap`.

`prob_seed` *(string, default `"0"`)*
: Default for `--prob-seed`.

`prob_min_inputs` *(string, default `"10"`)*
: The fewest cases a function (or the session, for Overall) needs
  before its [function-level interval](#function-level-intervals) is
  shown, and the fewest paired inputs a [comparison](#comparisons)
  needs for its bootstrap interval. At least 2. Ini only; use
  `-o prob_min_inputs=20` for a one-off.

`prob_compare` *(string, default empty)*
: Default for `--prob-compare`. Empty means no comparison.

`prob_adjust` *(string, default `"none"`)*
: Default for `--prob-adjust`.

`prob_metric` *(string, default empty)*
: Default for `--prob-metric`: metric names separated by commas,
  spaces or new lines (`prob_metric = pass^3, pass@5`). Empty means
  none.

`prob_latency` *(bool, default `false`)*
: Default for `--prob-latency`.

`prob_latency_quantile` *(string, default `"0.95"`)*
: The latency quantile reported for cases whose mark sets no
  `latency_quantile`, strictly between 0 and 1 — `0.99`, not `99`.
  Ini only; use `-o prob_latency_quantile=0.99` for a one-off.

`prob_stop` *(string, default `"off"`)*
: Default for `--prob-stop`: `off`, `curtail` or `sequential`.

`prob_gate_items` *(bool, default `true`)*
: Collect [gate items](#gate-items); `false` (or `--prob-no-gate-items`)
  leaves them out.

Invalid values for the statistical, bootstrap and gate options are
reported as pytest usage errors before anything runs.

## The `probability` marker

```python
@pytest.mark.probability(min_rate=0.9)                               # exact, at prob_confidence
@pytest.mark.probability(min_rate=0.9, method="bayes", prior=(1, 1))
@pytest.mark.probability(min_passes=19, runs=20)                     # count rule
@pytest.mark.probability(runs=40)                                    # run count only, no gate
@pytest.mark.probability(compare="style", baseline="terse")          # compare the arms of an axis
@pytest.mark.probability(compare="style", margin=0.02)               # non-inferiority gate
@pytest.mark.probability(compare="style", margin=0.02, equivalence=True)
@pytest.mark.probability(max_latency=2.0)                            # p95 of run time under 2s
@pytest.mark.probability(latency_quantile=0.99, max_latency=5.0)     # p99 under 5s
```

| Argument | Meaning |
|---|---|
| `min_rate` | Gate on the pass rate: the case's interval must lie above it. Strictly between 0 and 1. |
| `min_passes` | Gate on a count instead: the case passes when passes ≥ `min_passes`. A positive integer. |
| `runs` | This function's (or case's) run count. A positive integer. |
| `confidence` | The level of this gate's interval, instead of `prob_confidence`. |
| `method` | `exact`, `wilson` or `bayes`, instead of `prob_method`. |
| `prior` | The Beta prior for `bayes`, as `(a, b)`, instead of `prob_prior`. |
| `compare` | A parametrize argument whose values (the *arms*) are [compared](#comparisons), each against the baseline. Instead of `--prob-compare`. |
| `baseline` | The arm the others are compared with, by its id. Default: the first value in the parametrize list. |
| `margin` | Make the comparison a gate: the arm may be at most `margin` worse than the baseline (non-inferiority). Strictly between 0 and 1: `0.02` is 2 percentage points. |
| `equivalence` | With `margin`: the arm must be within ±`margin` of the baseline instead. `True` or `False`. |
| `latency_quantile` | The quantile of the case's run times that is reported (and gated, with `max_latency`), instead of `prob_latency_quantile`. Strictly between 0 and 1: `0.95` is the 95th percentile. |
| `max_latency` | Gate on [latency](#latency): the interval for that quantile must lie below this many seconds. A positive number. |

- **Where it applies:** on a `bench_*` function, every case of it; on
  one case with `pytest.param(..., marks=pytest.mark.probability(...))`.
  A case's mark overrides the function's argument by argument, except
  that `min_rate` and `min_passes` are one setting: a case mark that
  names either replaces the function's rule.
- **One rule per mark:** `min_rate` and `min_passes` can't be used
  together.
- **Run count precedence:** `--prob-runs` beats `runs=`, which beats
  the `prob_runs` ini value.
- **Gated or not:** a case is gated when its mark sets `min_rate` or
  `min_passes`, or a global min rate is set. `runs=`, `confidence=`,
  `method=` and `prior=` alone don't create a gate. `max_latency` adds
  a separate latency gate; `latency_quantile` alone only chooses the
  quantile reported. `confidence=` sets the level of both gates'
  intervals; `method=` and `prior=` don't apply to latency.
- **Under `--prob-stop=sequential`** a rate gate whose case can stop is
  judged by the [confidence sequence](#sequential-stopping) at its
  `confidence=`; `method=` and `prior=` don't apply to it.
- **Comparison arguments are per function:** `compare`, `baseline`,
  `margin` and `equivalence` go on the bench function's mark, together
  (`baseline=` without `compare=` is an error), and are rejected on a
  `pytest.param` mark. `confidence=`, `method=` and `prior=` don't apply
  to comparisons, which use the session's level.
- **Validation:** the marker is registered, so `--strict-markers`
  accepts it. A bad argument is a collection error that names the
  case, e.g. `classify::identify_pii: invalid probability mark: min_rate
  must be strictly between 0 and 1, got 90 (did you mean 0.9?)`.

## Gates

A gate decides whether a case passes from its estimated pass
probability, instead of requiring every run to pass.

**The verdict rule** is the same for every method. The interval is the
gate's (its `method`, `confidence` and `prior`) over the gate's sample,
compared with unrounded bounds:

| Interval vs `min_rate` | Verdict |
|---|---|
| Entirely above | PASS |
| Entirely below | FAIL |
| Straddles it | UNDECIDED |

UNDECIDED means there isn't enough data to tell yet: run more. It
fails the session by default; `--prob-undecided=pass` /
`prob_undecided = pass` lets it through. Under the count rule
(`min_passes`) there is no UNDECIDED: the case passes when passes ≥
`min_passes`, and fails otherwise.

**How runs are reported.** In a gated case, a run that fails an
`assert` is a sample, not a verdict, so it is reported as *xfailed*:

- `-rx` lists each one with its assert message as the reason
  (`XFAIL …::identify_pii[run3] - probability gate: assert 'other' ==
  'pii'`), and pytest 8's `--xfail-tb` prints the kept traceback.
- `-x` / `--maxfail` don't stop on it.
- `--runxfail` turns this off: gated failing runs are reported as
  ordinary failures again. The gate is still judged.

The verdicts are decided after every run is in, from the aggregated
counts — in-process, or on the pytest-xdist controller, so they are the
same with and without `-n`.

### Errors in gated cases

`prob_errors` decides what an errored run (any exception other than
`AssertionError`) means for a gate:

- `count` (default): errors fail the session as they always do, and
  count as non-passes in the gate's sample.
- `exclude`: errors leave the gate's sample (`7/10` with 3 errors is
  judged as `7/7`) and are reported as xfailed, so they don't fail the
  session. The summary row shows `(3 errored, excluded)`. A case whose
  every run errored has no sample left and is UNDECIDED.

Ungated cases are never affected.

### Feasibility warning

At collection, a gate that couldn't pass even if every run passed
raises an `InfeasibleGateWarning`:

```text
InfeasibleGateWarning: classify::identify_pii: min_rate=0.9 at 95% needs ≥36 runs; this case has 10
```

Cases of one function that share a gate get one warning
(`classify (3 cases): … each has 10`). With the exact method at 95%,
`min_rate` needs at least:

| `min_rate` | 0.8 | 0.9 | 0.95 | 0.99 |
|---|---|---|---|---|
| Runs | 17 | 36 | 72 | 368 |

A count gate needs `min_passes` runs. The warning is an ordinary pytest
warning: filter it with
`-W ignore::pytest_probability.InfeasibleGateWarning`, or make it an
error with `-W error::…`.

### Gate items

Each gated case also gets one more item, after its last run: its *gate
item*, which passes or fails with the case's verdict. So the verdict
shows in `-v`, in JUnit XML (`--junitxml`) as a failed testcase, and to
`--lf` as a failure to rerun:

```text
bench_classify.py::bench_classify::is_question[gate] PASSED
bench_classify.py::bench_classify::identify_pii[gate] FAILED
bench_classify.py::bench_classify::never[gate] FAILED
```

- **Ids:** `<file>::<bench-function>::<case-id>[gate]`, and
  `<file>::<bench-function>::gate` for an unparametrized function (see
  [Item ids](#item-ids)). A case with a rate or count gate, a latency
  gate (`max_latency`), or both, gets one; ungated cases get none.
- **Order:** right after the case's last run, case-major or
  `--prob-transpose`. A plugin that reorders items (shuffling, say)
  can't put it before its runs: it is moved back after them, within
  its file.
- **Verdict:** judged when it runs, from the case's recorded runs, with
  the code that makes the gates block, so the two agree. UNDECIDED
  fails it unless `--prob-undecided=pass`. Its failure message is the
  gates block's line and the plain-language reading:

  ```text
  ____________________________ gate: classify::never _____________________________
  classify::never  3/40  [2%, 20%]  ≥90%  FAIL
  3 of 40 runs passed. The true pass rate is probably between 2% and 20% (95% confidence). Your bar is 90%, and that whole range is below it, so this case falls short of the bar.
  Next: look at the failing runs: -rx lists them with their assert messages, and --xfail-tb shows their tracebacks. If a lower pass rate is acceptable for this case, lower the bar.
  ```

  The first line is the short summary's message and JUnit's `message`
  attribute; each verdict's line is also a `probability_verdict`
  property of the testcase. A case with two gates puts the failing one
  first.
- **Margins:** a function whose [comparison](#margins) has a margin
  gets `<file>::<bench-function>[compare:<arm>]`, one per arm judged
  against the baseline arm, and a function `--prob-margin` judges
  against a [baseline](#baseline) gets `<file>::<bench-function>[baseline]`.
  They follow all of the function's items and judge all its runs.
- **Selection:** a gate item carries its case's marks, so `-m` selects
  it with its runs, and `-k gate` / `-k "not gate"` select gate items
  alone or leave them out. One selected without any of its runs brings
  them back — `-k "never and gate"` runs `never`'s runs too, and the
  header says `probability: selected 40 runs for 1 verdict item selected
  without them`. Selected with only some of its runs, it judges those,
  as the gates block does.
- **`--lf` and `--ff`:** a failed gate item is a failure to rerun, and
  it brings its case's runs with it (`probability: rerunning 80 runs
  with the 2 failed verdict items that need them`): a verdict needs its
  runs. pytest's own `rerun previous N failures` counts them.
- **`-x` / `--maxfail`:** a failed gate item is a failure, so `-x` stops
  after the first gate that fails; the gated runs themselves still
  don't stop it.
- **pytest-xdist:** a gate item needs every run of its case on its own
  worker, so it judges only under `--dist loadgroup`, where the plugin
  puts each gated case (runs and gate item) in an `xdist_group` of its
  own, `…::never[gate]@classify::never` (a function with a margin item
  is one group, `…::bench_triage[compare:few_shot]@triage`; an
  `xdist_group` of your own is kept). Under any other `--dist`, gate
  items skip with `probability: judged at session end; under
  pytest-xdist a verdict item needs --dist loadgroup to run on the
  worker that ran its runs`, and the verdicts are judged at session end
  as without them.
- **Early stopping:** a stopped case's gate item runs after its skipped
  runs and judges the runs that ran, like the gates block — under
  `--prob-stop=sequential`, on the case's confidence sequence, its line
  tagged `seq`.
- **Nothing to judge:** when none of the case's runs recorded a result
  (all skipped, or a skip mark on the case), its gate item skips.
- **Off:** `--prob-no-gate-items` or `prob_gate_items = false`. Then a
  failed gate has no failing item: JUnit XML records no failure for it
  and `--lf` has nothing to rerun; the exit status and the JSON report
  still carry the verdict. `--prob-plan` collects no gate items.

## Comparisons

A comparison sets the values of one parametrize argument — the
*arms*: prompts, models, thresholds — against a *baseline* arm, on the
same inputs:

```python
@pytest.mark.probability(compare="style")          # or --prob-compare=style
@pytest.mark.parametrize("style", ["terse", "chain_of_thought", "few_shot"])
@pytest.mark.parametrize("text", CASES)
def bench_triage(text, style):
    assert my_classifier(text, prompt_style=style) == "billing"
```

```text
=========================== probability: comparisons ===========================
  triage[style]  chain_of_thought − terse  +12.0 pp [+6.3, +18.0]  p<0.001  40 paired  cost ×4.0
  triage[style]  few_shot − terse           +2.8 pp [−3.2,  +8.5]  p=0.41   40 paired  cost ×1.0

  p not adjusted for 2 comparisons: exploratory (--prob-adjust=holm adjusts them)
```

**Pairing.** At collection every case gets an *arm* — its id on the
compared axis (`chain_of_thought`) — and an *input* — the ids of its
other parameters, joined with `-` as in the case id (`refund`, or
`refund-m1` with a `model` axis too). Each arm other than the
baseline, in parametrize order, is compared with the baseline over the
inputs that ran in both. The baseline is the first value of the axis
unless `baseline=` names another by its id. An input that ran in only
one of the two — after `-k`, `-m` or a skip — is left out and counted:
`38 paired, 2 unpaired`.

**The difference** is the arm's pass fraction minus the baseline's, in
percentage points, averaged over the paired inputs so each input
counts equally. Every run counts and errored runs count as non-passes,
as in the row fraction, whatever `prob_errors` says. How the interval
and p-value are made depends on the number of pairs:

| Paired inputs | Interval | p-value | Line |
|---|---|---|---|
| 1 | Newcombe (hybrid score), from that input's runs | Fisher's exact test | whole points: `+20 pp [−11, +51]` |
| 2 to `prob_min_inputs` − 1 | none: too few inputs to resample | sign-flip | the average, then one line per input with its own Newcombe interval and Fisher p |
| `prob_min_inputs` or more | paired bootstrap over inputs | sign-flip | one decimal: `+12.0 pp [+6.3, +18.0]` |

- **One input:** the two arms' runs are independent samples, so the
  interval and test are for two proportions. They describe that input
  only.
- **Paired bootstrap:** the inputs are re-drawn with replacement
  `--prob-bootstrap` times, each keeping both arms' runs together, and
  the line shows the percentile interval of the mean difference at
  `prob_confidence`. Like a [function-level
  interval](#function-level-intervals), it treats the inputs as a
  sample, and it is not shown with fewer than `prob_min_inputs` pairs,
  where it comes out too narrow.
- **Sign-flip test:** if the arms were interchangeable, each input's
  difference would be as likely to come out negated. p is the share of
  the 2^N ways of flipping signs whose total is at least as far from 0
  as the observed one. It is exact (every pattern counted) when that is
  cheap — a couple of dozen inputs, a few hundred with 10 runs per arm,
  over a thousand with one — and otherwise estimated from
  `--prob-bootstrap` random patterns seeded by `--prob-seed`. With one
  run per arm it is McNemar's exact test.
- p and the interval come from different procedures and can disagree
  at the edge (`p=0.04` beside an interval that just touches 0).
  Verdicts come from the interval only.
- **Cost:** `cost ×4.0` is the arm's recorded cost per run over the
  baseline's, on the paired inputs; shown when both recorded cost.

**More than two arms:** each arm is compared with the baseline, and
the comparisons of the whole session form one family.
`--prob-adjust=holm` (or `bonferroni`, or `bh` for Benjamini-Hochberg)
adjusts their p-values for it, and the block says so. With the default
`none` and more than one comparison, the p-values are labelled
exploratory. Adjustment changes p only: intervals and margin verdicts
stay at `prob_confidence`, the one level everything uses. The
per-input lines' p-values are never adjusted.

### Margins

`margin=` turns a comparison into a gate, with the verdict read off
the interval printed beside it, as for [Gates](#gates):

| Rule | PASS | FAIL | UNDECIDED |
|---|---|---|---|
| Non-inferiority: `margin=0.02`, shown `≥−2 pp` | lower bound > −2 pp | upper bound < −2 pp | otherwise |
| Equivalence: `margin=0.02, equivalence=True`, shown `±2 pp` | −2 pp < lower and upper < +2 pp | upper < −2 pp or lower > +2 pp | otherwise |

- Bounds are compared unrounded; a bound exactly on a margin is not
  past it.
- A 95% two-sided interval makes each side a 2.5% test: the usual
  non-inferiority convention, and stricter for equivalence than the
  90% interval of the two one-sided tests.
- With no interval — fewer than `prob_min_inputs` pairs, or none at
  all — the verdict is UNDECIDED.
- As in a gated case, the function's failing runs are reported as
  xfailed (`-rx` lists them as `probability comparison: …`), and the
  verdicts set the exit status: FAIL fails the session, and UNDECIDED
  does unless `--prob-undecided=pass`. Errored runs still fail it.
- Without a margin a comparison only reports: failing runs fail the
  session as usual, and the exit status is the same as without it.

## Metrics

A pass rate says how often one run passes. Two other questions come up
when the code under test is called more than once:

- **pass^k — reliability:** the chance that k runs of the same input
  *all* pass. For code that has to work every time it is called. It
  can only fall as k grows.
- **pass@k — best-of-k:** the chance that *at least one* of k runs of
  the same input passes. For when a failed attempt can be retried, or
  the best of k answers kept (by a checker that recognizes a right
  one). It can only rise as k grows.

Both are opt-in:

```bash
pytest benchmarks/ --prob-runs=10 --prob-metric='pass^3,pass@5'
```

```text
============================= probability: metrics =============================
  classify  pass^3  N=12 inputs  34.3%  [17.2%,  53.8%]
            pass@5  N=12 inputs  99.4%  [98.8%, 100.0%]
  triage    pass^3  N=3 inputs   69.4%
            pass@5  N=2 inputs   99.8%                   1 left out (fewer than 5 runs)
  Overall   pass^3  N=15 inputs  41.3%  [22.9%,  61.2%]
            pass@5  N=14 inputs  99.5%  [98.9%,  99.9%]  1 left out (fewer than 5 runs)

  pass^k: the chance that k runs of an input all pass (reliability)
  pass@k: the chance that at least one of k runs of an input passes (best-of-k)
```

Here classify passes 66.7% of its runs on average, but three runs in a
row of the same input all pass only about a third of the time, while
one of five runs passes almost always.

- **Per case:** from c passes in n runs, pass^k is C(c, k)/C(n, k) — of
  all the ways to pick k of the n runs, the share in which all k passed
  — and pass@k is 1 − C(n − c, k)/C(n, k), the share with at least one
  pass. Both are unbiased: averaged over what the runs could have been,
  they give exactly p^k and 1 − (1 − p)^k for a pass probability p.
  Raising the observed rate to the power k instead would overstate
  pass^k on average.
  Computed in exact integer arithmetic, then rounded once. `pass^1`
  and `pass@1` are the pass rate. Per-case values are in the JSON
  report's `rows[].metrics`, not the terminal.
- **k is the number of runs drawn,** not the number each case had; k
  must not exceed it. **A case with fewer than k runs is left out** of
  that metric (`null` in its row) and counted on the line: `1 left out
  (fewer than 5 runs)`. A line with no case long enough shows `N=0
  inputs` and no value.
- **Per function and Overall:** the mean of the per-case values over
  the cases with enough runs, each case counting equally, with the
  same percentile interval from a cluster bootstrap over cases as a
  [function-level line](#function-level-intervals) — the same
  `prob_confidence`, `--prob-bootstrap`, `--prob-seed` and case-id
  order, and no interval with fewer than `prob_min_inputs` cases.
  Each metric is one more bootstrap per line.
- **Which lines:** every bench function, by name, and `Overall` (left
  out when there is a single function), whatever its number of
  inputs: unlike the function-level block, this block is the only place
  the terminal shows the metrics. `--prob-no-intervals` hides the
  intervals but keeps the values.
- **Errored runs** count as non-passes (c is the passes, n every run),
  as in the row fraction, even under `prob_errors = exclude`.
- Metrics only report: they never change verdicts or the exit status.
  Without `--prob-metric` the output is exactly as before.

`--prob-explain` reads every line of the block in plain words — what
the metric means, its average beside the plain pass rate of the same
inputs, its range, how many inputs were too short — and the glossary
explains pass^k and pass@k.

Inspired by pass@k from Chen et al., [Evaluating Large Language Models
Trained on Code](https://arxiv.org/abs/2107.03374) (2021), and pass^k
from Yao et al., [τ-bench](https://arxiv.org/abs/2406.12045) (2024).

## Planning

`--prob-plan` answers "how many runs do I need, and what will they
cost?" before you spend anything. It collects as usual, prints one row
per selected case and exits 0 without running a single item:

```text
$ pytest benchmarks/ -o prob_runs=40 --prob-plan --prob-plan-report=last.json
============================== probability: plan ===============================
  case               runs  min runs  runs for 80%  chance now  catch 10%  catch 1%     cost
  classify::refund     40        36           100         30%        99%       33%  $0.0800
  classify::billing    40        36           100         30%        99%       33%  $0.0800
  classify::pii        40        36           100         30%        99%       33%  $0.0800
  smoke                20        19            20         88%        88%       18%  $0.2000
  ungated              40         —             —           —        99%       33%  $0.0000

  Plan:    5 cases, 180 runs; nothing was run.
  Cost:    $0.4400 projected from last.json

  runs          Runs planned for the case (--prob-runs, a runs= mark or
                prob_runs), counting only the runs -k/-m selected.
  min runs      Fewest runs with which the gate can pass at all, and then only
                if every one of them passes.
  runs for 80%  Runs with which the gate passes 80% of the time if the case
                really passes 97% of its runs (--prob-plan-assume); never, if
                97% is not above the gate's bar.
  chance now    The chance the gate passes with the planned runs, if the case
                really passes 97% of its runs.
  catch 10%     The chance the planned runs show at least one failure if 10% of
                runs fail; 29 runs make it 95%.
  catch 1%      The chance the planned runs show at least one failure if 1% of
                runs fail; 299 runs make it 95%.
  cost          Planned runs times the case's cost per run in last.json; blank
                for a case it doesn't have.
```

Here `classify` is gated at `min_rate=0.9`, `smoke` at
`min_passes=19, runs=20`, and `ungated` has no gate.

| Column | How it is computed |
|---|---|
| `runs` | The runs that would execute: the case's run count, after `-k`/`-m` selection (selecting `run1` and `run2` plans 2). |
| `min runs` | `Gate.min_runs()`, as in the [feasibility warning](#feasibility-warning): the fewest runs whose all-pass result is a PASS verdict. For a latency gate, `stats.quantile_min_n(quantile, level)`, the fewest runs whose quantile interval has an upper end (72 for p95, 6 for p50, 368 for p99 at 95%); a case with both gates shows the larger. |
| `runs for 80%` | The smallest n whose *power* — the chance of a PASS verdict when each run passes with probability `--prob-plan-assume` — is at least 80%. Computed exactly: the Binomial(n, rate) probability of every pass count the gate's own `verdict()` calls PASS (the passing counts are one tail, so one boundary per n). Power saw-tooths in n, so every n from `min runs` up is tried; the search stops at 10,000 (`>10,000`). `never` when the assumed rate is at or below a rate gate's bar: then more runs make a PASS *less* likely. |
| `chance now` | That same power at the planned `runs`. |
| `catch F` | 1 − (1 − F)^runs, the chance the planned runs show at least one failure when a fraction F of runs fail. The note gives the runs that make it `prob_confidence` (95%): ⌈ln 0.05 / ln(1 − F)⌉ — 29 at 10%, 299 at 1%. |
| `cost` | Only with `--prob-plan-report`: runs × the case's cost per run in that report. Blank for a case the report doesn't have; the footer totals the column and counts those cases. |

- Gate columns use each case's own gate — bar, method, level, prior —
  so a plan agrees with the verdict a real session would give.
  A count gate (`min_passes`) passes on the count alone, so its
  `runs for 80%` is where P(at least `min_passes` passes) reaches 80%.
- Ungated cases are listed too, with `—` in the gate columns: the
  flake and cost columns apply to them. A suite with no gate at all
  shows no gate columns.
- Rows are green when `chance now` is at least 80%, yellow below, and
  red when `runs` is under `min runs` (the gate can't pass).
- Latency gates (`max_latency=`) are planned only as far as `min runs`:
  their chance of passing depends on how slow the code really is, which
  a plan doesn't know. A latency-only case shows its `min runs` and `—`
  in `runs for 80%` and `chance now`; it is red below `min runs` and
  plain otherwise. A case with both gates shows the larger `min runs`,
  and its `runs for 80%` and `chance now` describe the pass-rate gate
  alone, marked `*` (a column note says so):

  ```text
    case          runs  min runs  runs for 80%  chance now  catch 10%  catch 1%
    latency_only    40        72             —           —        99%       33%
    both            40        72          100*        30%*        99%       33%
    both_p50       100        36          100*        82%*        99%       63%
  ```

  Here `both` meets its pass-rate gate's minimum (36) but not its p95
  latency gate's (72), so the row is red; `both_p50` uses
  `latency_quantile=0.5`, whose minimum is 6. A suite without latency
  gates prints exactly what it did before.
- Under `--prob-stop=sequential` a [sequential](#sequential-stopping)
  gate's columns are for its stopping rule: `min runs` is where its
  sequence can first PASS (53 for `min_rate=0.9` at 95%), `chance now`
  the chance it PASSes at some run within the planned ones, and `runs
  for 80%` the budget that makes that chance 80% (it only grows with
  the budget, so the first that gets there is the answer). An
  `expected runs` column gives the runs each gated case would use on
  average at the assumed rate — its planned runs when it can't stop,
  and for a count gate the runs curtailment leaves. All are exact
  forward passes over the pass counts, through the same stopping rule
  a session uses:

  ```text
  $ pytest benchmarks/ --prob-stop=sequential --prob-plan
    case             runs  min runs  runs for 80%  chance now  expected runs  catch 10%  catch 1%
    classify::solid   100        53           197         33%             76        99%       63%
    smoke::steady      20        19            20         88%             19        88%       18%
  ```

  Here a case that really passes 97% of its runs has only a 33% chance
  to clear a 90% bar within 100 runs (82% with a fixed count of 100):
  the sequence is wider. A planned 200 runs would make it 81%, at about
  124 runs used on average. Without `--prob-stop=sequential` the plan is
  unchanged.
- `prob_errors` doesn't enter the plan: the assumed rate is the chance
  a run passes.
- The 80% target and the 10,000-run cap are fixed. A rate gate barely
  below the assumed rate takes longest (about 2 s at a bar of 0.96 with
  the exact method).
- `-n` is turned off: nothing runs, so no workers are started, and the
  plan is the same as without it. `--prob-json` is not written, so a
  plan never overwrites the report it reads.
- Collection errors stop the plan as they stop a session (exit 2);
  nothing selected exits 5, like pytest.

## Baseline

`--prob-baseline` sets this run against an earlier `--prob-json`
report — typically main's, cached in CI — case by case, and
`--prob-margin` makes that a regression gate:

```bash
pytest benchmarks/ --prob-runs=10 --prob-json=report.json \
    --prob-baseline=main.json --prob-margin=0.05
```

```text
============================ probability: baseline =============================
  against main.json (2026-10-08T19:24:03): 52 cases paired, 1 only in this run
  classify  current − baseline   −0.2 pp [ −3.0, +2.7]  p=1     40 paired              ≥−5 pp  PASS
  triage    current − baseline  −15.0 pp [−25.0, −5.8]  p=0.03  12 paired, 1 unpaired  ≥−5 pp  FAIL

  p not adjusted for 2 comparisons: exploratory (--prob-adjust=holm adjusts them)

  only in this run: triage::new_case
```

Here `triage` lost 15 points and fails the gate; `classify` barely
moved, and its interval, all above −5 points, passes it. With
`--prob-margin=0.02` it would be UNDECIDED: 40 cases × 10 runs can't
rule out a 3-point drop. A tight margin needs many inputs.

**Pairing.** A case in both reports is paired with itself by its case
id (`triage::refund`); each bench function with at least one paired
case gets one line: the mean, over its paired cases, of this run's pass
fraction minus the baseline's, with the interval and p-value of a
[comparison](#comparisons) — the same three regimes by the number of
pairs (one case: Newcombe and Fisher; 2 to `prob_min_inputs` − 1: the
average and the sign-flip p, then one line per case; more: a paired
bootstrap over cases and the sign-flip p), the same `prob_bootstrap`,
`prob_seed` and one confidence level. Every run counts and errored runs
count as non-passes in both reports. `cost ×N` compares the recorded
cost per run when both reports have one.

**Cases in one report only** are never paired: new cases, removed
ones, renamed ones, and cases deselected this time with `-k`/`-m`. The
header counts them, the function's line says `N unpaired`, and the
block lists the first few ids of each kind (the JSON report lists them
all). A function with no case in both reports gets no line. When no
case at all is paired, the header is yellow and nothing is judged —
check that the baseline comes from the same suite.

**The margin** is a non-inferiority bar, read off the printed
interval as for an axis comparison's `margin=`:

| `--prob-margin` | Shown | PASS | FAIL | UNDECIDED |
|---|---|---|---|---|
| `0.02` | `≥−2 pp` | lower bound > −2 pp | upper bound < −2 pp | otherwise, or no interval |
| `0` | `>0 pp` | lower bound > 0: this run is shown better | upper bound < 0 | otherwise |

- With a margin, the failing runs of **cases found in the baseline**
  are reported as xfailed (`-rx` lists them as `probability baseline:
  …`) and the verdicts set the exit status: FAIL fails the session, and
  UNDECIDED does unless `--prob-undecided=pass`. A new case is not
  covered by any verdict, so its failing runs fail the session as
  usual. Errored runs still fail it.
- With fewer than `prob_min_inputs` paired cases there is no
  interval, so the verdict is UNDECIDED: add inputs, or allow it with
  `--prob-undecided=pass`.
- **Without `--prob-margin`** the block only reports: failing runs
  fail the session as usual and the exit status is the same as without
  `--prob-baseline`.
- `--prob-adjust` adjusts the baseline lines' p-values as one family,
  apart from the axis comparisons'. Verdicts come from the intervals
  only.
- Only `rows[]` with `case`, `passes` and `total` are read (`errors`
  and `cost` when present), so reports from 0.2.0 on work; the
  baseline's method, level and gates don't matter. Rows sharing a case
  id are pooled.
- A case can be judged by a gate, an axis comparison's margin and the
  baseline at once: the session fails when any of their verdicts does.
- `--prob-explain` reads the header and every line in plain words. See
  {doc}`json-report` for the `baseline` block and a GitHub Actions
  recipe that caches main's report.

## Latency

Every run is timed: its `elapsed` is the wall-clock time of the bench
body (`time.perf_counter`), not the delay between runs. `--prob-latency`
shows a quantile of each case's run times with an interval, and
`max_latency=` gates a case on it:

```python
@pytest.mark.probability(max_latency=2.0)            # p95 under 2 seconds
@pytest.mark.parametrize("case", ["search", "summarize", "translate"])
def bench_api(case):
    call_service(case)
```

```text
$ pytest benchmarks/ --prob-runs=100 --prob-latency
============================= probability: latency =============================
  api::search     100 runs  p95  769ms  [600ms, 933ms]  ≤2s  PASS
  api::summarize  100 runs  p95  2.00s  [1.71s, 4.84s]  ≤2s  UNDECIDED
  api::translate  100 runs  p95  3.00s  [2.60s, 3.25s]  ≤2s  FAIL
```

**The quantile** is `latency_quantile=` on the mark, else
`prob_latency_quantile` (0.95). `p95` is the run time 95% of runs
finish within; the estimate is the observed one — the ⌈0.95·n⌉-th
fastest of n runs.

**The interval** makes no assumption about the shape of the run-time
distribution — skewed, long-tailed or lumpy, it only needs the runs to
be independent draws. Its ends are two of the observed run times, the
r-th and s-th fastest, with ranks from the binomial distribution: the
number of runs faster than the true quantile is Binomial(n, q), so
r is the largest rank that is too high with probability at most
(1 − level)/2, and s the smallest that is too low with at most that.
Each end is wrong at most 2.5% of the time at 95%, so the interval
covers the true quantile at least 95% of the time — exactly
P(r ≤ B ≤ s − 1) for continuous run times, slightly more than 95%
because ranks are whole numbers (the JSON report's `coverage`), and at
least that with ties. It uses `prob_confidence`, or the mark's
`confidence=` for a latency gate, like a rate gate.

**Too few runs.** An upper end exists only once qⁿ ≤ (1 − level)/2,
and a lower end once (1 − q)ⁿ is: until then that side is open and
prints `—`. Both ends need at least:

| Quantile | p50 | p90 | p95 | p99 |
|---|---|---|---|---|
| Runs at 95% | 6 | 36 | 72 | 368 |

(the same numbers as a rate gate's bars, for the same reason.) With
fewer, `[600ms, —]` still says the quantile is probably at least
600ms, at the same confidence.

**Which runs count.** Every recorded run: passed, failed and errored,
because a wrong answer took as long as a right one. Under
`prob_errors = exclude` a gated case (a rate or a latency gate) leaves
its errored runs out, as its rate gate does — `(2 errored, excluded)`
on the line — since a run that crashed early or hit a broken service
says little about the code's speed. Skipped runs are never recorded.

**The gate** is the [gate rule](#gates) with lower being better,
on unrounded bounds:

| Interval vs `max_latency` | Verdict |
|---|---|
| Entirely below | PASS |
| Entirely above | FAIL |
| Straddles it, or has no upper end yet | UNDECIDED |

- A missing upper end can't PASS, but a lower end above the limit
  FAILs: with 20 runs, `[2.50s, —]` against `≤1s` is already too
  slow. A case with every run excluded is UNDECIDED.
- The verdicts set the exit status like a rate gate's: FAIL fails the
  session, and UNDECIDED does unless `--prob-undecided=pass`. They are
  counted on the `Gates:` line and non-PASS ones are listed in the
  gates block, with or without `--prob-latency`.
- **A latency gate judges speed, not answers.** It doesn't turn failing
  runs into xfails: a failing `assert` still fails the session, as in
  an ungated case, unless the case also has a rate gate (`min_rate=` or
  `min_passes=`) or a margin, which judges the answers. So
  `max_latency=2.0` alone means "every run right, and p95 under 2s";
  with `min_rate=0.9` it means "90% right, and p95 under 2s".
- At collection, a latency gate with fewer runs than the table above
  gets an `InfeasibleGateWarning`: `api (3 cases): max_latency=2 (p95)
  at 95% needs ≥72 runs; each has 20`.
- Under pytest-xdist the run times travel in the run records and the
  controller sorts them before computing anything, so the lines and
  verdicts are the same as a serial run.
- Durations print to three significant figures in s, ms or µs; the JSON
  report keeps the unrounded seconds, and the verdict uses those.

## Early stopping

A gated case often settles long before its last run: 9 failures in a
row already rule out `min_rate=0.9` over 40 runs, and 19 passes meet
`min_passes=19` whatever the twentieth does. `--prob-stop=curtail`
skips the rest:

```text
$ pytest benchmarks/ --prob-stop=curtail
================================= probability ==================================
  classify::solid  40/40  [91%, 100%]  $0.0400
  classify::close  37/38  [86%,  99%]  $0.0380  FLAKY  decided after 38/40
  classify::weak    3/12  [ 5%,  57%]  $0.0120  FLAKY  decided after 12/40
  smoke::steady    19/19  [82%, 100%]  $0.0190  decided after 19/20
  smoke::broken      2/4  [ 7%,  93%]  $0.0040  FLAKY  decided after 4/20

  Overall: 101/113 passed (89%)
  Gates:   2 passed, 2 failed, 1 undecided
  Stopped: 4 cases early, saving 47 of 160 runs (29%) and about $0.0470
  Cost:    $0.1130
```

**When a case stops.** After each run, the plugin asks whether every
way the remaining runs could go — each passing, failing, erroring or
skipping itself — gives the same verdict at the case's planned run
count. Interval bounds rise with passes and fall with non-passes, so
the two extremes decide:

| Stops as | when it holds even if |
|---|---|
| PASS | every remaining run fails |
| FAIL | every remaining run passes |
| UNDECIDED | neither PASS nor FAIL is reachable any more |

The thresholds come from the gate itself: for a fixed number of runs,
the fewest passes that PASS and the most that FAIL, found once by
bisection over the gate's own `verdict()`. With `min_rate=0.9` over 40
runs at 95% (exact), PASS needs 40 of 40 and FAIL is at most 31
passes, so `close` above (37 passes, then failures) stops as UNDECIDED
at its first failure, and `weak` (3 passes, then failures) as FAIL at
its ninth.

**Exact.** A stopped case's verdict is the one all its runs would have
given, so error rates are those of running every run: a stop is only
made when no remaining outcome could change the verdict. The verdict on
the runs that did run is then already the same, so the gates block and
the JSON report judge the case on those runs, with the interval its
verdict was read from. It stops as soon as that holds, not later: one
run fewer and some outcome of the rest would still change the verdict.

- **Errors.** Under `prob_errors = count` a remaining run may error
  as a non-pass; under `exclude` it may leave the sample instead, so
  the case's final sample could be anything from today's runs to all
  of them. Both are covered: a case whose every run so far errored and
  was excluded only stops when judging it on no runs at all would also
  give its verdict.
- **Runs that skip themselves** (`pytest.skip`) are not samples, as
  always; they still count among the planned runs.
- **What is never stopped:** ungated cases; a case with no recorded
  run yet (stopping would leave it without a row); and gated cases
  whose runs another verdict needs — those of a function with a
  comparison `margin=`, those paired against `--prob-baseline` with
  `--prob-margin`, and those with a latency gate (`max_latency=`),
  whose sample of run times skipping would shrink. Being conservative
  keeps every verdict in the session the one all the runs would give.
- "Remaining" is the case's items still to run in this session, after
  `-k`/`-m`, in whatever order they run: case-major or
  `--prob-transpose`.

**Skipped runs** are reported by pytest as skipped, with the reason
`probability gate: decided after 38/40 runs (UNDECIDED)` (`-rs` lists
them), and are not samples: they are neither in the row nor in the
JSON `records`. Rows of stopped cases end with `decided after 38/40`,
here and in the gates block. The `Stopped:` footer line counts the
cases, the runs skipped out of the runs that ran plus those skipped,
and, when cost was recorded, the cost avoided: each stopped case's
skipped runs at its own average cost per run.

**What it hides.** A case that stops as soon as its verdict is certain
has a fraction from fewer runs that leans toward that verdict: 3/12
for `weak` above, where all 40 runs give 3/40. So any function
(and Overall) with a stopped case shows no function-level interval but
a note, `classify  N=40 inputs × k=12–40  interval hidden: 3 cases
stopped early`; its [metrics](#metrics) line shows `hidden: 3 cases
stopped early` instead of a value; and a [comparison](#comparisons) or
[baseline](#baseline) line over a stopped case shows neither difference,
interval nor p-value, only the note. The JSON report keeps the
estimates as data, with `suppressed` and `stopped` saying why there is
no interval. The row's own interval stays: it describes the runs that
ran. A latency quantile of a stopped case comes from the runs that ran.

**Under pytest-xdist**, a case can only be stopped by the process that
runs all of its runs, so `--prob-stop` needs `--dist
loadgroup`: the plugin puts each stoppable case's items in an
`xdist_group` of its own (named after the case, which xdist appends to
the node id: `…::weak[run3]@classify::weak`), so one worker runs them
and decides. A case that already has an `xdist_group` mark keeps it.
With any other `--dist`, a `CurtailmentWarning` says so and every run
runs. Either way, the controller rebuilds the stops from the skipped
runs' reports, so the summary and JSON report match a serial run.

`--prob-explain` says, for each stopped case, why it could stop and
what that means for its numbers, and for each hidden line why it is
hidden.

### Sequential stopping

Curtailment stops a case only once its verdict can't change, which
for a clearly good case is near the end: `solid` above ran all 40.
`--prob-stop=sequential` asks a different question after every run —
is the case already clearly above or below its bar? — and stops as
soon as the answer is yes. The run count (`--prob-runs`, `runs=`)
becomes the most a case may use:

```text
$ pytest benchmarks/ --prob-stop=sequential     # classify: min_rate=0.9, runs=100
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
```

The same suite under `curtail` saves 53 of the 340 runs: `solid` needs
96 of its 100 there, against 53 here. The verdicts happen to agree.

**Why a different interval.** A fixed-run interval (Clopper-Pearson,
Wilson, Bayesian) is valid for a run count chosen in advance. Looked
at after every run, and acted on, it is not: the chance that a 95%
Clopper-Pearson interval leaves out the true rate at some point in 300
runs is about 32% (for a rate of 50%), and in 2,000 runs about 46%.
Stopping the moment it cleared the bar would pass cases that don't
meet it. The interval tagged `seq` is a *confidence sequence*: the
chance that it ever leaves out the true rate, checked after every run
for as long as the case runs, is at most 1 − `prob_confidence`. Worked
out exactly over every path of 2,000 runs, it is at most 3.7% at 95%.

**How it is built.** After x passes in n runs, the interval holds
every rate p whose evidence against it is still below 1/α
(α = 1 − level, so 20 at 95%):

```text
Mₙ(p) = B(½ + x, ½ + n − x) / (B(½, ½) · pˣ · (1 − p)ⁿ⁻ˣ)
```

— the probability the runs got under a Beta(½, ½) mixture of pass
rates, over their probability under p (a beta-binomial mixture, in the
style of Robbins' method of mixtures). If the runs really pass with
probability p, Mₙ(p) is a fair bet that starts at 1 (a nonnegative
martingale), so by Ville's inequality the chance it ever reaches 1/α is
at most α. The bounds are found by Newton's method on log Mₙ, with
`math.lgamma`; they depend only on the counts, not on the order of the
runs. The guarantee holds for any mixture, so the choice is about
width: Jeffreys' Beta(½, ½) has the smallest worst-case penalty (about
½·log n) and keeps it small at n of n and 0 of n passes, where
pass-rate gates live — 53 runs clear a 90% bar, where a uniform
Beta(1, 1) mixture would need 69. It is symmetric and fixed, so there
is nothing to tune after looking. One level drives both ends: the
interval is two-sided, as the printed range must be.

**The price is width.** At the same level the sequence is never
narrower than Clopper-Pearson (checked for every count up to 333
runs): about 1.2 times as wide at 10 runs, 1.6 times at 100 and 1.8
times at 1,000. A gate needs more runs to PASS at all — 53 of 53 for a
90% bar at 95% (36 with a fixed count), 116 for 95%, 680 for 99% — and
the [feasibility warning](#feasibility-warning) counts those. So it
pays off for cases clearly on one side of their bar and costs runs for
cases near it: with 100 runs, a case that really passes 97% of its runs
clears a 90% bar with a 33% chance sequentially and 82% with a fixed
count. `--prob-plan` shows the trade ([Planning](#planning)).

**When a case stops:**

| Stops as | when |
|---|---|
| PASS | its sequence's interval is entirely above the bar |
| FAIL | it is entirely below the bar |
| UNDECIDED | neither can happen within its planned runs, however they go |

A PASS or FAIL is the verdict at the run the case stopped at; unlike
curtailment, it is not "what all the runs would give", and the
guarantee is what makes it trustworthy anyway. A case that reaches its
budget is judged on the sequence there: UNDECIDED if it still
straddles the bar, even when a fixed-run interval over the same runs
would decide — switching at the end would break the guarantee. The
UNDECIDED stop uses curtailment's extremes argument: each pass moves
both bounds up and each non-pass moves them down, so if every remaining
run passing can't reach PASS at the last run, it can't at any run
before. That holds from `confidence` 1 − e^(−½) ≈ 0.39 up (well below
any level in use); below it, a case only stops on PASS or FAIL.

- **What it applies to:** rate gates whose case can stop — the cases
  curtailment would stop. Each is judged by the sequence at its
  `confidence=`, in place of its `method=` and `prior=`. Count gates
  (`min_passes`) have no interval to watch, since they pass on a count
  over all their runs, so they are curtailed, exactly as under
  `curtail`. Cases that can't stop — ungated, or with a comparison
  `margin=`, `--prob-margin` or a latency gate — keep their fixed-run
  interval and run every run.
- **Errors** follow `prob_errors`: under `count` an errored run is a
  non-pass of the sequence, under `exclude` it is not one of its runs.
  Runs that skip themselves are not runs of it either.
- **Output:** a row judged by a sequence shows the sequence's interval
  — in the main table, the gates block and `--prob-explain` — tagged
  `seq`, so it isn't read as a fixed-run one; in the JSON report its
  `rows[].ci` and `rows[].gate` say `"method": "sequential"`. Skipped
  runs read `probability gate: decided after 53/100 runs (PASS,
  sequential)`. A stopped case's fraction leans toward its verdict as
  under curtailment, so function-level, metric, comparison and
  baseline intervals over it are hidden the same way; its own
  sequence's interval allows for the stop.
- **Under pytest-xdist** as above: `--dist loadgroup`, or a
  `CurtailmentWarning` and every case runs every run, judged by its
  fixed-run interval, since nothing stopped.

`--prob-explain` says which side of the bar the range was on when a
case stopped, that the range holds however early it stopped and is
wider for it, and, for an UNDECIDED case, that `runs=` is the most it
may use.

## Python API

Everything importable lives in the top-level package:

```python
from pytest_probability import (
    CurtailmentWarning, InfeasibleGateWarning, TokenUsage, record_cost,
    record_usage,
)
```

### `record_usage(usage=None, /, **fields)`

Attribute per-model token usage to the current bench run. Accepts a
`TokenUsage`, any object or dict with the same field names, or the
fields directly as keywords. Call once per model call; entries
aggregate per model. Usage recorded before a failing `assert` is kept
— spend is never lost to a wrong answer. Raises `RuntimeError` outside
a bench run.

### `record_cost(amount)`

Add a non-token cost to the current bench run. A run's total cost is
its `record_cost` amounts plus the `cost` of every usage entry. Raises
`RuntimeError` outside a bench run.

### `TokenUsage`

```python
@dataclass
class TokenUsage:
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    cost: float = 0.0
```

Token accounting for one model's calls within a run. See {doc}`cost`
for aggregation rules.

### `InfeasibleGateWarning`

A `pytest.PytestWarning` subclass, raised at collection for a gate that
cannot pass even if every run passes. See
[Feasibility warning](#feasibility-warning).

### `CurtailmentWarning`

A `pytest.PytestWarning` subclass, raised at startup when
`--prob-stop` (`curtail` or `sequential`) runs under pytest-xdist
without `--dist loadgroup`: no case can stop early then. See
[Early stopping](#early-stopping).

## Benchmark module contract

| Name | Required | Contract |
|---|---|---|
| `bench_*(**params)` | at least one | a benchmark per function; an ordinary test body that `assert`s; cases come from its `parametrize` marks (none → a single case named after the function) |
| `setup()` | no | called once per file, before its first item |
| `teardown()` | no | called once per file, after its last item |

## Item ids

```text
<file>::<bench-function>::<case-id>[run<N>]
```

- `<bench-function>` — the full function name (`bench_classify`).
- `<case-id>` — pytest's composed parametrize id: value text for
  scalars, `pytest.param(id=...)`/`ids=` overrides, stacked decorators
  joined with `-` (`refund-terse`). For an unparametrized function the
  item is named `run<N>` directly.
- `[run<N>]` — appended when `--prob-runs` > 1; 1-based.

[Gate items](#gate-items) follow the same scheme:

```text
<file>::<bench-function>::<case-id>[gate]       a gated case's verdict
<file>::<bench-function>::gate                  … of an unparametrized function
<file>::<bench-function>[compare:<arm>]         an arm's comparison margin
<file>::<bench-function>[baseline]              a function's --prob-margin verdict
```

Under pytest-xdist's `--dist loadgroup`, xdist appends the item's
group: `@<case>` for a gated case's items, `@<function-short>` for a
function with a margin item.

Summary rows and JSON use the function-qualified case id:
`<function-short>::<case-id>`, where the short name strips the
`bench_` prefix (`classify::identify_pii`); an unparametrized
function's row is just the short name.

## Row statuses

Every run lands in one of **three classes**, decided by how the
function body ends — a clean return **passes**, an `AssertionError`
**fails**, any other exception **errors** — and the row status names
the combination, so model nondeterminism and infrastructure trouble
never blur into one word. Fractions render as `passes/total`: every
run counts.

| Status | Definition | Color |
|---|---|---|
| `pass` | every run passed | green |
| `flaky` | passes and fails mixed (real nondeterminism) | yellow |
| `errored` | passes and errors only — the model never answered wrong, the harness broke | yellow |
| `fail` | no run passed, at least one real failure | red |
| `error` | every run errored | red |

A run counts as *errored* when the bench function raised anything
other than `AssertionError`.

The status word is the verdict on your code, derived from the pass/fail
evidence; errors are an orthogonal fact about the harness and ride as
an annotation when they appear alongside real outcomes. Three classes
give seven possible combinations, and each renders distinctly:

| Classes present | Rendered | Example |
|---|---|---|
| pass | *(green, no word)* | `10/10` |
| fail | `FAIL` | `0/10  FAIL` |
| error | `ERROR` | `0/10  ERROR` |
| pass + fail | `FLAKY` | `5/10  FLAKY` |
| pass + error | `N ERRORED` | `7/10  3 ERRORED` |
| fail + error | `FAIL (N errored)` | `0/10  FAIL (2 errored)` |
| pass + fail + error | `FLAKY (N errored)` | `5/10  FLAKY (1 errored)` |

In the JSON report the question never arises: rows carry the three raw
class counts, so every combination is exactly recoverable regardless of
the status word.

In a gated case under `prob_errors = exclude`, the error annotation
reads `(N errored, excluded)` instead: `5/10  FLAKY (1 errored,
excluded)`, `7/10  (3 errored, excluded)` for passes and errors only,
`0/10  ERROR (10 errored, excluded)`. The fraction and interval on the
row still count every run; the gates block shows the gate's own
sample.

## Terminal summary anatomy

```text
================================= probability ==================================
  classify::identify_pii   7/10  [35%,  93%]  $0.0020  FLAKY
   \_ function::case-id  \_ fraction  \_ interval  \_ cost  \_ status (omitted when pass)

  classify  N=40 inputs × k=10    81.0%  [69.5%, 90.8%]  ρ=0.76   # functions with ≥10 cases,
            runs ×2 → interval −1%  ·  inputs ×2 → −29%  ·  each +$0.0400   # ρ: only with >1 run
  Overall   N=55 inputs × k=5–10  75.1%  [66.0%, 83.8%]  ρ=0.56   # then all of them
            runs ×2 → interval −2%  ·  inputs ×2 → −29%  ·  each +$0.0525   # cost: only when recorded

  Overall: 35/50 passed (70%), 3 errored   # errored count only when present
  Gates:   3 passed, 1 failed, 1 undecided     # only when a case is gated
  Stopped: 2 cases early, saving 30 of 80 runs (38%) and about $0.0030   # --prob-stop
  Cost:    $0.0110                                 # only when cost was recorded
  Tokens:  m-small  1,200 in / 80 out / 640 cached  $0.0010   # per model,
           m-large  4,800 in / 900 out              $0.0040   # only with usage
  Report:  report.json                             # only with --prob-json
```

The section renders only when at least one benchmark item ran. With
`--prob-metric`, a [`probability: metrics`](#metrics) section follows
it. When a function is [compared](#comparisons), a `probability:
comparisons` section comes next, then with `--prob-baseline` a
[`probability: baseline`](#baseline) section, both before the gates
block. With `--prob-latency`, a [`probability: latency`](#latency)
section comes right after the metrics.

### The gates block

When any gate is FAIL or UNDECIDED, a second section lists those cases
with the interval and bar each verdict came from:

```text
============================== probability: gates ==============================
  classify::identify_pii  37/40  [80%, 98%]  ≥90%        UNDECIDED
  classify::never          3/40  [ 2%, 20%]  ≥90%        FAIL
  triage::refund          17/20  [62%, 97%]  ≥19 passes  FAIL
```

- The fraction and interval are the gate's own: its `method`,
  `confidence` and `prior`, over its sample (errored runs left out
  under `prob_errors = exclude`, with a trailing `(N errored,
  excluded)`). So a printed verdict always sits next to the interval it
  was read from, even when the gate overrides the session settings.
- Intervals are shown even with `--prob-no-intervals` or a single run:
  the verdict depends on them.
- A gate judged by a [confidence sequence](#sequential-stopping)
  (`--prob-stop=sequential`) has `seq` after its interval.
- PASS cases are only counted, on the `Gates:` line. `(allowed)` marks
  UNDECIDED cases that `--prob-undecided=pass` lets through.
- [Latency gates](#latency) that did not PASS follow the rate gates, in
  their own columns: `api::slow  100 runs  p95  3.00s  [2.60s, 3.25s]
  ≤2s  FAIL`. A case with both gates can appear twice, and the `Gates:`
  line counts every verdict.

### The explain section

When any gate is FAIL or UNDECIDED, any comparison, metric or
baseline line is shown, or any case [stopped early](#early-stopping),
the summary ends with a hint:

```text
  Run with --prob-explain for a plain-language reading.
```

With `--prob-explain` (or `prob_explain = true`) a third section
replaces the hint. It reads every gated case, and every ungated row
that did not pass all its runs, in plain words: what was measured,
what the numbers mean, and a next step where there is one. Then a
short glossary explains each term those lines used, naming the
interval method and level actually in effect:

```text
============================ probability: explained ============================
  classify::close  37/40  [80%, 98%]  ≥90%  UNDECIDED
    37 of 40 runs passed. The true pass rate is probably between 80% and 98%
    (95% confidence). Your bar is 90%, and that range has values both above and
    below it, so there isn't enough data yet to tell whether this case meets the
    bar. Until that's settled, the case fails the test session
    (--prob-undecided=pass would let it through).
    Next: run more. If it keeps passing at today's rate (92.5%), about 570 runs
    in total would settle it.

  classify::weak  3/40  [2%, 20%]  ≥90%  FAIL
    3 of 40 runs passed. The true pass rate is probably between 2% and 20% (95%
    confidence). Your bar is 90%, and that whole range is below it, so this case
    falls short of the bar.
    Next: look at the failing runs: -rx lists them with their assert messages,
    and --xfail-tb shows their tracebacks. If a lower pass rate is acceptable
    for this case, lower the bar.

  Methods used
    [low, high]  The range the true pass rate probably falls in, given the runs
                 so far. More runs make it narrower. (Clopper-Pearson, 95%
                 confidence: errs on the side of a wider range.)
    Verdict      PASS: the whole range is above the bar. FAIL: the whole range
                 is below it. UNDECIDED: the range crosses the bar, so more runs
                 are needed.
  Full guide: https://pytest-probability.readthedocs.io/en/latest/statistics.html
```

- Text wraps to the terminal width; headings and the link don't.
- "About N runs" is the smallest total that would settle the verdict
  if the observed pass rate held, computed with the gate's own
  interval and rounded up to two significant figures. When the rate is
  too close to the bar to settle within 10,000 runs, the reading says
  so instead.
- Count gates (`min_passes`) are explained in counts; errored runs
  left out under `prob_errors = exclude` are called out.
- Each [latency gate](#latency) gets a reading: the quantile in words
  ("the time 95% of its runs finish within"), its range, the verdict
  against the limit and a next step — for a range with no upper end
  yet, how many runs it needs. With `--prob-latency`, an ungated
  latency line is read too when its range is missing an end, and the
  glossary explains the latency columns.
- Each [function-level line](#function-level-intervals) gets a reading
  too: the average, its range, the "inputs treated as a sample"
  caveat, and a next step. When the line shows a
  [ρ](#runs-or-inputs), the reading says in words what it means for
  this function, how much doubling the runs or the inputs would narrow
  the range and what that would cost, and the next step follows from
  those numbers.
- Each line of the [metrics block](#metrics) gets a reading: what
  pass^k or pass@k means, its average next to the plain pass rate of
  the same inputs, its range or why there is none, the inputs left out
  for having fewer than k runs, and a next step when one helps.
- Each [comparison](#comparisons) gets a reading: the difference and
  what its range says about which arm is better, what "one input" or
  "a sample of inputs" means for it, p as how often a gap this big
  would turn up by chance if there were no real difference, any
  adjustment, the cost ratio, the margin's verdict, and a next step.
- A case that [stopped early](#early-stopping) gets a paragraph saying
  after how many of its planned runs it stopped, why its verdict could
  no longer change ("it would fall short of the bar even if every
  remaining run passed"), that the verdict is the one all its runs
  would give, and that its fraction can lean toward the verdict. A
  function-level, metrics, comparison or baseline line hidden by a
  stopped case says why it is hidden and that running without
  `--prob-stop=curtail` shows it. The glossary explains "decided
  after". Under `--prob-stop=sequential` a case its sequence stopped
  is read instead as stopped "as soon as its range was entirely above
  the bar" (or below it, or "could no longer get clear of the bar
  within its planned runs"), with a range that holds however early it
  stopped; the glossary's `[low, high]` entry explains `seq`.
- Under pytest-xdist the controller writes the section from the
  aggregated counts, so it reads the same as a serial run.

### The interval column

`[low, high]` is a two-sided interval for the case's true pass
probability, at `prob_confidence` (95% by default) by `prob_method`
(Clopper-Pearson by default). Every run counts toward it, exactly as
in the fraction, so errors count as non-passes.

- Bounds are whole percents, rounded half up; the JSON report keeps
  the unrounded values. `0%` and `100%` appear only for bounds that
  are exactly 0 or 1, so `9/10` reads `[55%,  99%]`, not
  `[55%, 100%]`.
- Low and high are right-aligned separately, so brackets, commas and
  the columns after them line up when widths vary.
- A row with a single run gets a blank cell; when no row has more than
  one run the column is omitted, so `--prob-runs=1` output looks as it
  did before intervals existed.
- A row judged by a [confidence sequence](#sequential-stopping)
  (`--prob-stop=sequential`) shows that sequence's interval over every
  run, at its gate's level, tagged `seq`: for a case that may stop as
  soon as it looks clear, a fixed-run interval would claim more than
  the runs support.
- `--prob-no-intervals` / `prob_intervals = false` hide the column.

### Function-level intervals

Below the rows, each bench function with at least `prob_min_inputs`
cases (10 by default) gets one line for the function as a whole, and
an `Overall` line covers every case in the session:

```text
  classify  N=40 inputs × k=10    81.0%  [69.5%, 90.8%]  ρ=0.76
            runs ×2 → interval −1%  ·  inputs ×2 → −29%  ·  each +$0.0400
  triage    N=15 inputs × k=5–10  59.3%  [47.3%, 70.7%]  ρ=0.09
            runs ×2 → interval −15%  ·  inputs ×2 → −29%  ·  each +$0.0125
  Overall   N=55 inputs × k=5–10  75.1%  [66.0%, 83.8%]  ρ=0.56
            runs ×2 → interval −2%  ·  inputs ×2 → −29%  ·  each +$0.0525
```

(ρ and the indented line below each are explained in
[Runs or inputs?](#runs-or-inputs); they appear only when cases have
more than one run.)

- **N inputs × k:** the number of cases (inputs) and the runs each
  had; a range like `k=5–10` when run counts differ.
- **The estimate** is the mean of the per-case pass fractions, so
  every input counts equally however many runs it had. When every
  case has the same number of runs it equals the pooled rate on the
  `Overall:` footer line.
- **The interval** treats the cases as a sample of inputs and each
  case's runs as one correlated group: a cluster bootstrap re-draws the
  cases with replacement — keeping all of a case's runs together —
  `--prob-bootstrap` times (5,000), and the line shows the percentile
  interval of the re-drawn means at `prob_confidence`. Runs of one
  input tend to pass or fail together, so treating them as independent
  would give a range that is too narrow. More inputs narrow it; more
  runs of the same inputs narrow it less.
- **Inputs treated as a sample:** the range allows for a different set
  of inputs doing better or worse. If the cases were picked by hand
  rather than drawn at random, it measures the cases chosen, not inputs
  in general.
- **Too few inputs:** with fewer than `prob_min_inputs` cases the
  bootstrap underestimates the uncertainty, so no line is shown; the
  JSON report still has the estimate, with `suppressed` saying why.
- **Errored runs** count as non-passes, as in the row fraction, even
  under `prob_errors = exclude` (a gate setting).
- **Deterministic:** the bootstrap is seeded (`--prob-seed`, default
  0) and draws from the cases sorted by id, and functions are listed
  by name, so the same results print the same lines — with or without
  pytest-xdist. Bounds have one decimal; `0.0%` and `100.0%` mean
  exactly 0 and 1.
- The `Overall` line is left out when the session has a single bench
  function (it would repeat that function's line), and
  `--prob-no-intervals` hides the whole block. The JSON report's
  `aggregates[]` always has every function and Overall, with a
  normal-approximation interval as a cross-check.

### Runs or inputs?

When the cases have more than one run, each function-level line also
shows **ρ**, and a second line says how much the interval would narrow
with twice the runs or twice the inputs (the example above):

- **ρ** (the intraclass correlation) says how alike the runs of one
  input are, from 0 to 1:
  - **Low ρ** (triage): the outputs vary from run to run, so more runs
    help — here doubling them narrows the interval about half as much
    as doubling the inputs.
  - **High ρ** (classify): each input is consistently right or
    consistently wrong, so more runs of it mostly repeat what you
    already know: add inputs instead.
- **How it is estimated:** a one-way analysis of variance on the
  pass/fail outcome of every run, with inputs as the groups — ICC(1).
  Cases with different run counts use the adjusted average group size
  k₀ = (Σkᵢ − Σkᵢ²/Σkᵢ)/(N − 1). Errored runs count as non-passes, as
  in the estimate. The estimate can come out below 0 when inputs are
  more alike than chance allows; it is clipped to [0, 1].
- **The projection:** an interval over N inputs with k runs each has a
  width proportional to √((1 + (k − 1)ρ)/k)/√N. Doubling the inputs
  always narrows it by 1 − 1/√2, about 29%; doubling every case's runs
  narrows it by that much at ρ = 0 and not at all at ρ = 1. At k = 10
  going to 20 runs:

  | ρ | runs ×2 | inputs ×2 |
  |---|---|---|
  | 0.025 | −22% | −29% |
  | 0.3 | −5% | −29% |
  | 0.6 | −2% | −29% |

  With different run counts per case, k is their harmonic mean, which
  is exact for the equally weighted average. `−<1%` is a change too
  small to round to 1%; `±0%` is none at all (ρ = 1).
- **Cost:** both options double the number of runs, so at the function's
  average cost per run each adds the function's recorded cost again;
  the line assumes new inputs cost as much per run as the existing
  ones. The `each +$…` part appears only when cost was recorded.
- **When it is left out:** when every case ran once (nothing to
  compare within an input), and when every run passed or every run
  failed (no variation to split, so ρ is undefined). Like the interval
  line itself, it needs `prob_min_inputs` cases and is hidden by
  `--prob-no-intervals`. The `Overall` line gets its own ρ over every
  case.
- ρ is an estimate too, and a rough one with few inputs: read the
  projection as a direction, not a promise.
- The JSON report's `aggregates[]` carry `icc` and `width_factor`
  (`null` when ρ is left out), for every function, including those
  without a terminal line.

## Exit status

Without gates, standard pytest semantics: any failed or errored run
makes the session exit nonzero.

With gates, a gated case's failing runs are xfailed, so they never fail
the session themselves; the gate's verdict does. Its [gate
item](#gate-items) fails, which makes pytest exit 1 like any failed
test. Where there is no gate item to fail — `--prob-no-gate-items`, a
gate item skipped under pytest-xdist without `--dist loadgroup`, or one
deselected — `pytest_sessionfinish` sets exit code 1 (`TESTS_FAILED`)
when any gate is FAIL, or UNDECIDED under `prob_undecided = fail`, and
pytest would otherwise have exited 0. A verdict is never counted twice:
it fails one item, and the exit code is 1 either way.

| Session | Exit code |
|---|---|
| Every gate PASS, nothing else failed | 0 |
| UNDECIDED gates only, `--prob-undecided=pass` | 0 |
| A gate FAIL, or UNDECIDED under the default `fail` | 1 |
| A comparison's margin FAIL, or UNDECIDED under the default `fail` | 1 |
| A function's `--prob-margin` FAIL against the baseline, or UNDECIDED under the default `fail` | 1 |
| A latency gate FAIL, or UNDECIDED under the default `fail` | 1 |
| An ungated run failed or errored (whatever the gates) | 1 |
| A failing run in a case with only a latency gate | 1 |
| An errored run in a gated case, `prob_errors = count` | 1 |
| Interrupted, usage error, no tests… | pytest's own code, unchanged |

pytest's last line counts runs and gate items: a session ends `2
failed, 81 passed, 40 xfailed` when two gates fail — one failed gate
item per verdict in the gates block. Without gate items it counts runs
alone and can end `80 passed, 40 xfailed` and still exit 1: the
`Gates:` line and the gates block (or the verdicts in the comparisons
block) say why.
Comparisons without a margin, and a baseline without
`--prob-margin`, never change the exit status. The JSON report's `exit_status` is the final code,
gates included. `--prob-stop=curtail` never changes it either: every
verdict is the one all the runs would give, and the skipped runs are
skips. `--prob-stop=sequential` can: its rate gates are judged by a
different, wider interval, at whatever run they stopped.
