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


# ---------------------------------------------------------------------------
# Gates
# ---------------------------------------------------------------------------

# Each case passes its first k runs; under --prob-runs=40 the three
# cases land on the three verdicts at min_rate=0.9:
#   solid 40/40 [91%, 100%] PASS, close 37/40 [80%, 98%] UNDECIDED,
#   weak 3/40 [2%, 20%] FAIL.
BENCH_GATED = """
import pytest

_calls = {}

@pytest.mark.probability(min_rate=0.9)
@pytest.mark.parametrize("case,k", [
    pytest.param("solid", 40, id="solid"),
    pytest.param("close", 37, id="close"),
    pytest.param("weak", 3, id="weak"),
])
def bench_classify(case, k):
    _calls[case] = _calls.get(case, 0) + 1
    assert _calls[case] <= k, f"wrong answer for {case}"
"""


def _gated(marker="min_rate=0.9", cases=(("solid", 40), ("close", 37), ("weak", 3))):
    """BENCH_GATED with another marker and/or other per-case pass counts."""
    params = "\n".join(f'    pytest.param("{c}", {k}, id="{c}"),' for c, k in cases)
    return f"""
import pytest

_calls = {{}}

@pytest.mark.probability({marker})
@pytest.mark.parametrize("case,k", [
{params}
])
def bench_classify(case, k):
    _calls[case] = _calls.get(case, 0) + 1
    assert _calls[case] <= k, f"wrong answer for {{case}}"
"""


def _gate_rows(result):
    """The lines of the ``probability: gates`` block ([] when absent)."""
    lines = result.stdout.lines
    try:
        start = next(i for i, ln in enumerate(lines) if "= probability: gates =" in ln)
    except StopIteration:
        return []
    rows = []
    for ln in lines[start + 1 :]:
        if not ln.startswith("  "):
            break
        rows.append(ln)
    return rows


def _tally(result):
    return next(ln for ln in result.stdout.lines if ln.startswith("  Gates:"))


def _json(pytester, name="r.json"):
    import json

    return json.loads((pytester.path / name).read_text())


def _gates(data):
    return {r["case"]: r["gate"] for r in data["rows"]}


def test_marker_is_registered(pytester):
    pytester.makepyfile(bench_g=BENCH_GATED)
    result = _run(pytester, "--strict-markers", "--prob-runs=40")
    result.assert_outcomes(passed=80, xfailed=40)
    result = pytester.runpytest("--markers")
    result.stdout.fnmatch_lines(["@pytest.mark.probability(min_rate=None, *"])


def test_each_verdict(pytester):
    pytester.makepyfile(bench_g=BENCH_GATED)
    result = _run(pytester, "--prob-runs=40", "--prob-json=r.json")
    # failing runs of a gated case are samples: xfailed, not failed
    result.assert_outcomes(passed=80, xfailed=40)
    assert result.ret == pytest.ExitCode.TESTS_FAILED
    # the main table is unchanged: fractions, row intervals, statuses
    assert _summary_rows(result) == [
        "  classify::solid  40/40  [91%, 100%]",
        "  classify::close  37/40  [80%,  98%]  FLAKY",
        "  classify::weak    3/40  [ 2%,  20%]  FLAKY",
    ]
    assert _tally(result) == "  Gates:   1 passed, 1 failed, 1 undecided"
    # PASS is not listed; each listed verdict sits next to its interval
    assert _gate_rows(result) == [
        "  classify::close  37/40  [80%, 98%]  ≥90%  UNDECIDED",
        "  classify::weak    3/40  [ 2%, 20%]  ≥90%  FAIL",
    ]
    gates = _gates(_json(pytester))
    assert {c: g["verdict"] for c, g in gates.items()} == {
        "classify::solid": "pass",
        "classify::close": "undecided",
        "classify::weak": "fail",
    }


def test_all_gates_pass_exits_zero(pytester):
    # a lower bar: close clears it, weak still fails it
    pytester.makepyfile(bench_g=_gated("min_rate=0.6"))
    result = _run(pytester, "--prob-runs=40")
    assert _gate_rows(result) == ["  classify::weak  3/40  [2%, 20%]  ≥60%  FAIL"]

    pytester.makepyfile(
        bench_g=_gated("min_rate=0.6", (("solid", 40), ("close", 37)))
    )
    result = _run(pytester, "--prob-runs=40")
    result.assert_outcomes(passed=77, xfailed=3)
    assert result.ret == pytest.ExitCode.OK
    assert _tally(result) == "  Gates:   2 passed"
    assert "probability: gates" not in result.stdout.str()


