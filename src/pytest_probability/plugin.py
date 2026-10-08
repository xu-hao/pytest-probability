"""pytest-probability — empirical pass probabilities for flaky-prone tests.

Collects ``bench_*.py`` files the way pytest collects ``test_*.py``:
every module-level ``bench_*`` function is a benchmark — an ordinary
test body that asserts. Each parameter combination is a case; every
case runs N times; and a pass-fraction summary — ``7/10 FLAKY`` —
renders after pytest's own output.

There is no authoring surface beyond stock pytest:
``@pytest.mark.parametrize`` supplies the cases (values arrive as
function arguments, ids compose pytest-style), ``pytest.param`` names
and marks individual cases, ``-k``/``-m`` select them, a function with
no parametrize marks simply runs as a single case, and the body
``assert``\\ s. The only additions are ``record_usage``/``record_cost``
for attributing spend to a run.

Design notes:

- Multi-run execution is modeled as one pytest item per (case, run),
  so ``-k``, ``-x``, xdist, and JUnit XML all apply per run.
- Run outcomes are three classes: a clean return passes, an
  ``AssertionError`` fails (the code answered wrong), any other
  exception errors (the harness broke). ``pytest.skip``/``xfail``
  bypass recording entirely.
- Results ride on ``report.user_properties`` as plain dicts so the
  aggregate survives pytest-xdist's worker-to-controller serialization.
- A pass fraction only exists across items, so it cannot be attached to
  any single test outcome; it is rendered in ``pytest_terminal_summary``.
- A gate (``@pytest.mark.probability(min_rate=...)``) judges a case on
  its interval instead. Its failing runs are reported as xfailed, the
  aggregator decides the verdict from the counts, and
  ``pytest_sessionfinish`` turns a failed gate into a failed session.
- A function with enough cases also gets a function-level line: the
  mean of its per-case fractions, with a seeded cluster-bootstrap
  interval over its cases (``aggregate()``), and so does the session.
- ``--prob-metric`` adds pass^k and pass@k: unbiased per-case
  estimates, averaged over cases by ``aggregate()`` the same way.
- ``--prob-baseline`` pairs the cases with an earlier JSON report's by
  case id and compares each function, current − baseline, through the
  same ``compare_pairs()`` as an axis comparison; ``--prob-margin``
  makes that a gate.
- Every run's ``elapsed`` gives each case a latency quantile with a
  distribution-free order-statistic interval (``LatencySpec``);
  ``max_latency=`` gates on it (lower is better) and ``--prob-latency``
  shows it.
- ``--prob-stop=curtail`` skips a gated case's remaining runs once
  ``Gate.settled()`` finds that no way they could go changes its
  verdict (``Curtailer``), so verdicts are those of running every run.
"""
from __future__ import annotations

import ast
import contextvars
import dataclasses
import fnmatch
import functools
import hashlib
import importlib.util
import inspect
import json
import math
import re
import statistics
import sys
import time
import warnings
from collections import Counter, OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Container, Iterable, Iterator, NamedTuple

import pytest

from . import stats

# ---------------------------------------------------------------------------
# Author-facing API
# ---------------------------------------------------------------------------


@dataclass
class TokenUsage:
    """Token accounting for one model's calls within a run.

    A run may record several of these — one per model it touched.
    """

    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    cost: float = 0.0


class _RunRecorder:
    """Per-run accumulator fed by ``record_usage``/``record_cost``."""

    __slots__ = ("usage", "cost")

    def __init__(self) -> None:
        self.usage: list[dict[str, Any]] = []
        self.cost = 0.0


# The active run's recorder. ContextVar (not a global) so parallel
# in-process runs — e.g. under pytest-xdist workers or user threads
# driven through contextvars — cannot cross-contaminate.
_RUN_RECORDER: contextvars.ContextVar[_RunRecorder | None] = contextvars.ContextVar(
    "pytest_probability_run", default=None
)


def _current_recorder(caller: str) -> _RunRecorder:
    rec = _RUN_RECORDER.get()
    if rec is None:
        raise RuntimeError(f"{caller}() called outside a bench run")
    return rec


def record_usage(usage: Any = None, /, **fields: Any) -> None:
    """Attribute per-model token usage to the current bench run.

    Accepts a ``TokenUsage``, any object with the same attributes, a
    plain dict — or the fields directly as keyword arguments::

        record_usage(model="m-small", input_tokens=120, output_tokens=8,
                     cost=0.0001)

    Call it as many times as the run makes model calls; entries
    aggregate per model. Recorded usage survives a failing ``assert``
    that comes after it — spend is never lost to a wrong answer.
    """
    if usage is not None and fields:
        raise TypeError("pass a usage object or keyword fields, not both")
    _current_recorder("record_usage").usage.append(
        _usage_to_dict(usage if usage is not None else fields)
    )


def record_cost(amount: float) -> None:
    """Add a non-token cost to the current bench run.

    The run's total cost is ``record_cost`` amounts plus the ``cost``
    of every recorded usage entry.
    """
    _current_recorder("record_cost").cost += float(amount)


# ---------------------------------------------------------------------------
# Options
# ---------------------------------------------------------------------------


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("probability")
    group.addoption(
        "--prob-runs",
        dest="prob_runs",
        type=int,
        default=None,
        metavar="N",
        help="Run every case N times (default: prob_runs ini or 1)",
    )
    group.addoption(
        "--prob-json",
        dest="prob_json",
        default=None,
        metavar="PATH",
        help="Write a JSON report (fractions, statuses, per-run records) to PATH",
    )
    group.addoption(
        "--prob-delay",
        dest="prob_delay",
        type=float,
        default=None,
        metavar="SECONDS",
        help="Sleep between case executions, e.g. for rate-limited backends"
        " (default: prob_delay ini or 0)",
    )
    group.addoption(
        "--prob-transpose",
        dest="prob_transpose",
        action="store_true",
        default=None,
        help="Run-major order: run 1 of every case, then run 2, ..."
        " (default: prob_transpose ini or case-major)",
    )
    group.addoption(
        "--prob-method",
        dest="prob_method",
        default=None,
        choices=stats.METHODS,
        help="Interval method for per-case pass rates: exact (Clopper-Pearson),"
        " wilson, or bayes (default: prob_method ini or exact)",
    )
    group.addoption(
        "--prob-confidence",
        dest="prob_confidence",
        type=float,
        default=None,
        metavar="LEVEL",
        help="Two-sided confidence level for intervals, strictly between 0"
        " and 1 (default: prob_confidence ini or 0.95)",
    )
    group.addoption(
        "--prob-no-intervals",
        dest="prob_no_intervals",
        action="store_true",
        default=None,
        help="Hide the interval column in the summary"
        " (default: prob_intervals ini or shown)",
    )
    group.addoption(
        "--prob-min-rate",
        dest="prob_min_rate",
        type=float,
        default=None,
        metavar="RATE",
        help="Gate every case on its pass rate: PASS when the whole interval"
        " lies above RATE (default: prob_min_rate ini or no gate)",
    )
    group.addoption(
        "--prob-undecided",
        dest="prob_undecided",
        default=None,
        choices=UNDECIDED_POLICIES,
        help="Whether an UNDECIDED gate verdict fails the session"
        " (default: prob_undecided ini or fail)",
    )
    group.addoption(
        "--prob-explain",
        dest="prob_explain",
        action="store_true",
        default=None,
        help="Add a plain-language reading of the results to the summary"
        " and the JSON report (default: prob_explain ini or off)",
    )
    group.addoption(
        "--prob-bootstrap",
        dest="prob_bootstrap",
        type=int,
        default=None,
        metavar="N",
        help="Bootstrap resamples for function-level and overall intervals"
        " (default: prob_bootstrap ini or 5000)",
    )
    group.addoption(
        "--prob-seed",
        dest="prob_seed",
        type=int,
        default=None,
        metavar="SEED",
        help="Seed for every resampling procedure, so the same results"
        " always give the same intervals (default: prob_seed ini or 0)",
    )
    group.addoption(
        "--prob-compare",
        dest="prob_compare",
        default=None,
        metavar="AXIS",
        help="Compare the values of the parametrize argument AXIS in every"
        " bench function that has it, each against the first"
        " (default: prob_compare ini or no comparison)",
    )
    group.addoption(
        "--prob-adjust",
        dest="prob_adjust",
        default=None,
        choices=stats.ADJUSTMENTS,
        help="Adjust comparison p-values for the number of comparisons:"
        " none (exploratory), holm, bonferroni or bh"
        " (default: prob_adjust ini or none)",
    )
    group.addoption(
        "--prob-metric",
        dest="prob_metric",
        action="append",
        default=None,
        metavar="METRIC",
        help="Also report pass^K (all K runs of an input pass: reliability)"
        " and/or pass@K (at least one of K passes: best-of-K) per case and"
        " per function; comma-separated and repeatable, e.g."
        " --prob-metric=pass^3,pass@5 (default: prob_metric ini or none)",
    )
    group.addoption(
        "--prob-plan",
        dest="prob_plan",
        action="store_true",
        default=False,
        help="Collect, print a run budget per case (runs needed to pass a"
        " gate, to catch a rare failure, projected cost) and exit without"
        " running anything",
    )
    group.addoption(
        "--prob-plan-assume",
        dest="prob_plan_assume",
        type=float,
        default=None,
        metavar="RATE",
        help="--prob-plan: the true pass rate to plan for, strictly between"
        f" 0 and 1 (default: {_PLAN_ASSUME:g})",
    )
    group.addoption(
        "--prob-plan-flake",
        dest="prob_plan_flake",
        default=None,
        metavar="RATES",
        help="--prob-plan: comma-separated failure rates to plan to catch"
        f" (default: {_PLAN_FLAKE})",
    )
    group.addoption(
        "--prob-plan-report",
        dest="prob_plan_report",
        default=None,
        metavar="PATH",
        help="--prob-plan: a previous --prob-json report to read each"
        " case's cost per run from, to project the cost of the plan",
    )
    group.addoption(
        "--prob-baseline",
        dest="prob_baseline",
        default=None,
        metavar="PATH",
        help="Compare every bench function with a previous --prob-json report,"
        " pairing cases by id (default: no baseline)",
    )
    group.addoption(
        "--prob-margin",
        dest="prob_margin",
        type=float,
        default=None,
        metavar="MARGIN",
        help="With --prob-baseline: fail a function whose pass rate may have"
        " dropped by more than MARGIN, e.g. 0.02 (default: report only)",
    )
    group.addoption(
        "--prob-latency",
        dest="prob_latency",
        action="store_true",
        default=None,
        help="Show each case's latency quantile (prob_latency_quantile, or the"
        " mark's latency_quantile) with its interval"
        " (default: prob_latency ini or off)",
    )
    group.addoption(
        "--prob-stop",
        dest="prob_stop",
        default=None,
        choices=STOP_MODES,
        help="curtail: skip a gated case's remaining runs once its verdict can"
        " no longer change; the verdicts are the same as running every run"
        " (default: prob_stop ini or off)",
    )
    parser.addini("prob_runs", "Default number of runs per case", default="1")
    parser.addini(
        "prob_delay", "Default seconds between case executions", default="0"
    )
    parser.addini(
        "prob_transpose", "Run-major execution order", type="bool", default=False
    )
    parser.addini(
        "prob_pattern", "Glob pattern for benchmark files", default="bench_*.py"
    )
    parser.addini(
        "prob_method",
        "Interval method: exact (Clopper-Pearson), wilson, or bayes",
        default="exact",
    )
    parser.addini(
        "prob_confidence",
        "Two-sided confidence level for intervals",
        default="0.95",
    )
    parser.addini(
        "prob_prior",
        "Beta prior 'a,b' for the bayes method",
        default="1,1",
    )
    parser.addini(
        "prob_intervals",
        "Show the interval column in the summary",
        type="bool",
        default=True,
    )
    parser.addini(
        "prob_min_rate",
        "Gate every case on its pass rate (empty: no global gate)",
        default="",
    )
    parser.addini(
        "prob_undecided",
        "Whether an UNDECIDED gate verdict fails the session: fail or pass",
        default="fail",
    )
    parser.addini(
        "prob_errors",
        "Errored runs in gated cases: count (as non-passes) or exclude",
        default="count",
    )
    parser.addini(
        "prob_explain",
        "Explain the results in plain language (summary and JSON report)",
        type="bool",
        default=False,
    )
    parser.addini(
        "prob_bootstrap",
        "Bootstrap resamples for function-level and overall intervals",
        default="5000",
    )
    parser.addini("prob_seed", "Seed for every resampling procedure", default="0")
    parser.addini(
        "prob_min_inputs",
        "Fewest cases a function needs for its function-level interval",
        default="10",
    )
    parser.addini(
        "prob_compare",
        "Parametrize argument to compare in every bench function that has it",
        default="",
    )
    parser.addini(
        "prob_adjust",
        "Multiple-comparison adjustment: none, holm, bonferroni or bh",
        default="none",
    )
    parser.addini(
        "prob_metric",
        "Metrics to report, comma-separated: pass^K and/or pass@K",
        default="",
    )
    parser.addini(
        "prob_latency",
        "Show each case's latency quantile and its interval",
        type="bool",
        default=False,
    )
    parser.addini(
        "prob_latency_quantile",
        "The latency quantile reported for cases whose mark sets none",
        default="0.95",
    )
    parser.addini(
        "prob_stop",
        "Early stopping: off, or curtail (skip a gated case's remaining runs"
        " once its verdict can no longer change)",
        default="off",
    )


def _runs(config: pytest.Config, marked: int | None = None) -> int:
    """Runs per case: ``--prob-runs``, else a ``probability(runs=...)``
    mark's value, else the ``prob_runs`` ini setting."""
    runs = config.getoption("prob_runs")
    if runs is None:
        runs = marked if marked is not None else int(config.getini("prob_runs"))
    return max(1, runs)


# Set once the first bench item of this process has run: the delay
# applies *between* executions, never before the first. Per-process on
# purpose — under xdist every worker throttles its own stream.
_DELAYED_ONCE = pytest.StashKey[bool]()


def _delay(config: pytest.Config) -> float:
    delay = config.getoption("prob_delay")
    if delay is None:
        delay = float(config.getini("prob_delay"))
    return max(0.0, delay)


def _transpose(config: pytest.Config) -> bool:
    transpose = config.getoption("prob_transpose")
    if transpose is None:
        transpose = bool(config.getini("prob_transpose"))
    return transpose


def _explain(config: pytest.Config) -> bool:
    explain = config.getoption("prob_explain")
    if explain is None:
        explain = bool(config.getini("prob_explain"))
    return explain


@dataclass(frozen=True)
class StatsConfig:
    """The resolved statistical settings for a session.

    One object so every consumer — row intervals, gate verdicts,
    function-level intervals, the explain section, the JSON
    ``stats_config`` block — reads the same method, level and prior.
    ``resamples``, ``seed`` and ``min_inputs`` drive the bootstrap
    behind function-level and overall intervals (``aggregate()``), at
    the same ``level``.
    """

    method: str = "exact"
    level: float = 0.95
    prior: tuple[float, float] = (1.0, 1.0)
    intervals: bool = True
    resamples: int = 5000
    seed: int = 0
    min_inputs: int = 10

    def interval(self, passes: int, total: int) -> tuple[float, float]:
        """Interval on the pass probability; ``total`` must be ≥ 1."""
        return stats.proportion_interval(
            passes, total, self.level, self.method, self.prior
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "level": self.level,
            "prior": list(self.prior),
            "resamples": self.resamples,
            "seed": self.seed,
            "min_inputs": self.min_inputs,
        }


_STATS_CONFIG = pytest.StashKey[StatsConfig]()


def _parse_level(raw: Any, source: str) -> float:
    try:
        level = float(raw)
    except (TypeError, ValueError):
        raise pytest.UsageError(
            f"{source} must be a number strictly between 0 and 1, got {raw!r}"
        ) from None
    # Positive test so NaN fails too.
    if not 0.0 < level < 1.0:
        hint = f" (did you mean {level / 100:g}?)" if 1.0 < level < 100.0 else ""
        raise pytest.UsageError(
            f"{source} must be strictly between 0 and 1, got {raw!r}{hint}"
        )
    return level


def _parse_prior(raw: Any, source: str = "prob_prior") -> tuple[float, float]:
    # An "a,b" string (ini) or an (a, b) pair (a mark's prior=).
    if isinstance(raw, (tuple, list)):
        parts = list(raw)
    else:
        parts = [p.strip() for p in str(raw).split(",")]
    try:
        if len(parts) != 2:
            raise ValueError
        a, b = float(parts[0]), float(parts[1])
        if not all(v > 0.0 and math.isfinite(v) for v in (a, b)):
            raise ValueError
    except (TypeError, ValueError):
        raise pytest.UsageError(
            f"{source} must be two positive numbers 'a,b' (e.g. 1,1 or"
            f" 0.5,0.5), got {raw!r}"
        ) from None
    return a, b


def _parse_int(raw: Any, source: str, minimum: int | None = None) -> int:
    # Option values arrive as ints (argparse) or strings (ini).
    try:
        value = int(str(raw).strip())
    except ValueError:
        raise pytest.UsageError(f"{source} must be an integer, got {raw!r}") from None
    if minimum is not None and value < minimum:
        raise pytest.UsageError(f"{source} must be at least {minimum}, got {raw!r}")
    return value


def _option_or_ini(config: pytest.Config, name: str, flag: str) -> tuple[Any, str]:
    # The CLI value and its spelling, else the ini value and its name.
    raw = config.getoption(name)
    if raw is None:
        return config.getini(name), name
    return raw, flag


def _resolve_stats_config(config: pytest.Config) -> StatsConfig:
    method = config.getoption("prob_method")
    if method is None:
        method = str(config.getini("prob_method")).strip()
        if method not in stats.METHODS:
            raise pytest.UsageError(
                f"prob_method must be one of {', '.join(stats.METHODS)},"
                f" got {method!r}"
            )
    level = config.getoption("prob_confidence")
    if level is None:
        level = _parse_level(config.getini("prob_confidence"), "prob_confidence")
    else:
        level = _parse_level(level, "--prob-confidence")
    prior = _parse_prior(config.getini("prob_prior"))
    if config.getoption("prob_no_intervals"):
        intervals = False
    else:
        intervals = bool(config.getini("prob_intervals"))
    resamples = _parse_int(
        *_option_or_ini(config, "prob_bootstrap", "--prob-bootstrap"), minimum=1
    )
    seed = _parse_int(*_option_or_ini(config, "prob_seed", "--prob-seed"))
    # One input is no sample: resampling it gives a zero-width interval.
    min_inputs = _parse_int(
        config.getini("prob_min_inputs"), "prob_min_inputs", minimum=2
    )
    return StatsConfig(
        method=method,
        level=level,
        prior=prior,
        intervals=intervals,
        resamples=resamples,
        seed=seed,
        min_inputs=min_inputs,
    )


def stats_config(config: pytest.Config) -> StatsConfig:
    """The session's resolved ``StatsConfig`` (validated at configure)."""
    cfg = config.stash.get(_STATS_CONFIG, None)
    if cfg is None:
        cfg = config.stash[_STATS_CONFIG] = _resolve_stats_config(config)
    return cfg


def pytest_configure(config: pytest.Config) -> None:
    # Registered so --strict-markers accepts it.
    config.addinivalue_line(
        "markers",
        "probability(min_rate=None, min_passes=None, runs=None,"
        " confidence=None, method=None, prior=None, compare=None,"
        " baseline=None, margin=None, equivalence=False,"
        " latency_quantile=None, max_latency=None): gate a benchmark"
        " case on its pass rate (min_rate) or pass count (min_passes), set its"
        " run count, compare the values of one parametrize argument"
        " (compare) against a baseline, and/or gate a latency quantile"
        " (max_latency). See"
        " https://pytest-probability.readthedocs.io/en/latest/reference.html",
    )
    # Resolve (and validate) up front so a bad value is a usage error
    # before anything runs — on the xdist controller, which renders,
    # as much as anywhere.
    stats_config(config)
    gate_config(config)
    compare_config(config)
    metrics_config(config)
    plan_config(config)
    baseline_of(config)
    latency_config(config)
    if stop_config(config).curtails:
        config.pluginmanager.register(Curtailer(), "probability-curtailer")
    config.pluginmanager.register(
        ProbabilityAggregator(config), "probability-aggregator"
    )


# ---------------------------------------------------------------------------
# Gates
# ---------------------------------------------------------------------------

# Gate verdicts as the JSON report spells them; the terminal upper-cases.
PASS, FAIL, UNDECIDED = "pass", "fail", "undecided"

UNDECIDED_POLICIES = ("fail", "pass")
ERROR_MODES = ("count", "exclude")


class InfeasibleGateWarning(pytest.PytestWarning):
    """A gate that cannot pass even if every run passes: too few runs."""


def interval_verdict(low: float, high: float, bar: float) -> str:
    """The verdict rule shared by every interval-based gate.

    PASS when the whole interval lies above ``bar``, FAIL when it lies
    entirely below, UNDECIDED when it straddles it — the data cannot
    tell yet. Callers pass the unrounded bounds of the interval they
    print, so a printed interval and its verdict never disagree.
    """
    if low > bar:
        return PASS
    if high < bar:
        return FAIL
    return UNDECIDED


