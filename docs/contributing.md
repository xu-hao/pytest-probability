# Contributing

## Development setup

The project uses a plain virtual environment; [uv](https://docs.astral.sh/uv/)
shown here, `python -m venv` + `pip` works identically:

```bash
git clone git@github.com:xu-hao/pytest-probability.git
cd pytest-probability
uv venv
uv pip install -e .
```

The plugin needs nothing beyond pytest. The `test` extra adds the
reference libraries the `stats` module is cross-checked against
(scipy, statsmodels) plus pytest-xdist; without them those
cross-checks skip and the rest of the suite still runs:

```bash
uv pip install -e '.[test]'
.venv/bin/python -m pytest tests -q
```

## How the tests work

The suite is end-to-end by design: every test uses pytest's own
[`pytester`](https://docs.pytest.org/en/stable/reference/reference.html#pytester)
fixture to write a case file into a temp directory, run a real
in-process pytest session with the plugin active, and assert on
outcomes, item ids, and the rendered summary.

The one exception is `tests/test_stats.py`: the `stats` module is pure
functions, so it is unit-tested directly, against scipy/statsmodels
reference values.

```python
def test_fraction_and_flaky_in_summary(pytester):
    pytester.makepyfile(bench_flaky=BENCH_FLAKY)
    result = pytester.runpytest("--prob-runs=2")
    result.assert_outcomes(passed=1, failed=1)
    result.stdout.fnmatch_lines(["*= probability =*", "*check::wobbly  1/2  FLAKY*"])
```

Conventions that keep the suite honest:

- **Assert behavior, not internals** — outcomes, ids, summary lines,
  JSON content. Internals are free to move.
- **Deterministic "flakiness"** — sample benches use module-level call
  counters (`pass on even runs`), never RNGs, so expected fractions are
  exact.
- `fnmatch_lines` treats `[` as a character class; use `?` as a
  single-character wildcard when matching bracketed ids
  (`"*wobbly?run1?*"`).
- Timing-sensitive tests (`--prob-delay`) assert generous lower bounds
  (sleep floors), not upper bounds tight enough to flake on slow CI.

## Adding a feature: the checklist

Using "add an option" as the template — most features are one:

1. `pytest_addoption()`: add the `--prob-*` flag to the `probability`
   group, and the matching `parser.addini()` default when a project-wide
   setting makes sense.
2. Add a small `_name(config)` resolver (CLI value, else ini, else
   default) next to `_runs`/`_delay`.
3. Implement, honoring the invariants listed at the end of
   {doc}`internals` — especially: anything crossing to the aggregator
   goes through `user_properties` as plain data, and nothing may break
   under xdist.
4. Add `pytester` tests: the behavior itself, the default, and the
   ini fallback if there is one.
5. Document: the option tables in {doc}`reference` and the README, the
   relevant guide page, and {doc}`changelog`.

## Docs

Documentation is Sphinx + [MyST](https://myst-parser.readthedocs.io/)
Markdown with the [furo](https://pradyunsg.me/furo/) theme:

```bash
uv pip install -r docs/requirements.txt
.venv/bin/python -m sphinx -W -b html docs docs/_build/html
```

`-W` turns warnings into errors; Read the Docs builds with
`fail_on_warning`, so a warning-clean local build is the bar. Pages are
plain Markdown — cross-reference with `` {doc}`page` `` and keep
command output in fenced `text` blocks.

## Release checklist

1. Update the version in `pyproject.toml` and
   `src/pytest_probability/__init__.py`, and date the
   {doc}`changelog` entry.
2. Run the suite and a strict docs build (commands above).
3. Tag: `git tag v0.x.y && git push --tags`.
4. Build and publish (once the package is public):
   `uv build && uv publish`.
5. Open the next `(unreleased)` changelog section.