def test_verdicts_use_unrounded_bounds(pytester):
    # 36/36 clears 0.9 (exact low 0.9026) but 35/35 does not (0.8999):
    # both print a whole-percent 90%, the verdicts still differ
    pytester.makepyfile(bench_g=_gated(cases=(("thirtysix", 36),)))
    result = _run(pytester, "--prob-runs=36")
    assert result.ret == pytest.ExitCode.OK
    pytester.makepyfile(bench_g=_gated(cases=(("thirtyfive", 35),)))
    result = _run(pytester, "--prob-runs=35", "-W", "ignore::pytest.PytestWarning")
    assert result.ret == pytest.ExitCode.TESTS_FAILED
    assert _gate_rows(result) == [
        "  classify::thirtyfive  35/35  [90%, 100%]  ≥90%  UNDECIDED"
    ]


@pytest.mark.parametrize("method", ["exact", "wilson", "bayes"])
def test_each_method_decides_with_its_interval(pytester, method):
    from pytest_probability.plugin import interval_verdict
    from pytest_probability.stats import proportion_interval

    # 37/40 against 0.8: exact straddles it, wilson and bayes clear it
    pytester.makepyfile(
        bench_g=_gated(f'min_rate=0.8, method="{method}"', (("close", 37),))
    )
    result = _run(pytester, "--prob-runs=40", "--prob-json=r.json")
    gate = _gates(_json(pytester))["classify::close"]
    low, high = proportion_interval(37, 40, 0.95, method)
    assert (gate["low"], gate["high"]) == (low, high)
    assert gate["method"] == method
    assert gate["verdict"] == interval_verdict(low, high, 0.8)
    assert gate["verdict"] == ("undecided" if method == "exact" else "pass")
    expected = 1 if method == "exact" else 0
    assert result.ret == expected


def test_session_method_drives_gates(pytester):
    pytester.makepyfile(bench_g=_gated("min_rate=0.8", (("close", 37),)))
    _run(pytester, "--prob-runs=40", "--prob-method=wilson", "--prob-json=r.json")
    gate = _gates(_json(pytester))["classify::close"]
    assert (gate["method"], gate["verdict"]) == ("wilson", "pass")


def test_marker_prior_and_confidence(pytester):
    from pytest_probability.stats import beta_credible, clopper_pearson

    pytester.makepyfile(
        bench_g=_gated(
            'min_rate=0.8, method="bayes", prior=(0.5, 0.5)', (("close", 37),)
        )
    )
    _run(pytester, "--prob-runs=40", "--prob-json=r.json")
    data = _json(pytester)
    gate = _gates(data)["classify::close"]
    assert gate["prior"] == [0.5, 0.5]
    assert (gate["low"], gate["high"]) == beta_credible(37, 40, 0.95, (0.5, 0.5))
    # a per-gate override leaves the session settings and row ci alone
    assert data["stats_config"]["method"] == "exact"
    assert data["rows"][0]["ci"]["method"] == "exact"

    # confidence=0.8 narrows the interval enough to clear the bar
    pytester.makepyfile(
        bench_g=_gated("min_rate=0.8, confidence=0.8", (("close", 37),))
    )
    result = _run(pytester, "--prob-runs=40", "--prob-json=r.json")
    gate = _gates(_json(pytester))["classify::close"]
    assert gate["confidence"] == 0.8
    assert (gate["low"], gate["high"]) == clopper_pearson(37, 40, 0.8)
    assert gate["verdict"] == "pass"
    assert result.ret == pytest.ExitCode.OK


def test_count_rule(pytester):
    pytester.makepyfile(
        bench_g=_gated(
            "min_passes=19, runs=20", (("nineteen", 19), ("eighteen", 18))
        )
    )
    result = _run(pytester, "--prob-json=r.json")
    result.assert_outcomes(passed=37, xfailed=3)
    assert result.ret == pytest.ExitCode.TESTS_FAILED
    assert _tally(result) == "  Gates:   1 passed, 1 failed"
    assert _gate_rows(result) == [
        "  classify::eighteen  18/20  [68%, 99%]  ≥19 passes  FAIL"
    ]
    gates = _gates(_json(pytester))
    assert gates["classify::nineteen"]["verdict"] == "pass"
    assert gates["classify::nineteen"]["rule"] == "count"
    assert gates["classify::nineteen"]["min_passes"] == 19
    assert "min_rate" not in gates["classify::nineteen"]


