"""End-to-end tests for pytest-probability, run via pytester."""

import time

BENCH_OK = """
import pytest
from pytest_probability import StepResult

@pytest.mark.parametrize("word", ["alpha", "beta"])
def bench_check(word):
    yield StepResult(label="check", passed=True)
"""

BENCH_FLAKY = """
import pytest
from pytest_probability import StepResult

_calls = {"n": 0}

@pytest.mark.parametrize("word", ["wobbly"])
def bench_check(word):
    _calls["n"] += 1
    yield StepResult(label="check", passed=_calls["n"] % 2 == 0)
"""

BENCH_FAIL_MSG = """
import pytest
from pytest_probability import StepResult

@pytest.mark.parametrize("word", ["bad"])
def bench_check(word):
    yield StepResult(label="classify", passed=False,
                     message="expected refund got billing")
"""

BENCH_COST = """
import pytest
from pytest_probability import StepResult

@pytest.mark.parametrize("word", ["paid"])
def bench_call(word):
    yield StepResult(label="llm", passed=True, cost=0.05)
"""

BENCH_USAGE = """
import pytest
from pytest_probability import StepResult, TokenUsage

@pytest.mark.parametrize("word", ["paid"])
def bench_call(word):
    yield StepResult(label="llm", passed=True, usage=[
        TokenUsage(model="m-small", input_tokens=100, output_tokens=20,
                   cost=0.01),
        # plain dicts are accepted too (duck-typed)
        {"model": "m-big", "input_tokens": 50, "output_tokens": 5,
         "cached_input_tokens": 30, "cost": 0.04},
    ])

@pytest.mark.parametrize("word", ["override"])
def bench_override(word):
    # explicit cost wins over the usage-derived sum
    yield StepResult(label="llm", passed=True, cost=0.2, usage=[
        TokenUsage(model="m-small", input_tokens=10, output_tokens=1,
                   cost=0.5),
    ])
"""

BENCH_DUCK_TYPED = """
import pytest

class Step:
    label = "quack"
    passed = True

@pytest.mark.parametrize("word", ["ducky"])
def bench_duck(word):
    yield Step()
"""

BENCH_ERROR = """
import pytest
from pytest_probability import StepResult

@pytest.mark.parametrize("word", ["crashy"])
def bench_check(word):
    yield StepResult(label="ok", passed=True)
    raise RuntimeError("boom")
"""

BENCH_LIFECYCLE = """
import pytest
from pathlib import Path
from pytest_probability import StepResult

LOG = Path(__file__).parent / "lifecycle.log"

def setup():
    prev = LOG.read_text() if LOG.exists() else ""
    LOG.write_text(prev + "setup\\n")

def teardown():
    LOG.write_text(LOG.read_text() + "teardown\\n")

@pytest.mark.parametrize("word", ["a", "b"])
def bench_check(word):
    yield StepResult(label="check", passed=True)
"""

BENCH_MARKED_CASES = """
import pytest
from pytest_probability import StepResult

@pytest.mark.parametrize("word", [
    pytest.param("fast_one", marks=pytest.mark.fast),
    pytest.param("slow_one", marks=pytest.mark.slow),
])
def bench_check(word):
    yield StepResult(label="check", passed=True)
"""

BENCH_MULTI_ARGNAMES = """
import pytest
from pytest_probability import StepResult

@pytest.mark.parametrize("text,expected", [
    pytest.param("my ssn is 078-05-1120", "pii", id="identify_pii"),
    pytest.param("is this a question?", "question", id="is_question"),
])
def bench_classify(text, expected):
    assert isinstance(text, str)
    yield StepResult(label="classify", passed=expected in ("pii", "question"))
"""

BENCH_STACKED = """
import pytest
from pytest_probability import StepResult

@pytest.mark.parametrize("lang", ["en", "de"])
@pytest.mark.parametrize("model", ["small", "large"])
def bench_x(model, lang):
    assert model in ("small", "large") and lang in ("en", "de")
    yield StepResult(label="check", passed=True)
"""

BENCH_VARIANT_PARAM_ID = """
import pytest
from pytest_probability import StepResult

@pytest.mark.parametrize("threshold", [
    pytest.param(0.5, id="loose"),
    pytest.param(0.9, id="strict", marks=pytest.mark.skip(reason="not yet")),
])
def bench_t(threshold):
    yield StepResult(label="check", passed=threshold == 0.5)
"""

BENCH_FN_SKIP = """
import pytest
from pytest_probability import StepResult

@pytest.mark.skip(reason="flaky infra")
@pytest.mark.parametrize("word", ["a", "b"])
def bench_check(word):
    yield StepResult(label="check", passed=False)
"""

BENCH_UNPARAMETRIZED = """
from pytest_probability import StepResult

def bench_smoke():
    yield StepResult(label="check", passed=True)
"""

