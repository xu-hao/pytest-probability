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
"""
from __future__ import annotations

import ast
import contextvars
import dataclasses
import fnmatch
import hashlib
import importlib.util
import inspect
import json
import math
import statistics
import sys
import time
import warnings
from collections import Counter, OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, NamedTuple

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
        " confidence=None, method=None, prior=None): gate a benchmark case on"
        " its pass rate (min_rate) or pass count (min_passes), and/or set its"
        " run count. See https://pytest-probability.readthedocs.io/en/latest/"
        "reference.html",
    )
    # Resolve (and validate) up front so a bad value is a usage error
    # before anything runs — on the xdist controller, which renders,
    # as much as anywhere.
    stats_config(config)
    gate_config(config)
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


_MARK_ARGS = ("min_rate", "min_passes", "runs", "confidence", "method", "prior")


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


def _parametrize_variants(
    marks: list,
) -> list[tuple[str, dict[str, Any], tuple]]:
    """Expand stacked ``@pytest.mark.parametrize`` decorators.

    Returns ``(id, params, marks)`` triples — the cartesian product
    across stacked decorators, processed in ``pytestmark`` order so
    composite ids read ``bottom-top`` like pytest's own. ``params`` are
    passed to the bench function as keyword arguments; ``marks`` come
    from ``pytest.param(..., marks=...)`` values. With no parametrize
    marks this returns the single empty variant: an unparametrized
    bench function is one case.
    """
    variants: list[tuple[str, dict[str, Any], tuple]] = [("", {}, ())]
    for mark in marks:
        argnames, argvalues = mark.args[0], mark.args[1]
        names = (
            [n.strip() for n in argnames.split(",")]
            if isinstance(argnames, str)
            else [str(n) for n in argnames]
        )
        ids_opt = mark.kwargs.get("ids")
        expanded: list[tuple[str, dict[str, Any], tuple]] = []
        for prev_id, prev_params, prev_marks in variants:
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
                    (
                        f"{prev_id}-{vid}" if prev_id else vid,
                        {**prev_params, **dict(zip(names, vals))},
                        prev_marks + vmarks,
                    )
                )
        variants = expanded
    return variants


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

        plans: list[_CasePlan] = []
        for variant_id, params, vmarks in _parametrize_variants(param_marks):
            # Unparametrized: one case, named after the function.
            case = f"{short}::{variant_id}" if variant_id else short
            case_prob = [m for m in vmarks if m.name == "probability"]
            try:
                runs, gate = _case_plan(self.config, [*case_prob, *fn_prob])
            except (ValueError, pytest.UsageError) as exc:
                raise self.CollectError(
                    f"{case}: invalid probability mark: {exc}"
                ) from None
            plans.append(_CasePlan(variant_id, params, vmarks, case, runs, gate))
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
            )
            for mark in (*other_marks, *plan.marks):
                # add_marker() only accepts MarkDecorator; these are raw
                # Mark objects, so attach the way it does internally
                # (skip/xfail/-m all read these).
                item.own_markers.append(mark)
                item.keywords[mark.name] = mark
            yield item

    def _warn_infeasible(self, short: str, plans: list[_CasePlan]) -> None:
        """Warn about gates that cannot pass even if every run passes.

        One warning per distinct gate, naming the case — or, when
        several cases share it (a global gate), how many.
        """
        infeasible: dict[Gate, list[str]] = {}
        passable: dict[Gate, bool] = {}
        for plan in plans:
            gate = plan.gate
            if gate is None:
                continue
            if gate not in passable:
                passable[gate] = gate.verdict(gate.runs, gate.runs) == PASS
            if not passable[gate]:
                infeasible.setdefault(gate, []).append(plan.case)
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
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.run_id = run_id
        self.bench_fn = bench_fn
        self.params = params
        # Function-qualified case id: the aggregation row.
        self.case = case
        self.gate = gate

    def runtest(self) -> None:
        delay = _delay(self.config)
        if delay > 0:
            if self.config.stash.get(_DELAYED_ONCE, False):
                time.sleep(delay)
            else:
                self.config.stash[_DELAYED_ONCE] = True

        recorder = _RunRecorder()
        token = _RUN_RECORDER.set(recorder)
        start = time.perf_counter()
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
                    "elapsed": time.perf_counter() - start,
                    "cost": cost or None,
                    "usage": recorder.usage,
                }
                if self.gate is not None:
                    # The controller decides verdicts without collecting,
                    # so the gate rides along with every run.
                    record["gate"] = self.gate.to_record()
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
    gate's verdict, not the run, decides pass or fail. Reporting it as
    xfailed keeps its traceback on the report (``--xfail-tb`` prints
    it, ``-rx`` lists the run with its assert message as the reason),
    keeps ``-x``/``--maxfail`` from stopping on it, and keeps it from
    failing the session. Errors do fail the session, unless
    ``prob_errors = exclude`` takes them out of the gate's sample.
    ``--runxfail`` turns all of this off, as it does for xfail marks.
    """
    outcome = yield
    if call.when != "call" or not isinstance(item, BenchItem) or item.gate is None:
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
        reason = f"probability gate: {record['message'] or 'AssertionError'}"
    elif record["outcome"] == "error" and item.gate.errors == "exclude":
        reason = f"probability gate, error excluded: {record['error']}"
    else:
        return
    report.outcome = "skipped"
    report.wasxfail = reason


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

    @property
    def inputs(self) -> int:
        return len(self.cases)

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

        def interval(method: str, bounds: tuple[float, float] | None):
            if bounds is None:
                return None
            return {
                "method": method,
                "level": self.level,
                "low": bounds[0],
                "high": bounds[1],
            }

        return {
            "scope": self.scope,
            "name": self.name,
            "inputs": self.inputs,
            "runs": {"min": low, "mean": mean, "max": high},
            "estimate": self.estimate,
            "ci": interval("bootstrap", self.ci),
            "normal_ci": interval("normal", self.normal_ci),
            "resamples": self.resamples,
            "seed": self.seed,
            "resampling_unit": "input",
            "note": "inputs treated as a sample",
            "suppressed": self.suppressed,
        }


