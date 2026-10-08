# pytest-probability

**A semantic bug won't reproduce in one run — and can't hide from ten.**

pytest-probability is a pytest plugin for testing nondeterministic code:
LLM-driven functions, model inference, integration points, anything
flaky-prone. A single-shot test samples a distribution once and calls the
result truth. This plugin runs every case N times and reports the
**empirical pass fraction** per case, so `7/10 FLAKY` stops hiding
inside a green checkmark.

```text
$ pytest benchmarks/ --prob-runs=10
...
================================= probability ==================================
  classify::is_question            10/10  [69%, 100%]  $0.0020
  classify::identify_pii            7/10  [35%,  93%]  $0.0020  FLAKY
  classify::extract_amount          0/10  [ 0%,  31%]  $0.0020  FAIL
  triage::refund-terse              8/10  [44%,  97%]  $0.0010  FLAKY
  triage::refund-chain_of_thought  10/10  [69%, 100%]  $0.0040

  Overall: 35/50 passed (70%)
  Cost:    $0.0110
  Tokens:  m-small  1,200 in / 80 out / 640 cached  $0.0010
           m-large  4,800 in / 900 out              $0.0040
```

## Vocabulary

Six words, used consistently across these docs:

benchmark file
: A `bench_*.py` module, collected by the plugin.

benchmark
: One `bench_*` function inside it — a parametrized generator.

case
: One parameter combination of a benchmark, named by its composed
  pytest id (`identify_pii`, `refund-terse`). An unparametrized
  benchmark is a single case.

run
: One execution of a case — one pytest item
  (`bench_x.py::bench_classify::identify_pii[run3]`). Lands in exactly
  one of three classes: a clean return **passes**, an `AssertionError`
  **fails**, any other exception **errors**.

row
: The aggregate of all runs of one case — a fraction, a status, and a
  cost in the summary table.

## Why fractions?

An LLM classifier that answers correctly 70% of the time will pass a
conventional test most days and fail it on the others. Both observations
are noise; the signal is the fraction. pytest-probability makes the
fraction the unit of reporting:

Every run lands in one of three classes — **pass**, **fail**, or
**error** — and the row status names the combination:

- **pass** — every run passed. Trustworthy.
- **flaky** — passes and fails mixed. This is the row that pays for
  the plugin: it looks green on a lucky day.
- **errored** — passes and errors mixed (`7/10  3 ERRORED`): the model
  never answered wrong, but the harness dropped runs. Different fix,
  different word.
- **fail** — no run passed, at least one real failure. Broken, at
  least deterministically so.
- **error** — every run errored. Your harness, not your model.

The fraction is always over all runs — `7/10` never dresses up as
`7/7` — while the status word tells you *which kind* of trouble made
up the gap.

## A/B-testing two prompts

The bread-and-butter use: put the prompts on a parametrize axis, and
every case splits into one fraction row per prompt — same inputs, same
sample size, cost attached:

```python
import pytest
from pytest_probability import record_usage

PROMPTS = {
    "terse": "Category for: {text}. One word.",
    "chain_of_thought": "Think step by step, then categorize: {text}",
}

@pytest.mark.parametrize("prompt", ["terse", "chain_of_thought"])
@pytest.mark.parametrize("text", [
    pytest.param("my card was charged twice", id="refund"),
])
def bench_triage(text, prompt):
    response = llm.complete(PROMPTS[prompt].format(text=text))
    record_usage(model=response.model,
                 input_tokens=response.input_tokens,
                 output_tokens=response.output_tokens,
                 cost=response.cost)
    assert response.category == "billing"
```

```text
$ pytest bench_triage.py --prob-runs=10 --prob-compare=prompt
...
  triage::refund-terse              8/10  [44%,  97%]  $0.0010  FLAKY
  triage::refund-chain_of_thought  10/10  [69%, 100%]  $0.0040

  Overall: 18/20 passed (90%)
  Cost:    $0.0050
  Tokens:  m-small  1,200 in / 80 out / 640 cached  $0.0010
           m-large  4,800 in / 900 out              $0.0040
=========================== probability: comparisons ===========================
  triage[prompt]  chain_of_thought − terse  +20 pp [−11, +51]  p=0.47  1 paired  cost ×4.0
```

`--prob-compare=prompt` sets each prompt against the first on the same
inputs. Here the honest reading is that the data can't tell yet: on
one input, +20 points has a 95% interval from −11 to +51, and p = 0.47
says a gap this big would turn up by chance about half the time even
if the prompts were equally good. What the line does settle is the
price: chain_of_thought costs four times as much per run. Add inputs
and the comparison becomes a paired average over them; give it a
`margin=` and it becomes a gate. Select one arm with `-k terse`, add a
third prompt by adding a string (each is compared with the baseline),
compare models the same way with a second axis. See
[Comparisons](reference.md#comparisons).

## Benchmark-driven development

The workflow this enables is TDD with the instrument swapped: for a
semantic function, **red is a low fraction, green is a fraction meeting
your bar, and refactor means beating the incumbent prompt on an axis.**
When a prompt fails in production, the loop starts from the incident:
turn the verbatim input into a case, sample it — `6/10 FLAKY` *is* the
reproduction a single playground retry would have closed as "cannot
reproduce" — fix against the incumbent on the same table, and keep the
case forever as a regression sentinel. The full loop, worked end to
end: {doc}`bdd`.

## Highlights

- **One pytest item per (case, run)** — `-k`, `-m`, `-x`, `--lf`, JUnit
  XML, and [pytest-xdist](https://pypi.org/project/pytest-xdist/) all
  operate per run, no special casing.
- **Zero declaration surface** — every `bench_*` function in a
  `bench_*.py` module is a benchmark, exactly like `test_*` in
  `test_*.py`; cases are stock `@pytest.mark.parametrize`, named with
  `pytest.param(id=...)`, tagged with marks. No DSLs, no data files,
  no plugin classes to declare with.
- **Fractions per combination** — every parameter combination gets its
  own row and pass fraction, stacked axes cross-product with pytest's
  exact id rules, and an unparametrized function runs as a single case.
- **Cost and token accounting** — `record_usage()`/`record_cost()`
  attribute spend to the current run (surviving failed asserts) and
  yield per-row cost plus a per-model input/output/cached token
  breakdown.
- **Machine-readable output** — `--prob-json` writes fractions,
  statuses, and raw per-run records to a single file.
- **One dependency: pytest.**

## Documentation

```{toctree}
:maxdepth: 2
:caption: User guide

installation
quickstart
bdd
cases
parametrization
running
cost
json-report
```

```{toctree}
:maxdepth: 1
:caption: Reference

reference
changelog
```

```{toctree}
:maxdepth: 2
:caption: Developer guide

internals
contributing
```