BENCH_MULTI_FN = """
import pytest
from pytest_probability import StepResult

@pytest.mark.parametrize("word", ["shared"])
def bench_first(word):
    yield StepResult(label="check", passed=True)

@pytest.mark.parametrize("word", ["shared"])
def bench_second(word):
    yield StepResult(label="check", passed=False)
"""

BENCH_OLD_STYLE = """
from pytest_probability import StepResult

def bench(word):   # not bench_* prefixed -> not a benchmark
    yield StepResult(label="check", passed=True)
"""


# ---------------------------------------------------------------------------
# Collection
# ---------------------------------------------------------------------------


def test_collects_function_case_ids(pytester):
    pytester.makepyfile(bench_demo=BENCH_OK)
    result = pytester.runpytest("--collect-only", "-q")
    result.stdout.fnmatch_lines(
        ["*bench_demo.py::bench_check::alpha*", "*bench_demo.py::bench_check::beta*"]
    )


def test_multiple_bench_functions_per_file(pytester):
    pytester.makepyfile(bench_multi=BENCH_MULTI_FN)
    result = pytester.runpytest()
    result.assert_outcomes(passed=1, failed=1)
    # same case id, distinct rows namespaced by function
    result.stdout.fnmatch_lines(["*first::shared* check*1/1*"])
    result.stdout.fnmatch_lines(["*second::shared* check*0/1  FAIL*"])


def test_k_selects_bench_function(pytester):
    pytester.makepyfile(bench_multi=BENCH_MULTI_FN)
    result = pytester.runpytest("-k", "bench_first")
    result.assert_outcomes(passed=1, deselected=1)


def test_unparametrized_function_is_one_case(pytester):
    pytester.makepyfile(bench_smoke=BENCH_UNPARAMETRIZED)
    result = pytester.runpytest("--collect-only", "-q")
    result.stdout.fnmatch_lines(["*bench_smoke.py::bench_smoke::run1*"])
    result = pytester.runpytest("--prob-runs=3")
    result.assert_outcomes(passed=3)
    result.stdout.fnmatch_lines(["*smoke* check  3/3*"])


def test_non_prefixed_functions_not_collected(pytester):
    import pytest

    pytester.makepyfile(bench_old=BENCH_OLD_STYLE)
    result = pytester.runpytest()
    assert result.ret == pytest.ExitCode.NO_TESTS_COLLECTED


def test_non_bench_python_files_untouched(pytester):
    pytester.makepyfile(bench_not_really="X = 1\n")
    pytester.makepyfile(test_plain="def test_ok():\n    assert True\n")
    result = pytester.runpytest()
    result.assert_outcomes(passed=1)
    assert result.ret == 0


# ---------------------------------------------------------------------------
# Runs and fractions
# ---------------------------------------------------------------------------


def test_runs_multiply_items(pytester):
    pytester.makepyfile(bench_demo=BENCH_OK)
    result = pytester.runpytest("--prob-runs=3")
    result.assert_outcomes(passed=6)
    assert result.ret == 0


def test_runs_from_ini(pytester):
    pytester.makeini("[pytest]\nprob_runs = 2\n")
    pytester.makepyfile(bench_demo=BENCH_OK)
    result = pytester.runpytest()
    result.assert_outcomes(passed=4)


def test_fraction_and_flaky_in_summary(pytester):
    pytester.makepyfile(bench_flaky=BENCH_FLAKY)
    result = pytester.runpytest("--prob-runs=2")
    result.assert_outcomes(passed=1, failed=1)
    assert result.ret == 1
    result.stdout.fnmatch_lines(
        ["*= probability =*", "*check::wobbly* check  1/2  FLAKY*"]
    )
    result.stdout.fnmatch_lines(["*Overall: 1/2 passed (50%)*"])


def test_all_passing_summary_and_exit_code(pytester):
    pytester.makepyfile(bench_demo=BENCH_OK)
    result = pytester.runpytest("--prob-runs=2")
    result.assert_outcomes(passed=4)
    assert result.ret == 0
    result.stdout.fnmatch_lines(
        ["*check::alpha* check  2/2*", "*Overall: 4/4 passed (100%)*"]
    )


def test_step_failure_repr_is_compact(pytester):
    pytester.makepyfile(bench_bad=BENCH_FAIL_MSG)
    result = pytester.runpytest()
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(
        ["*step 'classify' failed: expected refund got billing*"]
    )


