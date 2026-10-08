"""End-to-end tests for pytest-probability, run via pytester."""

import time

import pytest

BENCH_OK = """
import pytest

@pytest.mark.parametrize("word", ["alpha", "beta"])
def bench_check(word):
    assert word in ("alpha", "beta")
"""

BENCH_FLAKY = """
import pytest

_calls = {"n": 0}

@pytest.mark.parametrize("word", ["wobbly"])
def bench_check(word):
    _calls["n"] += 1
    assert _calls["n"] % 2 == 0
"""

BENCH_FAIL_MSG = """
import pytest

@pytest.mark.parametrize("word", ["bad"])
def bench_check(word):
    answer = "billing"
    assert answer == "refund", f"got '{answer}'"
"""

BENCH_COST = """
import pytest
from pytest_probability import record_cost

@pytest.mark.parametrize("word", ["paid"])
def bench_call(word):
    record_cost(0.05)
    assert True
"""

BENCH_USAGE = """
import pytest
from pytest_probability import TokenUsage, record_cost, record_usage

@pytest.mark.parametrize("word", ["paid"])
def bench_call(word):
    record_usage(TokenUsage(model="m-small", input_tokens=100, output_tokens=20,
                            cost=0.01))
    # plain dicts and keyword form are accepted too (duck-typed)
    record_usage({"model": "m-big", "input_tokens": 50, "output_tokens": 5,
                  "cached_input_tokens": 30, "cost": 0.04})
    assert True

@pytest.mark.parametrize("word", ["extra"])
def bench_extra(word):
    # run cost = record_cost amounts + usage entry costs
    record_cost(0.2)
    record_usage(model="m-small", input_tokens=10, output_tokens=1, cost=0.5)
    assert True
"""

BENCH_USAGE_SURVIVES_FAILURE = """
import pytest
from pytest_probability import record_usage

@pytest.mark.parametrize("word", ["expensive_wrong"])
def bench_call(word):
    record_usage(model="m-big", input_tokens=100, output_tokens=10, cost=0.03)
    assert False, "wrong answer"
"""

BENCH_ERROR = """
import pytest

@pytest.mark.parametrize("word", ["crashy"])
def bench_check(word):
    raise RuntimeError("boom")
"""

BENCH_OLD_GENERATOR = """
import pytest

@pytest.mark.parametrize("word", ["legacy"])
def bench_check(word):
    yield word
"""

BENCH_LIFECYCLE = """
import pytest
from pathlib import Path

LOG = Path(__file__).parent / "lifecycle.log"

def setup():
    prev = LOG.read_text() if LOG.exists() else ""
    LOG.write_text(prev + "setup\\n")

def teardown():
    LOG.write_text(LOG.read_text() + "teardown\\n")

@pytest.mark.parametrize("word", ["a", "b"])
def bench_check(word):
    assert True
"""

BENCH_MARKED_CASES = """
import pytest

@pytest.mark.parametrize("word", [
    pytest.param("fast_one", marks=pytest.mark.fast),
    pytest.param("slow_one", marks=pytest.mark.slow),
])
def bench_check(word):
    assert True
"""

BENCH_MULTI_ARGNAMES = """
import pytest

@pytest.mark.parametrize("text,expected", [
    pytest.param("my ssn is 078-05-1120", "pii", id="identify_pii"),
    pytest.param("is this a question?", "question", id="is_question"),
])
def bench_classify(text, expected):
    assert isinstance(text, str)
    assert expected in ("pii", "question")
"""

BENCH_STACKED = """
import pytest

@pytest.mark.parametrize("lang", ["en", "de"])
@pytest.mark.parametrize("model", ["small", "large"])
def bench_x(model, lang):
    assert model in ("small", "large") and lang in ("en", "de")
"""

BENCH_VARIANT_PARAM_ID = """
import pytest

@pytest.mark.parametrize("threshold", [
    pytest.param(0.5, id="loose"),
    pytest.param(0.9, id="strict", marks=pytest.mark.skip(reason="not yet")),
])
def bench_t(threshold):
    assert threshold == 0.5
"""

BENCH_FN_SKIP = """
import pytest

@pytest.mark.skip(reason="flaky infra")
@pytest.mark.parametrize("word", ["a", "b"])
def bench_check(word):
    assert False
"""

BENCH_UNPARAMETRIZED = """
def bench_smoke():
    assert True
"""