def equivalence_verdict(low: float, high: float, margin: float) -> str:
    """The two-sided verdict rule: are two arms within ±``margin``?

    PASS when the whole interval lies strictly inside (−margin,
    +margin), FAIL when it lies entirely outside — wholly below −margin
    or wholly above +margin — and UNDECIDED otherwise: the interval
    reaches past a margin while still overlapping the band between them.
    Unrounded bounds, as for ``interval_verdict``.
    """
    if -margin < low and high < margin:
        return PASS
    if high < -margin or low > margin:
        return FAIL
    return UNDECIDED


def margin_verdict(
    interval: tuple[float, float] | None, margin: float, equivalence: bool = False
) -> str:
    """The verdict on a difference (arm − baseline) with a margin.

    Non-inferiority (the default): ``interval_verdict`` against
    −``margin`` — PASS when the arm is at most ``margin`` worse.
    Equivalence: ``equivalence_verdict``. No interval (too few inputs to
    compute one) is UNDECIDED: the data cannot tell yet.
    """
    if interval is None:
        return UNDECIDED
    if equivalence:
        return equivalence_verdict(*interval, margin)
    return interval_verdict(*interval, -margin)


@dataclass(frozen=True)
class GateConfig:
    """Session-wide gate settings: the global bar and the two policies.

    ``min_rate`` is ``--prob-min-rate``/``prob_min_rate`` (``None``: no
    global gate). ``undecided`` says whether an UNDECIDED verdict fails
    the session. ``errors`` says whether errored runs of a gated case
    count as non-passes (``count``) or leave its sample (``exclude``).
    """

    min_rate: float | None = None
    undecided: str = "fail"
    errors: str = "count"

    def fails(self, verdict: str) -> bool:
        """Whether a verdict fails the session."""
        return verdict == FAIL or (verdict == UNDECIDED and self.undecided == "fail")


_GATE_CONFIG = pytest.StashKey[GateConfig]()


def _resolve_gate_config(config: pytest.Config) -> GateConfig:
    min_rate = config.getoption("prob_min_rate")
    if min_rate is not None:
        min_rate = _parse_level(min_rate, "--prob-min-rate")
    else:
        raw = str(config.getini("prob_min_rate")).strip()
        min_rate = _parse_level(raw, "prob_min_rate") if raw else None
    undecided = config.getoption("prob_undecided")
    if undecided is None:
        undecided = str(config.getini("prob_undecided")).strip()
        if undecided not in UNDECIDED_POLICIES:
            raise pytest.UsageError(
                f"prob_undecided must be one of {', '.join(UNDECIDED_POLICIES)},"
                f" got {undecided!r}"
            )
    errors = str(config.getini("prob_errors")).strip()
    if errors not in ERROR_MODES:
        raise pytest.UsageError(
            f"prob_errors must be one of {', '.join(ERROR_MODES)}, got {errors!r}"
        )
    return GateConfig(min_rate=min_rate, undecided=undecided, errors=errors)


def gate_config(config: pytest.Config) -> GateConfig:
    """The session's resolved ``GateConfig`` (validated at configure)."""
    cfg = config.stash.get(_GATE_CONFIG, None)
    if cfg is None:
        cfg = config.stash[_GATE_CONFIG] = _resolve_gate_config(config)
    return cfg


@dataclass(frozen=True)
class CompareConfig:
    """Session-wide comparison settings: ``axis`` is
    ``--prob-compare``/``prob_compare`` (``None``: only functions whose
    mark sets ``compare=`` are compared), ``adjust`` the
    multiple-comparison adjustment of their p-values."""

    axis: str | None = None
    adjust: str = "none"


_COMPARE_CONFIG = pytest.StashKey[CompareConfig]()


def _resolve_compare_config(config: pytest.Config) -> CompareConfig:
    axis = config.getoption("prob_compare")
    if axis is None:
        axis = config.getini("prob_compare")
    axis = str(axis).strip() or None
    adjust = config.getoption("prob_adjust")
    if adjust is None:
        adjust = str(config.getini("prob_adjust")).strip()
        if adjust not in stats.ADJUSTMENTS:
            raise pytest.UsageError(
                f"prob_adjust must be one of {', '.join(stats.ADJUSTMENTS)},"
                f" got {adjust!r}"
            )
    return CompareConfig(axis=axis, adjust=adjust)


def compare_config(config: pytest.Config) -> CompareConfig:
    """The session's resolved ``CompareConfig`` (validated at configure)."""
    cfg = config.stash.get(_COMPARE_CONFIG, None)
    if cfg is None:
        cfg = config.stash[_COMPARE_CONFIG] = _resolve_compare_config(config)
    return cfg


@dataclass(frozen=True)
class Metric:
    """A per-case metric over k attempts at an input (``--prob-metric``).

    ``kind`` is ``"^"`` for pass^k, the chance that k attempts all pass
    (reliability), or ``"@"`` for pass@k, the chance that at least one
    of k does (best-of-k). A case needs at least ``k`` runs for it.
    """

    kind: str
    k: int

    @property
    def name(self) -> str:
        """``pass^3``, ``pass@5``: the spelling of the option and of the
        JSON keys."""
        return f"pass{self.kind}{self.k}"

    def value(self, passes: int, total: int) -> float:
        """The unbiased estimate from ``passes`` in ``total`` ≥ k runs."""
        if self.kind == "^":
            return stats.pass_hat_k(passes, total, self.k)
        return stats.pass_at_k(passes, total, self.k)

    def of(self, s: CaseStats) -> float | None:
        """The metric for one row; ``None`` when it has fewer than k runs."""
        if s.total < self.k:
            return None
        return self.value(s.passes, s.total)


# pass^K or pass@K, K a run count (leading zeros allowed: pass^03 is pass^3).
_METRIC_RE = re.compile(r"pass([\^@])([0-9]+)")

_METRICS = pytest.StashKey[tuple]()


def parse_metrics(raw: Iterable[str], source: str) -> tuple[Metric, ...]:
    """The metrics named in ``raw`` — strings each holding one or more
    names separated by commas or whitespace — in first-seen order,
    duplicates dropped. A bad name is a usage error naming ``source``."""
    out: list[Metric] = []
    for chunk in raw:
        for item in re.split(r"[,\s]+", str(chunk).strip()):
            if not item:
                continue
            match = _METRIC_RE.fullmatch(item)
            if match is None or int(match[2]) < 1:
                raise pytest.UsageError(
                    f"{source} takes pass^K or pass@K with K a whole number"
                    f" of at least 1 (e.g. pass^3,pass@5), got {item!r}"
                )
            metric = Metric(match[1], int(match[2]))
            if metric not in out:
                out.append(metric)
    return tuple(out)


def metrics_config(config: pytest.Config) -> tuple[Metric, ...]:
    """The session's ``--prob-metric``/``prob_metric`` metrics (validated
    at configure); the command line replaces the ini value."""
    metrics = config.stash.get(_METRICS, None)
    if metrics is None:
        raw = config.getoption("prob_metric")
        if raw is None:
            metrics = parse_metrics([config.getini("prob_metric")], "prob_metric")
        else:
            metrics = parse_metrics(raw, "--prob-metric")
        config.stash[_METRICS] = metrics
    return metrics


@dataclass(frozen=True)
class LatencyConfig:
    """Session-wide latency settings: ``show`` is
    ``--prob-latency``/``prob_latency`` (the terminal's latency block),
    ``quantile`` the ``prob_latency_quantile`` reported for cases whose
    mark sets no ``latency_quantile``."""

    show: bool = False
    quantile: float = 0.95


_LATENCY_CONFIG = pytest.StashKey[LatencyConfig]()


def _resolve_latency_config(config: pytest.Config) -> LatencyConfig:
    show = config.getoption("prob_latency")
    if show is None:
        show = bool(config.getini("prob_latency"))
    quantile = _parse_level(
        config.getini("prob_latency_quantile"), "prob_latency_quantile"
    )
    return LatencyConfig(show=show, quantile=quantile)


def latency_config(config: pytest.Config) -> LatencyConfig:
    """The session's resolved ``LatencyConfig`` (validated at configure)."""
    cfg = config.stash.get(_LATENCY_CONFIG, None)
    if cfg is None:
        cfg = config.stash[_LATENCY_CONFIG] = _resolve_latency_config(config)
    return cfg


STOP_MODES = ("off", "curtail")


class CurtailmentWarning(pytest.PytestWarning):
    """``--prob-stop=curtail`` can't stop cases early in this session:
    under pytest-xdist it needs ``--dist loadgroup``."""


@dataclass(frozen=True)
class StopConfig:
    """Early stopping, from ``--prob-stop``/``prob_stop``.

    ``mode`` is ``"off"`` or ``"curtail"``. ``active`` says whether cases
    can stop early in this session at all: in-process, or under
    pytest-xdist with ``--dist loadgroup``, which keeps all of a case's
    runs on one worker. ``curtails`` says whether *this* process decides
    — the one running the items — and ``group`` whether it must put each
    case's items in an ``xdist_group`` (an xdist worker under
    ``--dist loadgroup``).
    """

    mode: str = "off"
    active: bool = False
    curtails: bool = False
    group: bool = False


_STOP_CONFIG = pytest.StashKey[StopConfig]()


def _resolve_stop_config(config: pytest.Config) -> StopConfig:
    mode = config.getoption("prob_stop")
    if mode is None:
        mode = str(config.getini("prob_stop")).strip()
        if mode not in STOP_MODES:
            raise pytest.UsageError(
                f"prob_stop must be one of {', '.join(STOP_MODES)}, got {mode!r}"
            )
    if mode == "off":
        return StopConfig()
    if hasattr(config, "workerinput"):
        # An xdist worker, where xdist turns --dist loadgroup into
        # option.loadgroup (and resets option.dist).
        grouped = bool(getattr(config.option, "loadgroup", False))
        return StopConfig(mode, active=grouped, curtails=grouped, group=grouped)
    dist = config.getoption("dist", "no")
    if dist != "no" and config.getoption("tx", None):
        # The xdist controller: the workers run the items and decide.
        if dist == "loadgroup":
            return StopConfig(mode, active=True)
        # A worker would see only its share of a case's runs, so it
        # couldn't tell when the verdict is settled.
        config.issue_config_time_warning(
            CurtailmentWarning(
                f"--prob-stop={mode} needs --dist loadgroup under pytest-xdist,"
                f" so that all runs of a case share a worker; with --dist {dist},"
                " every case runs all its runs"
            ),
            stacklevel=2,
        )
        return StopConfig(mode)
    return StopConfig(mode, active=True, curtails=True)


def stop_config(config: pytest.Config) -> StopConfig:
    """The session's resolved ``StopConfig`` (validated at configure)."""
    cfg = config.stash.get(_STOP_CONFIG, None)
    if cfg is None:
        cfg = config.stash[_STOP_CONFIG] = _resolve_stop_config(config)
    return cfg


def _pct_bar(rate: float) -> str:
    # A bar as the author wrote it: 0.9 -> "90%", 0.975 -> "97.5%".
    return f"{rate * 100:g}%"


@dataclass(frozen=True)
class Gate:
    """One case's resolved gate.

    ``rule`` is ``"rate"`` (judge the interval against ``min_rate``) or
    ``"count"`` (pass when passes ≥ ``min_passes``). ``stats`` holds the
    method, level and prior the gate's interval uses — the session's,
    with any per-gate overrides. ``runs`` is the case's planned run
    count, ``errors`` the session's error mode.

    Built at collection, then shipped as ``to_record()`` — a plain
    dict — inside every run record: under xdist the controller decides
    verdicts, and it never collects.
    """

    rule: str
    stats: StatsConfig
    min_rate: float | None = None
    min_passes: int | None = None
    errors: str = "count"
    runs: int = 1

    def judge(
        self, passes: int, total: int
    ) -> tuple[tuple[float, float] | None, str]:
        """``(interval, verdict)`` for ``passes`` out of ``total`` judged
        runs. The interval is ``None`` when there are no runs."""
        interval = self.stats.interval(passes, total) if total else None
        if self.rule == "count":
            return interval, PASS if passes >= self.min_passes else FAIL
        if interval is None:
            return None, UNDECIDED
        return interval, interval_verdict(*interval, self.min_rate)

    def verdict(self, passes: int, total: int) -> str:
        return self.judge(passes, total)[1]

    def evaluate(self, s: CaseStats) -> GateResult:
        """The verdict on a case's aggregated runs."""
        if self.errors == "exclude":
            # Errors are not evidence about the code: out of the sample.
            total, excluded = s.passes + s.fails, s.errors
        else:
            total, excluded = s.total, 0
        interval, verdict = self.judge(s.passes, total)
        return GateResult(s.case, self, s.passes, total, excluded, interval, verdict)

    def min_runs(self) -> int:
        """Fewest runs with which the gate can PASS — all of them passing.

        n/n only gets more convincing as n grows, for every method, so
        a doubling search and then bisection find it in O(log n)
        interval evaluations.
        """
        if self.rule == "count":
            return self.min_passes

        def passable(n: int) -> bool:
            return self.verdict(n, n) == PASS

        high = 1
        while not passable(high):
            high *= 2
        low = high // 2
        while high - low > 1:
            mid = (low + high) // 2
            if passable(mid):
                high = mid
            else:
                low = mid
        return high

    def runs_to_settle(self, passes: int, total: int, cap: int) -> int | None:
        """About how many runs in all would settle an UNDECIDED rate gate.

        The smallest n ≤ ``cap`` whose verdict on round(p̂·n) of n runs
        is not UNDECIDED, if the observed pass rate p̂ held — PASS above
        the bar, FAIL below. Judged with ``verdict()``, so with this
        gate's own interval. ``None`` for count gates, an empty sample,
        or when p̂ is too close to the bar to settle within ``cap``.

        Rounding p̂·n makes the verdict flicker for a stretch of n before
        it settles for good, so bisection lands somewhere in that
        stretch — between the first n that settles and the first from
        which every n does. That is fine for an estimate printed to two
        significant figures.
        """
        if self.rule != "rate" or not total:
            return None
        rate = passes / total

        def settled(n: int) -> bool:
            return self.verdict(math.floor(rate * n + 0.5), n) != UNDECIDED

        if settled(total):
            return total
        if cap <= total or not settled(cap):
            return None
        low, high = total, cap
        while high - low > 1:
            mid = (low + high) // 2
            if settled(mid):
                high = mid
            else:
                low = mid
        return high

    def critical_passes(self, n: int) -> int | None:
        """Fewest passes out of n runs that make the verdict PASS, or
        ``None`` when even n of n doesn't.

        More passes out of the same runs never make a verdict worse, so
        the passing counts are exactly ``critical_passes(n)`` to n, and
        bisection over the count finds the boundary.
        """
        if self.verdict(n, n) != PASS:
            return None
        low, high = -1, n  # verdict(low) is not PASS, verdict(high) is
        while high - low > 1:
            mid = (low + high) // 2
            if self.verdict(mid, n) == PASS:
                high = mid
            else:
                low = mid
        return high

    def power(self, n: int, rate: float) -> float:
        """The chance that n runs give a PASS verdict when every run
        passes with probability ``rate``: the Binomial(n, rate)
        probability of at least ``critical_passes(n)`` passes."""
        critical = self.critical_passes(n)
        if critical is None:
            return 0.0
        return stats.binom_sf(critical - 1, n, rate)

    def runs_for_power(
        self, rate: float, target: float = 0.8, cap: int = 10_000
    ) -> int | None:
        """Fewest runs n ≤ ``cap`` with ``power(n, rate) ≥ target``, or
        ``None`` when no n up to ``cap`` gets there — always the case
        for a rate gate whose bar is at or above ``rate``.

        Power is not monotone in n: the passing count moves in whole
        runs, so it saw-tooths on its way up. So after checking that
        ``cap`` itself is enough, every n from ``min_runs()`` is tried
        in turn. The passing count rises by at most one per extra run
        (an extra failure only lowers the interval, an extra pass only
        raises it), so it is tracked incrementally — about one verdict
        per n rather than a bisection.
        """
        if self.rule == "rate" and rate <= self.min_rate:
            return None
        start = self.min_runs()
        if start > cap or self.power(cap, rate) < target:
            return None
        critical = self.critical_passes(start)
        for n in range(start, cap + 1):
            if n > start:
                # Fewer than last time's count still fails (one more
                # failure can't help), so only ever step up.
                while self.verdict(critical, n) != PASS:
                    critical += 1
            if stats.binom_sf(critical - 1, n, rate) >= target:
                return n
        return cap  # pragma: no cover - power(cap) ≥ target was checked

    def cutoffs(self, total: int) -> tuple[int, int]:
        """``(pass_at, fail_at)`` with ``total`` judged runs: the fewest
        passes that PASS (``critical_passes``; ``total + 1`` when none
        does) and the most that FAIL (``-1`` when none does); anything in
        between is UNDECIDED.

        With the number of runs fixed, every interval bound rises with
        the passes, so the verdict only improves and bisection finds
        both. Cached per gate and total: curtailment asks after every run.
        """
        return _cutoffs(self, total)

    def settled(
        self, passes: int, fails: int, errors: int, remaining: int
    ) -> str | None:
        """The case's verdict if its ``remaining`` runs can no longer
        change it, else ``None``.

        ``passes``, ``fails`` and ``errors`` are its runs so far. Each
        remaining run may pass, fail, error or record nothing (a
        ``pytest.skip``), so the final sample holds the judged runs so
        far plus anywhere from none to all of the remaining ones. Bounds
        rise with passes and fall with non-passes, so the two extremes
        decide: PASS is certain when it holds even if every remaining run
        fails, FAIL when it holds even if every one passes, and UNDECIDED
        when neither is reachable. Whichever it is, the verdict on the
        runs so far is already the same, which is what lets a stopped
        case report it from the runs it has.

        Never settled before a run is recorded: stopping then would
        leave the case without a row. When every run so far errored
        under ``exclude``, the remaining runs could all error too and
        leave nothing to judge, so that verdict must agree as well.
        """
        if passes + fails + errors == 0:
            return None
        judged = passes + fails + (errors if self.errors == "count" else 0)
        total = judged + remaining
        pass_at, fail_at = self.cutoffs(total)
        if passes >= pass_at:
            verdict = PASS
        elif passes + remaining <= fail_at:
            verdict = FAIL
        elif fail_at < passes and passes + remaining < pass_at:
            verdict = UNDECIDED
        else:
            return None
        if judged == 0 and self.verdict(0, 0) != verdict:
            return None
        return verdict

    def bar(self) -> str:
        """The bar as the gates block prints it: ``≥90%``, ``≥19 passes``."""
        if self.rule == "count":
            return f"≥{self.min_passes} passes"
        return f"≥{_pct_bar(self.min_rate)}"

    def describe(self) -> str:
        """The gate as written: ``min_rate=0.9 at 95%``, ``min_passes=19``."""
        if self.rule == "count":
            return f"min_passes={self.min_passes}"
        text = f"min_rate={self.min_rate:g} at {_pct_bar(self.stats.level)}"
        if self.stats.method != "exact":
            text += f" ({self.stats.method})"
        return text

    def to_record(self) -> dict[str, Any]:
        rec: dict[str, Any] = {"rule": self.rule}
        if self.rule == "count":
            rec["min_passes"] = self.min_passes
        else:
            rec["min_rate"] = self.min_rate
        rec.update(
            confidence=self.stats.level,
            method=self.stats.method,
            prior=list(self.stats.prior),
            errors=self.errors,
            runs=self.runs,
        )
        return rec

    @classmethod
    def from_record(cls, rec: dict[str, Any]) -> Gate:
        return cls(
            rule=rec["rule"],
            stats=StatsConfig(
                method=rec["method"],
                level=rec["confidence"],
                prior=tuple(rec["prior"]),
            ),
            min_rate=rec.get("min_rate"),
            min_passes=rec.get("min_passes"),
            errors=rec["errors"],
            runs=rec["runs"],
        )


@functools.lru_cache(maxsize=4096)
def _cutoffs(gate: Gate, total: int) -> tuple[int, int]:
    critical = gate.critical_passes(total)
    pass_at = total + 1 if critical is None else critical
    # FAIL holds up to fail_at and not above: bisect for the first count
    # that doesn't FAIL (verdict(low) FAILs, verdict(high) doesn't).
    low, high = -1, total + 1
    while high - low > 1:
        mid = (low + high) // 2
        if gate.verdict(mid, total) != FAIL:
            high = mid
        else:
            low = mid
    return pass_at, high - 1


@dataclass(frozen=True)
class GateResult:
    """A gate's verdict on one case, with the sample and interval behind
    it: ``passes`` of ``total`` judged runs, ``excluded`` errored runs
    left out (``prob_errors = exclude``)."""

    case: str
    gate: Gate
    passes: int
    total: int
    excluded: int
    interval: tuple[float, float] | None
    verdict: str

    def to_json(self) -> dict[str, Any]:
        low, high = self.interval if self.interval is not None else (None, None)
        return {
            **self.gate.to_record(),
            "passes": self.passes,
            "total": self.total,
            "excluded": self.excluded,
            "low": low,
            "high": high,
            "verdict": self.verdict,
        }