def test_undecided_policy(pytester):
    pytester.makepyfile(bench_g=_gated(cases=(("solid", 40), ("close", 37))))
    result = _run(pytester, "--prob-runs=40")
    assert result.ret == pytest.ExitCode.TESTS_FAILED

    result = _run(pytester, "--prob-runs=40", "--prob-undecided=pass")
    assert result.ret == pytest.ExitCode.OK
    assert _tally(result) == "  Gates:   1 passed, 1 undecided (allowed)"
    # still listed: allowed is not the same as decided
    assert _gate_rows(result) == [
        "  classify::close  37/40  [80%, 98%]  ≥90%  UNDECIDED"
    ]

    pytester.makeini("[pytest]\nprob_undecided = pass\n")
    assert _run(pytester, "--prob-runs=40").ret == pytest.ExitCode.OK
    # CLI beats ini
    result = _run(pytester, "--prob-runs=40", "--prob-undecided=fail")
    assert result.ret == pytest.ExitCode.TESTS_FAILED

    # a FAIL fails the session whatever the policy
    pytester.makepyfile(bench_g=BENCH_GATED)
    assert _run(pytester, "--prob-runs=40").ret == pytest.ExitCode.TESTS_FAILED


def test_global_min_rate(pytester):
    # BENCH_COUNTS carries no marks: 10/10, 7/10 and 0/10
    pytester.makepyfile(bench_cls=BENCH_COUNTS)
    result = _run(pytester, "--prob-runs=10", "--prob-min-rate=0.3")
    result.assert_outcomes(passed=17, xfailed=13)
    assert _tally(result) == "  Gates:   2 passed, 1 undecided"
    assert _gate_rows(result) == [
        "  classify::never  0/10  [0%, 31%]  ≥30%  UNDECIDED"
    ]

    pytester.makeini("[pytest]\nprob_min_rate = 0.3\n")
    assert _gate_rows(_run(pytester, "--prob-runs=10")) == _gate_rows(result)
    # CLI beats ini: never's upper bound (30.8%) is now below the bar
    result = _run(pytester, "--prob-runs=10", "--prob-min-rate=0.35")
    assert _tally(result) == "  Gates:   1 passed, 1 failed, 1 undecided"


def test_marker_beats_global_min_rate(pytester):
    pytester.makepyfile(bench_g=_gated("min_rate=0.6", (("close", 37),)))
    result = _run(pytester, "--prob-runs=40", "--prob-min-rate=0.95")
    assert result.ret == pytest.ExitCode.OK
    assert _tally(result) == "  Gates:   1 passed"


def test_case_mark_overrides_function_mark(pytester):
    pytester.makepyfile(
        bench_g="""
import pytest

_calls = {}

@pytest.mark.probability(min_rate=0.9, runs=10)
@pytest.mark.parametrize("case,k", [
    pytest.param("strict", 10, id="strict"),
    pytest.param("count", 6, id="count",
                 marks=pytest.mark.probability(min_passes=5)),
    pytest.param("longer", 40, id="longer",
                 marks=pytest.mark.probability(runs=40)),
])
def bench_classify(case, k):
    _calls[case] = _calls.get(case, 0) + 1
    assert _calls[case] <= k
"""
    )
    result = _run(pytester, "--prob-json=r.json")
    result.assert_outcomes(passed=56, xfailed=4)
    gates = _gates(_json(pytester))
    # the case's min_passes replaces the function's min_rate outright
    assert gates["classify::count"]["rule"] == "count"
    assert gates["classify::count"]["verdict"] == "pass"
    # a case's runs= changes only that case; the rule is inherited
    assert gates["classify::longer"]["runs"] == 40
    assert gates["classify::longer"]["min_rate"] == 0.9
    assert gates["classify::longer"]["verdict"] == "pass"
    assert gates["classify::strict"]["verdict"] == "undecided"


