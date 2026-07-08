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
"""
from __future__ import annotations

import ast
import contextvars
import fnmatch
import hashlib
import importlib.util
import inspect
import json
import sys
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator

import pytest

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


def _runs(config: pytest.Config) -> int:
    runs = config.getoption("prob_runs")
    if runs is None:
        runs = int(config.getini("prob_runs"))
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


def pytest_configure(config: pytest.Config) -> None:
    config.pluginmanager.register(
        ProbabilityAggregator(config), "probability-aggregator"
    )


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
        variants = _parametrize_variants(param_marks)
        runs = _runs(self.config)
        short = self.name.removeprefix("bench_") or self.name

        if _transpose(self.config):
            # Run-major: run 1 of every case, then run 2, ...
            pairs = [
                (variant, run_id)
                for run_id in range(1, runs + 1)
                for variant in variants
            ]
        else:
            pairs = [
                (variant, run_id)
                for variant in variants
                for run_id in range(1, runs + 1)
            ]
        for (variant_id, params, vmarks), run_id in pairs:
            if variant_id:
                name = variant_id if runs == 1 else f"{variant_id}[run{run_id}]"
                case = f"{short}::{variant_id}"
            else:
                # Unparametrized: one case, named after the function.
                name = f"run{run_id}"
                case = short
            item = BenchItem.from_parent(
                self,
                name=name,
                run_id=run_id,
                bench_fn=self.bench_fn,
                params=params,
                case=case,
            )
            for mark in (*other_marks, *vmarks):
                # add_marker() only accepts MarkDecorator; these are raw
                # Mark objects, so attach the way it does internally
                # (skip/xfail/-m all read these).
                item.own_markers.append(mark)
                item.keywords[mark.name] = mark
            yield item


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
    """

    def __init__(
        self,
        *,
        run_id: int,
        bench_fn: Callable,
        params: dict[str, Any],
        case: str,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.run_id = run_id
        self.bench_fn = bench_fn
        self.params = params
        # Function-qualified case id: the aggregation row.
        self.case = case

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
                self.user_properties.append(
                    (
                        # Plain dicts only: user_properties must survive
                        # xdist's worker-to-controller serialization.
                        "probability",
                        {
                            "case": self.case,
                            "run": self.run_id,
                            "outcome": outcome,
                            "message": message,
                            "error": error,
                            "elapsed": time.perf_counter() - start,
                            "cost": cost or None,
                            "usage": recorder.usage,
                        },
                    )
                )

    def reportinfo(self):
        return self.path, 0, f"case: {self.case}"


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


_MARKUP = {
    "pass": {"green": True},
    "flaky": {"yellow": True},
    "errored": {"yellow": True},
    "fail": {"red": True},
    "error": {"red": True},
}


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
        # Raw per-run records, kept only when a JSON report was
        # requested.
        self._records: list[dict[str, Any]] | None = (
            [] if self._json_path else None
        )

    def pytest_runtest_logreport(self, report) -> None:
        if getattr(report, "when", None) != "call":
            return
        for name, value in report.user_properties:
            if name != "probability":
                continue
            if self._records is not None:
                self._records.append(value)
            st = self._stats.setdefault(value["case"], CaseStats(case=value["case"]))
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

    def pytest_sessionfinish(self, session: pytest.Session, exitstatus: int) -> None:
        if not self._json_path:
            return
        # Under pytest-xdist this hook also fires on workers, which only
        # hold a shard of the results — the controller writes the file.
        if hasattr(session.config, "workerinput"):
            return
        all_stats = list(self._stats.values())
        payload = {
            "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "runs": _runs(session.config),
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
                    "cost": s.cost,
                    "usage": s.usage,
                }
                for s in all_stats
            ],
            "records": self._records or [],
        }
        path = Path(self._json_path)
        if path.parent != Path(""):
            path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2))

    @staticmethod
    def _model_totals(all_stats: list[CaseStats]) -> dict[str, dict[str, Any]]:
        totals: dict[str, dict[str, Any]] = {}
        for s in all_stats:
            for model, agg in s.usage.items():
                _merge_usage(totals, {"model": model, **agg})
        return totals

    def pytest_terminal_summary(self, terminalreporter, exitstatus, config) -> None:
        del exitstatus, config
        if not self._stats:
            return
        all_stats = list(self._stats.values())
        tr = terminalreporter
        tr.write_sep("=", "probability")

        name_col = max(len(_row_name(s)) for s in all_stats)
        frac_col = max(len(f"{s.passes}/{s.total}") for s in all_stats)
        for s in all_stats:
            line = f"  {_row_name(s):<{name_col}}  {f'{s.passes}/{s.total}':>{frac_col}}"
            if s.cost:
                line += f"  ${s.cost:.4f}"
            # Three run classes, spelled out: the status word names the
            # combination, and mixed rows show the error count.
            if s.status == "errored":
                line += f"  {s.errors} ERRORED"
            elif s.status != "pass":
                line += f"  {s.status.upper()}"
                if s.errors and s.status != "error":
                    line += f" ({s.errors} errored)"
            tr.write_line(line, **_MARKUP.get(s.status, {}))

        total_passes = sum(s.passes for s in all_stats)
        total = sum(s.total for s in all_stats)
        total_errors = sum(s.errors for s in all_stats)
        pct = total_passes / total * 100 if total else 0.0
        overall = f"  Overall: {total_passes}/{total} passed ({pct:.0f}%)"
        if total_errors:
            overall += f", {total_errors} errored"
        tr.write_line("")
        tr.write_line(overall, bold=True)
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