def latency_verdict(
    low: float | None, high: float | None, max_latency: float
) -> str:
    """The verdict on a latency interval against a maximum: lower is
    better, so this is ``interval_verdict`` on the negated interval —
    PASS when the whole interval lies below ``max_latency``, FAIL when
    it lies entirely above, UNDECIDED otherwise. A bound that doesn't
    exist yet (``None``) is open on its side: with no upper bound the
    gate can't PASS, with no lower bound it can't FAIL.
    """
    return interval_verdict(
        -math.inf if high is None else -high,
        math.inf if low is None else -low,
        -max_latency,
    )


@dataclass(frozen=True)
class LatencySpec:
    """One case's latency settings: the ``quantile`` of its run times
    that is reported, at ``level``, and — with ``max_latency`` (seconds)
    — a gate on it. ``errors`` is the session's error mode and ``runs``
    the case's planned run count.

    Built at collection when a mark sets ``latency_quantile`` or
    ``max_latency``, and shipped in every run record (``to_record()``)
    like a ``Gate``; other cases get the session's default on the
    process that aggregates.
    """

    quantile: float
    level: float
    max_latency: float | None = None
    errors: str = "count"
    runs: int = 1

    @property
    def gated(self) -> bool:
        return self.max_latency is not None

    def label(self) -> str:
        """``p95``, ``p99.9``."""
        return f"p{self.quantile * 100:g}"

    def bar(self) -> str:
        """The limit as the gates block prints it: ``≤2s``."""
        return f"≤{self.max_latency:g}s" if self.gated else ""

    def describe(self) -> str:
        """``max_latency=2 (p95) at 95%``."""
        return (
            f"max_latency={self.max_latency:g} ({self.label()})"
            f" at {_pct_bar(self.level)}"
        )

    def min_runs(self) -> int:
        """Fewest timed runs for which both bounds exist: below it the
        interval has no upper bound, so a latency gate can't PASS."""
        return stats.quantile_min_n(self.quantile, self.level)

    def evaluate(self, s: CaseStats) -> LatencyResult:
        """The quantile, its interval and (when gated) verdict over a
        case's run times.

        Every recorded run counts — passed, failed and errored — except
        that a gated case (a rate or a latency gate) under
        ``prob_errors = exclude`` leaves its errored runs out, as its
        rate gate does. The times are sorted first, so the order runs
        arrived in (xdist) can't matter.
        """
        exclude = self.errors == "exclude" and (self.gated or s.gate is not None)
        times = sorted(s.times if exclude else [*s.times, *s.error_times])
        excluded = len(s.error_times) if exclude else 0
        n = len(times)
        if not n:
            return LatencyResult(
                case=s.case,
                spec=self,
                total=0,
                excluded=excluded,
                estimate=None,
                interval=(None, None),
                ranks=(None, None),
                coverage=None,
                verdict=UNDECIDED if self.gated else None,
            )
        r, k = stats.quantile_ranks(n, self.quantile, self.level)
        low = times[r - 1] if r is not None else None
        high = times[k - 1] if k is not None else None
        return LatencyResult(
            case=s.case,
            spec=self,
            total=n,
            excluded=excluded,
            estimate=stats.sample_quantile(times, self.quantile),
            interval=(low, high),
            ranks=(r, k),
            coverage=stats.quantile_coverage(n, self.quantile, r, k),
            verdict=(
                latency_verdict(low, high, self.max_latency) if self.gated else None
            ),
        )

    def to_record(self) -> dict[str, Any]:
        return {
            "quantile": self.quantile,
            "confidence": self.level,
            "max_latency": self.max_latency,
            "errors": self.errors,
            "runs": self.runs,
        }

    @classmethod
    def from_record(cls, rec: dict[str, Any]) -> LatencySpec:
        return cls(
            quantile=rec["quantile"],
            level=rec["confidence"],
            max_latency=rec["max_latency"],
            errors=rec["errors"],
            runs=rec["runs"],
        )


@dataclass(frozen=True)
class LatencyResult:
    """A case's latency quantile: ``estimate`` (the sample quantile) and
    ``interval`` (the order statistics at ``ranks``; a ``None`` bound is
    open) over ``total`` timed runs, with ``excluded`` errored runs left
    out, ``coverage`` the interval's guaranteed coverage and the gate's
    ``verdict`` (``None`` when ungated)."""

    case: str
    spec: LatencySpec
    total: int
    excluded: int
    estimate: float | None
    interval: tuple[float | None, float | None]
    ranks: tuple[int | None, int | None]
    coverage: float | None
    verdict: str | None

    @property
    def complete(self) -> bool:
        """Whether both bounds exist."""
        return None not in self.interval

    def to_json(self) -> dict[str, Any]:
        ci = None
        if self.total:
            ci = {
                "method": "order-statistic",
                "level": self.spec.level,
                "low": self.interval[0],
                "high": self.interval[1],
                "ranks": list(self.ranks),
                "coverage": self.coverage,
            }
        return {
            "quantile": self.spec.quantile,
            "total": self.total,
            "excluded": self.excluded,
            "estimate": self.estimate,
            "ci": ci,
            "min_runs": self.spec.min_runs(),
            "max_latency": self.spec.max_latency,
            "verdict": self.verdict,
        }


def _positive_seconds(raw: Any, name: str) -> float:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise ValueError(f"{name} must be a number of seconds, got {raw!r}")
    value = float(raw)
    # Positive test so NaN fails too.
    if not (value > 0.0 and math.isfinite(value)):
        raise ValueError(f"{name} must be a positive number of seconds, got {raw!r}")
    return value


def _latency_plan(
    config: pytest.Config, marks: list, runs: int
) -> LatencySpec | None:
    """A case's ``LatencySpec`` from its ``probability`` marks (closest
    first), or ``None`` when no mark sets ``latency_quantile`` or
    ``max_latency`` — the case then gets the session's default when its
    runs are aggregated.

    ``latency_quantile`` defaults to ``prob_latency_quantile``. A
    latency gate's level is the mark's ``confidence``, else the
    session's, as for a rate gate. Bad values raise
    ``ValueError``/``pytest.UsageError``.
    """
    settings = _merge_probability_marks(marks)
    if "latency_quantile" not in settings and "max_latency" not in settings:
        return None
    if "latency_quantile" in settings:
        raw = settings["latency_quantile"]
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise ValueError(f"latency_quantile must be a number, got {raw!r}")
        quantile = _parse_level(raw, "latency_quantile")
    else:
        quantile = latency_config(config).quantile
    max_latency = settings.get("max_latency")
    if max_latency is not None:
        max_latency = _positive_seconds(max_latency, "max_latency")
    level = stats_config(config).level
    if max_latency is not None and "confidence" in settings:
        level = _parse_level(settings["confidence"], "confidence")
    return LatencySpec(
        quantile=quantile,
        level=level,
        max_latency=max_latency,
        errors=gate_config(config).errors,
        runs=runs,
    )


# Arguments that set up a function's comparison: a case can't have one.
_COMPARE_ARGS = ("compare", "baseline", "margin", "equivalence")
_LATENCY_ARGS = ("latency_quantile", "max_latency")
_MARK_ARGS = (
    "min_rate", "min_passes", "runs", "confidence", "method", "prior",
    *_COMPARE_ARGS, *_LATENCY_ARGS,
)


def _merge_probability_marks(marks: list) -> dict[str, Any]:
    """Merge ``probability`` marks into one dict, closest mark first.

    A ``pytest.param`` mark comes before the function's, and a
    function's decorators run bottom-up (the one nearest the ``def``
    first, as ``get_closest_marker`` sees them). The closest mark wins
    key by key — except that ``min_rate`` and ``min_passes`` are one
    setting, so a closer mark naming either replaces both. ``None``
    values count as unset.
    """
    merged: dict[str, Any] = {}
    for mark in marks:
        if mark.args:
            raise ValueError("takes keyword arguments only")
        unknown = sorted(set(mark.kwargs) - set(_MARK_ARGS))
        if unknown:
            raise ValueError(
                f"unexpected argument {', '.join(unknown)};"
                f" expected one of {', '.join(_MARK_ARGS)}"
            )
        kwargs = {k: v for k, v in mark.kwargs.items() if v is not None}
        if "min_rate" in kwargs and "min_passes" in kwargs:
            raise ValueError("min_rate and min_passes can't be used together")
        rule_set = "min_rate" in merged or "min_passes" in merged
        for key, value in kwargs.items():
            if key in ("min_rate", "min_passes") and rule_set:
                continue
            merged.setdefault(key, value)
    return merged


def _positive_int(raw: Any, name: str) -> int:
    # bool is an int subclass; True as a count is a bug at the call site.
    if isinstance(raw, bool) or not hasattr(raw, "__index__") or raw.__index__() < 1:
        raise ValueError(f"{name} must be a positive integer, got {raw!r}")
    return raw.__index__()


def _case_plan(config: pytest.Config, marks: list) -> tuple[int, Gate | None]:
    """A case's run count and gate, from its ``probability`` marks
    (closest first) and the session settings.

    Every value given is validated, gate or no gate; bad ones raise
    ``ValueError``/``pytest.UsageError``. The case is gated when a mark
    sets ``min_rate`` or ``min_passes``, or a global min rate is set.
    """
    settings = _merge_probability_marks(marks)
    runs_mark = settings.get("runs")
    if runs_mark is not None:
        runs_mark = _positive_int(runs_mark, "runs")
    runs = _runs(config, runs_mark)
    session = stats_config(config)
    overrides: dict[str, Any] = {}
    if "confidence" in settings:
        overrides["level"] = _parse_level(settings["confidence"], "confidence")
    if "method" in settings:
        if settings["method"] not in stats.METHODS:
            raise ValueError(
                f"method must be one of {', '.join(stats.METHODS)},"
                f" got {settings['method']!r}"
            )
        overrides["method"] = settings["method"]
    if "prior" in settings:
        overrides["prior"] = _parse_prior(settings["prior"], "prior")
    gcfg = gate_config(config)
    common = dict(
        stats=dataclasses.replace(session, **overrides),
        errors=gcfg.errors,
        runs=runs,
    )
    if "min_passes" in settings:
        min_passes = _positive_int(settings["min_passes"], "min_passes")
        return runs, Gate(rule="count", min_passes=min_passes, **common)
    if "min_rate" in settings:
        min_rate = _parse_level(settings["min_rate"], "min_rate")
        return runs, Gate(rule="rate", min_rate=min_rate, **common)
    if gcfg.min_rate is not None:
        return runs, Gate(rule="rate", min_rate=gcfg.min_rate, **common)
    return runs, None


# ---------------------------------------------------------------------------
# @pytest.mark.parametrize expansion
# ---------------------------------------------------------------------------


def _default_param_id(value: Any, argname: str, index: int) -> str:
    if value is None or isinstance(value, (str, int, float, bool)):
        return str(value)
    return f"{argname}{index}"


class _Variant(NamedTuple):
    """One parameter combination of a bench function."""

    id: str
    params: dict[str, Any]
    marks: tuple
    # (value id, value index) per parametrize decorator, in pytestmark
    # order: what a comparison splits into its input and its arm.
    parts: tuple[tuple[str, int], ...] = ()


def _parametrize_variants(marks: list) -> list[_Variant]:
    """Expand stacked ``@pytest.mark.parametrize`` decorators.

    Returns ``(id, params, marks, parts)`` tuples — the cartesian
    product across stacked decorators, processed in ``pytestmark`` order
    so composite ids read ``bottom-top`` like pytest's own. ``params``
    are passed to the bench function as keyword arguments; ``marks``
    come from ``pytest.param(..., marks=...)`` values; ``parts`` are the
    pieces the id is joined from, one per decorator, with the index of
    the value each came from. With no parametrize marks this returns
    the single empty variant: an unparametrized bench function is one
    case.
    """
    variants: list[_Variant] = [_Variant("", {}, ())]
    for mark in marks:
        argnames, argvalues = mark.args[0], mark.args[1]
        names = (
            [n.strip() for n in argnames.split(",")]
            if isinstance(argnames, str)
            else [str(n) for n in argnames]
        )
        ids_opt = mark.kwargs.get("ids")
        expanded: list[_Variant] = []
        for prev_id, prev_params, prev_marks, prev_parts in variants:
            for i, value in enumerate(argvalues):
                vid: str | None = None
                vmarks: tuple = ()
                # pytest.param(...) → ParameterSet, duck-typed
                if hasattr(value, "values") and hasattr(value, "marks"):
                    vid = value.id
                    vmarks = tuple(value.marks)
                    vals = tuple(value.values)
                elif len(names) == 1:
                    vals = (value,)
                else:
                    vals = tuple(value)
                if vid is None:
                    if isinstance(ids_opt, (list, tuple)):
                        vid = str(ids_opt[i])
                    elif callable(ids_opt):
                        vid = "-".join(
                            str(ids_opt(v) or _default_param_id(v, names[j], i))
                            for j, v in enumerate(vals)
                        )
                    else:
                        vid = "-".join(
                            _default_param_id(v, names[j], i)
                            for j, v in enumerate(vals)
                        )
                expanded.append(
                    _Variant(
                        f"{prev_id}-{vid}" if prev_id else vid,
                        {**prev_params, **dict(zip(names, vals))},
                        prev_marks + vmarks,
                        (*prev_parts, (vid, i)),
                    )
                )
        variants = expanded
    return variants


def _argnames(mark) -> list[str]:
    argnames = mark.args[0]
    if isinstance(argnames, str):
        return [n.strip() for n in argnames.split(",")]
    return [str(n) for n in argnames]


@dataclass(frozen=True)
class CompareSpec:
    """A bench function's comparison: the values of the parametrize
    argument ``axis`` (the *arms*), each against the ``baseline`` arm,
    paired by *input* — the case's other parameters.

    ``margin`` (``None``: no verdict, the comparison only reports)
    makes it a gate: non-inferiority by default — the arm may be at
    most ``margin`` worse — or, with ``equivalence``, within ±margin.
    Built at collection; travels in every run record (``to_record()``,
    with the case's ``input``, ``arm`` and ``arm_index``) because the
    xdist controller compares without collecting.
    """

    axis: str
    baseline: str
    margin: float | None = None
    equivalence: bool = False

    def verdict(self, interval: tuple[float, float] | None) -> str | None:
        """The margin verdict on an interval, ``None`` without a margin."""
        if self.margin is None:
            return None
        return margin_verdict(interval, self.margin, self.equivalence)

    def bar(self) -> str:
        """``≥−2 pp`` (non-inferiority) or ``±2 pp`` (equivalence); ``""``
        without a margin."""
        if self.margin is None:
            return ""
        size = f"{self.margin * 100:g} pp"
        if self.equivalence:
            return f"±{size}"
        # A zero margin (only --prob-margin allows one) asks for better.
        return f"≥−{size}" if self.margin else ">0 pp"

    def to_record(self) -> dict[str, Any]:
        return {
            "axis": self.axis,
            "baseline": self.baseline,
            "margin": self.margin,
            "equivalence": self.equivalence,
        }

    @classmethod
    def from_record(cls, rec: dict[str, Any]) -> CompareSpec:
        return cls(
            axis=rec["axis"],
            baseline=rec["baseline"],
            margin=rec["margin"],
            equivalence=rec["equivalence"],
        )


def _compare_plan(
    config: pytest.Config,
    fn_prob: list,
    param_marks: list,
    variants: list[_Variant],
) -> tuple[CompareSpec, list[dict[str, Any]]] | None:
    """A function's comparison and each variant's part in it — the run
    record's ``compare`` dict — or ``None`` when it isn't compared.

    ``compare=`` on the function's mark beats ``--prob-compare``. An
    axis named by the mark must be a parametrize argument with at least
    two values; one from the command line applies only to the functions
    that have it. Bad arguments raise ``ValueError``.
    """
    # Only the comparison's arguments, closest mark first; the case
    # plans validate the marks as a whole.
    settings: dict[str, Any] = {}
    for mark in fn_prob:
        for key in _COMPARE_ARGS:
            if mark.kwargs.get(key) is not None:
                settings.setdefault(key, mark.kwargs[key])
    axis = settings.get("compare")
    explicit = axis is not None
    given = [
        k for k in ("baseline", "margin") if k in settings
    ] + (["equivalence"] if settings.get("equivalence") else [])
    if not explicit and given:
        raise ValueError(f"{', '.join(given)} needs compare= on the same function")
    if axis is None:
        axis = compare_config(config).axis
    if axis is None:
        return None
    if not isinstance(axis, str) or not axis:
        raise ValueError(f"compare must be a parametrize argument name, got {axis!r}")
    where = next(
        (j for j, m in enumerate(param_marks) if axis in _argnames(m)), None
    )
    if where is None:
        if not explicit:
            return None
        names = [n for m in param_marks for n in _argnames(m)]
        has = f"its arguments are {', '.join(names)}" if names else "it has none"
        raise ValueError(
            f"compare={axis!r} is not a parametrize argument ({has})"
        )
    arms: dict[int, str] = {}
    for v in variants:
        vid, index = v.parts[where]
        arms.setdefault(index, vid)
    ids = [arms[i] for i in sorted(arms)]
    if len(set(ids)) != len(ids):
        raise ValueError(
            f"the values of {axis!r} need distinct ids to be compared,"
            f" got {', '.join(ids)}"
        )
    if len(ids) < 2:
        if not explicit:
            return None
        raise ValueError(f"compare={axis!r} needs at least two values to compare")
    baseline = settings.get("baseline")
    if baseline is None:
        baseline = ids[0]
    elif str(baseline) not in ids:
        raise ValueError(
            f"baseline={baseline!r} is not a value of {axis!r};"
            f" expected one of {', '.join(ids)}"
        )
    margin = settings.get("margin")
    if margin is not None:
        if isinstance(margin, bool) or not isinstance(margin, (int, float)):
            raise ValueError(f"margin must be a number, got {margin!r}")
        margin = _parse_level(margin, "margin")
    equivalence = settings.get("equivalence", False)
    if not isinstance(equivalence, bool):
        raise ValueError(f"equivalence must be True or False, got {equivalence!r}")
    if equivalence and margin is None:
        raise ValueError("equivalence=True needs a margin=")
    spec = CompareSpec(
        axis=axis, baseline=str(baseline), margin=margin, equivalence=equivalence
    )
    records = []
    for v in variants:
        arm, arm_index = v.parts[where]
        records.append(
            {
                **spec.to_record(),
                "input": "-".join(
                    vid for j, (vid, _) in enumerate(v.parts) if j != where
                ),
                "arm": arm,
                "arm_index": arm_index,
            }
        )
    return spec, records


# ---------------------------------------------------------------------------
# Collection
# ---------------------------------------------------------------------------


def pytest_collect_file(file_path: Path, parent: pytest.Collector):
    pattern = parent.config.getini("prob_pattern")
    if fnmatch.fnmatch(file_path.name, pattern):
        return BenchFile.from_parent(parent, path=file_path)
    return None


try:
    # pytest's assertion rewriter. Internal module, but the function
    # signature has been stable for many years; we degrade gracefully
    # if it ever moves.
    from _pytest.assertion.rewrite import rewrite_asserts as _rewrite_asserts
except ImportError:  # pragma: no cover - future-pytest safety net
    _rewrite_asserts = None


def _compile_bench_source(source: bytes, path: Path, config: pytest.Config):
    """Compile a bench module, passing it through pytest's assertion
    rewriter so ``assert answer == expected`` reports full
    sub-expression introspection — the same behavior ``test_*.py``
    files get.

    Rewriting is skipped under ``--assert=plain``, when the module
    docstring contains ``PYTEST_DONT_REWRITE``, or if the rewriter is
    unavailable; asserts then behave like stock Python.
    """
    tree = ast.parse(source, filename=str(path))
    docstring = ast.get_docstring(tree, clean=False) or ""
    if (
        _rewrite_asserts is not None
        and config.getoption("assertmode", "rewrite") == "rewrite"
        and "PYTEST_DONT_REWRITE" not in docstring
    ):
        _rewrite_asserts(tree, source, str(path), config)
    return compile(tree, str(path), "exec", dont_inherit=True)


def _import_bench_module(path: Path, config: pytest.Config):
    """Import a benchmark file under a path-unique module name.

    Import errors propagate so pytest reports them as collection errors.
    """
    digest = hashlib.md5(str(path).encode()).hexdigest()[:8]
    module_name = f"pytest_probability_mods.{path.stem}_{digest}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    code = _compile_bench_source(path.read_bytes(), path, config)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    exec(code, mod.__dict__)
    return mod


class BenchFile(pytest.File):
    """A collected benchmark file."""

    _setup_fn: Callable[[], None] | None = None
    _teardown_fn: Callable[[], None] | None = None

    def collect(self) -> Iterator[pytest.Collector]:
        mod = _import_bench_module(self.path, self.config)

        setup = getattr(mod, "setup", None)
        teardown = getattr(mod, "teardown", None)
        self._setup_fn = setup if callable(setup) else None
        self._teardown_fn = teardown if callable(teardown) else None

        # Every module-level bench_* callable is a benchmark, in
        # definition order — the same convention pytest applies to
        # test_* functions.
        for name, obj in vars(mod).items():
            if name.startswith("bench_") and callable(obj):
                yield BenchFunction.from_parent(self, name=name, bench_fn=obj)

    # pytest's SetupState calls collector setup() before the first item
    # under this file and teardown() after the last — once-per-module
    # lifecycle semantics.
    def setup(self) -> None:
        if self._setup_fn is not None:
            self._setup_fn()

    def teardown(self) -> None:
        if self._teardown_fn is not None:
            self._teardown_fn()