def test_runs_precedence(pytester):
    pytester.makeini("[pytest]\nprob_runs = 2\n")
    pytester.makepyfile(
        bench_g="""
import pytest

@pytest.mark.probability(runs=5)
def bench_marked():
    assert True

def bench_plain():
    assert True
"""
    )
    # the marker beats the ini; unmarked functions keep the ini
    result = _run(pytester)
    result.assert_outcomes(passed=7)
    result.stdout.fnmatch_lines(["  marked  5/5*", "  plain   2/2*"])
    # the CLI beats the marker
    result = _run(pytester, "--prob-runs=3")
    result.assert_outcomes(passed=6)


def test_runs_only_mark_is_not_a_gate(pytester):
    pytester.makepyfile(
        bench_g="""
import pytest

_calls = {"n": 0}

@pytest.mark.probability(runs=4, confidence=0.99)
def bench_wobbly():
    _calls["n"] += 1
    assert _calls["n"] % 2 == 0
"""
    )
    result = _run(pytester, "--prob-json=r.json")
    # no bar anywhere: failing runs fail, exactly as without the mark
    result.assert_outcomes(passed=2, failed=2)
    assert "Gates:" not in result.stdout.str()
    assert _json(pytester)["rows"][0]["gate"] is None


def test_transpose_with_per_case_runs(pytester):
    pytester.makepyfile(
        bench_g="""
import pytest

@pytest.mark.parametrize("w", [
    "a", pytest.param("b", marks=pytest.mark.probability(runs=3)),
])
@pytest.mark.probability(runs=2)
def bench_t(w):
    assert True
"""
    )
    result = _run(pytester, "--prob-transpose", "--collect-only", "-q")
    result.stdout.fnmatch_lines(
        [
            "*::a?run1?",
            "*::b?run1?",
            "*::a?run2?",
            "*::b?run2?",
            "*::b?run3?",
        ]
    )


@pytest.mark.parametrize(
    "marker, message",
    [
        ("min_rate=0.9, min_passes=9", "min_rate and min_passes can't be used together"),
        ("min_rate=90", "min_rate must be strictly between 0 and 1, got 90 (did you mean 0.9?)"),
        ("min_rate=1", "min_rate must be strictly between 0 and 1*"),
        ("min_passes=0", "min_passes must be a positive integer, got 0"),
        ("min_passes=True", "min_passes must be a positive integer, got True"),
        ("runs=0", "runs must be a positive integer, got 0"),
        ("min_rate=0.9, confidence=95", "confidence must be strictly between 0 and 1*"),
        ('min_rate=0.9, method="frequentist"', "method must be one of exact, wilson, bayes*"),
        ("min_rate=0.9, prior=(0, 1)", "prior must be two positive numbers*"),
        ("min_rate=0.9, rate=0.9", "unexpected argument rate; expected one of*"),
        ("0.9", "takes keyword arguments only"),
    ],
)
def test_invalid_marker_is_a_collection_error(pytester, marker, message):
    pytester.makepyfile(bench_g=_gated(marker))
    result = pytester.runpytest("--prob-runs=40")
    assert result.ret == pytest.ExitCode.INTERRUPTED
    result.stdout.fnmatch_lines(
        [f"*classify::solid: invalid probability mark: {message}"]
    )


@pytest.mark.parametrize(
    "args, message",
    [
        (["--prob-min-rate=90"], "*--prob-min-rate must be strictly between 0 and 1*0.9?*"),
        (["-o", "prob_min_rate=high"], "*prob_min_rate must be a number*'high'*"),
        (["-o", "prob_undecided=maybe"], "*prob_undecided must be one of fail, pass*"),
        (["-o", "prob_errors=ignore"], "*prob_errors must be one of count, exclude*"),
        (["--prob-undecided=maybe"], "*invalid choice*maybe*"),
    ],
)
def test_invalid_gate_options_are_usage_errors(pytester, args, message):
    pytester.makepyfile(bench_g=BENCH_GATED)
    result = _run(pytester, *args)
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines([message])


