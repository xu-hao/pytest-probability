# Installation

## Requirements

- Python 3.11 or newer
- pytest 7.4 or newer

The plugin's only runtime dependency is `pytest` itself.

## Install

Until the package is published to PyPI, install from the repository:

```bash
pip install git+ssh://git@github.com/xu-hao/pytest-probability.git
```

or from a local checkout, editable for development:

```bash
git clone git@github.com:xu-hao/pytest-probability.git
pip install -e pytest-probability
```

## Verify the installation

pytest discovers the plugin automatically through its `pytest11` entry
point — there is nothing to enable. Two quick checks:

```text
$ pytest --version -v
...
registered plugins:
  probability-0.2.0 at .../pytest_probability/plugin.py
```

```text
$ pytest --help | grep -A2 prob-runs
  --prob-runs=N         Run every case N times (default: prob_runs ini
                        or 1)
```

The header of every test session also lists it:

```text
plugins: probability-0.2.0
```

## Disabling the plugin

For a single session:

```bash
pytest -p no:probability
```

With the plugin disabled, `bench_*.py` files are simply not collected;
your regular tests are unaffected either way.