BENCH_MULTI_FN = """
import pytest

@pytest.mark.parametrize("word", ["shared"])
def bench_first(word):
    assert True

@pytest.mark.parametrize("word", ["shared"])
def bench_second(word):
    assert False
"""

BENCH_OLD_STYLE = """
def bench(word):   # not bench_* prefixed -> not a benchmark
    assert True
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
    result.stdout.fnmatch_lines(["*first::shared*1/1*"])
    result.stdout.fnmatch_lines(["*second::shared*0/1  FAIL*"])


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
    result.stdout.fnmatch_lines(["*smoke*3/3*"])


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
# Outcomes: assert -> fail, exception -> error
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
        ["*= probability =*", "*check::wobbly  1/2  ?1%, 99%?  FLAKY*"]
    )
    result.stdout.fnmatch_lines(["*Overall: 1/2 passed (50%)*"])


def test_all_passing_summary_and_exit_code(pytester):
    pytester.makepyfile(bench_demo=BENCH_OK)
    result = pytester.runpytest("--prob-runs=2")
    result.assert_outcomes(passed=4)
    assert result.ret == 0
    result.stdout.fnmatch_lines(["*check::alpha  2/2*", "*Overall: 4/4 passed (100%)*"])


def test_assert_failure_shows_native_traceback(pytester):
    pytester.makepyfile(bench_bad=BENCH_FAIL_MSG)
    result = pytester.runpytest("--prob-json=report.json")
    result.assert_outcomes(failed=1)
    # the real assert line and message, straight from pytest
    result.stdout.fnmatch_lines(["*AssertionError: got 'billing'*"])
    result.stdout.fnmatch_lines(["*check::bad  0/1  FAIL*"])

    import json

    data = json.loads((pytester.path / "report.json").read_text())
    (rec,) = data["records"]
    assert rec["outcome"] == "fail"
    assert rec["message"] == "got 'billing'"
    assert rec["error"] is None


def test_exception_is_the_error_class(pytester):
    pytester.makepyfile(bench_crash=BENCH_ERROR)
    result = pytester.runpytest("--prob-json=report.json")
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*check::crashy  0/1  ERROR*"])
    result.stdout.fnmatch_lines(["*RuntimeError: boom*"])

    import json

    data = json.loads((pytester.path / "report.json").read_text())
    (rec,) = data["records"]
    assert rec["outcome"] == "error"
    assert rec["error"] == "RuntimeError: boom"


def test_errored_runs_are_a_distinct_class(pytester):
    import json

    pytester.makepyfile(
        bench_shaky="""
import pytest

_calls = {"n": 0}

@pytest.mark.parametrize("word", ["shaky_api"])
def bench_check(word):
    _calls["n"] += 1
    if _calls["n"] == 2:
        raise ConnectionError("API timeout")
    assert True
"""
    )
    result = pytester.runpytest("--prob-runs=3", "--prob-json=report.json")
    result.assert_outcomes(passed=2, failed=1)
    # three run classes: 2 pass, 0 fail, 1 error -> 2/3 with the error
    # class named, never a 100%-looking row
    result.stdout.fnmatch_lines(["*shaky_api  2/3  ?9%, 99%?  1 ERRORED*"])
    result.stdout.fnmatch_lines(["*Overall: 2/3 passed (67%), 1 errored*"])

    data = json.loads((pytester.path / "report.json").read_text())
    (row,) = data["rows"]
    assert (row["passes"], row["fails"], row["errors"]) == (2, 0, 1)
    assert row["status"] == "errored"
    assert 66 < row["pass_rate"] < 67


def test_assertion_rewriting_gives_introspection(pytester):
    import json

    pytester.makepyfile(
        bench_rw="""
import pytest

@pytest.mark.parametrize("word", ["intro"])
def bench_check(word):
    answer = "other"
    expected = "pii"
    assert answer == expected