@pytest.mark.parametrize("rate, need", [(0.8, 17), (0.9, 36), (0.95, 72), (0.99, 368)])
def test_feasibility_warning(pytester, rate, need):
    pytester.makepyfile(bench_g=_gated(f"min_rate={rate}", (("solid", 1000),)))
    result = _run(pytester, "--collect-only", f"--prob-runs={need - 1}")
    result.stdout.fnmatch_lines(
        [
            f"*InfeasibleGateWarning: classify::solid: min_rate={rate} at 95%"
            f" needs ≥{need} runs; this case has {need - 1}"
        ]
    )
    result = _run(pytester, "--collect-only", f"--prob-runs={need}")
    assert "InfeasibleGateWarning" not in result.stdout.str()


def test_feasibility_warning_variants(pytester):
    # a shared gate warns once for the function; other methods are named
    pytester.makepyfile(bench_cls=BENCH_COUNTS)
    result = _run(
        pytester, "--collect-only", "--prob-min-rate=0.9", "--prob-method=wilson"
    )
    result.stdout.fnmatch_lines(
        [
            "*InfeasibleGateWarning: classify (3 cases): min_rate=0.9 at 95%"
            " (wilson) needs ≥35 runs; each has 1"
        ]
    )
    assert result.stdout.str().count("InfeasibleGateWarning") == 1
    # the count rule needs min_passes runs
    pytester.makepyfile(bench_cls=_gated("min_passes=19", (("solid", 20),)))
    result = _run(pytester, "--collect-only", "--prob-runs=10")
    result.stdout.fnmatch_lines(
        ["*classify::solid: min_passes=19 needs ≥19 runs; this case has 10"]
    )
    # a dedicated category, so it can be filtered or made an error
    result = _run(
        pytester,
        "--prob-runs=10",
        "-W",
        "error::pytest_probability.InfeasibleGateWarning",
    )
    assert result.ret == pytest.ExitCode.INTERRUPTED


def test_min_runs_matches_brute_force():
    from dataclasses import replace

    from pytest_probability.plugin import PASS, Gate, StatsConfig
    from pytest_probability.stats import clopper_pearson

    exact = Gate(rule="rate", stats=StatsConfig(), min_rate=0.9)
    # the exact minimums pinned in test_stats
    for rate, need in [(0.8, 17), (0.9, 36), (0.95, 72), (0.99, 368)]:
        gate = replace(exact, min_rate=rate)
        assert gate.min_runs() == need
        assert clopper_pearson(need, need, 0.95)[0] > rate
        assert clopper_pearson(need - 1, need - 1, 0.95)[0] <= rate
    for method, prior in [
        ("wilson", (1.0, 1.0)),
        ("bayes", (1.0, 1.0)),
        ("bayes", (0.5, 0.5)),
    ]:
        for rate in (0.5, 0.8, 0.9, 0.97):
            gate = Gate(
                rule="rate",
                stats=StatsConfig(method=method, prior=prior),
                min_rate=rate,
            )
            need = next(n for n in range(1, 1000) if gate.verdict(n, n) == PASS)
            assert gate.min_runs() == need, (method, prior, rate)


def test_failing_runs_are_xfailed_with_reason(pytester):
    pytester.makepyfile(bench_g=_gated(cases=(("close", 37),)))
    result = pytester.runpytest("--prob-runs=40", "-rx")
    result.assert_outcomes(passed=37, xfailed=3)
    result.stdout.fnmatch_lines(
        [
            "XFAIL bench_g.py::bench_classify::close?run38? -"
            " probability gate: wrong answer for close*"
        ]
    )
    # no FAILURES section: the runs did not fail
    assert "= FAILURES =" not in result.stdout.str()


def test_xfailed_runs_keep_their_traceback(pytester):
    if int(pytest.__version__.split(".")[0]) < 8:
        pytest.skip("--xfail-tb needs pytest 8")
    pytester.makepyfile(bench_g=_gated(cases=(("close", 39),)))
    result = pytester.runpytest("--prob-runs=40", "--xfail-tb")
    result.stdout.fnmatch_lines(
        [
            "*= XFAILURES =*",
            "*>*assert _calls?case? <= k*",
            "E*AssertionError: wrong answer for close",
        ]
    )


def test_x_does_not_stop_on_gated_failures(pytester):
    pytester.makepyfile(bench_g=BENCH_GATED)
    result = _run(pytester, "--prob-runs=40", "-x")
    # every run executed; the gates still fail the session
    result.assert_outcomes(passed=80, xfailed=40)
    assert result.ret == pytest.ExitCode.TESTS_FAILED
    result = _run(pytester, "--prob-runs=40", "--maxfail=1")
    result.assert_outcomes(passed=80, xfailed=40)


