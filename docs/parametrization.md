# Parametrization and selection

There is exactly one way to declare cases: `@pytest.mark.parametrize`.
The plugin adds no declaration syntax of its own — it changes what a
parametrized function *means* (every combination becomes a fraction
row sampled N times) without changing how you write it.

## Axes and ids

Every decorator is an axis; stacked decorators cross-product. Ids
compose exactly like pytest's: bottom decorator leftmost, top varies
fastest, `pytest.param(id=...)` and `ids=` override per value.

```python
import pytest

@pytest.mark.parametrize("style", ["terse", "chain_of_thought"])
@pytest.mark.parametrize("text", [
    pytest.param("my card was charged twice", id="refund"),
])
def bench_triage(text, style):
    answer = my_classifier(text, prompt_style=style)
    assert answer == "billing"
```

```text
  triage::refund-terse              8/10  [44%,  97%]  $0.0010  FLAKY
  triage::refund-chain_of_thought  10/10  [69%, 100%]  $0.0040
```

Two rows, one comparison: the cheap prompt is a coin flip, the
expensive one holds — with the price difference right next to it.

Row identity is the function's short name plus the full composed id
(`triage::refund-terse`); items are
`bench_x.py::bench_triage::refund-terse[run3]`.

## Which axis is "the case"?

Whichever you want — there is no privileged axis. A useful convention:
put the *inputs* on one axis with explicit `id=`s, and the
*configurations you are comparing* (models, prompts, thresholds) on
their own axes with value-derived ids. The composed id then reads
`<input>-<config>`, and the summary rows group naturally.

## Marks

Marks are pytest's selection metadata, and they work on bench items
exactly as on tests:

```python
@pytest.mark.parametrize("text,expected", [
    pytest.param("hallo", "greeting", id="german",
                 marks=[pytest.mark.eu, pytest.mark.slow]),   # one case
    pytest.param("bad case", "x", id="broken",
                 marks=pytest.mark.skip(reason="not yet")),   # skip one case
])
@pytest.mark.llm                                              # every case
def bench_classify(text, expected): ...
```

- `skip` / `skipif` / `xfail` behave exactly as in pytest; skipped
  items never enter the fractions.
- Custom marks select with `-m`: `-m "llm and eu"`, `-m "not slow"`.
- Register custom marks in your pytest config to avoid unknown-mark
  warnings (and use `--strict-markers` to make typos fail):

  ```ini
  [pytest]
  markers =
      fast: quick cases, safe for pre-commit
      slow: expensive cases, nightly only
  ```

```{note}
`indirect=True` is **not** supported: it routes parameters through
fixtures, and bench items do not participate in pytest's fixture
system. See {doc}`internals` for the reasoning.
```

## Selecting cases

Item ids contain the composed case id, so pytest's whole selection
toolbox applies with no plugin-specific filters:

```bash
pytest benchmarks/ -k identify_pii            # one case, all runs
pytest benchmarks/ -k bench_triage            # one benchmark function
pytest benchmarks/ -k "refund and not terse"  # expressions over ids
pytest benchmarks/ -m "fast and not eu"       # marks
pytest "benchmarks/bench_x.py::bench_triage::refund-terse[run3]"  # one run
```

One sharp edge inherited from pytest: the `-k` expression grammar
accepts brackets and dashes but **rejects `=`** — select on id
substrings and mark names, not key=value pairs. If a value must be
selectable, give it an id-visible axis (its value text lands in the
id) or a mark.