def test_errored_runs_are_a_distinct_class(pytester):
    import json

    pytester.makepyfile(
        bench_shaky="""
import pytest
from pytest_probability import StepResult

_calls = {"n": 0}

@pytest.mark.parametrize("word", ["shaky_api"])
def bench_check(word):
    _calls["n"] += 1
    if _calls["n"] == 2:
        yield StepResult(label="check", passed=False, error="API timeout")
    else:
        yield StepResult(label="check", passed=True)
"""
    )
    result = pytester.runpytest("--prob-runs=3", "--prob-json=report.json")
    result.assert_outcomes(passed=2, failed=1)
    # three run classes: 2 pass, 0 fail, 1 error -> 2/3 with the error
    # class named, never a 100%-looking row
    result.stdout.fnmatch_lines(["*shaky_api* check  2/3  1 ERRORED*"])
    result.stdout.fnmatch_lines(["*Overall: 2/3 passed (67%), 1 errored*"])

    data = json.loads((pytester.path / "report.json").read_text())
    (row,) = data["rows"]
    assert (row["passes"], row["fails"], row["errors"]) == (2, 0, 1)
    assert row["total"] == 3
    assert 66 < row["pass_rate"] < 67
    assert row["status"] == "errored"
    assert data["totals"]["errors"] == 1
    assert 66 < data["totals"]["pass_rate"] < 67


def test_exception_becomes_error_step(pytester):
    pytester.makepyfile(bench_crash=BENCH_ERROR)
    result = pytester.runpytest()
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*check::crashy* ok     1/1*"])
    result.stdout.fnmatch_lines(["*check::crashy* error  0/1  ERROR*"])
    result.stdout.fnmatch_lines(["*RuntimeError: boom*"])


def test_duck_typed_steps(pytester):
    pytester.makepyfile(bench_duck=BENCH_DUCK_TYPED)
    result = pytester.runpytest()
    result.assert_outcomes(passed=1)
    result.stdout.fnmatch_lines(["*duck::ducky* quack  1/1*"])


def test_module_setup_teardown_run_once(pytester):
    pytester.makepyfile(bench_lifecycle=BENCH_LIFECYCLE)
    result = pytester.runpytest("--prob-runs=3")
    result.assert_outcomes(passed=6)
    log = pytester.path / "lifecycle.log"
    assert log.read_text() == "setup\nteardown\n"


# ---------------------------------------------------------------------------
# Cost and token usage
# ---------------------------------------------------------------------------


def test_cost_scalar_in_summary(pytester):
    pytester.makepyfile(bench_cost=BENCH_COST)
    result = pytester.runpytest()
    result.assert_outcomes(passed=1)
    result.stdout.fnmatch_lines(["*call::paid* llm  1/1  $0.0500*"])
    result.stdout.fnmatch_lines(["*Cost:    $0.0500*"])


def test_detailed_usage_per_model(pytester):
    import json

    pytester.makepyfile(bench_usage=BENCH_USAGE)
    result = pytester.runpytest("--prob-json=report.json")
    result.assert_outcomes(passed=2)
    # row cost derives from usage when cost is unset
    result.stdout.fnmatch_lines(["*call::paid* llm*$0.0500*"])
    # explicit cost wins over usage-derived sum
    result.stdout.fnmatch_lines(["*override::override* llm*$0.2000*"])
    # per-model token breakdown, cached shown only when non-zero
    result.stdout.fnmatch_lines(["*Tokens:  m-small  110 in / 21 out*$0.5100*"])
    result.stdout.fnmatch_lines(["*m-big    50 in / 5 out / 30 cached  $0.0400*"])

    data = json.loads((pytester.path / "report.json").read_text())
    paid_row = next(r for r in data["rows"] if r["case"] == "call::paid")
    assert paid_row["usage"]["m-small"]["input_tokens"] == 100
    assert paid_row["usage"]["m-big"]["cached_input_tokens"] == 30
    assert data["totals"]["usage"]["m-small"]["input_tokens"] == 110
    assert data["totals"]["cost"] == 0.25
    step = data["records"][0]["steps"][0]
    assert {u["model"] for u in step["usage"]} == {"m-small", "m-big"}


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------


def test_k_selects_cases(pytester):
    pytester.makepyfile(bench_demo=BENCH_OK)
    result = pytester.runpytest("-k", "alpha", "--prob-runs=2")
    result.assert_outcomes(passed=2, deselected=2)


def test_m_selects_marked_cases(pytester):
    pytester.makeini("[pytest]\nmarkers =\n    fast: quick\n    slow: expensive\n")
    pytester.makepyfile(bench_marked=BENCH_MARKED_CASES)
    result = pytester.runpytest("-m", "fast", "--prob-runs=2")
    result.assert_outcomes(passed=2, deselected=2)


# ---------------------------------------------------------------------------
# Parametrization
# ---------------------------------------------------------------------------


