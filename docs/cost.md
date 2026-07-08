# Cost and token usage

Sampling nondeterministic code N times has a literal price when the code
calls a paid API. The plugin makes spend visible next to the fractions
it bought — per row, in total, and broken down by model and token type:

```text
  [triage::refund-terse] triage              8/10  $0.0010  FLAKY
  [triage::refund-chain_of_thought] triage  10/10  $0.0040

  Overall: 18/20 passed (90%)
  Cost:    $0.0050
  Tokens:  m-small  1,200 in / 80 out / 640 cached  $0.0010
           m-large  4,800 in / 900 out              $0.0040
```

That table is an argument: the chain-of-thought prompt is four times
the price and eliminates the flakiness — now the trade-off is a number,
not a feeling.

## Detailed usage: `TokenUsage`

Attach one `TokenUsage` per model the step touched:

```python
from pytest_probability import StepResult, TokenUsage

def bench_triage(text, style):
    response = client.complete(prompt(text, style))
    yield StepResult(
        label="triage",
        passed=grade(response),
        usage=[
            TokenUsage(
                model=response.model,
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
                cached_input_tokens=response.usage.cache_read_tokens,
                cost=price_of(response),
            ),
        ],
    )
```

`usage` is a list because one step may call several models — a router
plus a worker, a generator plus a judge. Each entry carries the token
types separately (`input_tokens`, `output_tokens`,
`cached_input_tokens`) so cache effectiveness stays visible instead of
disappearing into a blended number.

Entries are duck-typed: any object with those attribute names works,
and **plain dicts are accepted too** — handy when the numbers come
straight from an API response:

```python
yield StepResult(label="llm", passed=ok, usage=[
    {"model": "m-big", "input_tokens": 50, "output_tokens": 5,
     "cached_input_tokens": 30, "cost": 0.04},
])
```

Pricing is yours: the plugin does not know provider price sheets, it
sums what you report.

## Simple usage: `cost`

When you only care about the total, skip `usage` and set the scalar:

```python
yield StepResult(label="llm", passed=ok, cost=0.0002)
```

## How the numbers combine

- A step's **cost** is its explicit `cost` if set, otherwise the
  sum of its `usage` entries' `cost`. (If you set both, the
  explicit scalar wins for row/total cost, while the per-model token
  numbers still aggregate — so don't double-report.)
- **Rows** sum step cost across all runs of the `(case, label)`
  pair, and merge `usage` per model.
- **Totals**: the `Cost:` line sums all rows; the `Tokens:` block
  merges all rows per model. Both appear only when something was
  recorded; the `cached` segment appears only when non-zero.

## Where it all shows up

- **Terminal summary** — `$` per row, `Cost:` overall, and the
  per-model `Tokens:` block.
- **JSON report** — `rows[].cost`, `rows[].usage` (keyed by
  model), `totals.cost`, `totals.usage`, and the raw per-step
  `usage` lists in `records`; see {doc}`json-report`.

## Practical notes

- The `cost` field is currency-neutral — report whatever unit you
  like, consistently. The `$` in the terminal is a money marker, not a
  currency claim.
- Steps report usage independently of pass/fail — failed runs cost
  money too, which is precisely the kind of thing you want visible.
- When comparing variants for price, remember the per-row figure is the
  sum over N runs, so divide by `--prob-runs` for per-call cost.