def test_x_still_stops_on_ungated_failures_and_counted_errors(pytester):
    pytester.makepyfile(bench_flaky=BENCH_FLAKY)
    result = _run(pytester, "--prob-runs=4", "-x")
    result.assert_outcomes(failed=1)

    pytester.makepyfile(
        bench_flaky="""
import pytest

@pytest.mark.probability(min_rate=0.5)
def bench_crash():
    raise RuntimeError("boom")
"""
    )
    result = _run(pytester, "--prob-runs=4", "-x")
    result.assert_outcomes(failed=1)


def test_runxfail_reports_gated_failures_as_failures(pytester):
    pytester.makepyfile(bench_g=_gated(cases=(("close", 37),)))
    result = _run(pytester, "--prob-runs=40", "--runxfail")
    result.assert_outcomes(passed=37, failed=3)
    # the gate is still judged and reported
    assert _gate_rows(result) == [
        "  classify::close  37/40  [80%, 98%]  ≥90%  UNDECIDED"
    ]


BENCH_GATED_ERRORS = """
import pytest

_calls = {"n": 0}

@pytest.mark.probability(min_rate=0.5)
def bench_shaky():
    # 12 runs: errors on 3, 6, 9, 12; one real failure on run 4
    _calls["n"] += 1
    if _calls["n"] % 3 == 0:
        raise ConnectionError("API timeout")
    assert _calls["n"] != 4, "wrong answer"

@pytest.mark.probability(min_rate=0.5)
def bench_down():
    raise ConnectionError("API down")
"""


def test_errors_count_as_non_passes_by_default(pytester):
    pytester.makepyfile(bench_e=BENCH_GATED_ERRORS)
    result = _run(pytester, "--prob-runs=12", "--prob-json=r.json")
    # errors fail the session as always; the assert failure is xfailed
    result.assert_outcomes(passed=7, failed=16, xfailed=1)
    assert result.ret == pytest.ExitCode.TESTS_FAILED
    assert _summary_rows(result) == [
        "  shaky  7/12  [28%, 85%]  FLAKY (4 errored)",
        "  down   0/12  [ 0%, 26%]  ERROR",
    ]
    assert _gate_rows(result) == [
        "  shaky  7/12  [28%, 85%]  ≥50%  UNDECIDED",
        "  down   0/12  [ 0%, 26%]  ≥50%  FAIL",
    ]
    gates = _gates(_json(pytester))
    assert (gates["shaky"]["total"], gates["shaky"]["excluded"]) == (12, 0)
    assert gates["shaky"]["errors"] == "count"


def test_errors_excluded(pytester):
    pytester.makeini("[pytest]\nprob_errors = exclude\n")
    pytester.makepyfile(bench_e=BENCH_GATED_ERRORS)
    result = _run(pytester, "--prob-runs=12", "--prob-json=r.json", "-rx")
    # errors leave the sample and no longer fail the session
    result.assert_outcomes(passed=7, xfailed=17)
    result.stdout.fnmatch_lines(
        [
            "XFAIL bench_e.py::bench_shaky::run3 - probability gate, error"
            " excluded: ConnectionError: API timeout"
        ]
    )
    # the row still counts every run; the annotation says what the gate did
    assert _summary_rows(result) == [
        "  shaky  7/12  [28%, 85%]  FLAKY (4 errored, excluded)",
        "  down   0/12  [ 0%, 26%]  ERROR (12 errored, excluded)",
    ]
    # the gate judges 7/8; an all-error case has no sample: UNDECIDED
    assert _gate_rows(result) == [
        "  shaky  7/8  [47%, 99%]  ≥50%  UNDECIDED  (4 errored, excluded)",
        "  down   0/0              ≥50%  UNDECIDED  (12 errored, excluded)",
    ]
    assert result.ret == pytest.ExitCode.TESTS_FAILED
    gates = _gates(_json(pytester))
    assert (gates["shaky"]["passes"], gates["shaky"]["total"]) == (7, 8)
    assert gates["shaky"]["excluded"] == 4
    assert gates["down"]["total"] == 0
    assert (gates["down"]["low"], gates["down"]["high"]) == (None, None)
    assert gates["down"]["verdict"] == "undecided"

    # with only errors as blemishes, an excluded-errors case can pass
    pytester.makepyfile(
        bench_e="""
import pytest

_calls = {"n": 0}

@pytest.mark.probability(min_rate=0.5)
def bench_shaky():
    _calls["n"] += 1
    if _calls["n"] % 4 == 0:
        raise ConnectionError("API timeout")
"""
    )
    result = _run(pytester, "--prob-runs=12")
    result.assert_outcomes(passed=9, xfailed=3)
    assert _summary_rows(result) == ["  shaky  9/12  [43%, 95%]  (3 errored, excluded)"]
    assert result.ret == pytest.ExitCode.OK