"""
    )
    result = pytester.runpytest("--prob-json=report.json")
    result.assert_outcomes(failed=1)
    # pytest's assertion rewriter ran on the bench module: a bare
    # assert reports both operands
    result.stdout.fnmatch_lines(["*assert 'other' == 'pii'*"])

    data = json.loads((pytester.path / "report.json").read_text())
    assert data["records"][0]["message"] == "assert 'other' == 'pii'"


def test_pytest_dont_rewrite_docstring_opt_out(pytester):
    import json

    pytester.makepyfile(
        bench_plain='''
"""PYTEST_DONT_REWRITE"""
import pytest

@pytest.mark.parametrize("word", ["plain"])
def bench_check(word):
    assert "other" == "pii"
'''
    )
    result = pytester.runpytest("--prob-json=report.json")
    result.assert_outcomes(failed=1)
    data = json.loads((pytester.path / "report.json").read_text())
    # bare AssertionError, no rewritten message
    assert data["records"][0]["message"] is None


def test_legacy_generator_bench_errors_loudly(pytester):
    pytester.makepyfile(bench_legacy=BENCH_OLD_GENERATOR)
    result = pytester.runpytest()
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*use assert instead of yielding*"])
    result.stdout.fnmatch_lines(["*check::legacy  0/1  ERROR*"])


def test_module_setup_teardown_run_once(pytester):
    pytester.makepyfile(bench_lifecycle=BENCH_LIFECYCLE)
    result = pytester.runpytest("--prob-runs=3")
    result.assert_outcomes(passed=6)
    log = pytester.path / "lifecycle.log"
    assert log.read_text() == "setup\nteardown\n"


# ---------------------------------------------------------------------------
# Cost and token usage
# ---------------------------------------------------------------------------


def test_record_cost_in_summary(pytester):
    pytester.makepyfile(bench_cost=BENCH_COST)
    result = pytester.runpytest()
    result.assert_outcomes(passed=1)
    result.stdout.fnmatch_lines(["*call::paid  1/1  $0.0500*"])
    result.stdout.fnmatch_lines(["*Cost:    $0.0500*"])


def test_record_usage_per_model(pytester):
    import json

    pytester.makepyfile(bench_usage=BENCH_USAGE)
    result = pytester.runpytest("--prob-json=report.json")
    result.assert_outcomes(passed=2)
    # run cost derives from usage entries
    result.stdout.fnmatch_lines(["*call::paid*$0.0500*"])
    # record_cost adds to usage costs
    result.stdout.fnmatch_lines(["*extra::extra*$0.7000*"])
    # per-model token breakdown, cached shown only when non-zero
    result.stdout.fnmatch_lines(["*Tokens:  m-small  110 in / 21 out*$0.5100*"])
    result.stdout.fnmatch_lines(["*m-big    50 in / 5 out / 30 cached  $0.0400*"])

    data = json.loads((pytester.path / "report.json").read_text())
    paid_row = next(r for r in data["rows"] if r["case"] == "call::paid")
    assert paid_row["usage"]["m-small"]["input_tokens"] == 100
    assert paid_row["usage"]["m-big"]["cached_input_tokens"] == 30
    assert data["totals"]["usage"]["m-small"]["input_tokens"] == 110
    assert abs(data["totals"]["cost"] - 0.75) < 1e-9
    (rec,) = [r for r in data["records"] if r["case"] == "call::paid"]
    assert {u["model"] for u in rec["usage"]} == {"m-small", "m-big"}
    assert rec["elapsed"] > 0


def test_usage_survives_failing_assert(pytester):
    import json

    pytester.makepyfile(bench_spent=BENCH_USAGE_SURVIVES_FAILURE)
    result = pytester.runpytest("--prob-json=report.json")
    result.assert_outcomes(failed=1)
    # the run failed, but the money it spent is still on the row
    result.stdout.fnmatch_lines(["*call::expensive_wrong  0/1  $0.0300  FAIL*"])

    data = json.loads((pytester.path / "report.json").read_text())
    (rec,) = data["records"]
    assert rec["outcome"] == "fail"
    assert rec["usage"][0]["cost"] == 0.03


def test_record_usage_outside_run_raises(pytester):
    pytester.makepyfile(
        test_outside="""
import pytest
from pytest_probability import record_usage

def test_outside():
    with pytest.raises(RuntimeError, match="outside a bench run"):
        record_usage(model="m", input_tokens=1)
