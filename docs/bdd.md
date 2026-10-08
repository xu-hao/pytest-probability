# Benchmark-driven development

A *semantic function* is a function whose implementation is a model
call: `triage(text) -> Category`, `summarize(thread) -> str`. Its
signature is code, but its behavior is a **distribution** — the honest
contract is not "returns `billing` for refund emails" but "returns
`billing` for refund emails at least 95% of the time, under $0.001 a
call."

Test-driven development gives you a loop — red, green, refactor — built
on an instrument that reads true or false. Pointed at a distribution,
that instrument lies in both directions: a wrong prompt can pass a
single-shot test on a lucky sample, and a good prompt can fail one on
an unlucky sample. Benchmark-driven development keeps the loop and
swaps the instrument: **the unit of feedback is the pass fraction.**

- **Red** is a low fraction, not a red cross: `6/10 FLAKY`.
- **Green** is a fraction meeting the bar you chose: `10/10`, or
  ≥ 95% by policy.
- **Refactor** means changing the prompt or model — compared on an
  axis against the incumbent, same cases, same sample size, cost
  attached.
- The benchmark stays forever as the regression net, exactly like the
  test you keep after fixing a bug.

The three outcome classes keep the feedback clean while you iterate:
`FAIL`/`FLAKY` rows are prompt problems, `ERRORED` rows are harness
problems — different fixes, never blurred into one signal.

## When a prompt fails in production

The loop most teams meet first is not greenfield — it's an incident.
A support bot misroutes a real message: *"my card was charged twice"*
lands in the wrong queue. Someone tries the message once in a
playground, gets the right answer, and the ticket drifts toward
*cannot reproduce*.

That is the single-shot trap. A semantic bug is usually not "the
prompt always fails on this input" — it's "the prompt is a coin flip
on this *shape* of input, and production finally sampled the bad
side."

```{important}
**The reproduction of a semantic bug is a fraction, not a red test.**

A semantic bug won't reproduce in one run — and can't hide from ten.
The playground retry that "works now" is a sample size of one; the
`6/10 FLAKY` row is the reproduction.
```

## The loop, applied to the incident

### 1. Reproduce: turn the incident into a case

Take the exact production input and pin its expected label as a case —
alongside the cases you already trust — then sample it:

```python
# bench_triage.py
import pytest
from pytest_probability import record_usage

PROMPT_V1 = "Categorize: {text}"

@pytest.mark.parametrize("text,expected", [
    # the production incident, verbatim
    pytest.param("my card was charged twice", "billing", id="refund"),
    # known-good coverage
    pytest.param("i cannot reset my password", "account", id="password"),
    pytest.param("the box arrived two weeks late", "shipping", id="late_box"),
])
def bench_triage(text, expected):
    response = llm.complete(PROMPT_V1.format(text=text))
    record_usage(model=response.model, input_tokens=response.input_tokens,
                 output_tokens=response.output_tokens, cost=response.cost)
    assert response.category == expected
```

```text
$ pytest bench_triage.py --prob-runs=10
...
  triage::refund-v1     6/10  [26%,  88%]  $0.0002  FLAKY
  triage::password-v1   8/10  [44%,  97%]  $0.0002  FLAKY
  triage::late_box-v1  10/10  [69%, 100%]  $0.0002

  Overall: 24/30 passed (80%)
```

Reproduced — and better than reproduced: the incident case fails 4
times in 10, and the sampling caught a *second* liability
(`password`, 8/10) nobody had filed a ticket for yet. This table is
the red state.

### 2. Fix: put the candidate prompt on an axis

Don't edit the prompt in place — add the candidate next to the
incumbent, so the comparison runs on the same cases, the same sample
size, with cost attached:

```python
PROMPTS = {
    "v1": "Categorize: {text}",
    "v2": ("You are a support triage bot. Categorize the message into "
           "exactly one of: billing, account, shipping. Reply with the "
           "category word only.\n\nMessage: {text}"),
}

@pytest.mark.probability(compare="prompt")         # v2 against v1, per case
@pytest.mark.parametrize("prompt", ["v1", "v2"])
@pytest.mark.parametrize("text,expected", [...])   # same cases
def bench_triage(text, expected, prompt):
    response = llm.complete(PROMPTS[prompt].format(text=text))
    ...
```

```text
  triage::refund-v1     6/10  [26%,  88%]  $0.0002  FLAKY
  triage::refund-v2    10/10  [69%, 100%]  $0.0006
  triage::password-v1   8/10  [44%,  97%]  $0.0002  FLAKY
  triage::password-v2   9/10  [55%,  99%]  $0.0006  FLAKY
  triage::late_box-v1  10/10  [69%, 100%]  $0.0002
  triage::late_box-v2  10/10  [69%, 100%]  $0.0006
...
=========================== probability: comparisons ===========================
  triage[prompt]  v2 − v1  +16.7 pp  p=0.50  3 paired  cost ×3.0
      late_box  10/10 vs 10/10    0 pp [−28, +28]  p=1
      password    9/10 vs 8/10  +10 pp [−24, +42]  p=1
      refund     10/10 vs 6/10  +40 pp [ +4, +69]  p=0.09
```

Now the decision is a table, not a vibe — and the comparison block
keeps it honest. On the incident case v2's gain is real: +40 points,
and even the low end of its interval (+4) is a gain. On `password` the
9/10 against 8/10 is within the noise ([−24, +42] includes no change),
and `late_box` is a tie. Averaged over the three cases v2 is 16.7
points ahead at three times the cost per run, but three inputs are too
few for an interval over inputs (that needs `prob_min_inputs`, 10 by
default); add cases before reading much into the average. Whether a
fixed incident is worth the price is a product call; the point is you
are making it with numbers.

### 3. Guard: the benchmark outlives the fix

To hold a candidate to the incumbent in CI instead, give the
comparison a margin: with `compare="prompt", margin=0.02` the session
passes once v2's interval shows it at most 2 points worse than v1, and
like a gate it fails while v2 is worse or the data can't tell yet —
which, with fewer than 10 cases and so no interval, is always. See
Comparisons in {doc}`reference`.

Promote v2, delete the v1 axis value, and keep the benchmark in CI —
the incident case is now a permanent regression sentinel, and every
future prompt tweak reruns it ten times. For a CI gate softer than
perfection, gate the case on the contract's rate, so its verdict
rather than each run decides the exit status:

```python
@pytest.mark.parametrize("text,expected", [
    pytest.param("my card was charged twice", "billing", id="refund",
                 marks=pytest.mark.probability(min_rate=0.95, runs=100)),
    ...
])
def bench_triage(text, expected):
    ...
```

The case passes once its interval lies above 95%, fails when it lies
entirely below, and stays UNDECIDED — failing CI by default — while the
runs can't tell. Proving a high rate takes runs: 95% at 95% confidence
needs at least 72 runs even if all of them pass, and the plugin warns
at collection when a gate asks for more than its runs can show. See
{doc}`running` for the exit status.

## The habit

Greenfield benchmark-driven development is the same loop starting one
step earlier: write the cases *before* the prompt — they are the
behavior spec — take your red `0/N` run, and iterate prompts up the
fractions with the cost column keeping score. Every production
incident thereafter follows the path above: verbatim input → case →
fraction → fix on an axis → sentinel forever.