def aggregate(
    scope: str,
    name: str,
    cases: Iterable[CaseStats],
    cfg: StatsConfig,
    value: Callable[[int, int], float] = pass_fraction,
) -> Aggregate:
    """The ``Aggregate`` of ``cases`` (each with at least one run).

    ``value(passes, total)`` is the per-case quantity averaged; later
    metrics (pass^k) can pass their own. Since the statistic is a mean
    of per-case values, resampling whole cases is resampling their
    values: each value carries all its case's runs. The bootstrap uses
    ``cfg``'s resamples, seed and level, through ``stats.bootstrap``'s
    private generator — the global ``random`` state is never touched.

    Every value counts as given: under ``prob_errors = exclude`` errored
    runs still count as non-passes here, as they do in the row
    fraction, because the exclusion is a gate setting.
    """
    ordered = sorted(cases, key=lambda s: s.case)
    counts = tuple((s.passes, s.total) for s in ordered)
    values = [value(c, n) for c, n in counts]
    agg = Aggregate(
        scope=scope,
        name=name,
        cases=tuple(s.case for s in ordered),
        counts=counts,
        estimate=statistics.fmean(values),
        level=cfg.level,
        resamples=cfg.resamples,
        seed=cfg.seed,
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
        self._json_path = config.getoption("prob_json")
        self._explain = _explain(config)
        # Raw per-run records, kept only when a JSON report was
        # requested.
        self._records: list[dict[str, Any]] | None = (
            [] if self._json_path else None
        )
        # aggregates(), computed once: the JSON report and the summary
        # both need it, and a bootstrap is the one costly step here.
        self._aggregates: list[Aggregate] | None = None

    def pytest_runtest_logreport(self, report) -> None:
        if getattr(report, "when", None) != "call":
            return
        for name, value in report.user_properties:
            if name != "probability":
                continue
            gate = value.get("gate")
            if self._records is not None:
                # The gate spec is per case: rows[].gate reports it once.
                self._records.append(
                    {k: v for k, v in value.items() if k != "gate"}
                    if gate is not None
                    else value
                )
            st = self._stats.setdefault(value["case"], CaseStats(case=value["case"]))
            if gate is not None and st.gate is None:
                st.gate = Gate.from_record(gate)
            outcome = value["outcome"]
            if outcome == "pass":
                st.passes += 1
            elif outcome == "error":
                st.errors += 1
            else:
                st.fails += 1
            if value["cost"]:
                st.cost += value["cost"]
            for usage in value.get("usage", ()):
                _merge_usage(st.usage, usage)

    def gate_results(self) -> dict[str, GateResult]:
        """The verdict for every gated row, from the aggregated counts."""
        return {
            s.case: s.gate.evaluate(s)
            for s in self._stats.values()
            if s.gate is not None
        }

    def aggregates(self) -> list[Aggregate]:
        """One ``Aggregate`` per bench function, by name, then the
        ``Overall`` one over every case (``[]`` when no case ran).
        Computed once, after every result is in. Nothing here depends on
        the order results arrived in — not the values (see
        ``Aggregate``), and not the list order, unlike the rows — so it
        is identical with and without xdist."""
        if self._aggregates is None:
            cfg = stats_config(self._config)
            by_function: dict[str, list[CaseStats]] = {}
            for s in self._stats.values():
                by_function.setdefault(function_of(s.case), []).append(s)
            out = [
                aggregate(FUNCTION, name, by_function[name], cfg)
                for name in sorted(by_function)
            ]
            if self._stats:
                out.append(aggregate(OVERALL, "Overall", self._stats.values(), cfg))
            self._aggregates = out
        return self._aggregates

    def _shown_aggregates(self) -> list[Aggregate]:
        """The aggregates the summary prints: those with an interval,
        unless intervals are hidden. Overall is left out when it would
        repeat the only function's line."""
        if not stats_config(self._config).intervals:
            return []
        aggs = self.aggregates()
        functions = sum(a.scope == FUNCTION for a in aggs)
        return [
            a
            for a in aggs
            if a.ci is not None and not (a.scope == OVERALL and functions == 1)
        ]

    def pytest_sessionfinish(self, session: pytest.Session, exitstatus: int) -> None:
        # Under pytest-xdist this hook also fires on workers, which only
        # hold a shard of the results — the controller decides gates and
        # writes the file.
        if hasattr(session.config, "workerinput"):
            return
        results = self.gate_results()
        gcfg = gate_config(session.config)
        if exitstatus == pytest.ExitCode.OK and any(
            gcfg.fails(r.verdict) for r in results.values()
        ):
            # Every run passed or was xfailed, but a gate did not hold.
            # Any other status (failures, interrupts) already says more.
            exitstatus = session.exitstatus = pytest.ExitCode.TESTS_FAILED
        if not self._json_path:
            return
        all_stats = list(self._stats.values())
        cfg = stats_config(session.config)
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
                    "cost": s.cost,
                    "usage": s.usage,
                }
                for s in all_stats
            ],
            "aggregates": [a.to_json() for a in self.aggregates()],
            "records": self._records or [],
        }
        if self._explain:
            for s, row in zip(all_stats, payload["rows"]):
                row["explanation"] = self._row_reading(s).text()
                if row["gate"] is not None:
                    row["gate"]["explanation"] = self._gate_reading(
                        s, results[s.case]
                    ).text()
            for agg, entry in zip(self.aggregates(), payload["aggregates"]):
                entry["explanation"] = self._aggregate_reading(agg).text()
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

    def _gate_tally(self, results: dict[str, GateResult]) -> tuple[str, dict]:
        """The footer's ``Gates:`` line and its color."""
        gcfg = gate_config(self._config)
        counts = Counter(r.verdict for r in results.values())
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
        PASS, with the interval and bar its verdict came from."""
        shown = [r for r in results.values() if r.verdict != PASS]
        if not shown:
            return
        tr.write_sep("=", "probability: gates")
        name_col = max(len(r.case) for r in shown)
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
            tr.write_line(line, **_VERDICT_MARKUP[r.verdict])

    @staticmethod
    def _aggregate_lines(shown: list[Aggregate]) -> list[str]:
        """``classify  N=40 inputs × k=10  81.4%  [75.0%, 87.2%]``, one
        line per shown aggregate, columns aligned across them."""
        if not shown:
            return []
        name_col = max(len(a.name) for a in shown)
        sizes = [a.size() for a in shown]
        size_col = max(len(z) for z in sizes)
        ests = [_pct1(a.estimate) for a in shown]
        est_col = max(len(e) for e in ests)
        bounds = [tuple(_pct1(v) for v in a.ci) for a in shown]
        low_w = max(len(b[0]) for b in bounds)
        high_w = max(len(b[1]) for b in bounds)
        return [
            f"  {a.name:<{name_col}}  {size:<{size_col}}  {est:>{est_col}}"
            f"  [{low:>{low_w}}, {high:>{high_w}}]"
            for a, size, est, (low, high) in zip(shown, sizes, ests, bounds)
        ]

    def _aggregate_reading(self, agg: Aggregate):
        from . import explain

        cfg = stats_config(self._config)
        return explain.aggregate_reading(agg, cfg.min_inputs)

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
        )

    def _row_reading(self, s: CaseStats):
        from . import explain

        cfg = stats_config(self._config)
        interval = cfg.interval(s.passes, s.total) if s.total else None
        return explain.row_reading(s, interval, cfg, gated=s.gate is not None)

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

        readings = []
        for s in all_stats:
            if s.case in results:
                readings.append(self._gate_reading(s, results[s.case]))
            elif s.status != "pass":
                readings.append(self._row_reading(s))
        readings.extend(self._aggregate_reading(a) for a in self._shown_aggregates())
        # The main table's interval column needs explaining even when no
        # row is notable.
        cfg = stats_config(self._config)
        extra = []
        if any(c is not None for c in self._ci_cells(cfg, all_stats)):
            extra.append(("interval", explain.stats_key(cfg)))
        width = getattr(getattr(tr, "_tw", None), "fullwidth", 80)
        tr.write_sep("=", "probability: explained")
        markup = {**_MARKUP, **_VERDICT_MARKUP}
        for line, tone in explain.render_section(readings, width, extra):
            tr.write_line(line, **markup.get(tone, {}))

    def pytest_terminal_summary(self, terminalreporter, exitstatus, config) -> None:
        del exitstatus, config
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
        if results:
            tally, markup = self._gate_tally(results)
            tr.write_line(tally, **markup)
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
        self._write_gates(tr, results)
        if self._explain:
            self._write_explained(tr, all_stats, results)
        elif any(r.verdict != PASS for r in results.values()):
            from .explain import HINT

            tr.write_line("")
            tr.write_line(f"  {HINT}")