"""
    )
    result = pytester.runpytest()
    result.assert_outcomes(passed=1)


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
    result.stdout.fnmatch_lines(["*classify::identify_pii*1/1*"])


def test_stacked_decorators(pytester):
    pytester.makepyfile(bench_stacked=BENCH_STACKED)
    result = pytester.runpytest("--collect-only", "-q")
    # bottom decorator sits leftmost, top varies fastest — like pytest
    result.stdout.fnmatch_lines(
        ["*small-en*", "*small-de*", "*large-en*", "*large-de*"]
    )
    result = pytester.runpytest()
    result.assert_outcomes(passed=4)
    result.stdout.fnmatch_lines(["*x::small-en*1/1*"])


def test_pytest_param_id_and_marks(pytester):
    pytester.makepyfile(bench_ids=BENCH_VARIANT_PARAM_ID)
    result = pytester.runpytest()
    result.assert_outcomes(passed=1, skipped=1)
    result.stdout.fnmatch_lines(["*t::loose*1/1*"])


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
    assert row["status"] == "flaky"
    assert (row["passes"], row["fails"], row["total"]) == (1, 1, 2)
    assert row["usage"] == {}
    assert len(data["records"]) == 2
    assert {r["run"] for r in data["records"]} == {1, 2}
    assert {r["outcome"] for r in data["records"]} == {"pass", "fail"}
    assert all(r["case"] == "check::wobbly" for r in data["records"])


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


# ---------------------------------------------------------------------------
# Row intervals
# ---------------------------------------------------------------------------

# Deterministic pass counts per case under --prob-runs=10: each case
# passes its first k runs. Counters are per process, so the xdist test
# keeps a file on one worker with --dist loadfile.
BENCH_COUNTS = """
import pytest

_calls = {}

@pytest.mark.parametrize("case,k", [
    pytest.param("is_question", 10, id="is_question"),
    pytest.param("identify_pii", 7, id="identify_pii"),
    pytest.param("never", 0, id="never"),
])
def bench_classify(case, k):
    _calls[case] = _calls.get(case, 0) + 1
    assert _calls[case] <= k
