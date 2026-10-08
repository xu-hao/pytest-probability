# Statistics guide

This page explains what the numbers in a pytest-probability summary
mean, what they assume, and how to decide with them. The options and
output layouts are in {doc}`reference`; the report fields are in
{doc}`json-report`. Run with `--prob-explain` and the summary reads its
own notable lines to you in the words used in the next section.

## For non-statisticians

You ran something that can answer differently each time. A case passed
7 of 10 runs. The question is what that says about the case, and
whether it meets your bar. The terms below use the same wording as
`--prob-explain`.

**The interval, `[low, high]`.** The range the true pass rate probably
falls in, given the runs so far. More runs make it narrower. `7/10`
with `[35%, 93%]` means: the case could really pass anywhere from about
a third of the time to nearly always, and ten runs can't narrow that
down. `70/100` gives about `[60%, 79%]`. The confidence level (95% by
default) says how often a range built this way contains the truth.

**PASS, FAIL, UNDECIDED.** A gate is a bar on the pass rate, such as
"at least 90%". PASS: the whole range is above the bar. FAIL: the whole
range is below it. UNDECIDED: the range crosses the bar, so more runs
are needed. `37/40` against a 90% bar is 92.5%, yet its range
`[80%, 98%]` has values on both sides of 90%, so it is UNDECIDED, not
PASS. UNDECIDED fails the session by default (`--prob-undecided=pass`
lets it through). Passing the bar needs enough runs: a 90% bar can't
pass at all with fewer than 36.

**p.** How easily luck alone could explain the gap between two arms
(two prompts, two models): if both really passed equally often, the
chance of a gap at least this big. Small p: luck is an unlikely
explanation. Large p: the data can't tell the arms apart. It is not the
chance that either arm is better, and a large p is not proof that they
are the same. Read the range of the difference first; p is a second
opinion.

**A function-level line, `N inputs × k`.** An average over inputs: each
input's share of passing runs, averaged so every input counts equally.
N is the number of inputs and k the runs each had. Its range comes from
re-drawing the set of inputs at random many times, keeping each input's
runs together, and seeing how far the average moves. It treats your
inputs as a sample.

**ρ.** How alike the runs of one input are, from 0 (an input's runs
differ as much as runs of different inputs) to 1 (every run of an input
gives the same result). Low: outputs vary from run to run, so more runs
help. High: each input is consistently right or wrong, so add inputs
instead.

**pass^k and pass@k.** pass^k is the chance that k runs of the same
input all pass: reliability, for code that has to work every time it is
called. It can only fall as k grows. pass@k is the chance that at least
one of k runs passes: best-of-k, for when a failed attempt can be
retried. It can only rise as k grows. With a pass rate of 80%, pass^3
is about 51% and pass@3 about 99% (if runs were independent).

**decided after.** With `--prob-stop=curtail` a gated case stops as
soon as its verdict can no longer change, whatever its remaining runs
would do: "decided after 12/40" means 12 of its 40 planned runs ran and
the rest were skipped. The verdict is the one all 40 runs would give.
The case's fraction can lean toward its verdict, so averages and
comparisons over such cases are hidden.

**p95 and the latency range.** A latency quantile: p95 is the run time
that 95% of runs finish within. Every run counts, failed ones too. The
range is where the true value probably falls; a dash marks an end that
needs more runs, and the higher the quantile, the more runs its upper
end takes (72 for p95 at 95% confidence).

## Assumptions

Every interval and p-value here rests on a few assumptions. Know them
before you trust a number.

- **Runs of one input are independent.** Run 5 doesn't know about run
  4. The interval on a case's pass rate treats the runs as that many
  coin flips from the same coin.
- **Inputs are a sample.** Function-level intervals ask how much the
  average would move with a different draw of inputs. That is a fair
  question when your inputs are drawn from something larger, such as
  production traffic.
- **A curated suite measures the cases you chose, not a wider
  population.** If you hand-picked the twelve hardest prompts, the
  average describes those twelve. Don't read its interval as the pass
  rate on real traffic. `--prob-explain` says so whenever it reads an
  average.
- **What breaks independence:** a cache that returns the same answer
  after the first call; module-level state that one run leaves for the
  next; a rate limit that makes a burst of runs fail together; a
  provider having a bad minute. Dependent runs make an interval look
  tighter than the evidence supports. Remedies: make the code under
  test stateless, bypass or vary caches, and use `--prob-transpose` and
  `--prob-delay` so one case's runs are spread over time rather than
  back to back.