class _CasePlan(NamedTuple):
    """One case of a bench function, resolved at collection."""

    variant_id: str
    params: dict[str, Any]
    marks: tuple
    case: str
    runs: int
    gate: Gate | None
    # The case's part in its function's comparison (run record form).
    compare: dict[str, Any] | None = None
    # The case's latency settings, when a mark sets them.
    latency: LatencySpec | None = None


class BenchFunction(pytest.Collector):
    """One ``bench_*`` function: an ordinary parametrized generator.

    Emits one item per (parameter combination, run). Summary rows are
    namespaced by the function's short name (``bench_classify`` →
    ``classify::<case-id>``) so identical case ids in different
    benchmarks aggregate separately. A function with no parametrize
    marks is a single case named after the function.
    """

    def __init__(self, *, bench_fn: Callable, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.bench_fn = bench_fn

    def collect(self) -> Iterator[pytest.Item]:
        all_marks = list(getattr(self.bench_fn, "pytestmark", []))
        param_marks = [m for m in all_marks if m.name == "parametrize"]
        other_marks = [m for m in all_marks if m.name != "parametrize"]
        fn_prob = [m for m in other_marks if m.name == "probability"]
        short = self.name.removeprefix("bench_") or self.name

        variants = _parametrize_variants(param_marks)
        try:
            planned = _compare_plan(self.config, fn_prob, param_marks, variants)
        except (ValueError, pytest.UsageError) as exc:
            raise self.CollectError(
                f"{short}: invalid probability mark: {exc}"
            ) from None
        compares = planned[1] if planned else [None] * len(variants)
        plans: list[_CasePlan] = []
        for (variant_id, params, vmarks, _), compare in zip(variants, compares):
            # Unparametrized: one case, named after the function.
            case = f"{short}::{variant_id}" if variant_id else short
            case_prob = [m for m in vmarks if m.name == "probability"]
            try:
                for mark in case_prob:
                    named = [k for k in _COMPARE_ARGS if k in mark.kwargs]
                    if named:
                        raise ValueError(
                            f"{', '.join(named)} applies to the whole function:"
                            " put it on the bench function's mark"
                        )
                runs, gate = _case_plan(self.config, [*case_prob, *fn_prob])
                latency = _latency_plan(self.config, [*case_prob, *fn_prob], runs)
            except (ValueError, pytest.UsageError) as exc:
                raise self.CollectError(
                    f"{case}: invalid probability mark: {exc}"
                ) from None
            plans.append(
                _CasePlan(
                    variant_id, params, vmarks, case, runs, gate, compare, latency
                )
            )
        self._warn_infeasible(short, plans)

        if _transpose(self.config):
            # Run-major: run 1 of every case, then run 2, ...
            most = max((p.runs for p in plans), default=0)
            pairs = [
                (plan, run_id)
                for run_id in range(1, most + 1)
                for plan in plans
                if run_id <= plan.runs
            ]
        else:
            pairs = [
                (plan, run_id) for plan in plans for run_id in range(1, plan.runs + 1)
            ]
        group = stop_config(self.config).group
        for plan, run_id in pairs:
            if plan.variant_id:
                name = (
                    plan.variant_id
                    if plan.runs == 1
                    else f"{plan.variant_id}[run{run_id}]"
                )
            else:
                name = f"run{run_id}"
            item = BenchItem.from_parent(
                self,
                name=name,
                run_id=run_id,
                bench_fn=self.bench_fn,
                params=plan.params,
                case=plan.case,
                gate=plan.gate,
                compare=plan.compare,
                latency=plan.latency,
            )
            for mark in (*other_marks, *plan.marks):
                # add_marker() only accepts MarkDecorator; these are raw
                # Mark objects, so attach the way it does internally
                # (skip/xfail/-m all read these).
                item.own_markers.append(mark)
                item.keywords[mark.name] = mark
            if (
                group
                and item.curtailable
                and next(item.iter_markers("xdist_group"), None) is None
            ):
                # --dist loadgroup sends a group to one worker: then that
                # worker runs every run of the case and can tell when its
                # verdict is settled. A group of the author's own also
                # keeps a case's runs together, so it is left alone.
                item.add_marker(pytest.mark.xdist_group(_stop_group(plan.case)))
            yield item

    def _warn_infeasible(self, short: str, plans: list[_CasePlan]) -> None:
        """Warn about gates that cannot pass even if every run passes —
        or, for a latency gate, however fast every run is.

        One warning per distinct gate, naming the case — or, when
        several cases share it (a global gate), how many.
        """
        infeasible: dict[Gate | LatencySpec, list[str]] = {}
        passable: dict[Gate | LatencySpec, bool] = {}
        for plan in plans:
            gate = plan.gate
            if gate is None:
                continue
            if gate not in passable:
                passable[gate] = gate.verdict(gate.runs, gate.runs) == PASS
            if not passable[gate]:
                infeasible.setdefault(gate, []).append(plan.case)
        # A latency gate below min_runs has no upper bound to pass on.
        for plan in plans:
            spec = plan.latency
            if spec is None or not spec.gated:
                continue
            if spec not in passable:
                passable[spec] = spec.runs >= spec.min_runs()
            if not passable[spec]:
                infeasible.setdefault(spec, []).append(plan.case)
        code = getattr(self.bench_fn, "__code__", None)
        for gate, cases in infeasible.items():
            if len(cases) == 1:
                who, has = cases[0], "this case has"
            else:
                who, has = f"{short} ({len(cases)} cases)", "each has"
            # warn_explicit, like Node.warn, but pointing at the function.
            warnings.warn_explicit(
                InfeasibleGateWarning(
                    f"{who}: {gate.describe()} needs ≥{gate.min_runs()} runs;"
                    f" {has} {gate.runs}"
                ),
                category=None,
                filename=str(self.path),
                lineno=code.co_firstlineno if code is not None else 1,
            )


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


def _usage_to_dict(u: Any) -> dict[str, Any]:
    """Normalize a usage entry (``TokenUsage``, any object with the same
    attributes, or a plain dict) into a serializable dict."""
    if isinstance(u, dict):
        get = u.get
    else:
        get = lambda k, d=None: getattr(u, k, d)  # noqa: E731
    return {
        "model": str(get("model") or ""),
        "input_tokens": int(get("input_tokens") or 0),
        "output_tokens": int(get("output_tokens") or 0),
        "cached_input_tokens": int(get("cached_input_tokens") or 0),
        "cost": float(get("cost") or 0.0),
    }


# The clock behind every run's ``elapsed``: perf_counter around the
# body, not the inter-run delay. A module global looked up at each run,
# so tests can substitute a deterministic fake.
_clock: Callable[[], float] = time.perf_counter


class BenchItem(pytest.Item):
    """One (case, run) execution of a bench function.

    The function body is an ordinary pytest test body: a passing body
    is a pass, an ``AssertionError`` is a fail (your code answered
    wrong), any other exception is an error (your harness broke).
    ``pytest.skip``/``xfail`` raise outcomes that bypass recording
    entirely, exactly like pytest.

    In a gated case a failing run is a sample, not a verdict, so
    ``pytest_runtest_makereport`` reports it as xfailed.
    """

    def __init__(
        self,
        *,
        run_id: int,
        bench_fn: Callable,
        params: dict[str, Any],
        case: str,
        gate: Gate | None = None,
        compare: dict[str, Any] | None = None,
        latency: LatencySpec | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.run_id = run_id
        self.bench_fn = bench_fn
        self.params = params
        # Function-qualified case id: the aggregation row.
        self.case = case
        self.gate = gate
        # The case's input, arm and its function's comparison spec.
        self.compare = compare
        # Latency settings from a mark. A latency gate judges speed, not
        # answers, so it doesn't make the case judged: failing runs
        # still fail the session unless a rate gate or a margin judges
        # the case.
        self.latency = latency
        # The skip reason once --prob-stop=curtail has settled the case.
        self.stopped: str | None = None

    @property
    def judged(self) -> bool:
        """Whether a verdict, not each run, decides this case: it is
        gated, its function's comparison has a margin, or it is in the
        baseline report and ``--prob-margin`` is set."""
        if self.gate is not None:
            return True
        if self.compare and self.compare["margin"] is not None:
            return True
        baseline = baseline_of(self.config)
        return baseline is not None and baseline.judges(self.case)

    @property
    def curtailable(self) -> bool:
        """Whether ``--prob-stop=curtail`` may stop this case early: it is
        gated, and no other verdict needs every one of its runs — not its
        function's comparison margin, not ``--prob-margin`` against a
        baseline, and not a latency gate (``max_latency``), whose sample
        of run times skipped runs would shrink. Being conservative keeps
        every verdict the one all the runs would give."""
        if self.gate is None:
            return False
        if self.latency is not None and self.latency.gated:
            return False
        if self.compare and self.compare["margin"] is not None:
            return False
        baseline = baseline_of(self.config)
        return not (baseline is not None and baseline.judges(self.case))

    def runtest(self) -> None:
        if self.stopped is not None:
            # The Curtailer's skip mark ends the run in setup; this
            # covers -p no:skipping, which ignores skip marks.
            pytest.skip(self.stopped)
        delay = _delay(self.config)
        if delay > 0:
            if self.config.stash.get(_DELAYED_ONCE, False):
                time.sleep(delay)
            else:
                self.config.stash[_DELAYED_ONCE] = True

        recorder = _RunRecorder()
        token = _RUN_RECORDER.set(recorder)
        start = _clock()
        outcome: str | None = None
        message: str | None = None
        error: str | None = None
        try:
            result = self.bench_fn(**self.params)
            if inspect.isgenerator(result):
                raise TypeError(
                    "bench functions are plain test bodies since 0.2.0 —"
                    " use assert instead of yielding step results"
                )
            outcome = "pass"
        except AssertionError as exc:
            outcome = "fail"
            first = str(exc).split("\n", 1)[0].strip()
            message = first or None
            raise
        except Exception as exc:
            outcome = "error"
            error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            _RUN_RECORDER.reset(token)
            # outcome is None for pytest.skip()/fail() and other
            # control-flow BaseExceptions: those runs are not samples.
            if outcome is not None:
                cost = recorder.cost + sum(u["cost"] for u in recorder.usage)
                record = {
                    "case": self.case,
                    "run": self.run_id,
                    "outcome": outcome,
                    "message": message,
                    "error": error,
                    "elapsed": _clock() - start,
                    "cost": cost or None,
                    "usage": recorder.usage,
                }
                if self.gate is not None:
                    # The controller decides verdicts without collecting,
                    # so the gate rides along with every run.
                    record["gate"] = self.gate.to_record()
                if self.compare is not None:
                    # Likewise the comparison, with this case's input/arm.
                    record["compare"] = dict(self.compare)
                if self.latency is not None:
                    # And the latency settings a mark gave the case.
                    record["latency"] = self.latency.to_record()
                # Plain dicts only: user_properties must survive xdist's
                # worker-to-controller serialization.
                self.user_properties.append(("probability", record))

    def reportinfo(self):
        return self.path, 0, f"case: {self.case}"


# Old-style wrapper (not wrapper=True) so pytest 7.4 with older pluggy
# keeps working.
@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo[None]):
    """Report a gated case's failing runs as xfailed.

    A failing run of a gated case is one sample of its pass rate; the
    gate's verdict, not the run, decides pass or fail. The same holds
    for every case of a function whose comparison has a margin, and for
    every case in the baseline report under ``--prob-margin``: the
    margin's verdict decides. Reporting it as
    xfailed keeps its traceback on the report (``--xfail-tb`` prints
    it, ``-rx`` lists the run with its assert message as the reason),
    keeps ``-x``/``--maxfail`` from stopping on it, and keeps it from
    failing the session. Errors do fail the session, unless
    ``prob_errors = exclude`` takes them out of a gate's sample (a
    comparison always counts them as non-passes).
    ``--runxfail`` turns all of this off, as it does for xfail marks.
    """
    outcome = yield
    if call.when != "call" or not isinstance(item, BenchItem) or not item.judged:
        return
    report = outcome.get_result()
    if not report.failed or getattr(item.config.option, "runxfail", False):
        return
    record = next(
        (v for k, v in report.user_properties if k == "probability"), None
    )
    if record is None:
        # pytest.fail() and other control flow: not a sample.
        return
    if record["outcome"] == "fail":
        if item.gate is not None:
            what = "gate"
        elif item.compare and item.compare["margin"] is not None:
            what = "comparison"
        else:
            what = "baseline"
        reason = f"probability {what}: {record['message'] or 'AssertionError'}"
    elif (
        record["outcome"] == "error"
        and item.gate is not None
        and item.gate.errors == "exclude"
    ):
        reason = f"probability gate, error excluded: {record['error']}"
    else:
        return
    report.outcome = "skipped"
    report.wasxfail = reason


# ---------------------------------------------------------------------------
# Early stopping by curtailment
# ---------------------------------------------------------------------------

# The user property a run skipped by curtailment carries: its case, the
# runs that had run when the verdict settled, the case's planned runs
# and that verdict.
STOP_KEY = "probability_stop"


def _stop_group(case: str) -> str:
    # The xdist_group name for a case. xdist splits a node id at its last
    # "@" when no "]" follows it, so the name must contain neither.
    return case.replace("@", "_").replace("]", "_")


@dataclass
class _Tally:
    """One case's runs so far, in the process that runs all of them."""

    gate: Gate
    planned: int
    seen: int = 0
    passes: int = 0
    fails: int = 0
    errors: int = 0
    # (verdict, runs seen when it settled) once it has.
    decided: tuple[str, int] | None = None


class Curtailer:
    """``--prob-stop=curtail``: skip a gated case's remaining runs once
    its verdict can no longer change.

    Registered only in a process that runs every run of each case it
    runs: in-process, or an xdist worker under ``--dist loadgroup``
    (each case is one ``xdist_group``). It tallies each case's runs as
    they finish, from the records they leave on ``user_properties``, and
    asks ``Gate.settled()`` after every one; from then on the case's
    runs are skipped with a ``skip`` mark and a ``STOP_KEY`` property,
    so they are reported as skips — not samples — and the aggregator,
    wherever it runs, can count them.

    The cases it may stop are those whose every item is ``curtailable``
    with the same gate; "remaining" is how many of their items are left
    in ``session.items`` (after ``-k``/``-m``), in whatever order they
    run.
    """

    def __init__(self) -> None:
        self._tallies: dict[str, _Tally] | None = None

    def _plan(self, items: list[pytest.Item]) -> dict[str, _Tally]:
        by_case: dict[str, list[BenchItem]] = {}
        for item in items:
            if isinstance(item, BenchItem):
                by_case.setdefault(item.case, []).append(item)
        tallies = {}
        for case, mine in by_case.items():
            gate = mine[0].gate
            # Two files may share a case id (one row): only stop the row
            # if every item agrees on what judges it.
            if all(i.curtailable and i.gate == gate for i in mine):
                tallies[case] = _Tally(gate=gate, planned=len(mine))
        return tallies

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_protocol(self, item: pytest.Item, nextitem):
        if self._tallies is None:
            self._tallies = self._plan(item.session.items)
        tally = (
            self._tallies.get(item.case) if isinstance(item, BenchItem) else None
        )
        if tally is None:
            yield
            return
        if tally.decided is not None:
            verdict, after = tally.decided
            item.stopped = (
                f"probability gate: decided after {after}/{tally.planned} runs"
                f" ({verdict.upper()})"
            )
            item.user_properties.append(
                (
                    STOP_KEY,
                    {
                        "case": item.case,
                        "run": item.run_id,
                        "after": after,
                        "planned": tally.planned,
                        "verdict": verdict,
                    },
                )
            )
            item.add_marker(pytest.mark.skip(reason=item.stopped))
        start = len(item.user_properties)
        yield
        tally.seen += 1
        if tally.decided is not None:
            return
        for name, record in item.user_properties[start:]:
            if name != "probability":
                continue
            if record["outcome"] == "pass":
                tally.passes += 1
            elif record["outcome"] == "error":
                tally.errors += 1
            else:
                tally.fails += 1
        if tally.seen < tally.planned:
            verdict = tally.gate.settled(
                tally.passes, tally.fails, tally.errors, tally.planned - tally.seen
            )
            if verdict is not None:
                tally.decided = (verdict, tally.seen)


@dataclass
class Stop:
    """A case that stopped early, as the aggregator rebuilds it from the
    skipped runs' ``STOP_KEY`` properties: its verdict settled after
    ``after`` of its ``planned`` runs, and ``skipped`` were skipped."""

    case: str
    after: int
    planned: int
    verdict: str
    skipped: int = 0

    def label(self) -> str:
        """``decided after 12/40``: the note on its rows."""
        return f"decided after {self.after}/{self.planned}"


# ---------------------------------------------------------------------------
# Aggregation + the fraction summary
# ---------------------------------------------------------------------------


def _merge_usage(into: dict[str, dict[str, Any]], usage: dict[str, Any]) -> None:
    """Fold one normalized usage entry into a per-model aggregate."""
    model = usage["model"] or "unknown"
    agg = into.setdefault(
        model,
        {
            "input_tokens": 0,
            "output_tokens": 0,
            "cached_input_tokens": 0,
            "cost": 0.0,
        },
    )
    agg["input_tokens"] += usage["input_tokens"]
    agg["output_tokens"] += usage["output_tokens"]
    agg["cached_input_tokens"] += usage["cached_input_tokens"]
    agg["cost"] += usage["cost"]


@dataclass
class CaseStats:
    """Aggregated results for one case across all its runs."""

    case: str
    passes: int = 0
    fails: int = 0
    errors: int = 0
    cost: float = 0.0
    # per-model token aggregates: model -> {input_tokens, output_tokens, ...}
    usage: dict[str, dict[str, Any]] = field(default_factory=dict)
    # The case's gate, rebuilt from its first run record; None: ungated.
    gate: Gate | None = None
    # The case's part in a comparison (its first record's ``compare``
    # dict: spec, input and arm); None: not compared.
    compare: dict[str, Any] | None = None
    # Latency settings from the case's marks (its first record's
    # ``latency``); None: the session's default.
    latency: LatencySpec | None = None
    # Every run's elapsed seconds, in arrival order: passed and failed
    # runs, and errored ones apart (a gate may leave them out).
    times: list[float] = field(default_factory=list)
    error_times: list[float] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.passes + self.fails + self.errors

    @property
    def status(self) -> str:
        """Runs are a 3-class outcome (pass / fail / error); the row
        status names the combination so the classes never blur:
        ``flaky`` means real pass/fail nondeterminism, ``errored``
        means the only blemishes were errors (infra, not the model)."""
        if self.total == 0:
            return "pending"
        if self.passes == self.total:
            return "pass"
        if self.errors == self.total:
            return "error"
        if self.passes == 0:
            return "fail"
        if self.fails == 0:
            return "errored"
        return "flaky"


# ---------------------------------------------------------------------------
# Function-level and overall intervals
# ---------------------------------------------------------------------------

FUNCTION, OVERALL = "function", "overall"


def function_of(case: str) -> str:
    """The bench function (short name) a case id belongs to.

    Case ids are ``<short>::<variant>`` or just ``<short>``, and a short
    name is a Python identifier, so the first ``::`` always ends it —
    even when a parametrize id contains ``::`` itself.
    """
    return case.split("::", 1)[0]


def pass_fraction(passes: int, total: int) -> float:
    """A case's pass fraction over every run — the row's own fraction."""
    return passes / total


@dataclass(frozen=True)
class Aggregate:
    """A function's (or the session's) average over its cases.

    ``estimate`` is the mean of the per-case values (by default the pass
    fraction), so every input counts equally whatever its run count.
    ``ci`` is a percentile interval from a cluster bootstrap: whole
    cases are resampled with replacement, keeping all their runs.
    ``normal_ci`` is the normal-approximation cross-check (JSON only).
    Both are ``None`` when ``suppressed`` says why there is no interval
    — today, fewer than ``min_inputs`` cases.

    ``cases`` and ``counts`` (``(passes, total)`` per case) are in
    canonical order — sorted by case id — which is the order the
    bootstrap draws from, so the result doesn't depend on the order
    results arrived in (xdist).

    ``icc`` is the intraclass correlation ρ of the runs' pass/fail
    outcomes (``stats.icc``) and ``width_factor`` the matching
    ``stats.width_factor`` at the harmonic mean of the run counts; both
    are ``None`` when ρ is not defined (every case run once, or every
    run with the same outcome). ``cost`` is the cases' recorded cost,
    which prices ``projection()``.

    ``stopped`` counts the cases that stopped early under
    ``--prob-stop=curtail`` (``suppress_stopped()``); with any, there is
    no interval.
    """

    scope: str
    name: str
    cases: tuple[str, ...]
    counts: tuple[tuple[int, int], ...]
    estimate: float
    level: float
    resamples: int
    seed: int
    ci: tuple[float, float] | None = None
    normal_ci: tuple[float, float] | None = None
    suppressed: str | None = None
    icc: float | None = None
    width_factor: float | None = None
    cost: float = 0.0
    # One per --prob-metric, in option order.
    metrics: tuple[MetricAggregate, ...] = ()
    stopped: int = 0

    @property
    def inputs(self) -> int:
        return len(self.cases)

    @property
    def harmonic_runs(self) -> float:
        """The harmonic mean of the run counts: the k at which
        ``width_factor`` describes this equally weighted average
        exactly (the run count itself when every case has the same)."""
        return statistics.harmonic_mean([n for _, n in self.counts])

    def projection(self) -> tuple[float, float] | None:
        """``(runs_x2, inputs_x2)``: the relative change in interval
        width from doubling every case's runs, and from doubling the
        number of cases (with runs like today's) — ``-0.29`` is 29%
        narrower. ``None`` without ρ.

        Both options double the number of runs, so at the cost per run
        recorded so far each would add ``cost`` again (assuming new
        inputs cost as much per run as these did).
        """
        if self.icc is None:
            return None
        k = self.harmonic_runs
        return (
            stats.projected_width(k, self.icc, runs=2.0) - 1.0,
            stats.projected_width(k, self.icc, inputs=2.0) - 1.0,
        )

    @property
    def runs(self) -> tuple[int, float, int]:
        """Runs per case: ``(min, mean, max)``."""
        totals = [n for _, n in self.counts]
        return min(totals), statistics.fmean(totals), max(totals)

    def size(self) -> str:
        """``N=40 inputs × k=10``; ``k=5–10`` when run counts differ."""
        low, _, high = self.runs
        k = f"{low}" if low == high else f"{low}–{high}"
        return f"N={self.inputs} inputs × k={k}"

    def to_json(self) -> dict[str, Any]:
        low, mean, high = self.runs
        return {
            "scope": self.scope,
            "name": self.name,
            "inputs": self.inputs,
            "runs": {"min": low, "mean": mean, "max": high},
            "estimate": self.estimate,
            "ci": _interval_json("bootstrap", self.level, self.ci),
            "normal_ci": _interval_json("normal", self.level, self.normal_ci),
            "resamples": self.resamples,
            "seed": self.seed,
            "resampling_unit": "input",
            "note": "inputs treated as a sample",
            "suppressed": self.suppressed,
            "icc": self.icc,
            "width_factor": self.width_factor,
            "metrics": {m.metric.name: m.to_json() for m in self.metrics},
            "stopped": self.stopped,
        }


def _interval_json(
    method: str, level: float, bounds: tuple[float, float] | None
) -> dict[str, Any] | None:
    if bounds is None:
        return None
    return {"method": method, "level": level, "low": bounds[0], "high": bounds[1]}


@dataclass(frozen=True)
class MetricAggregate:
    """One ``Metric`` over a function's (or the session's) cases.

    ``aggregate`` is the ``Aggregate`` of the metric's per-case values
    over the cases with at least k runs — the same mean, cluster
    bootstrap and ``min_inputs`` rule as the pass rate's — and ``None``
    when no case has k runs. ``left_out`` counts the cases with fewer.
    """

    metric: Metric
    aggregate: Aggregate | None
    left_out: int = 0

    @property
    def inputs(self) -> int:
        return self.aggregate.inputs if self.aggregate is not None else 0

    @property
    def estimate(self) -> float | None:
        return self.aggregate.estimate if self.aggregate is not None else None

    @property
    def ci(self) -> tuple[float, float] | None:
        return self.aggregate.ci if self.aggregate is not None else None

    @property
    def suppressed(self) -> str | None:
        if self.aggregate is None:
            return f"no input with at least {self.metric.k} runs"
        return self.aggregate.suppressed

    @property
    def stopped(self) -> int:
        """Inputs behind it that stopped early (``--prob-stop``)."""
        return self.aggregate.stopped if self.aggregate is not None else 0

    def to_json(self) -> dict[str, Any]:
        agg = self.aggregate
        level = agg.level if agg is not None else None
        return {
            "k": self.metric.k,
            "inputs": self.inputs,
            "left_out": self.left_out,
            "estimate": self.estimate,
            "ci": _interval_json("bootstrap", level, self.ci),
            "normal_ci": _interval_json(
                "normal", level, agg.normal_ci if agg is not None else None
            ),
            "suppressed": self.suppressed,
            "stopped": self.stopped,
        }


def aggregate(
    scope: str,
    name: str,
    cases: Iterable[CaseStats],
    cfg: StatsConfig,
    value: Callable[[int, int], float] = pass_fraction,
    metrics: Iterable[Metric] = (),
) -> Aggregate:
    """The ``Aggregate`` of ``cases`` (each with at least one run).

    ``value(passes, total)`` is the per-case quantity averaged. Each of
    ``metrics`` gets a ``MetricAggregate``: this same procedure with the
    metric as ``value``, over the cases with at least k runs (the others
    are left out and counted). Since the statistic is a mean of
    per-case values, resampling whole cases is resampling their values:
    each value carries all its case's runs. The bootstrap uses
    ``cfg``'s resamples, seed and level, through ``stats.bootstrap``'s
    private generator — the global ``random`` state is never touched.

    Every value counts as given: under ``prob_errors = exclude`` errored
    runs still count as non-passes here, as they do in the row
    fraction, because the exclusion is a gate setting — in ρ too, which
    is computed from the same ``(passes, total)`` counts whatever
    ``value`` is, and whether or not the interval is suppressed (it is
    data; the summary shows it only beside an interval).
    """
    ordered = sorted(cases, key=lambda s: s.case)
    counts = tuple((s.passes, s.total) for s in ordered)
    values = [value(c, n) for c, n in counts]
    rho = stats.icc(counts)
    agg = Aggregate(
        scope=scope,
        name=name,
        cases=tuple(s.case for s in ordered),
        counts=counts,
        estimate=statistics.fmean(values),
        level=cfg.level,
        resamples=cfg.resamples,
        seed=cfg.seed,
        icc=rho,
        # fsum is exactly rounded: the same total in any arrival order.
        cost=math.fsum(s.cost for s in ordered),
        metrics=tuple(
            _metric_aggregate(scope, name, ordered, cfg, m) for m in metrics
        ),
    )
    if rho is not None:
        agg = dataclasses.replace(
            agg, width_factor=stats.width_factor(agg.harmonic_runs, rho)
        )
    if len(values) < cfg.min_inputs:
        # Too few inputs: the bootstrap's spread underestimates the
        # real uncertainty, so no interval rather than a misleading one.
        return dataclasses.replace(
            agg, suppressed=f"fewer than {cfg.min_inputs} inputs"
        )
    samples = stats.bootstrap(
        values, statistics.fmean, resamples=cfg.resamples, seed=cfg.seed
    )
    return dataclasses.replace(
        agg,
        ci=stats.percentile_interval(samples, cfg.level),
        normal_ci=stats.normal_interval(values, cfg.level),
    )


def _metric_aggregate(
    scope: str, name: str, cases: list[CaseStats], cfg: StatsConfig, metric: Metric
) -> MetricAggregate:
    eligible = [s for s in cases if s.total >= metric.k]
    return MetricAggregate(
        metric=metric,
        aggregate=(
            aggregate(scope, name, eligible, cfg, value=metric.value)
            if eligible
            else None
        ),
        left_out=len(cases) - len(eligible),
    )


def suppress_stopped(agg: Aggregate, stopped: Container[str]) -> Aggregate:
    """``agg`` without its intervals when any of its cases is in
    ``stopped`` — stopped early by ``--prob-stop=curtail`` — and with
    ``stopped`` counting them; unchanged otherwise.

    A case stops as soon as its verdict is certain, so its fraction
    comes from fewer runs and leans toward that verdict: an average over
    such cases would mislead. The estimate, ρ and the per-case counts
    stay, as data. Each metric's own aggregate is treated the same way,
    over the cases it kept. An interval already suppressed (too few
    inputs) keeps its reason.
    """
    n = sum(case in stopped for case in agg.cases)
    if not n:
        return agg
    metrics = tuple(
        dataclasses.replace(m, aggregate=suppress_stopped(m.aggregate, stopped))
        if m.aggregate is not None
        else m
        for m in agg.metrics
    )
    if agg.ci is None:
        return dataclasses.replace(agg, stopped=n, metrics=metrics)
    return dataclasses.replace(
        agg,
        ci=None,
        normal_ci=None,
        suppressed=f"{_cases(n)} stopped early",
        stopped=n,
        metrics=metrics,
    )


# ---------------------------------------------------------------------------
# Paired comparisons
# ---------------------------------------------------------------------------


class Pair(NamedTuple):
    """One input that ran in both arms: ``(passes, total)`` for each."""

    input: str
    baseline: tuple[int, int]
    arm: tuple[int, int]


@dataclass(frozen=True)
class PairedInput:
    """One input's difference (arm − baseline pass fraction), with its
    Newcombe interval and Fisher exact p-value: the two arms' runs are
    independent samples."""

    input: str
    baseline: tuple[int, int]
    arm: tuple[int, int]
    difference: float
    ci: tuple[float, float]
    p: float

    def to_json(self, level: float) -> dict[str, Any]:
        return {
            "input": self.input,
            "baseline": {"passes": self.baseline[0], "total": self.baseline[1]},
            "arm": {"passes": self.arm[0], "total": self.arm[1]},
            "difference": self.difference,
            "ci": {
                "method": "newcombe",
                "level": level,
                "low": self.ci[0],
                "high": self.ci[1],
            },
            "p": self.p,
            "p_method": "fisher",
        }


def paired_input(pair: Pair, level: float) -> PairedInput:
    (xb, nb), (xa, na) = pair.baseline, pair.arm
    return PairedInput(
        input=pair.input,
        baseline=pair.baseline,
        arm=pair.arm,
        difference=xa / na - xb / nb,
        ci=stats.newcombe(xa, na, xb, nb, level),
        p=stats.fisher_exact(xa, na, xb, nb),
    )


@dataclass(frozen=True)
class Comparison:
    """One arm against the baseline, over the inputs both ran.

    ``estimate`` is the mean of the per-input differences (arm minus
    baseline pass fraction), every input counting equally. How the
    interval and p-value are made depends on the number of pairs:

    - **one input:** that input's Newcombe interval and Fisher p
      (``ci_method`` ``"newcombe"``, ``p_method`` ``"fisher"``);
    - **at least** ``min_inputs``: a paired cluster bootstrap that
      re-draws inputs with both arms' runs kept together, and a
      sign-flip permutation p on the per-input differences
      (``"bootstrap"``, ``"sign-flip"``);
    - **in between:** the sign-flip p, but no interval
      (``suppressed`` says why), as for ``aggregate()``;
    - **none:** neither.

    ``verdict`` is the margin's (``None`` without one), read off ``ci``
    alone. ``p_adjusted`` is ``p`` after ``adjustment`` over the
    session's ``family`` of comparisons. ``inputs`` (each with its own
    Newcombe/Fisher) and ``unpaired`` are sorted by input id: the
    bootstrap draws from that order, so the result doesn't depend on
    the order results arrived in.

    ``stopped`` counts the cases behind it that stopped early under
    ``--prob-stop=curtail`` (``suppress_stopped_comparison()``); with
    any, there is neither an interval nor a p-value.
    """

    function: str
    spec: CompareSpec
    arm: str
    inputs: tuple[PairedInput, ...]
    unpaired: tuple[str, ...]
    level: float
    resamples: int
    seed: int
    estimate: float | None = None
    ci: tuple[float, float] | None = None
    ci_method: str | None = None
    p: float | None = None
    p_method: str | None = None
    exact: bool | None = None
    suppressed: str | None = None
    verdict: str | None = None
    cost_ratio: float | None = None
    p_adjusted: float | None = None
    adjustment: str = "none"
    family: int = 1
    stopped: int = 0

    @property
    def pairs(self) -> int:
        return len(self.inputs)

    @property
    def axis(self) -> str:
        return self.spec.axis

    @property
    def baseline(self) -> str:
        return self.spec.baseline

    @property
    def exploratory(self) -> bool:
        """Unadjusted p-values among several comparisons."""
        return self.adjustment == "none" and self.family > 1

    @property
    def shown_p(self) -> float | None:
        """The p-value to print: adjusted, when there is an adjustment."""
        return self.p if self.adjustment == "none" else self.p_adjusted

    def to_json(self) -> dict[str, Any]:
        ci = None
        if self.ci is not None:
            ci = {
                "method": self.ci_method,
                "level": self.level,
                "low": self.ci[0],
                "high": self.ci[1],
            }
        return {
            "function": self.function,
            "axis": self.axis,
            "baseline": self.baseline,
            "arm": self.arm,
            "pairs": self.pairs,
            "unpaired": list(self.unpaired),
            "difference": self.estimate,
            "ci": ci,
            "p": self.p,
            "p_method": self.p_method,
            "exact": self.exact,
            "p_adjusted": self.p_adjusted,
            "adjustment": self.adjustment,
            "family": self.family,
            "exploratory": self.exploratory,
            "margin": self.spec.margin,
            "equivalence": self.spec.equivalence,
            "verdict": self.verdict,
            "suppressed": self.suppressed,
            "resamples": self.resamples,
            "seed": self.seed,
            "cost_ratio": self.cost_ratio,
            "inputs": [i.to_json(self.level) for i in self.inputs],
            "stopped": self.stopped,
        }


def suppress_stopped_comparison(cmp: Comparison, stopped: int) -> Comparison:
    """``cmp`` without its interval and p-value: ``stopped`` of the cases
    behind it stopped early (``--prob-stop=curtail``), so their
    fractions lean toward their verdicts and so would a difference
    between arms. The difference and the per-input entries stay, as
    data. With no interval a margin would be UNDECIDED, but a function
    with a margin never stops early (``BenchItem.curtailable``).
    Adjust the session's p-values after this: the family is the
    comparisons that keep one.
    """
    return dataclasses.replace(
        cmp,
        ci=None,
        ci_method=None,
        p=None,
        p_method=None,
        exact=None,
        p_adjusted=None,
        suppressed=f"{_cases(stopped)} stopped early",
        stopped=stopped,
        verdict=cmp.spec.verdict(None),
    )


def _mean_difference(sample: list[tuple[float, float]]) -> float:
    return statistics.fmean([arm - base for base, arm in sample])


def compare_pairs(
    function: str,
    spec: CompareSpec,
    arm: str,
    pairs: Iterable[Pair],
    cfg: StatsConfig,
    *,
    unpaired: Iterable[str] = (),
    cost_ratio: float | None = None,
) -> Comparison:
    """The ``Comparison`` of ``arm`` with ``spec.baseline`` over
    ``pairs``, at ``cfg``'s level, resamples, seed and min_inputs.

    Pure: everything comes from the pass counts, so a regression gate
    against a stored report can pair cases the same way and reuse it.
    The bootstrap resamples ``(baseline, arm)`` fraction pairs sorted by
    input, through ``stats.bootstrap``'s private generator; the
    sign-flip test has its own, from the same seed. The margin verdict
    is read off the interval returned with it.
    """
    ordered = tuple(
        paired_input(p, cfg.level) for p in sorted(pairs, key=lambda p: p.input)
    )
    cmp = Comparison(
        function=function,
        spec=spec,
        arm=arm,
        inputs=ordered,
        unpaired=tuple(sorted(unpaired)),
        level=cfg.level,
        resamples=cfg.resamples,
        seed=cfg.seed,
        cost_ratio=cost_ratio,
    )
    n = len(ordered)
    if n == 0:
        return dataclasses.replace(
            cmp, suppressed="no paired inputs", verdict=spec.verdict(None)
        )
    if n == 1:
        only = ordered[0]
        return dataclasses.replace(
            cmp,
            estimate=only.difference,
            ci=only.ci,
            ci_method="newcombe",
            p=only.p,
            p_method="fisher",
            exact=True,
            verdict=spec.verdict(only.ci),
        )
    diffs = [i.difference for i in ordered]
    p, exact = stats.sign_flip_test(diffs, resamples=cfg.resamples, seed=cfg.seed)
    cmp = dataclasses.replace(
        cmp,
        estimate=statistics.fmean(diffs),
        p=p,
        p_method="sign-flip",
        exact=exact,
    )
    if n < cfg.min_inputs:
        # As for aggregate(): with few inputs the bootstrap's spread
        # underestimates the uncertainty.
        return dataclasses.replace(
            cmp,
            suppressed=f"fewer than {cfg.min_inputs} paired inputs",
            verdict=spec.verdict(None),
        )
    fractions = [
        (i.baseline[0] / i.baseline[1], i.arm[0] / i.arm[1]) for i in ordered
    ]
    samples = stats.bootstrap(
        fractions, _mean_difference, resamples=cfg.resamples, seed=cfg.seed
    )
    ci = stats.percentile_interval(samples, cfg.level)
    return dataclasses.replace(
        cmp, ci=ci, ci_method="bootstrap", verdict=spec.verdict(ci)
    )


def adjust_comparisons(
    comparisons: list[Comparison], method: str
) -> list[Comparison]:
    """``comparisons`` with ``p_adjusted`` filled in: ``method`` applied
    over every one that has a p-value — the session's family."""
    family = [c for c in comparisons if c.p is not None]
    adjusted = dict(
        zip(
            (id(c) for c in family),
            stats.adjust_pvalues([c.p for c in family], method),
        )
    )
    return [
        dataclasses.replace(
            c,
            p_adjusted=adjusted.get(id(c)),
            adjustment=method,
            family=len(family),
        )
        for c in comparisons
    ]


def _cost_ratio(base: list[CaseStats], arm: list[CaseStats]) -> float | None:
    # Cost per run, arm over baseline; None unless both recorded cost.
    base_cost = math.fsum(s.cost for s in base)
    arm_cost = math.fsum(s.cost for s in arm)
    if not (base_cost > 0 and arm_cost > 0):
        return None
    per_run = lambda cost, cases: cost / sum(s.total for s in cases)  # noqa: E731
    return per_run(arm_cost, arm) / per_run(base_cost, base)


def comparisons_of(
    cases: Iterable[CaseStats], cfg: StatsConfig, adjust: str = "none"
) -> list[Comparison]:
    """Every comparison among ``cases``: per compared function (by
    name), each arm (in parametrize order) against its baseline, paired
    by input, then adjusted as one family.

    An input missing either arm — after ``-k``, say — is left out of
    that comparison's pairs and listed in ``unpaired``. Cases sharing an
    input and an arm (duplicate ids) are pooled. Errored runs count as
    non-passes, as in the row fraction.
    """
    by_function: dict[str, list[CaseStats]] = {}
    for s in cases:
        if s.compare is not None:
            by_function.setdefault(function_of(s.case), []).append(s)
    out: list[Comparison] = []
    for function in sorted(by_function):
        group = by_function[function]
        spec = CompareSpec.from_record(group[0].compare)
        cells: dict[str, dict[str, list[CaseStats]]] = {}
        order: dict[str, int] = {}
        for s in group:
            arm = s.compare["arm"]
            order[arm] = s.compare["arm_index"]
            cells.setdefault(s.compare["input"], {}).setdefault(arm, []).append(s)
        for arm in sorted(order, key=order.__getitem__):
            if arm == spec.baseline:
                continue
            pairs, unpaired = [], []
            base_cases: list[CaseStats] = []
            arm_cases: list[CaseStats] = []
            for name, arms in cells.items():
                if spec.baseline in arms and arm in arms:
                    b, a = arms[spec.baseline], arms[arm]
                    base_cases += b
                    arm_cases += a
                    pairs.append(
                        Pair(
                            name,
                            (sum(s.passes for s in b), sum(s.total for s in b)),
                            (sum(s.passes for s in a), sum(s.total for s in a)),
                        )
                    )
                elif spec.baseline in arms or arm in arms:
                    unpaired.append(name)
            base_cases.sort(key=lambda s: s.case)
            arm_cases.sort(key=lambda s: s.case)
            out.append(
                compare_pairs(
                    function,
                    spec,
                    arm,
                    pairs,
                    cfg,
                    unpaired=unpaired,
                    cost_ratio=_cost_ratio(base_cases, arm_cases),
                )
            )
    return adjust_comparisons(out, adjust)


# ---------------------------------------------------------------------------
# Planning (--prob-plan)
# ---------------------------------------------------------------------------

# The true pass rate a plan assumes, the failure rates it plans to
# catch, the chance it asks a gate to pass with, and how far it looks.
_PLAN_ASSUME = 0.97
_PLAN_FLAKE = "0.1,0.01"
_PLAN_POWER = 0.8
_PLAN_CAP = 10_000


@dataclass(frozen=True)
class PlanConfig:
    """``--prob-plan``'s settings: the assumed true pass rate, the
    failure rates to catch, and each case's cost per run from a
    previous report (``costs`` is empty without one)."""

    assume: float = _PLAN_ASSUME
    flakes: tuple[float, ...] = (0.1, 0.01)
    report: str | None = None
    costs: dict[str, float] = field(default_factory=dict, compare=False)


_PLAN_CONFIG = pytest.StashKey["PlanConfig | None"]()


def _parse_rates(raw: Any, source: str) -> tuple[float, ...]:
    parts = [p.strip() for p in str(raw).split(",")]
    if not all(parts):
        raise pytest.UsageError(
            f"{source} must be comma-separated rates strictly between 0 and 1"
            f" (e.g. 0.1,0.01), got {raw!r}"
        )
    rates = tuple(_parse_level(p, source) for p in parts)
    return tuple(dict.fromkeys(rates))


def _read_plan_costs(path: str) -> dict[str, float]:
    """Cost per run of every case in a ``--prob-json`` report: its
    ``rows[].cost`` over ``rows[].total``."""
    source = "--prob-plan-report"
    try:
        payload = json.loads(Path(path).read_text())
    except OSError as exc:
        raise pytest.UsageError(
            f"{source}: cannot read {path}: {exc.strerror or exc}"
        ) from None
    except ValueError as exc:
        raise pytest.UsageError(f"{source}: {path} is not JSON: {exc}") from None
    rows = payload.get("rows") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise pytest.UsageError(
            f"{source}: {path} is not a pytest-probability report (no rows)"
        )
    costs: dict[str, float] = {}
    for row in rows:
        try:
            case, cost, total = row["case"], float(row["cost"]), int(row["total"])
        except (KeyError, TypeError, ValueError):
            raise pytest.UsageError(
                f"{source}: {path} has a row without case, cost and total"
            ) from None
        if total > 0 and math.isfinite(cost):
            costs[str(case)] = cost / total
    return costs


def _resolve_plan_config(config: pytest.Config) -> PlanConfig | None:
    assume = config.getoption("prob_plan_assume")
    assume = (
        _PLAN_ASSUME if assume is None else _parse_level(assume, "--prob-plan-assume")
    )
    flake = config.getoption("prob_plan_flake")
    flakes = _parse_rates(
        _PLAN_FLAKE if flake is None else flake, "--prob-plan-flake"
    )
    if not config.getoption("prob_plan"):
        return None
    report = config.getoption("prob_plan_report")
    costs = _read_plan_costs(report) if report else {}
    return PlanConfig(assume=assume, flakes=flakes, report=report, costs=costs)


def plan_config(config: pytest.Config) -> PlanConfig | None:
    """The session's ``PlanConfig``, or ``None`` without ``--prob-plan``
    (validated at configure)."""
    if _PLAN_CONFIG not in config.stash:
        config.stash[_PLAN_CONFIG] = _resolve_plan_config(config)
    return config.stash[_PLAN_CONFIG]


@dataclass(frozen=True)
class PlanRow:
    """One case's budget. ``runs`` is what would run (selection
    applied); the gate columns are ``None`` for an ungated case, and
    ``power_runs`` also when no run count up to the cap gets there.
    ``cost_per_run`` is ``None`` when no report priced the case."""

    case: str
    runs: int
    gate: Gate | None
    min_runs: int | None = None
    power_runs: int | None = None
    chance: float | None = None
    cost_per_run: float | None = None

    @property
    def cost(self) -> float | None:
        if self.cost_per_run is None:
            return None
        return self.cost_per_run * self.runs


def plan_rows(items: Iterable[pytest.Item], pcfg: PlanConfig) -> list[PlanRow]:
    """The plan for the bench items that would run, one row per case
    in collection order. A gate's numbers don't depend on its planned
    run count, so they are worked out once per distinct gate."""
    cases: OrderedDict[str, list[Any]] = OrderedDict()
    for item in items:
        if isinstance(item, BenchItem):
            entry = cases.setdefault(item.case, [0, item.gate])
            entry[0] += 1
    per_gate: dict[Gate, tuple[int, int | None]] = {}
    rows = []
    for case, (runs, gate) in cases.items():
        extra: dict[str, Any] = {"cost_per_run": pcfg.costs.get(case)}
        if gate is not None:
            key = dataclasses.replace(gate, runs=0)
            if key not in per_gate:
                per_gate[key] = (
                    gate.min_runs(),
                    gate.runs_for_power(pcfg.assume, _PLAN_POWER, _PLAN_CAP),
                )
            extra["min_runs"], extra["power_runs"] = per_gate[key]
            extra["chance"] = gate.power(runs, pcfg.assume)
        rows.append(PlanRow(case, runs, gate, **extra))
    return rows


@pytest.hookimpl(hookwrapper=True)
def pytest_cmdline_main(config: pytest.Config):
    # A plan runs nothing, so it needs no workers — and an xdist
    # controller never collects, so it would have nothing to plan.
    # Turning -n off before xdist reads it keeps the plan in this
    # process.
    if config.getoption("prob_plan") and hasattr(config.option, "numprocesses"):
        config.option.numprocesses = 0
    yield


@pytest.hookimpl(tryfirst=True)
def pytest_runtestloop(session: pytest.Session):
    if plan_config(session.config) is None:
        return None
    # pytest's own runtestloop stops on collection errors; so does a plan.
    if session.testsfailed and not session.config.option.continue_on_collection_errors:
        raise session.Interrupted(
            f"{session.testsfailed} error{'s' if session.testsfailed != 1 else ''}"
            " during collection"
        )
    aggregator = session.config.pluginmanager.get_plugin("probability-aggregator")
    aggregator.plan = plan_rows(session.items, plan_config(session.config))
    return True


# ---------------------------------------------------------------------------
# Regression gate against a baseline report
# ---------------------------------------------------------------------------

# The two "arms" of a baseline comparison, as the line and JSON name them.
BASELINE_ARM, CURRENT_ARM = "baseline", "current"


@dataclass(frozen=True)
class Baseline:
    """A previous ``--prob-json`` report (``--prob-baseline``), reduced
    to what the regression gate needs: each case's counts.

    ``cases`` maps case id to a ``CaseStats`` rebuilt from its row —
    ``passes`` and ``total`` (all a 0.2.0 report has to offer), plus
    ``errors`` and ``cost`` when present. ``margin`` is
    ``--prob-margin`` (``None``: report only, no verdict). Loaded and
    validated in ``pytest_configure`` on every process: workers need
    it too, to know which cases a verdict judges.
    """

    path: str
    created: str | None
    cases: dict[str, CaseStats]
    margin: float | None = None

    @property
    def spec(self) -> CompareSpec:
        # No axis: the two arms are the two reports.
        return CompareSpec(axis=None, baseline=BASELINE_ARM, margin=self.margin)

    def judges(self, case: str) -> bool:
        """Whether a verdict, not each run, decides ``case``: there is a
        margin and the case is in the baseline (so it will be paired)."""
        return self.margin is not None and case in self.cases


def _count(row: dict[str, Any], key: str, default: int | None = None) -> int:
    value = row.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(key)
    return value


def load_baseline(text: str) -> tuple[str | None, dict[str, CaseStats]]:
    """``(created, cases)`` from a report's JSON text; ``ValueError``
    (its message says what is wrong) for anything else.

    Only ``rows[]`` with ``case``, ``passes`` and ``total`` are needed,
    so reports from 0.2.0 on work. Rows with no runs are skipped and
    rows sharing a case id are pooled.
    """
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise ValueError(f"not valid JSON ({exc})") from None
    rows = data.get("rows") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise ValueError("no rows[]; is it a --prob-json report?")
    cases: dict[str, CaseStats] = {}
    for i, row in enumerate(rows):
        try:
            if not isinstance(row, dict) or not isinstance(row.get("case"), str):
                raise ValueError("case")
            passes, total = _count(row, "passes"), _count(row, "total")
            errors = _count(row, "errors", 0)
            if passes + errors > total:
                raise ValueError("passes")
            cost = row.get("cost") or 0.0
            if isinstance(cost, bool) or not isinstance(cost, (int, float)):
                raise ValueError("cost")
        except ValueError as exc:
            raise ValueError(
                f"rows[{i}] needs a case id and run counts with passes <= total"
                f" (bad {exc})"
            ) from None
        if not total:
            continue
        s = cases.setdefault(row["case"], CaseStats(case=row["case"]))
        s.passes += passes
        s.errors += errors
        s.fails += total - passes - errors
        s.cost += float(cost)
    created = data.get("created")
    return (created if isinstance(created, str) else None), cases


_BASELINE = pytest.StashKey["Baseline | None"]()


def _resolve_baseline(config: pytest.Config) -> Baseline | None:
    raw = config.getoption("prob_baseline")
    margin = config.getoption("prob_margin")
    if raw is None:
        if margin is not None:
            raise pytest.UsageError("--prob-margin needs --prob-baseline")
        return None
    # Positive test so NaN fails too.
    if margin is not None and not 0.0 <= margin < 1.0:
        hint = f" (did you mean {margin / 100:g}?)" if 1.0 <= margin < 100.0 else ""
        raise pytest.UsageError(
            f"--prob-margin must be at least 0 and below 1, got {margin:g}{hint}"
        )
    try:
        text = Path(raw).read_text(encoding="utf-8")
    except FileNotFoundError:
        raise pytest.UsageError(f"--prob-baseline: no such file: {raw}") from None
    except (OSError, UnicodeDecodeError) as exc:
        raise pytest.UsageError(f"--prob-baseline: cannot read {raw}: {exc}") from None
    try:
        created, cases = load_baseline(text)
    except ValueError as exc:
        raise pytest.UsageError(f"--prob-baseline: {raw}: {exc}") from None
    return Baseline(path=raw, created=created, cases=cases, margin=margin)


def baseline_of(config: pytest.Config) -> Baseline | None:
    """The session's ``Baseline`` (``None`` without ``--prob-baseline``),
    loaded and validated at configure."""
    if _BASELINE not in config.stash:
        config.stash[_BASELINE] = _resolve_baseline(config)
    return config.stash[_BASELINE]


@dataclass(frozen=True)
class BaselineResult:
    """This run against the baseline: one ``Comparison`` per bench
    function with at least one case in both reports (by name; arm
    ``current``, baseline ``baseline``), and the ids of the cases found
    in only one of them, sorted. Those are listed, never paired, so
    they can't fail the gate; a function with no paired case has no
    comparison."""

    baseline: Baseline
    comparisons: tuple[Comparison, ...]
    only_current: tuple[str, ...]
    only_baseline: tuple[str, ...]

    @property
    def paired(self) -> int:
        return sum(c.pairs for c in self.comparisons)

    def to_json(self) -> dict[str, Any]:
        return {
            "path": self.baseline.path,
            "created": self.baseline.created,
            "margin": self.baseline.margin,
            "paired": self.paired,
            "only_current": list(self.only_current),
            "only_baseline": list(self.only_baseline),
            "comparisons": [c.to_json() for c in self.comparisons],
        }


def _variant(case: str) -> str:
    # The case id after its function: the input a baseline pair is
    # keyed by ("" for an unparametrized function).
    return case.split("::", 1)[1] if "::" in case else ""


def compare_with_baseline(
    cases: Iterable[CaseStats],
    baseline: Baseline,
    cfg: StatsConfig,
    adjust: str = "none",
) -> BaselineResult:
    """Pair ``cases`` with the baseline's by case id and compare each
    bench function, current − baseline, through ``compare_pairs``: the
    same regimes, interval and margin verdict as a comparison on an
    axis. Errored runs count as non-passes, as in the row fraction. The
    comparisons are one family for ``adjust``, apart from the axis
    comparisons.
    """
    current = {s.case: s for s in cases if s.total}
    functions: dict[str, list[str]] = {}
    for case in {*current, *baseline.cases}:
        functions.setdefault(function_of(case), []).append(case)
    out: list[Comparison] = []
    for function in sorted(functions):
        ids = sorted(functions[function])
        paired = [c for c in ids if c in current and c in baseline.cases]
        if not paired:
            continue
        base = [baseline.cases[c] for c in paired]
        now = [current[c] for c in paired]
        out.append(
            compare_pairs(
                function,
                baseline.spec,
                CURRENT_ARM,
                [
                    Pair(_variant(b.case), (b.passes, b.total), (n.passes, n.total))
                    for b, n in zip(base, now)
                ],
                cfg,
                unpaired=[_variant(c) for c in sorted(set(ids) - set(paired))],
                cost_ratio=_cost_ratio(base, now),
            )
        )
    return BaselineResult(
        baseline=baseline,
        comparisons=tuple(adjust_comparisons(out, adjust)),
        only_current=tuple(sorted(c for c in current if c not in baseline.cases)),
        only_baseline=tuple(sorted(c for c in baseline.cases if c not in current)),
    )


_MARKUP = {
    "pass": {"green": True},
    "flaky": {"yellow": True},
    "errored": {"yellow": True},
    "fail": {"red": True},
    "error": {"red": True},
}

# How far runs_to_settle() looks before calling a rate too close to its
# bar to settle: beyond this, "run more" stops being useful advice.
_SETTLE_CAP = 10_000

_VERDICT_MARKUP = {
    PASS: {"green": True},
    UNDECIDED: {"yellow": True},
    FAIL: {"red": True},
}


def _pct(p: float) -> str:
    """A probability as a whole percent, rounded half up (JSON keeps
    the unrounded value).

    ``0%`` and ``100%`` are reserved for bounds that are exactly 0 or
    1: an upper bound of 99.7% prints as ``99%``, so ``100%`` always
    means the data could not rule out "never fails".
    """
    pct = math.floor(p * 100 + 0.5)
    if pct >= 100 and p < 1.0:
        pct = 99
    elif pct <= 0 and p > 0.0:
        pct = 1
    return f"{pct}%"


def _chance(p: float) -> str:
    """A planned chance as a whole percent. Rates strictly between 0
    and 1 never make one certain, so ``100%`` is never printed even
    when the float rounds to 1.0; ``0%`` still means impossible."""
    return _pct(min(p, 0.999))


def _pct1(p: float) -> str:
    """A probability to one decimal, rounded half up — ``81.4%`` — with
    ``0.0%``/``100.0%`` reserved for exactly 0 and 1, as in ``_pct``.
    For function-level lines, whose averages move in finer steps than
    one case's fraction."""
    tenths = math.floor(p * 1000 + 0.5)
    if tenths >= 1000 and p < 1.0:
        tenths = 999
    elif tenths <= 0 and p > 0.0:
        tenths = 1
    return f"{tenths / 10:.1f}%"


def _secs(t: float | None) -> str:
    """A duration to three significant figures in s, ms or µs —
    ``2.40s``, ``512ms``, ``12.3ms``, ``850µs`` — rounded half up (the
    JSON report keeps the unrounded value). ``None``, a bound that
    doesn't exist yet, is ``—``."""
    if t is None:
        return "—"
    if t <= 0.0:
        return "0s"
    # Thresholds sit where rounding carries into the next unit or
    # digit: 0.9996s is 1.00s, not 1000ms; 9.996s is 10.0s.
    if t >= 0.9995:
        value, unit = t, "s"
    elif t >= 0.0009995:
        value, unit = t * 1e3, "ms"
    else:
        value, unit = t * 1e6, "µs"
    decimals = 0 if value >= 99.95 else 1 if value >= 9.995 else 2
    scale = 10**decimals
    return f"{math.floor(value * scale + 0.5) / scale:.{decimals}f}{unit}"


def _change(c: float) -> str:
    """A relative change in interval width as a whole percent with a
    real minus sign — ``−22%``. A shrink too small to round to 1% is
    ``−<1%`` and no change at all is ``±0%``, so ``−0%`` never shows."""
    pct = math.floor(abs(c) * 100 + 0.5)
    if abs(c) < 1e-12:
        return "±0%"
    sign = "−" if c < 0 else "+"
    return f"{sign}{pct}%" if pct else f"{sign}<1%"


def _pp(d: float, decimals: int = 0) -> str:
    """A difference in percentage points, signed with a real minus —
    ``+20``, ``−11``, ``+11.7`` — rounded half up on its size. Only an
    exact 0 is unsigned."""
    scale = 10**decimals
    size = math.floor(abs(d) * 100 * scale + 0.5) / scale
    text = f"{size:.{decimals}f}"
    if d == 0:
        return text
    return f"{'−' if d < 0 else '+'}{text}"


def _p(p: float) -> str:
    """A p-value as printed: ``0.47``, ``0.004``, ``<0.001``; ``1`` is
    kept for exactly 1."""
    if p >= 1.0:
        return "1"
    if p < 0.001:
        return "<0.001"
    if p < 0.01:
        return f"{p:.3f}"
    return f"{min(p, 0.99):.2f}"


def _p_cell(p: float | None) -> str:
    if p is None:
        return ""
    text = _p(p)
    return f"p{text}" if text.startswith("<") else f"p={text}"


def _ratio(r: float) -> str:
    """A cost ratio: ``×4.0``, ``×0.25``."""
    return f"×{r:.1f}" if r >= 1.0 else f"×{r:.2f}"


def _pp_decimals(cmp: Any) -> int:
    # One input's difference moves in whole-run steps, like a row's
    # fraction; an average over inputs in finer ones, like an aggregate.
    return 0 if cmp.ci_method == "newcombe" else 1


def _difference_cell(cmp: Any) -> tuple[str, tuple[str, str] | None]:
    """``("+20 pp", ("−11", "+51"))``; the interval part is ``None``
    when there is none, and the difference ``""`` with no pairs or when
    cases behind it stopped early."""
    if cmp.estimate is None or cmp.stopped:
        return "", None
    dec = _pp_decimals(cmp)
    bounds = None
    if cmp.ci is not None:
        bounds = (_pp(cmp.ci[0], dec), _pp(cmp.ci[1], dec))
    return f"{_pp(cmp.estimate, dec)} pp", bounds


def _comparison_lines(
    comparisons: list[Comparison],
    label: Callable[[Comparison], str] | None = None,
) -> list[tuple[str, str | None]]:
    """The comparisons block: one line per comparison, columns aligned —
    ``triage[style]  chain_of_thought − terse  +20 pp [−11, +51]  p=0.47
    1 paired  cost ×4.0`` — each with its verdict (``None``: no margin).

    A comparison with no interval but several pairs (fewer than
    ``prob_min_inputs``) is followed by one indented line per input
    with that input's own interval and p. One with cases that stopped
    early shows neither, only ``interval hidden: 2 cases stopped
    early``. When the session makes more than one comparison, a closing
    line says whether the p-values were adjusted for that. ``label``
    names a comparison's line (default: ``function[axis]``).
    """
    if label is None:
        label = lambda c: f"{c.function}[{c.axis}]"  # noqa: E731
    names = [label(c) for c in comparisons]
    arms = [f"{c.arm} − {c.baseline}" for c in comparisons]
    diffs = [_difference_cell(c) for c in comparisons]
    ps = [_p_cell(c.shown_p) for c in comparisons]
    sizes = [
        f"{c.pairs} paired" + (f", {len(c.unpaired)} unpaired" if c.unpaired else "")
        for c in comparisons
    ]
    costs = [
        f"cost {_ratio(c.cost_ratio)}" if c.cost_ratio is not None else ""
        for c in comparisons
    ]
    bars = [c.spec.bar() for c in comparisons]

    def width(cells: list[str]) -> int:
        return max((len(x) for x in cells), default=0)

    shown = [b for _, b in diffs if b is not None]
    low_w = max((len(b[0]) for b in shown), default=0)
    high_w = max((len(b[1]) for b in shown), default=0)
    interval_w = low_w + high_w + 4 if shown else 0
    widths = {
        "name": width(names),
        "arms": width(arms),
        "diff": width([d for d, _ in diffs]),
        "p": width(ps),
        "size": width(sizes),
        "cost": width(costs),
        "bar": width(bars),
    }
    lines: list[tuple[str, str | None]] = []
    for c, name, arm, (diff, bounds), p, size, cost, bar in zip(
        comparisons, names, arms, diffs, ps, sizes, costs, bars
    ):
        cell = f"{diff:>{widths['diff']}}"
        if interval_w:
            interval = (
                f"[{bounds[0]:>{low_w}}, {bounds[1]:>{high_w}}]" if bounds else ""
            )
            cell += f" {interval:<{interval_w}}"
        parts = [
            f"{name:<{widths['name']}}",
            f"{arm:<{widths['arms']}}",
            cell,
            f"{p:<{widths['p']}}",
            f"{size:<{widths['size']}}",
        ]
        if widths["cost"]:
            parts.append(f"{cost:<{widths['cost']}}")
        if c.stopped:
            parts.append(f"interval hidden: {c.suppressed}")
        if c.verdict is not None:
            parts.append(f"{bar:<{widths['bar']}}  {c.verdict.upper()}")
        # Columns no comparison uses are left out altogether.
        lines.append(("  " + "  ".join(x for x in parts if x).rstrip(), c.verdict))
        if c.ci is None and c.pairs > 1 and not c.stopped:
            lines.extend((ln, None) for ln in _input_lines(c))
    family = comparisons[0].family if comparisons else 0
    if family > 1:
        adjustment = comparisons[0].adjustment
        if adjustment == "none":
            note = (
                f"p not adjusted for {family} comparisons: exploratory"
                " (--prob-adjust=holm adjusts them)"
            )
        else:
            note = f"p adjusted for {family} comparisons ({adjustment})"
        lines.append(("", None))
        lines.append((f"  {note}", None))
    return lines


def _latency_lines(
    results: list[LatencyResult], name_col: int = 0
) -> list[tuple[str, str | None]]:
    """One line per latency result, columns aligned across them —
    ``classify::slow  40 runs  p95  2.40s  [2.10s, 2.90s]  ≤2s  FAIL`` —
    each with its verdict (``None``: ungated). A bound that doesn't
    exist yet prints as ``—``; the bar and verdict appear on gated
    lines only."""
    if not results:
        return []
    names = [r.case for r in results]
    runs = [f"{r.total} run" + ("" if r.total == 1 else "s") for r in results]
    labels = [r.spec.label() for r in results]
    ests = [_secs(r.estimate) for r in results]
    bounds = [tuple(_secs(v) for v in r.interval) for r in results]
    bars = [r.spec.bar() for r in results]
    name_w = max(name_col, *(len(x) for x in names))
    runs_w = max(len(x) for x in runs)
    label_w = max(len(x) for x in labels)
    est_w = max(len(x) for x in ests)
    low_w = max(len(b[0]) for b in bounds)
    high_w = max(len(b[1]) for b in bounds)
    bar_w = max(len(x) for x in bars)
    lines = []
    for r, name, n, label, est, (low, high), bar in zip(
        results, names, runs, labels, ests, bounds, bars
    ):
        line = (
            f"  {name:<{name_w}}  {n:>{runs_w}}  {label:<{label_w}}  {est:>{est_w}}"
            f"  [{low:>{low_w}}, {high:>{high_w}}]"
        )
        if r.verdict is not None:
            line += f"  {bar:<{bar_w}}  {r.verdict.upper()}"
        if r.excluded:
            line += f"  ({r.excluded} errored, excluded)"
        lines.append((line.rstrip(), r.verdict))
    return lines


def _input_lines(cmp: Comparison) -> list[str]:
    """Per-input lines under a comparison without an interval:
    ``refund  10/10 vs 8/10  +20 pp [−11, +51]  p=0.47`` (each input's
    own Newcombe interval and Fisher p, not adjusted)."""
    names = [i.input for i in cmp.inputs]
    counts = [
        f"{i.arm[0]}/{i.arm[1]} vs {i.baseline[0]}/{i.baseline[1]}"
        for i in cmp.inputs
    ]
    diffs = [f"{_pp(i.difference)} pp" for i in cmp.inputs]
    bounds = [(_pp(i.ci[0]), _pp(i.ci[1])) for i in cmp.inputs]
    name_w = max(len(x) for x in names)
    count_w = max(len(x) for x in counts)
    diff_w = max(len(x) for x in diffs)
    low_w = max(len(b[0]) for b in bounds)
    high_w = max(len(b[1]) for b in bounds)
    return [
        f"      {name:<{name_w}}  {count:>{count_w}}  {diff:>{diff_w}}"
        f" [{low:>{low_w}}, {high:>{high_w}}]  {_p_cell(i.p)}"
        for i, name, count, diff, (low, high) in zip(
            cmp.inputs, names, counts, diffs, bounds
        )
    ]


def _metric_lines(
    entries: list[tuple[Aggregate, MetricAggregate]], intervals: bool = True
) -> list[str]:
    """The metrics block: one line per (aggregate, metric), columns
    aligned — ``classify  pass^3  N=12 inputs  41.2%  [30.1%, 52.0%]`` —
    the aggregate's name only on its first line. The interval is left
    out when there is none (fewer than ``prob_min_inputs`` inputs with k
    runs) or ``intervals`` is off; ``2 left out (fewer than 5 runs)``
    counts the cases too short for the metric. When some of the inputs
    stopped early (``--prob-stop=curtail``) the line shows neither
    estimate nor interval, only ``hidden: 2 cases stopped early``."""
    names, prev = [], None
    for agg, _ in entries:
        names.append(agg.name if agg is not prev else "")
        prev = agg
    metrics = [m.metric.name for _, m in entries]
    sizes = [f"N={m.inputs} input{'' if m.inputs == 1 else 's'}" for _, m in entries]
    ests = [
        _pct1(m.estimate) if m.estimate is not None and not m.stopped else ""
        for _, m in entries
    ]
    bounds = [
        (_pct1(m.ci[0]), _pct1(m.ci[1])) if intervals and m.ci is not None else None
        for _, m in entries
    ]
    notes = [
        ", ".join(
            note
            for note in (
                f"hidden: {_cases(m.stopped)} stopped early" if m.stopped else "",
                f"{m.left_out} left out (fewer than {m.metric.k} runs)"
                if m.left_out
                else "",
            )
            if note
        )
        for _, m in entries
    ]

    def width(cells: list[str]) -> int:
        return max((len(x) for x in cells), default=0)

    shown = [b for b in bounds if b is not None]
    low_w = max((len(b[0]) for b in shown), default=0)
    high_w = max((len(b[1]) for b in shown), default=0)
    interval_w = low_w + high_w + 4 if shown else 0
    name_w, metric_w, size_w, est_w = map(width, (names, metrics, sizes, ests))
    lines = []
    for name, metric, size, est, b, note in zip(
        names, metrics, sizes, ests, bounds, notes
    ):
        parts = [f"{name:<{name_w}}", f"{metric:<{metric_w}}", f"{size:<{size_w}}"]
        # Columns no line uses are left out altogether.
        if est_w:
            parts.append(f"{est:>{est_w}}")
        if interval_w:
            cell = f"[{b[0]:>{low_w}}, {b[1]:>{high_w}}]" if b is not None else ""
            parts.append(f"{cell:<{interval_w}}")
        parts.append(note)
        lines.append(("  " + "  ".join(parts)).rstrip())
    return lines


# The metrics block's closing legend, one line per kind used.
_METRIC_LEGEND = {
    "^": "pass^k: the chance that k runs of an input all pass (reliability)",
    "@": "pass@k: the chance that at least one of k runs of an input passes"
    " (best-of-k)",
}


# How many unmatched case ids a line of the baseline block names.
_UNMATCHED_SHOWN = 5


def _cases(n: int) -> str:
    return f"{n} case" if n == 1 else f"{n} cases"


def _baseline_header(result: BaselineResult) -> str:
    """``against main.json (2026-10-01T12:00:00): 26 cases paired, 1 only
    in this run, 2 only in the baseline``."""
    b = result.baseline
    when = f" ({b.created})" if b.created else ""
    parts = [f"{_cases(result.paired)} paired"]
    if result.only_current:
        parts.append(f"{len(result.only_current)} only in this run")
    if result.only_baseline:
        parts.append(f"{len(result.only_baseline)} only in the baseline")
    return f"against {b.path}{when}: {', '.join(parts)}"


def _case_list(ids: tuple[str, ...]) -> str:
    shown = ", ".join(ids[:_UNMATCHED_SHOWN])
    more = len(ids) - _UNMATCHED_SHOWN
    return f"{shown} and {more} more" if more > 0 else shown


def _baseline_lines(result: BaselineResult) -> list[tuple[str, str | None]]:
    """The baseline block: a header naming the report and the pairing,
    one comparison line per function (``classify  current − baseline
    −1.2 pp [−4.0, +1.5]  p=0.41  40 paired  ≥−2 pp  PASS``), then the
    cases found in only one report (the first few ids of each). The
    header is UNDECIDED-colored when no case paired at all."""
    lines: list[tuple[str, str | None]] = [
        (f"  {_baseline_header(result)}", None if result.paired else UNDECIDED)
    ]
    lines.extend(
        _comparison_lines(list(result.comparisons), label=lambda c: c.function)
    )
    unmatched = [
        ("only in this run:", result.only_current),
        ("only in the baseline:", result.only_baseline),
    ]
    unmatched = [(name, ids) for name, ids in unmatched if ids]
    if unmatched:
        lines.append(("", None))
        width = max(len(name) for name, _ in unmatched)
        lines.extend(
            (f"  {name:<{width}} {_case_list(ids)}", None) for name, ids in unmatched
        )
    return lines


def _row_name(s: CaseStats) -> str:
    return s.case


class ProbabilityAggregator:
    """Rebuilds cross-run stats from test reports.

    Consuming reports (rather than recording in-process during runtest)
    keeps the aggregate correct under pytest-xdist: workers serialize
    ``user_properties`` back to the controller, which is where the
    summary renders.
    """

    def __init__(self, config: pytest.Config) -> None:
        self._config = config
        self._stats: OrderedDict[str, CaseStats] = OrderedDict()
        # --prob-plan runs nothing, so it never writes a report: it
        # would only overwrite the last real one.
        self._json_path = (
            None if config.getoption("prob_plan") else config.getoption("prob_json")
        )
        # --prob-plan's rows, set by pytest_runtestloop instead of running.
        self.plan: list[PlanRow] | None = None
        self._explain = _explain(config)
        # Raw per-run records, kept only when a JSON report was
        # requested.
        self._records: list[dict[str, Any]] | None = (
            [] if self._json_path else None
        )
        # aggregates(), computed once: the JSON report and the summary
        # both need it, and a bootstrap is the one costly step here.
        self._aggregates: list[Aggregate] | None = None
        # comparisons(), likewise.
        self._comparisons: list[Comparison] | None = None
        # baseline_result(), likewise; stays None without --prob-baseline.
        self._baseline_result: BaselineResult | None = None
        # latency_results(), likewise.
        self._latency: dict[str, LatencyResult] | None = None
        # Cases stopped early (--prob-stop=curtail), from the skipped
        # runs' reports, in the order they stopped.
        self._stops: dict[str, Stop] = {}

    def pytest_runtest_logreport(self, report) -> None:
        when = getattr(report, "when", None)
        if when in ("setup", "call") and report.skipped:
            self._note_stop(report)
        if when != "call":
            return
        for name, value in report.user_properties:
            if name != "probability":
                continue
            gate = value.get("gate")
            compare = value.get("compare")
            latency = value.get("latency")
            if self._records is not None:
                # The gate, comparison and latency specs are per case:
                # rows[].gate, comparisons[] and rows[].latency report
                # them once.
                self._records.append(
                    {
                        k: v
                        for k, v in value.items()
                        if k not in ("gate", "compare", "latency")
                    }
                    if gate is not None or compare is not None or latency is not None
                    else value
                )
            st = self._stats.setdefault(value["case"], CaseStats(case=value["case"]))
            if gate is not None and st.gate is None:
                st.gate = Gate.from_record(gate)
            if compare is not None and st.compare is None:
                st.compare = compare
            if latency is not None and st.latency is None:
                st.latency = LatencySpec.from_record(latency)
            outcome = value["outcome"]
            if outcome == "pass":
                st.passes += 1
            elif outcome == "error":
                st.errors += 1
            else:
                st.fails += 1
            (st.error_times if outcome == "error" else st.times).append(
                value["elapsed"]
            )
            if value["cost"]:
                st.cost += value["cost"]
            for usage in value.get("usage", ()):
                _merge_usage(st.usage, usage)

    def _note_stop(self, report) -> None:
        # A run the Curtailer skipped (normally in setup, by its mark).
        for name, value in report.user_properties:
            if name != STOP_KEY:
                continue
            stop = self._stops.get(value["case"])
            if stop is None:
                stop = self._stops[value["case"]] = Stop(
                    case=value["case"],
                    after=value["after"],
                    planned=value["planned"],
                    verdict=value["verdict"],
                )
            stop.skipped += 1

    def stops(self) -> dict[str, Stop]:
        """The cases that stopped early, by case id."""
        return self._stops

    def _cost_avoided(self, stop: Stop) -> float | None:
        """A stopped case's skipped runs priced at its recorded cost per
        run; ``None`` when it recorded none."""
        s = self._stats.get(stop.case)
        if s is None or not s.cost or not s.total:
            return None
        return s.cost / s.total * stop.skipped

    def _stopping_json(self) -> dict[str, Any]:
        stops = list(self._stops.values())
        costs = [c for c in map(self._cost_avoided, stops) if c is not None]
        scfg = stop_config(self._config)
        skipped = sum(st.skipped for st in stops)
        return {
            "mode": scfg.mode,
            "active": scfg.active,
            "cases": len(stops),
            "runs_skipped": skipped,
            "runs_planned": sum(s.total for s in self._stats.values()) + skipped,
            "cost_avoided": math.fsum(costs) if costs else None,
        }

    def _stop_json(self, case: str) -> dict[str, Any] | None:
        stop = self._stops.get(case)
        if stop is None:
            return None
        return {
            "after": stop.after,
            "planned": stop.planned,
            "skipped": stop.skipped,
            "verdict": stop.verdict,
            "cost_avoided": self._cost_avoided(stop),
        }

    def _stop_line(self) -> str | None:
        """The footer's ``Stopped:`` line; ``None`` when nothing stopped."""
        if not self._stops:
            return None
        info = self._stopping_json()
        skipped, planned = info["runs_skipped"], info["runs_planned"]
        line = (
            f"  Stopped: {_cases(info['cases'])} early, saving {skipped} of"
            f" {planned} runs ({skipped / planned * 100:.0f}%)"
        )
        if info["cost_avoided"]:
            line += f" and about ${info['cost_avoided']:.4f}"
        return line

    def gate_results(self) -> dict[str, GateResult]:
        """The verdict for every gated row, from the aggregated counts."""
        return {
            s.case: s.gate.evaluate(s)
            for s in self._stats.values()
            if s.gate is not None
        }

    def latency_results(self) -> dict[str, LatencyResult]:
        """Every row's latency quantile, interval and (when gated)
        verdict, from its aggregated run times, computed once after
        every result is in. A case whose mark set no latency arguments
        gets the session's ``prob_latency_quantile`` at
        ``prob_confidence``."""
        if self._latency is None:
            default = LatencySpec(
                quantile=latency_config(self._config).quantile,
                level=stats_config(self._config).level,
                errors=gate_config(self._config).errors,
            )
            self._latency = {
                s.case: (s.latency or default).evaluate(s)
                for s in self._stats.values()
            }
        return self._latency

    def _latency_gates(self) -> list[LatencyResult]:
        return [r for r in self.latency_results().values() if r.verdict is not None]

    def aggregates(self) -> list[Aggregate]:
        """One ``Aggregate`` per bench function, by name, then the
        ``Overall`` one over every case (``[]`` when no case ran).
        Computed once, after every result is in. Nothing here depends on
        the order results arrived in — not the values (see
        ``Aggregate``), and not the list order, unlike the rows — so it
        is identical with and without xdist. Those over a case that
        stopped early have no interval (``suppress_stopped``)."""
        if self._aggregates is None:
            cfg = stats_config(self._config)
            by_function: dict[str, list[CaseStats]] = {}
            for s in self._stats.values():
                by_function.setdefault(function_of(s.case), []).append(s)
            metrics = metrics_config(self._config)
            out = [
                aggregate(FUNCTION, name, by_function[name], cfg, metrics=metrics)
                for name in sorted(by_function)
            ]
            if self._stats:
                out.append(
                    aggregate(
                        OVERALL, "Overall", self._stats.values(), cfg, metrics=metrics
                    )
                )
            if self._stops:
                out = [suppress_stopped(a, self._stops) for a in out]
            self._aggregates = out
        return self._aggregates

    def comparisons(self) -> list[Comparison]:
        """Every comparison (``comparisons_of``), computed once after
        every result is in; like ``aggregates()``, independent of the
        order results arrived in. One whose arm or baseline has a paired
        case that stopped early has no interval or p-value
        (``suppress_stopped_comparison``)."""
        if self._comparisons is None:
            adjust = compare_config(self._config).adjust
            out = comparisons_of(
                self._stats.values(), stats_config(self._config), adjust
            )
            stopped = self._stopped_per_comparison(out)
            if any(stopped):
                out = adjust_comparisons(
                    [
                        suppress_stopped_comparison(c, n) if n else c
                        for c, n in zip(out, stopped)
                    ],
                    adjust,
                )
            self._comparisons = out
        return self._comparisons

    def baseline_result(self) -> BaselineResult | None:
        """This run against ``--prob-baseline`` (``None`` without it),
        computed once after every result is in; like ``comparisons()``,
        independent of the order results arrived in. A function whose
        paired cases include one that stopped early has no interval or
        p-value (``suppress_stopped_comparison``)."""
        baseline = baseline_of(self._config)
        if baseline is None:
            return None
        if self._baseline_result is None:
            adjust = compare_config(self._config).adjust
            result = compare_with_baseline(
                self._stats.values(), baseline, stats_config(self._config), adjust
            )
            stopped = [
                sum(
                    function_of(case) == c.function
                    and _variant(case) in {i.input for i in c.inputs}
                    for case in self._stops
                )
                for c in result.comparisons
            ]
            if any(stopped):
                result = dataclasses.replace(
                    result,
                    comparisons=tuple(
                        adjust_comparisons(
                            [
                                suppress_stopped_comparison(c, n) if n else c
                                for c, n in zip(result.comparisons, stopped)
                            ],
                            adjust,
                        )
                    ),
                )
            self._baseline_result = result
        return self._baseline_result

    def _baseline_comparisons(self) -> tuple[Comparison, ...]:
        result = self.baseline_result()
        return result.comparisons if result is not None else ()

    def _stopped_per_comparison(self, comparisons: list[Comparison]) -> list[int]:
        # How many of each comparison's paired cases stopped early.
        if not self._stops:
            return [0] * len(comparisons)
        stopped = [
            self._stats[case]
            for case in self._stops
            if case in self._stats and self._stats[case].compare is not None
        ]
        return [
            sum(
                function_of(s.case) == c.function
                and s.compare["arm"] in (c.arm, c.baseline)
                and s.compare["input"] in {i.input for i in c.inputs}
                for s in stopped
            )
            for c in comparisons
        ]

    def _shown_aggregates(self) -> list[Aggregate]:
        """The aggregates the summary prints: those with an interval, and
        those that would have one but for cases that stopped early (as a
        note), unless intervals are hidden. Overall is left out when it
        would repeat the only function's line."""
        cfg = stats_config(self._config)
        if not cfg.intervals:
            return []
        aggs = self.aggregates()
        functions = sum(a.scope == FUNCTION for a in aggs)
        return [
            a
            for a in aggs
            if (a.ci is not None or (a.stopped and a.inputs >= cfg.min_inputs))
            and not (a.scope == OVERALL and functions == 1)
        ]

    def _shown_metrics(self) -> list[tuple[Aggregate, MetricAggregate]]:
        """The metrics block's lines: every function's metrics, by
        function name, then Overall's — left out, as in the aggregate
        block, when there is a single function. Unlike that block,
        functions with too few inputs for an interval are listed (with
        the estimate alone): the block is the terminal's only view of
        the metrics."""
        if not metrics_config(self._config):
            return []
        aggs = self.aggregates()
        functions = sum(a.scope == FUNCTION for a in aggs)
        return [
            (a, m)
            for a in aggs
            if not (a.scope == OVERALL and functions == 1)
            for m in a.metrics
        ]

    def pytest_sessionfinish(self, session: pytest.Session, exitstatus: int) -> None:
        # Under pytest-xdist this hook also fires on workers, which only
        # hold a shard of the results — the controller decides gates and
        # writes the file.
        if hasattr(session.config, "workerinput"):
            return
        results = self.gate_results()
        gcfg = gate_config(session.config)
        verdicts = (
            [r.verdict for r in results.values()]
            + [
                c.verdict
                for c in (*self.comparisons(), *self._baseline_comparisons())
                if c.verdict is not None
            ]
            + [r.verdict for r in self._latency_gates()]
        )
        if exitstatus == pytest.ExitCode.OK and any(map(gcfg.fails, verdicts)):
            # Every run passed or was xfailed, but a gate or a margin did
            # not hold. Any other status (failures, interrupts) already
            # says more.
            exitstatus = session.exitstatus = pytest.ExitCode.TESTS_FAILED
        if not self._json_path:
            return
        all_stats = list(self._stats.values())
        cfg = stats_config(session.config)
        metrics = metrics_config(session.config)
        latency = self.latency_results()
        payload = {
            "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "runs": _runs(session.config),
            "stats_config": cfg.to_json(),
            "exit_status": int(exitstatus),
            "totals": {
                "passes": sum(s.passes for s in all_stats),
                "fails": sum(s.fails for s in all_stats),
                "errors": sum(s.errors for s in all_stats),
                "count": sum(s.total for s in all_stats),
                "pass_rate": (
                    sum(s.passes for s in all_stats)
                    / sum(s.total for s in all_stats)
                    * 100
                    if all_stats and sum(s.total for s in all_stats)
                    else 0.0
                ),
                "cost": sum(s.cost for s in all_stats),
                "usage": self._model_totals(all_stats),
            },
            "rows": [
                {
                    "case": s.case,
                    "passes": s.passes,
                    "fails": s.fails,
                    "errors": s.errors,
                    "total": s.total,
                    "pass_rate": s.passes / s.total * 100 if s.total else 0.0,
                    "status": s.status,
                    "ci": self._ci_json(cfg, s),
                    "gate": (
                        results[s.case].to_json() if s.case in results else None
                    ),
                    "latency": latency[s.case].to_json(),
                    "cost": s.cost,
                    "usage": s.usage,
                    "metrics": {m.name: m.of(s) for m in metrics},
                    "stopped": self._stop_json(s.case),
                }
                for s in all_stats
            ],
            "aggregates": [a.to_json() for a in self.aggregates()],
            "comparisons": [c.to_json() for c in self.comparisons()],
            "baseline": (
                self.baseline_result().to_json()
                if self.baseline_result() is not None
                else None
            ),
            "stopping": self._stopping_json(),
            "records": self._records or [],
        }
        if self._explain:
            for s, row in zip(all_stats, payload["rows"]):
                row["explanation"] = self._row_reading(s).text()
                if row["gate"] is not None:
                    row["gate"]["explanation"] = self._gate_reading(
                        s, results[s.case]
                    ).text()
                row["latency"]["explanation"] = self._latency_reading(
                    latency[s.case]
                ).text()
            for agg, entry in zip(self.aggregates(), payload["aggregates"]):
                entry["explanation"] = self._aggregate_reading(agg).text()
                for m in agg.metrics:
                    entry["metrics"][m.metric.name]["explanation"] = (
                        self._metric_reading(agg, m).text()
                    )
            for cmp, entry in zip(self.comparisons(), payload["comparisons"]):
                entry["explanation"] = self._comparison_reading(cmp).text()
            if payload["baseline"] is not None:
                block = payload["baseline"]
                block["explanation"] = self._baseline_summary_reading().text()
                for cmp, entry in zip(
                    self._baseline_comparisons(), block["comparisons"]
                ):
                    entry["explanation"] = self._baseline_reading(cmp).text()
        path = Path(self._json_path)
        if path.parent != Path(""):
            path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2))

    @staticmethod
    def _ci_json(cfg: StatsConfig, s: CaseStats) -> dict[str, Any] | None:
        # Always computed (it is data, not display): --prob-no-intervals
        # and the runs == 1 rule only affect the terminal.
        if not s.total:
            return None
        low, high = cfg.interval(s.passes, s.total)
        return {"method": cfg.method, "level": cfg.level, "low": low, "high": high}

    @staticmethod
    def _model_totals(all_stats: list[CaseStats]) -> dict[str, dict[str, Any]]:
        totals: dict[str, dict[str, Any]] = {}
        for s in all_stats:
            for model, agg in s.usage.items():
                _merge_usage(totals, {"model": model, **agg})
        return totals

    @staticmethod
    def _interval_cells(bounds: list[tuple[float, float] | None]) -> list[str]:
        """Padded ``[low, high]`` cells, one per entry; blank for ``None``,
        and all empty strings when every entry is ``None``.

        Low and high are right-aligned separately so the brackets and
        comma line up when widths vary.
        """
        texts = [tuple(_pct(v) for v in b) if b is not None else None for b in bounds]
        shown = [t for t in texts if t is not None]
        if not shown:
            return [""] * len(bounds)
        low_w = max(len(t[0]) for t in shown)
        high_w = max(len(t[1]) for t in shown)
        width = low_w + high_w + 4  # "[", ", ", "]"
        return [
            f"[{t[0]:>{low_w}}, {t[1]:>{high_w}}]" if t is not None else " " * width
            for t in texts
        ]

    @classmethod
    def _ci_cells(
        cls, cfg: StatsConfig, all_stats: list[CaseStats]
    ) -> list[str | None]:
        """The interval column, one padded cell per row (or ``None``s
        when the column is off).

        A single run says almost nothing about a rate, so rows with one
        run get a blank cell, and the column disappears entirely when no
        row has more.
        """
        if not cfg.intervals or all(s.total <= 1 for s in all_stats):
            return [None] * len(all_stats)
        return cls._interval_cells(
            [
                cfg.interval(s.passes, s.total) if s.total > 1 else None
                for s in all_stats
            ]
        )

    @staticmethod
    def _status_text(s: CaseStats) -> str:
        # Three run classes, spelled out: the status word names the
        # combination, and mixed rows show the error count — marked
        # "excluded" when the case's gate left errors out of its sample.
        if s.errors and s.gate is not None and s.gate.errors == "exclude":
            word = "" if s.status == "errored" else f"{s.status.upper()} "
            return f"{word}({s.errors} errored, excluded)"
        if s.status == "errored":
            return f"{s.errors} ERRORED"
        if s.status == "pass":
            return ""
        if s.errors and s.status != "error":
            return f"{s.status.upper()} ({s.errors} errored)"
        return s.status.upper()

    def _gate_tally(self, verdicts: list[str]) -> tuple[str, dict]:
        """The footer's ``Gates:`` line and its color: every rate and
        latency gate's verdict."""
        gcfg = gate_config(self._config)
        counts = Counter(verdicts)
        parts = []
        if counts[PASS]:
            parts.append(f"{counts[PASS]} passed")
        if counts[FAIL]:
            parts.append(f"{counts[FAIL]} failed")
        if counts[UNDECIDED]:
            allowed = "" if gcfg.fails(UNDECIDED) else " (allowed)"
            parts.append(f"{counts[UNDECIDED]} undecided{allowed}")
        if any(gcfg.fails(v) for v in counts):
            markup = _VERDICT_MARKUP[FAIL]
        elif counts[UNDECIDED]:
            markup = _VERDICT_MARKUP[UNDECIDED]
        else:
            markup = _VERDICT_MARKUP[PASS]
        return f"  Gates:   {', '.join(parts)}", markup

    def _write_gates(self, tr, results: dict[str, GateResult]) -> None:
        """The ``probability: gates`` block: every gate that did not
        PASS, with the interval and bar its verdict came from — rate
        gates, then latency gates."""
        shown = [r for r in results.values() if r.verdict != PASS]
        slow = [r for r in self._latency_gates() if r.verdict != PASS]
        if not shown and not slow:
            return
        tr.write_sep("=", "probability: gates")
        name_col = max(len(r.case) for r in [*shown, *slow])
        for line, verdict in self._rate_gate_lines(shown, name_col):
            tr.write_line(line, **_VERDICT_MARKUP[verdict])
        for line, verdict in _latency_lines(slow, name_col):
            tr.write_line(line, **_VERDICT_MARKUP[verdict])

    def _rate_gate_lines(
        self, shown: list[GateResult], name_col: int
    ) -> list[tuple[str, str]]:
        if not shown:
            return []
        lines = []
        fracs = [f"{r.passes}/{r.total}" for r in shown]
        frac_col = max(len(f) for f in fracs)
        # Always shown, whatever the interval column's settings: the
        # verdict is read off this interval.
        cells = self._interval_cells([r.interval for r in shown])
        bars = [r.gate.bar() for r in shown]
        bar_col = max(len(b) for b in bars)
        for r, frac, cell, bar in zip(shown, fracs, cells, bars):
            line = f"  {r.case:<{name_col}}  {frac:>{frac_col}}"
            if cell:
                line += f"  {cell}"
            line += f"  {bar:<{bar_col}}  {r.verdict.upper()}"
            if r.excluded:
                line += f"  ({r.excluded} errored, excluded)"
            stop = self._stops.get(r.case)
            if stop is not None:
                line += f"  {stop.label()}"
            lines.append((line, r.verdict))
        return lines

    def _write_latency(self, tr) -> None:
        """The ``probability: latency`` block (``--prob-latency``): every
        row's latency quantile and interval, with the verdict of a
        latency gate."""
        if not latency_config(self._config).show:
            return
        tr.write_sep("=", "probability: latency")
        for line, verdict in _latency_lines(list(self.latency_results().values())):
            tr.write_line(line, **_VERDICT_MARKUP.get(verdict, {}))

    def _write_metrics(self, tr) -> None:
        """The ``probability: metrics`` block (nothing without
        ``--prob-metric``)."""
        entries = self._shown_metrics()
        if not entries:
            return
        tr.write_sep("=", "probability: metrics")
        for line in _metric_lines(entries, stats_config(self._config).intervals):
            tr.write_line(line)
        tr.write_line("")
        kinds = {m.metric.kind for _, m in entries}
        for kind, legend in _METRIC_LEGEND.items():
            if kind in kinds:
                tr.write_line(f"  {legend}")

    def _write_baseline(self, tr) -> None:
        """The ``probability: baseline`` block (nothing without
        ``--prob-baseline``)."""
        result = self.baseline_result()
        if result is None:
            return
        tr.write_sep("=", "probability: baseline")
        for line, verdict in _baseline_lines(result):
            tr.write_line(line, **_VERDICT_MARKUP.get(verdict, {}))

    def _write_comparisons(self, tr) -> None:
        """The ``probability: comparisons`` block (nothing when no
        function is compared)."""
        comparisons = self.comparisons()
        if not comparisons:
            return
        tr.write_sep("=", "probability: comparisons")
        for line, verdict in _comparison_lines(comparisons):
            tr.write_line(line, **_VERDICT_MARKUP.get(verdict, {}))

    @staticmethod
    def _aggregate_lines(shown: list[Aggregate]) -> list[str]:
        """``classify  N=40 inputs × k=10  81.4%  [75.0%, 87.2%]  ρ=0.60``,
        one line per shown aggregate, columns aligned across them. An
        aggregate with a ρ gets a continuation line under its size —
        ``runs ×2 → interval −2%  ·  inputs ×2 → −29%`` — priced when
        cost was recorded. One without an interval (cases stopped early)
        is a note instead: ``classify  N=12 inputs × k=3–20  interval
        hidden: 2 cases stopped early``."""
        if not shown:
            return []
        name_col = max(len(a.name) for a in shown)
        sizes = [a.size() for a in shown]
        size_col = max(len(z) for z in sizes)
        with_ci = [a for a in shown if a.ci is not None]
        ests = [_pct1(a.estimate) if a.ci is not None else "" for a in shown]
        est_col = max(len(e) for e in ests)
        bounds = [
            tuple(_pct1(v) for v in a.ci) if a.ci is not None else ("", "")
            for a in shown
        ]
        low_w = max((len(_pct1(a.ci[0])) for a in with_ci), default=0)
        high_w = max((len(_pct1(a.ci[1])) for a in with_ci), default=0)
        lines = []
        for a, size, est, (low, high) in zip(shown, sizes, ests, bounds):
            if a.ci is None:
                lines.append(
                    f"  {a.name:<{name_col}}  {size:<{size_col}}"
                    f"  interval hidden: {a.suppressed}"
                )
                continue
            line = (
                f"  {a.name:<{name_col}}  {size:<{size_col}}  {est:>{est_col}}"
                f"  [{low:>{low_w}}, {high:>{high_w}}]"
            )
            projection = a.projection()
            if projection is None:
                lines.append(line)
                continue
            lines.append(f"{line}  ρ={a.icc:.2f}")
            runs, inputs = (_change(c) for c in projection)
            more = f"runs ×2 → interval {runs}  ·  inputs ×2 → {inputs}"
            if a.cost:
                more += f"  ·  each +${a.cost:.4f}"
            lines.append(" " * (name_col + 4) + more)
        return lines

    def _aggregate_reading(self, agg: Aggregate):
        from . import explain

        cfg = stats_config(self._config)
        return explain.aggregate_reading(agg, cfg.min_inputs)

    def _metric_reading(self, agg: Aggregate, m: MetricAggregate):
        from . import explain

        cfg = stats_config(self._config)
        return explain.metric_reading(agg, m, cfg.min_inputs, intervals=cfg.intervals)

    def _comparison_reading(self, cmp: Comparison):
        from . import explain

        return explain.comparison_reading(
            cmp,
            undecided_fails=gate_config(self._config).fails(UNDECIDED),
            min_inputs=stats_config(self._config).min_inputs,
        )

    def _baseline_reading(self, cmp: Comparison):
        from . import explain

        result = self.baseline_result()
        return explain.baseline_reading(
            cmp,
            path=result.baseline.path,
            undecided_fails=gate_config(self._config).fails(UNDECIDED),
            min_inputs=stats_config(self._config).min_inputs,
        )

    def _baseline_summary_reading(self):
        from . import explain

        return explain.baseline_summary_reading(self.baseline_result())

    def _gate_reading(self, s: CaseStats, result: GateResult):
        from . import explain

        settle = None
        if result.verdict == UNDECIDED:
            settle = result.gate.runs_to_settle(
                result.passes, result.total, _SETTLE_CAP
            )
        return explain.gate_reading(
            result,
            errors=s.errors,
            undecided_fails=gate_config(self._config).fails(UNDECIDED),
            settle=settle,
            cap=_SETTLE_CAP,
            stop=self._stops.get(s.case),
        )

    def _row_reading(self, s: CaseStats):
        from . import explain

        cfg = stats_config(self._config)
        interval = cfg.interval(s.passes, s.total) if s.total else None
        # A margin, like a gate, judges the case: its reading has the step.
        baseline = baseline_of(self._config)
        judged = (
            s.gate is not None
            or bool(s.compare and s.compare["margin"] is not None)
            or (baseline is not None and baseline.judges(s.case))
        )
        return explain.row_reading(
            s, interval, cfg, gated=judged, stop=self._stops.get(s.case)
        )

    def _latency_reading(self, result: LatencyResult):
        from . import explain

        return explain.latency_reading(
            result, undecided_fails=gate_config(self._config).fails(UNDECIDED)
        )

    def _write_explained(
        self, tr, all_stats: list[CaseStats], results: dict[str, GateResult]
    ) -> None:
        """The ``probability: explained`` section: a plain-language
        reading of every gated case and every ungated row that did not
        pass every run, then a glossary of the methods they used.

        The wording lives in ``explain.py``; this only picks the lines
        and supplies the numbers. Rendered from the aggregated counts,
        so it is the same under xdist.
        """
        from . import explain

        latency = self.latency_results()
        show_latency = latency_config(self._config).show
        readings = []
        for s in all_stats:
            if s.case in results:
                readings.append(self._gate_reading(s, results[s.case]))
            elif s.status != "pass":
                readings.append(self._row_reading(s))
            # A latency gate, or a shown latency line missing a bound.
            lat = latency[s.case]
            if lat.verdict is not None or (show_latency and not lat.complete):
                readings.append(self._latency_reading(lat))
        readings.extend(self._aggregate_reading(a) for a in self._shown_aggregates())
        readings.extend(self._metric_reading(a, m) for a, m in self._shown_metrics())
        readings.extend(self._comparison_reading(c) for c in self.comparisons())
        if self.baseline_result() is not None:
            readings.append(self._baseline_summary_reading())
            readings.extend(
                self._baseline_reading(c) for c in self._baseline_comparisons()
            )
        # The main table's interval column needs explaining even when no
        # row is notable.
        cfg = stats_config(self._config)
        extra = []
        if any(c is not None for c in self._ci_cells(cfg, all_stats)):
            extra.append(("interval", explain.stats_key(cfg)))
        if show_latency:
            # So does the latency block.
            extra.extend(explain.latency_terms(r) for r in latency.values())
        width = getattr(getattr(tr, "_tw", None), "fullwidth", 80)
        tr.write_sep("=", "probability: explained")
        markup = {**_MARKUP, **_VERDICT_MARKUP}
        for line, tone in explain.render_section(readings, width, extra):
            tr.write_line(line, **markup.get(tone, {}))

    def _write_plan(self, tr, rows: list[PlanRow]) -> None:
        from . import explain

        pcfg = plan_config(self._config)
        level = stats_config(self._config).level
        gated = any(r.gate is not None for r in rows)
        priced = any(r.cost is not None for r in rows)

        def runs_cell(r: PlanRow) -> str:
            if r.gate is None:
                return "—"
            if r.power_runs is not None:
                return f"{r.power_runs:,}"
            if r.gate.rule == "rate" and pcfg.assume <= r.gate.min_rate:
                return "never"
            return f">{_PLAN_CAP:,}"

        header = ["case", "runs"]
        if gated:
            header += ["min runs", "runs for 80%", "chance now"]
        header += [f"catch {_pct_bar(f)}" for f in pcfg.flakes]
        if priced:
            header.append("cost")
        table = []
        for r in rows:
            cells = [_row_name(r), f"{r.runs:,}"]
            if gated:
                if r.gate is None:
                    cells += ["—", "—", "—"]
                else:
                    cells += [f"{r.min_runs:,}", runs_cell(r), _chance(r.chance)]
            cells += [_chance(stats.detection_chance(f, r.runs)) for f in pcfg.flakes]
            if priced:
                cells.append("" if r.cost is None else f"${r.cost:.4f}")
            table.append(cells)
        widths = [max(len(c[i]) for c in [header, *table]) for i in range(len(header))]

        def line(cells: list[str]) -> str:
            first = f"  {cells[0]:<{widths[0]}}"
            rest = "".join(f"  {c:>{w}}" for c, w in zip(cells[1:], widths[1:]))
            return (first + rest).rstrip()

        tr.write_sep("=", "probability: plan")
        tr.write_line(line(header), bold=True)
        for r, cells in zip(rows, table):
            tr.write_line(line(cells), **_MARKUP.get(self._plan_tone(r), {}))
        tr.write_line("")
        total = sum(r.runs for r in rows)
        cases = f"{len(rows)} case{'s' if len(rows) != 1 else ''}"
        tr.write_line(
            f"  Plan:    {cases}, {total:,} run{'s' if total != 1 else ''};"
            " nothing was run.",
            bold=True,
        )
        if pcfg.report is not None:
            unpriced = sum(r.cost is None for r in rows)
            cost = f"  Cost:    ${math.fsum(r.cost or 0.0 for r in rows):.4f} projected"
            cost += f" from {pcfg.report}"
            if unpriced:
                cost += f" ({unpriced} case{'s' if unpriced != 1 else ''} not in it)"
            tr.write_line(cost)
        tr.write_line("")
        width = getattr(getattr(tr, "_tw", None), "fullwidth", 80)
        notes = explain.plan_notes(
            assume=pcfg.assume,
            power=_PLAN_POWER,
            flakes={f: stats.runs_to_see_failure(f, level) for f in pcfg.flakes},
            level=level,
            gated=gated,
            report=pcfg.report if priced else None,
        )
        for text in explain.render_notes(notes, width):
            tr.write_line(text)

    @staticmethod
    def _plan_tone(r: PlanRow) -> str | None:
        # Red: the gate can't pass with these runs; yellow: it passes
        # less than 80% of the time at the assumed rate; green: enough.
        if r.gate is None:
            return None
        if r.runs < r.min_runs:
            return "fail"
        return "pass" if r.chance >= _PLAN_POWER else "flaky"

    def pytest_terminal_summary(self, terminalreporter, exitstatus, config) -> None:
        del exitstatus, config
        if self.plan:
            self._write_plan(terminalreporter, self.plan)
            return
        if not self._stats:
            return
        all_stats = list(self._stats.values())
        tr = terminalreporter
        tr.write_sep("=", "probability")

        name_col = max(len(_row_name(s)) for s in all_stats)
        frac_col = max(len(f"{s.passes}/{s.total}") for s in all_stats)
        cis = self._ci_cells(stats_config(self._config), all_stats)
        for s, ci in zip(all_stats, cis):
            line = f"  {_row_name(s):<{name_col}}  {f'{s.passes}/{s.total}':>{frac_col}}"
            if ci is not None:
                line += f"  {ci}"
            if s.cost:
                line += f"  ${s.cost:.4f}"
            status = self._status_text(s)
            if status:
                line += f"  {status}"
            stop = self._stops.get(s.case)
            if stop is not None:
                line += f"  {stop.label()}"
            tr.write_line(line.rstrip(), **_MARKUP.get(s.status, {}))

        aggregate_lines = self._aggregate_lines(self._shown_aggregates())
        if aggregate_lines:
            tr.write_line("")
            for line in aggregate_lines:
                tr.write_line(line)

        total_passes = sum(s.passes for s in all_stats)
        total = sum(s.total for s in all_stats)
        total_errors = sum(s.errors for s in all_stats)
        pct = total_passes / total * 100 if total else 0.0
        overall = f"  Overall: {total_passes}/{total} passed ({pct:.0f}%)"
        if total_errors:
            overall += f", {total_errors} errored"
        tr.write_line("")
        tr.write_line(overall, bold=True)
        results = self.gate_results()
        verdicts = [r.verdict for r in results.values()] + [
            r.verdict for r in self._latency_gates()
        ]
        if verdicts:
            tally, markup = self._gate_tally(verdicts)
            tr.write_line(tally, **markup)
        stop_line = self._stop_line()
        if stop_line is not None:
            tr.write_line(stop_line)
        total_cost = sum(s.cost for s in all_stats)
        if total_cost:
            tr.write_line(f"  Cost:    ${total_cost:.4f}")
        model_totals = self._model_totals(all_stats)
        if model_totals:
            def _tokens(agg: dict[str, Any]) -> str:
                out = f"{agg['input_tokens']:,} in / {agg['output_tokens']:,} out"
                if agg["cached_input_tokens"]:
                    out += f" / {agg['cached_input_tokens']:,} cached"
                return out

            model_col = max(len(m) for m in model_totals)
            tokens_col = max(len(_tokens(a)) for a in model_totals.values())
            for i, (model, agg) in enumerate(model_totals.items()):
                prefix = "  Tokens:  " if i == 0 else "           "
                line = f"{prefix}{model:<{model_col}}  {_tokens(agg):<{tokens_col}}"
                if agg["cost"]:
                    line += f"  ${agg['cost']:.4f}"
                tr.write_line(line.rstrip())
        if self._json_path:
            tr.write_line(f"  Report:  {self._json_path}")
        self._write_metrics(tr)
        self._write_latency(tr)
        self._write_comparisons(tr)
        self._write_baseline(tr)
        self._write_gates(tr, results)
        if self._explain:
            self._write_explained(tr, all_stats, results)
        elif (
            self.comparisons()
            or self._shown_metrics()
            or self._baseline_comparisons()
            or any(v != PASS for v in verdicts)
            or self._stops
        ):
            from .explain import HINT

            tr.write_line("")
            tr.write_line(f"  {HINT}")