def test_errors_excluded_only_affects_gated_cases(pytester):
    pytester.makeini("[pytest]\nprob_errors = exclude\n")
    pytester.makepyfile(bench_crash=BENCH_ERROR)
    result = _run(pytester)
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*check::crashy  0/1  ERROR"])


BENCH_GATE_OK = """
import pytest

@pytest.mark.probability(min_rate=0.5)
def bench_ok():
    assert True
"""

BENCH_GATE_BAD = """
import pytest

_calls = {"n": 0}

@pytest.mark.probability(min_rate=0.9)
def bench_bad():
    _calls["n"] += 1
    assert _calls["n"] <= 3
"""

BENCH_UNGATED_FAIL = """
def bench_ungated():
    assert False
"""

TEST_PLAIN = "def test_ok():\n    assert True\n"


@pytest.mark.parametrize(
    "files, outcomes, ret",
    [
        (("gate_ok", "plain"), {"passed": 11}, 0),
        (("gate_ok", "ungated_fail"), {"passed": 10, "failed": 10}, 1),
        (("gate_bad", "plain"), {"passed": 4, "xfailed": 7}, 1),
        (("gate_bad", "ungated_fail"), {"passed": 3, "failed": 10, "xfailed": 7}, 1),
        (("gate_ok", "gate_bad"), {"passed": 13, "xfailed": 7}, 1),
    ],
)
def test_exit_status_in_mixed_sessions(pytester, files, outcomes, ret):
    sources = {
        "gate_ok": ("bench_gate_ok", BENCH_GATE_OK),
        "gate_bad": ("bench_gate_bad", BENCH_GATE_BAD),
        "ungated_fail": ("bench_ungated", BENCH_UNGATED_FAIL),
        "plain": ("test_plain", TEST_PLAIN),
    }
    pytester.makepyfile(**dict(sources[f] for f in files))
    result = _run(pytester, "--prob-runs=10", "--prob-json=r.json")
    result.assert_outcomes(**outcomes)
    assert result.ret == ret
    assert _json(pytester)["exit_status"] == ret


def test_interrupted_session_keeps_its_status(pytester):
    pytester.makepyfile(bench_gate_bad=BENCH_GATE_BAD)
    pytester.makepyfile(
        bench_broken="import pytest\n\ndef bench_x(:\n    pass\n"
    )
    result = _run(pytester, "--prob-runs=10")
    # a collection error interrupts before anything runs
    assert result.ret == pytest.ExitCode.INTERRUPTED


def test_gate_failures_are_invisible_to_lf_and_junit(pytester):
    # the documented limitation: a failed gate has no failing item
    pytester.makepyfile(bench_gate_bad=BENCH_GATE_BAD)
    result = _run(pytester, "--prob-runs=10", "--junitxml=j.xml")
    assert result.ret == pytest.ExitCode.TESTS_FAILED
    xml = (pytester.path / "j.xml").read_text()
    assert 'failures="0"' in xml
    assert 'type="pytest.xfail"' in xml
    result = _run(pytester, "--prob-runs=10", "--lf")
    result.stdout.fnmatch_lines(["*no previously failed tests*"])
    result.assert_outcomes(passed=3, xfailed=7)