- **No fixes after looking.** Intervals and p-values are valid for a
  question asked before the data. Tune the bar after seeing the result
  and they are no longer.

## Reading intervals, gates and verdicts

A row shows `passes/runs`, then an interval for the case's true pass
rate. The default method is Clopper-Pearson (`exact`): it never covers
less than the stated level, which makes it a little wide. `wilson` is
narrower and close to the stated level on average; `bayes` gives a
credible interval from a Beta prior. Change it with `--prob-method`,
the level with `--prob-confidence`. Pick one at the start and keep it:
switching methods to get a verdict you like is a way to fool yourself.

Reading rules of thumb:

- A wide interval means not enough runs, not a bad function.
- Compare the whole interval with your bar, not the fraction. 92.5%
  against a 90% bar says little when the range runs from 80% to 98%.
- Roughly four times the runs halve the width; the first runs buy the
  most.
- A verdict is a statement about the interval at your confidence level.
  Across many gated cases some wrong verdicts are expected; that is the
  price of testing many cases at once.

**Gates.** The rate gate (`min_rate`) is PASS when the interval is
entirely above the bar, FAIL when entirely below, and UNDECIDED
otherwise. A count gate (`min_passes`) has no UNDECIDED: it passes when
that many runs passed, and any interval beside it is for information.
See Gates in {doc}`reference`.

**UNDECIDED policy.** UNDECIDED is "not enough data", and it fails the
session by default so that a green run always means a decided case.
`--prob-undecided=pass` accepts it, which suits a smoke check in a
fast loop but not a release gate. When a case keeps landing UNDECIDED,
either its true rate is close to the bar, or it needs more runs than you
give it: `--prob-explain` estimates how many, and `--prob-plan` can
budget them up front.

**Errors policy.** An error (any exception other than a failed assert)
is not a failed assert, but by default `prob_errors = count` treats it
as a non-pass in a gate's sample, and it fails the session. That is the
cautious choice: a broken harness can't hide behind a pass. Use
`prob_errors = exclude` only when errors are infrastructure noise
unrelated to quality, such as a network blip. Then `7/10` with 3 errors
is judged as `7/7`, and an excluded error is not a pass but is also not
counted against the case, so look at how many were excluded. A case
whose every run errored is UNDECIDED.

## Averages over inputs

With at least `prob_min_inputs` cases (10 by default) a bench function
gets a line like `classify  N=40 inputs × k=10  83.0%  [75.5%, 89.8%]`.

- **The estimate** is the mean of the per-case pass fractions, so each
  input counts equally whatever its run count.
- **The interval** is a cluster bootstrap: re-draw whole inputs with
  replacement, keep each input's runs together, recompute the average,
  repeat, and take the middle `prob_confidence` of the results. Keeping
  runs together is the point: runs of one input aren't independent
  evidence about the function, so treating all N×k runs as separate
  samples would give a falsely narrow interval.
- **Too few inputs:** with fewer than 10 the bootstrap range comes out
  too narrow, so it isn't shown.

### Runs or inputs

When the cases have more than one run, the line also shows **ρ** and a
projection of how much doubling the runs, or doubling the inputs, would
narrow the interval (and what each would cost).

- If ρ is near 0, the runs of an input vary like independent coin flips
  and extra runs of the same input help.
- If ρ is near 1, an input is consistently right or consistently wrong,
  and ten more runs of it tell you what you know. Add inputs.
- Doubling inputs always narrows the interval by about 29%. Doubling
  runs does that only at ρ = 0 and nothing at ρ = 1.

LLM suites often land high: the variation between inputs outweighs the
variation within one. So the habit of cranking `--prob-runs` to 100 on
twenty inputs often buys less than writing twenty more inputs. ρ is
itself an estimate, and a rough one with few inputs: read the
projection as a direction. Reference: Runs or inputs? in
{doc}`reference`.

### pass^k and pass@k

