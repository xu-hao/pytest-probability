# Reference

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
  `method=` and `prior=` alone don't create a gate.
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

### Known limitation

A failed gate has no failing item: the runs passed or were xfailed. So
JUnit XML (`--junitxml`) records no failure for it, and `--lf` has no
failed items to rerun. The exit status and the JSON report do carry
the verdict. Gate items for JUnit XML and `--lf` are planned.

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

## Python API

Everything importable lives in the top-level package:

```python
from pytest_probability import (
    InfeasibleGateWarning, TokenUsage, record_cost, record_usage,
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
  Cost:    $0.0110                                 # only when cost was recorded
  Tokens:  m-small  1,200 in / 80 out / 640 cached  $0.0010   # per model,
           m-large  4,800 in / 900 out              $0.0040   # only with usage
  Report:  report.json                             # only with --prob-json
```

The section renders only when at least one benchmark item ran. With
`--prob-metric`, a [`probability: metrics`](#metrics) section follows
it. When a function is [compared](#comparisons), a `probability:
comparisons` section comes next, before the gates block.

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
- PASS cases are only counted, on the `Gates:` line. `(allowed)` marks
  UNDECIDED cases that `--prob-undecided=pass` lets through.

### The explain section

When any gate is FAIL or UNDECIDED, or any comparison or metric is
shown, the summary ends with a hint:

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
  Full guide: https://pytest-probability.readthedocs.io/en/latest/reference.html#gates
```

- Text wraps to the terminal width; headings and the link don't.
- "About N runs" is the smallest total that would settle the verdict
  if the observed pass rate held, computed with the gate's own
  interval and rounded up to two significant figures. When the rate is
  too close to the bar to settle within 10,000 runs, the reading says
  so instead.
- Count gates (`min_passes`) are explained in counts; errored runs
  left out under `prob_errors = exclude` are called out.
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
the session themselves; the gate's verdict does. After every run,
`pytest_sessionfinish` sets exit code 1 (`TESTS_FAILED`) when any gate
is FAIL, or UNDECIDED under `prob_undecided = fail`, and pytest would
otherwise have exited 0.

| Session | Exit code |
|---|---|
| Every gate PASS, nothing else failed | 0 |
| UNDECIDED gates only, `--prob-undecided=pass` | 0 |
| A gate FAIL, or UNDECIDED under the default `fail` | 1 |
| A comparison's margin FAIL, or UNDECIDED under the default `fail` | 1 |
| An ungated run failed or errored (whatever the gates) | 1 |
| An errored run in a gated case, `prob_errors = count` | 1 |
| Interrupted, usage error, no tests… | pytest's own code, unchanged |

pytest's last line counts runs, not gates, so a session can end
`137 passed, 43 xfailed` and still exit 1: the `Gates:` line and the
gates block (or the verdicts in the comparisons block) say why.
Comparisons without a margin never change the exit status. The JSON report's `exit_status` is the final code,
gates included.