def test_json_gate_fields(pytester):
    pytester.makepyfile(bench_g=BENCH_GATED, bench_cls=BENCH_COUNTS)
    _run(pytester, "--prob-runs=40", "--prob-json=r.json")
    data = _json(pytester)
    assert data["exit_status"] == 1
    gates = _gates(data)
    # ungated rows carry gate: null
    assert gates["classify::is_question"] is None
    gate = gates["classify::close"]
    assert set(gate) == {
        "rule", "min_rate", "confidence", "method", "prior", "errors", "runs",
        "passes", "total", "excluded", "low", "high", "verdict",
    }
    assert gate["rule"] == "rate" and gate["min_rate"] == 0.9
    assert gate["confidence"] == 0.95 and gate["method"] == "exact"
    assert (gate["passes"], gate["total"], gate["runs"]) == (37, 40, 40)
    row = next(r for r in data["rows"] if r["case"] == "classify::close")
    # with errors counted and no overrides, the gate's interval is the row's
    assert (gate["low"], gate["high"]) == (row["ci"]["low"], row["ci"]["high"])
    # the gate spec is per row, not repeated in every record
    assert all("gate" not in rec for rec in data["records"])


def test_gate_spec_travels_in_user_properties(pytester):
    pytester.makeconftest(
        """
import json

def pytest_runtest_logreport(report):
    if report.when == "call":
        for name, value in report.user_properties:
            if name == "probability" and "gate" in value:
                json.dumps(value)  # plain data only
                print("GATE", value["gate"]["rule"], value["gate"]["runs"])
"""
    )
    pytester.makepyfile(bench_g=_gated(cases=(("solid", 40),)))
    result = pytester.runpytest(
        "--prob-runs=2", "-s", "-W", "ignore::pytest.PytestWarning"
    )
    result.stdout.fnmatch_lines(["*GATE rate 2*"])


def test_xdist_gate_parity(pytester):
    pytest.importorskip("xdist")
    pytester.makepyfile(
        bench_g=BENCH_GATED,
        bench_e=BENCH_GATED_ERRORS.replace("bench_shaky", "bench_shaky2"),
        bench_count=_gated(
            "min_passes=19, runs=20", (("nineteen", 19), ("eighteen", 18))
        ).replace("bench_classify", "bench_counted"),
        bench_cls=BENCH_COUNTS,
    )
    pytester.makeini("[pytest]\nprob_errors = exclude\n")
    serial = _run(pytester, "--prob-runs=40", "--prob-json=serial.json")
    dist = _run(
        pytester,
        "--prob-runs=40",
        "--prob-json=dist.json",
        "-n",
        "4",
        "--dist",
        "loadfile",
    )
    assert serial.ret == dist.ret == pytest.ExitCode.TESTS_FAILED
    assert serial.parseoutcomes() == dist.parseoutcomes()
    assert sorted(_gate_rows(serial)) == sorted(_gate_rows(dist))
    assert _tally(serial) == _tally(dist)
    key = lambda r: r["case"]  # noqa: E731
    s, d = _json(pytester, "serial.json"), _json(pytester, "dist.json")
    assert [r["gate"] for r in sorted(s["rows"], key=key)] == [
        r["gate"] for r in sorted(d["rows"], key=key)
    ]
    assert s["exit_status"] == d["exit_status"] == 1


def test_ungated_suite_unchanged_by_gate_options(pytester):
    pytester.makepyfile(bench_cls=BENCH_COUNTS, bench_flaky=BENCH_FLAKY)
    base = _run(pytester, "--prob-runs=10", "-p", "no:cacheprovider")
    with_opts = _run(
        pytester,
        "--prob-runs=10",
        "-p",
        "no:cacheprovider",
        "--prob-undecided=pass",
        "-o",
        "prob_errors=exclude",
    )
    strip = lambda r: [ln for ln in r.stdout.lines if " in " not in ln]  # noqa: E731
    assert strip(base) == strip(with_opts)
    assert base.ret == with_opts.ret == pytest.ExitCode.TESTS_FAILED
    assert "Gates:" not in base.stdout.str()
    assert "xfail" not in base.stdout.str()


def test_regular_suite_unchanged_by_global_gate(pytester):
    pytester.makepyfile(test_plain=TEST_PLAIN)
    base = _run(pytester, "-p", "no:cacheprovider")
    gated = _run(pytester, "-p", "no:cacheprovider", "--prob-min-rate=0.9")
    strip = lambda r: [ln for ln in r.stdout.lines if " in " not in ln]  # noqa: E731
    assert strip(base) == strip(gated)
    assert gated.ret == pytest.ExitCode.OK