`--prob-metric=pass^3,pass@5` adds an average of each input's chance
that k runs all pass (pass^k), or at least one does (pass@k). Each
input's value is an unbiased estimate from its own n runs, `C(c,k)/C(n,k)`
for pass^k with c passes, and averaged with the same cluster bootstrap.
Choose by how the code is used: pass^k when every call has to work (an
agent doing several steps), pass@k when a failure can be retried or
the best of k answers is kept. Inputs with fewer than k runs are left
out, and the note says how many.

## Comparing two arms

A comparison (`compare="prompt"`, `--prob-compare`) pairs each input's
result under one arm (say a new prompt) with its result under the
baseline arm. Pairing removes the variation between inputs, which is
usually the largest source of noise, so a paired comparison can see a
small difference an unpaired one can't.

- **The difference** is in percentage points (pp), the arm's minus the
  baseline's, with an interval. Read the interval first: if it spans
  zero, the data doesn't show which arm is better.
- **p** answers how easily luck alone could explain the gap (see the
  first section). It is computed by a sign-flip test on the per-input
  differences (Fisher's exact test for a single input).
- **One input** gets the Newcombe interval, and its two arms' runs are
  separate samples. With few inputs there is no honest interval across
  them, so you see one line per input; with `prob_min_inputs` or more,
  a paired bootstrap over inputs.

### Margins: non-inferiority and equivalence

"Not different" is not what "no significant difference" means. A
comparison that fails to find a difference with 5 inputs hasn't shown
the arms are the same. To claim that, state how big a difference you
would accept, and require the interval to rule out anything larger:

- **Non-inferiority** (`margin=0.02`): the new arm may be at most 2
  points worse. PASS when the whole interval is above -2 points, FAIL
  when it is entirely below, UNDECIDED otherwise. Use it for a cheaper
  prompt or model: "no worse than 2 points, at a quarter of the cost".
- **Equivalence** (`equivalence=True`): PASS when the whole interval
  lies within plus or minus the margin. Use it when the arm shouldn't
  be better or worse, such as a refactor.

A tight margin needs a lot of data. With 40 inputs of 10 runs each you
can rule out a 5-point drop more easily than a 2-point one; choose the
margin by what matters for the product, then budget the runs, not the
other way round.

### Multiple comparisons

Every comparison is another chance for luck to produce a small p. Test
twenty arms and one will look "significant" at 5% by luck alone. By
default (`--prob-adjust=none`) the p-values are labelled **exploratory**:
good for finding candidates, not for confirming a winner.

- `holm` and `bonferroni` control the chance of even one false
  alarm among the comparisons. Holm is never stricter than Bonferroni.
- `bh` (Benjamini-Hochberg) controls the share of false alarms among
  the small p-values. It is more lenient, and suits screening many arms.
- The adjusted values are the confirmatory ones: decide which
  comparisons you will make, and which adjustment, before looking at the
  results, and report that.

The intervals themselves are not adjusted; the adjustment applies to p.

## Regression gate against a baseline

`--prob-baseline=main.json --prob-margin=0.05` sets this run against an
earlier report, case by case, and gates each function on a
non-inferiority margin: the current pass fraction minus the baseline's,
with the same intervals and p-values as a comparison. Two things to
keep in mind:

- **Noise is on both sides.** Both reports are samples. A change of
  3 points between two runs of ten each is within noise; the interval
  says so, which is why a gate on the interval doesn't flap while a
  comparison of raw fractions does.
- **A tight margin needs many inputs.** With 40 cases of 10 runs each,
  a 2-point margin is UNDECIDED even if nothing changed: the data can't
  rule a 3-point drop out. Either loosen the margin, add inputs, or
  accept UNDECIDED (`--prob-undecided=pass`) for that check.
- It compares the same suite: cases are paired by case id. Renamed or
  new cases are listed and left out.

## Early stopping

`--prob-stop=curtail` skips the remaining runs of a gated case once its
verdict can no longer change. It never changes a verdict: a case stops
only when every way the remaining runs could go gives the same answer.
It saves runs (and cost) on cases that are clearly passing or failing.

The price is that the stopped case's own fraction is no longer an
honest estimate. It stops when it crosses a threshold, so its fraction
leans toward the verdict (3/12 instead of 3/40). Averaging such
fractions would bias the function's mean, the metrics and any
comparison, so the intervals over a function containing a stopped case
are hidden, with a note. If you need those numbers, run without
curtailment. Decide before the run: stopping early *because the
result looks good* is a different thing, and this option doesn't do it.

## Latency

A latency gate (`max_latency=2.0`) judges a quantile of the run times
the same way a rate gate judges a pass rate: PASS when the interval is
entirely below the limit, FAIL when entirely above, UNDECIDED
otherwise.

- The interval is distribution-free: it makes no assumption about the
  shape of run times, which are skewed and long-tailed, only that the
  runs are independent draws. Its ends are two of the observed times.
- High quantiles need many runs. An upper end for p95 at 95%
  confidence needs 72 runs; before that the range is open (`—`) and the
  gate can't pass. For p99 it takes far more. A p50 or p90 is cheaper to
  settle.
- Run times include failed runs (and errored ones, unless a gate leaves
  them out), and measure the bench body, not the delay between runs.
- Latency from back-to-back runs on a shared machine is correlated:
  warm caches, noisy neighbours. Check the assumptions section.
- A latency gate judges speed only. Failing asserts still fail the
  session unless the case also has a rate gate.

## Planning runs

Statistical power is a budget question: the data you need is set by how
close you will be to the bar, not by habit. `--prob-plan` prints, per
case and without running anything, the fewest runs with which a gate
can pass at all, the runs that give it an 80% chance to pass if the case
really passes at a rate you assume (`--prob-plan-assume`), the chance
the planned runs catch a flake of 10% or 1% (`--prob-plan-flake`), and
the projected cost from a previous report.

Two things to remember: a plan is only as good as the pass rate you
assume for the case; and a
flake that fails 1% of runs needs about 300 runs to be caught with 95%
probability. If that is too many, say so and accept the risk knowingly,
rather than reading a clean 20-run result as proof. Details in
Planning in {doc}`reference`.

## Reproducibility

The intervals from a plain count (Clopper-Pearson, Wilson) are
deterministic. Bootstraps and Monte Carlo permutation p-values use
random draws, so they are seeded:

- `--prob-seed` / `prob_seed` (default 0) and `--prob-bootstrap` /
  `prob_bootstrap`, the number of resamples (default 5000), are
  recorded in the JSON report next to every estimate they produced.
- Inputs are drawn in case-id order, so the same results give the same
  numbers, with or without pytest-xdist.
- Different seeds give slightly different bounds. If a verdict flips
  between seeds, it was too close to call: add data rather than
  searching for a seed.
- The results themselves are not reproducible: rerunning a
  nondeterministic benchmark gives different runs. To reproduce a
  report, keep the JSON report, not the command line.

## What to report

When you quote a result, include what a reader needs to judge it:

- [ ] **N**, the number of inputs, and whether they are a sample of
      something or a curated list.
- [ ] **k**, the runs per input (and whether it varied).
- [ ] The **resampling unit**: the input, with runs kept together.
- [ ] The **number of resamples** and the **seed**.
- [ ] The **interval** method and confidence level.
- [ ] **ρ**, if you chose runs and inputs on it.
- [ ] For comparisons: the **margin**, the **adjustment** (`none` is
      exploratory), and the **number of comparisons** made.
- [ ] Whether the analysis was **exploratory or confirmatory**: the
      bar, margin and arms were chosen before looking at the results.
- [ ] Any **errors** and how they were treated (`prob_errors`), and
      cases that **stopped early**.

The JSON report holds all of these (`stats_config`, `aggregates[]`,
`comparisons[]`), so quote from it, not from memory.

## Inspired by

The statistical design is inspired by and informed by:

- [pytest-repeated](https://github.com/sinan-ozel/pytest-repeated):
  statistical (frequentist and Bayesian) pass/fail decisions for
  repeated tests.
- Indeed Engineering, [Bootstrap confidence intervals for LLM
  evaluation](https://engineering.indeedblog.com/blog/2026/07/bootstrap-confidence-intervals-for-llm-evaluation/)
  (2026): resampling whole inputs, intraclass correlation, the
  trade-off between more runs and more inputs, paired comparisons, and
  what to report.
- E. Miller, [Adding Error Bars to Evals](https://arxiv.org/abs/2411.00640)
  (2024)
- Chen et al., [pass@k](https://arxiv.org/abs/2107.03374) (2021)
- Yao et al., [τ-bench pass^k](https://arxiv.org/abs/2406.12045) (2024)
- Turner & Grünwald, [anytime-valid inference](https://arxiv.org/abs/2203.09785)