"""


def _run(pytester, *args):
    # Tracebacks of the deliberately failing runs are noise here.
    return pytester.runpytest("--tb=no", *args)


def _summary_rows(result):
    lines = result.stdout.lines
    start = next(i for i, ln in enumerate(lines) if "= probability =" in ln)
    rows = []
    for ln in lines[start + 1 :]:
        if not ln.strip():
            break
        rows.append(ln)
    return rows


def _cell(row):
    """The interval cell of a summary row, alignment padding removed."""
    import re

    return re.sub(r"\s+", " ", re.search(r"\[[^]]*\]", row).group())


def _expected_cell(x, n, level=0.95, method="exact", prior=(1.0, 1.0)):
    import math

    from pytest_probability.stats import proportion_interval

    low, high = proportion_interval(x, n, level, method, prior)
    return tuple(
        f"{min(max(math.floor(v * 100 + 0.5), v > 0), 100 - (v < 1))}%"
        for v in (low, high)
    )


def test_interval_column_matches_issue_example(pytester):
    pytester.makepyfile(bench_cls=BENCH_COUNTS)
    result = _run(pytester, "--prob-runs=10")
    assert _summary_rows(result) == [
        "  classify::is_question   10/10  [69%, 100%]",
        "  classify::identify_pii   7/10  [35%,  93%]  FLAKY",
        "  classify::never          0/10  [ 0%,  31%]  FAIL",
    ]


def test_interval_columns_align_with_cost(pytester):
    pytester.makepyfile(
        bench_cost=BENCH_COUNTS.replace(
            "import pytest\n",
            "import pytest\nfrom pytest_probability import record_cost\n",
        ).replace("    _calls[case] =", "    record_cost(0.0002)\n    _calls[case] =")
    )
    result = _run(pytester, "--prob-runs=10")
    rows = _summary_rows(result)
    assert rows[1] == "  classify::identify_pii   7/10  [35%,  93%]  $0.0020  FLAKY"
    # brackets, comma and the cost column sit at the same offset on every row
    for ch in "[,]$":
        assert len({r.index(ch) for r in rows}) == 1, (ch, rows)


def test_interval_endpoints_reserved_for_exact_bounds(pytester):
    pytester.makepyfile(
        bench_nine=BENCH_COUNTS.replace('"identify_pii", 7', '"identify_pii", 9')
    )
    result = _run(pytester, "--prob-runs=10")
    # 9/10's exact upper bound is 99.7%: printed 99%, never 100%
    assert _cell(_summary_rows(result)[1]) == "[55%, 99%]"
    assert _cell(_summary_rows(result)[0]) == "[69%, 100%]"


def test_interval_hidden_for_single_runs(pytester):
    pytester.makepyfile(bench_cls=BENCH_COUNTS)
    result = _run(pytester)
    rows = _summary_rows(result)
    assert rows[0] == "  classify::is_question   1/1"
    assert not any("[" in r for r in rows)


def test_single_run_row_is_blank_in_mixed_table(pytester):
    # -k keeps only run1 of one case: that row has one run, the others ten
    pytester.makepyfile(bench_cls=BENCH_COUNTS)
    result = _run(
        pytester,
        "--prob-runs=10", "-k", "not identify_pii or run1]"
    )
    rows = _summary_rows(result)
    pii = next(r for r in rows if "identify_pii" in r)
    assert pii.rstrip().endswith("1/1")
    assert "[" not in pii
    assert any("[69%, 100%]" in r for r in rows)


def test_no_intervals_flag_and_ini(pytester):
    pytester.makepyfile(bench_cls=BENCH_COUNTS)
    result = _run(pytester, "--prob-runs=10", "--prob-no-intervals")
    assert _summary_rows(result)[1] == "  classify::identify_pii   7/10  FLAKY"

    pytester.makeini("[pytest]\nprob_intervals = false\n")
    result = _run(pytester, "--prob-runs=10")
    assert not any("[" in r for r in _summary_rows(result))


@pytest.mark.parametrize("method", ["exact", "wilson", "bayes"])
def test_each_method(pytester, method):
    import json

    pytester.makepyfile(bench_cls=BENCH_COUNTS)
    result = _run(
        pytester,
        "--prob-runs=10", f"--prob-method={method}", "--prob-json=r.json"
    )
    low, high = _expected_cell(7, 10, method=method)
    pii = next(r for r in _summary_rows(result) if "identify_pii" in r)
    assert _cell(pii) == f"[{low}, {high}]"

    data = json.loads((pytester.path / "r.json").read_text())
    from pytest_probability.stats import proportion_interval

    row = next(r for r in data["rows"] if r["case"] == "classify::identify_pii")
    assert row["ci"]["method"] == method
    assert (row["ci"]["low"], row["ci"]["high"]) == pytest.approx(
        proportion_interval(7, 10, 0.95, method)
    )


def test_methods_give_different_intervals(pytester):
    pytester.makepyfile(bench_cls=BENCH_COUNTS)
    cells = set()
    for method in ("exact", "wilson", "bayes"):
        result = _run(pytester, "--prob-runs=10", f"--prob-method={method}")
        cells.add(next(r for r in _summary_rows(result) if "identify_pii" in r))
    assert len(cells) == 3


def test_bayes_uses_prior(pytester):
    import json

    pytester.makeini("[pytest]\nprob_method = bayes\nprob_prior = 0.5, 0.5\n")
    pytester.makepyfile(bench_cls=BENCH_COUNTS)
    _run(pytester, "--prob-runs=10", "--prob-json=r.json")
    data = json.loads((pytester.path / "r.json").read_text())
    from pytest_probability.stats import beta_credible

    assert data["stats_config"] == {
        "method": "bayes",
        "level": 0.95,
        "prior": [0.5, 0.5],
    }
    row = next(r for r in data["rows"] if r["case"] == "classify::identify_pii")
    assert (row["ci"]["low"], row["ci"]["high"]) == pytest.approx(
        beta_credible(7, 10, 0.95, (0.5, 0.5))
    )


def test_confidence_level(pytester):
    pytester.makepyfile(bench_cls=BENCH_COUNTS)
    result = _run(pytester, "--prob-runs=10", "--prob-confidence=0.8")
    low, high = _expected_cell(7, 10, level=0.8)
    assert _cell(_summary_rows(result)[1]) == f"[{low}, {high}]"
    assert (low, high) != _expected_cell(7, 10)


def test_cli_beats_ini(pytester):
    import json

    pytester.makeini(
        "[pytest]\nprob_method = bayes\nprob_confidence = 0.9\n"
        "prob_intervals = false\n"
    )
    pytester.makepyfile(bench_cls=BENCH_COUNTS)
    _run(pytester, "--prob-runs=10", "--prob-json=ini.json")
    data = json.loads((pytester.path / "ini.json").read_text())
    assert data["stats_config"]["method"] == "bayes"
    assert data["stats_config"]["level"] == 0.9

    result = _run(
        pytester,
        "--prob-runs=10",
        "--prob-method=wilson",
        "--prob-confidence=0.99",
        "--prob-json=cli.json",
    )
    data = json.loads((pytester.path / "cli.json").read_text())
    assert data["stats_config"] == {
        "method": "wilson",
        "level": 0.99,
        "prior": [1.0, 1.0],
    }
    # ini prob_intervals = false still hides the column (no CLI to re-enable)
    assert not any("[" in r for r in _summary_rows(result))


def test_json_ci_and_stats_config_defaults(pytester):
    import json

    pytester.makepyfile(bench_cls=BENCH_COUNTS)
    # hiding the column is display-only: JSON always carries ci
    _run(pytester, "--prob-runs=10", "--prob-no-intervals", "--prob-json=r.json")
    data = json.loads((pytester.path / "r.json").read_text())
    assert data["stats_config"] == {
        "method": "exact",
        "level": 0.95,
        "prior": [1.0, 1.0],
    }
    rows = {r["case"]: r for r in data["rows"]}
    ci = rows["classify::is_question"]["ci"]
    assert set(ci) == {"method", "level", "low", "high"}
    assert ci["method"] == "exact" and ci["level"] == 0.95
    assert ci["high"] == 1.0 and 0.69 < ci["low"] < 0.70
    assert rows["classify::never"]["ci"]["low"] == 0.0


def test_json_ci_present_for_single_run(pytester):
    import json

    pytester.makepyfile(bench_cls=BENCH_COUNTS)
    _run(pytester, "--prob-json=r.json")
    data = json.loads((pytester.path / "r.json").read_text())
    assert all(r["ci"]["low"] <= r["ci"]["high"] for r in data["rows"])


@pytest.mark.parametrize(
    "args, message",
    [
        (["--prob-confidence=95"], "*--prob-confidence must be strictly between 0 and 1*0.95?*"),
        (["--prob-confidence=1"], "*--prob-confidence must be strictly between 0 and 1*"),
        (["-o", "prob_confidence=high"], "*prob_confidence must be a number*'high'*"),
        (["-o", "prob_method=frequentist"], "*prob_method must be one of exact, wilson, bayes*"),
        (["-o", "prob_prior=1"], "*prob_prior must be two positive numbers*"),
        (["-o", "prob_prior=0,1"], "*prob_prior must be two positive numbers*"),
    ],
)
def test_invalid_stats_options_are_usage_errors(pytester, args, message):
    pytester.makepyfile(bench_cls=BENCH_COUNTS)
    result = _run(pytester, *args)
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines([message])


def test_invalid_method_flag_rejected(pytester):
    pytester.makepyfile(bench_cls=BENCH_COUNTS)
    result = _run(pytester, "--prob-method=frequentist")
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(["*invalid choice*frequentist*"])


def test_regular_suite_output_unchanged_by_stats_options(pytester):
    pytester.makepyfile(test_plain="def test_ok():\n    assert True\n")
    base = _run(pytester, "-p", "no:cacheprovider")
    with_opts = _run(
        pytester,
        "-p", "no:cacheprovider", "--prob-method=wilson", "--prob-confidence=0.9"
    )
    strip = lambda r: [ln for ln in r.stdout.lines if " in " not in ln]  # noqa: E731
    assert strip(base) == strip(with_opts)
    assert "= probability =" not in with_opts.stdout.str()


def test_xdist_parity(pytester):
    import json

    pytest.importorskip("xdist")
    pytester.makepyfile(bench_cls=BENCH_COUNTS)
    serial = _run(
        pytester,
        "--prob-runs=10", "--prob-method=wilson", "--prob-json=serial.json"
    )
    dist = _run(
        pytester,
        "--prob-runs=10",
        "--prob-method=wilson",
        "--prob-json=dist.json",
        "-n",
        "2",
        "--dist",
        "loadfile",
    )
    assert sorted(_summary_rows(serial)) == sorted(_summary_rows(dist))
    s = json.loads((pytester.path / "serial.json").read_text())
    d = json.loads((pytester.path / "dist.json").read_text())
    assert s["stats_config"] == d["stats_config"]
    key = lambda r: r["case"]  # noqa: E731
    assert [r["ci"] for r in sorted(s["rows"], key=key)] == [
        r["ci"] for r in sorted(d["rows"], key=key)
    ]
