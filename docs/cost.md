# Cost and token usage

Sampling nondeterministic code N times has a literal price when the code
calls a paid API. The plugin makes spend visible next to the fractions
it bought — per row, in total, and broken down by model and token type:

```text
  triage::refund-terse              8/10  $0.0010  FLAKY
  triage::refund-chain_of_thought  10/10  $0.0040

  Overall: 18/20 passed (90%)
  Cost:    $0.0050
  Tokens:  m-small  1,200 in / 80 out / 640 cached  $0.0010
           m-large  4,800 in / 900 out              $0.0040
```

That table is an argument: the chain-of-thought prompt is four times
the price and eliminates the flakiness — now the trade-off is a number,
not a feeling.

## Recording usage: `record_usage`

Call `record_usage` from inside the bench function, once per model
call:

```python
import pytest
from pytest_probability import record_usage

@pytest.mark.parametrize("text", CASES)
def bench_triage(text):
    response = client.complete(prompt(text))
    record_usage(
        model=response.model,
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
        cached_input_tokens=response.usage.cache_read_tokens,
        cost=price_of(response),
    )
    assert grade(response)
```

It accepts the fields as keywords (above), a `TokenUsage` instance, or
any duck-typed object/dict with the same attribute names — handy when
the numbers come straight from an API response:

```python
record_usage({"model": "m-big", "input_tokens": 50, "output_tokens": 5,
              "cached_input_tokens": 30, "cost": 0.04})
```

Call it as many times as the run makes calls — a router plus a worker,
a generator plus a judge — and entries aggregate per model. The token
types stay separate (`input_tokens`, `output_tokens`,
`cached_input_tokens`) so cache effectiveness remains visible instead
of disappearing into a blended number.

**Record before you assert.** Usage recorded earlier in the body
survives a failing assert — a wrong answer never loses the money it
cost, which is precisely the kind of thing you want visible:

```python
record_usage(model=response.model, ..., cost=0.03)
assert response.answer == expected      # fails -> the $0.03 still reports
```

Pricing is yours: the plugin does not know provider price sheets, it
sums what you report.

## Recording plain cost: `record_cost`

When there is no token breakdown to report — a flat per-call fee, a
metered tool — record the number directly:

```python
from pytest_probability import record_cost

def bench_search(query):
    record_cost(0.0002)
    assert search(query).ok
```

## How the numbers combine

- A run's **cost** is the sum of its `record_cost` amounts plus the
  `cost` of every `record_usage` entry.
- **Rows** sum run cost across all runs of the case, and merge usage
  per model.
- **Totals**: the `Cost:` line sums all rows; the `Tokens:` block
  merges all rows per model. Both appear only when something was
  recorded; the `cached` segment appears only when non-zero.
- Calling either function **outside a bench run** raises
  `RuntimeError` — they only make sense with a run to attribute to.

## Where it all shows up

- **Terminal summary** — `$` per row, `Cost:` overall, and the
  per-model `Tokens:` block.
- **JSON report** — `rows[].cost`, `rows[].usage` (keyed by model),
  `totals.cost`, `totals.usage`, and per-run `cost`/`usage` in
  `records`; see {doc}`json-report`.

## Practical notes

- The `cost` field is currency-neutral — report whatever unit you
  like, consistently. The `$` in the terminal is a money marker, not a
  currency claim.
- When comparing variants for price, remember the per-row figure is the
  sum over N runs, so divide by `--prob-runs` for per-call cost.