def test_multi_argname_values_arrive_as_arguments(pytester):
    pytester.makepyfile(bench_multiarg=BENCH_MULTI_ARGNAMES)
    result = pytester.runpytest("--collect-only", "-q")
    result.stdout.fnmatch_lines(
        ["*bench_classify::identify_pii*", "*bench_classify::is_question*"]
    )
    result = pytester.runpytest()
    result.assert_outcomes(passed=2)
    result.stdout.fnmatch_lines(["*classify::identify_pii* classify*1/1*"])


def test_stacked_decorators(pytester):
    pytester.makepyfile(bench_stacked=BENCH_STACKED)
    result = pytester.runpytest("--collect-only", "-q")
    # bottom decorator sits leftmost, top varies fastest — like pytest
    result.stdout.fnmatch_lines(
        ["*small-en*", "*small-de*", "*large-en*", "*large-de*"]
    )
    result = pytester.runpytest()
    result.assert_outcomes(passed=4)
    result.stdout.fnmatch_lines(["*x::small-en* check*1/1*"])


def test_pytest_param_id_and_marks(pytester):
    pytester.makepyfile(bench_ids=BENCH_VARIANT_PARAM_ID)
    result = pytester.runpytest()
    result.assert_outcomes(passed=1, skipped=1)
    result.stdout.fnmatch_lines(["*t::loose* check*1/1*"])


def test_function_level_skip_mark(pytester):
    pytester.makepyfile(bench_skip=BENCH_FN_SKIP)
    result = pytester.runpytest("--prob-runs=3")
    result.assert_outcomes(skipped=6)
    assert result.ret == 0


# ---------------------------------------------------------------------------
# Ordering and throttling
# ---------------------------------------------------------------------------


def test_delay_sleeps_between_executions(pytester):
    pytester.makepyfile(bench_demo=BENCH_OK)
    t0 = time.monotonic()
    result = pytester.runpytest("--prob-runs=2", "--prob-delay=0.15")
    elapsed = time.monotonic() - t0
    result.assert_outcomes(passed=4)
    # 4 items -> 3 gaps of 0.15s (no sleep before the first)
    assert elapsed >= 0.45


def test_no_delay_before_first_execution(pytester):
    pytester.makepyfile(bench_single=BENCH_FAIL_MSG)  # exactly one item
    t0 = time.monotonic()
    result = pytester.runpytest("--prob-delay=5")
    elapsed = time.monotonic() - t0
    result.assert_outcomes(failed=1)
    assert elapsed < 3


def test_default_order_is_case_major(pytester):
    pytester.makepyfile(bench_demo=BENCH_OK)
    result = pytester.runpytest("--prob-runs=2", "--collect-only", "-q")
    result.stdout.fnmatch_lines(
        ["*alpha?run1?*", "*alpha?run2?*", "*beta?run1?*", "*beta?run2?*"]
    )


def test_transpose_is_run_major(pytester):
    pytester.makepyfile(bench_demo=BENCH_OK)
    result = pytester.runpytest(
        "--prob-runs=2", "--prob-transpose", "--collect-only", "-q"
    )
    result.stdout.fnmatch_lines(
        ["*alpha?run1?*", "*beta?run1?*", "*alpha?run2?*", "*beta?run2?*"]
    )


# ---------------------------------------------------------------------------
# JSON report
# ---------------------------------------------------------------------------


def test_json_report(pytester):
    import json

    pytester.makepyfile(bench_flaky=BENCH_FLAKY)
    result = pytester.runpytest("--prob-runs=2", "--prob-json=out/report.json")
    result.assert_outcomes(passed=1, failed=1)
    result.stdout.fnmatch_lines(["*Report:  out/report.json*"])

    data = json.loads((pytester.path / "out" / "report.json").read_text())
    assert data["runs"] == 2
    assert data["exit_status"] == 1
    assert data["totals"]["passes"] == 1
    assert data["totals"]["count"] == 2
    (row,) = data["rows"]
    assert row["case"] == "check::wobbly"
    assert row["label"] == "check"
    assert row["status"] == "flaky"
    assert (row["passes"], row["fails"], row["total"]) == (1, 1, 2)
    assert row["usage"] == {}
    assert len(data["records"]) == 2
    assert {r["run"] for r in data["records"]} == {1, 2}
    assert data["records"][0]["case"] == "check::wobbly"
    assert data["records"][0]["steps"][0]["label"] == "check"


def test_no_json_report_by_default(pytester):
    pytester.makepyfile(bench_demo=BENCH_OK)
    result = pytester.runpytest()
    result.assert_outcomes(passed=2)
    assert not list(pytester.path.glob("**/*.json"))


def test_no_probability_section_without_items(pytester):
    pytester.makepyfile(test_plain="def test_ok():\n    assert True\n")
    result = pytester.runpytest()
    result.assert_outcomes(passed=1)
    assert "= probability =" not in result.stdout.str()
