"""Verdict items (#13): ``…::case[gate]``, ``…::bench_fn[compare:ARM]``
and ``…::bench_fn[baseline]`` carry each verdict into ``-v``, JUnit XML
and ``--lf``.

The claim under test: a verdict item passes or fails with the verdict
the summary prints, runs after every run it needs (whatever reorders
them), never judges a sample it can't see — it skips instead — and
leaves ungated suites alone.
"""

import json
import re
import xml.etree.ElementTree as ET

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# The i-th run of a case follows its pattern, repeated: P passes, F
# fails an assert, E raises, S calls pytest.skip. Counters are per
# process, which is why xdist needs every run of a case on one worker.
_HEAD = '''
import pytest

_calls = {}


def _step(key, pattern):
    i = _calls[key] = _calls.get(key, 0) + 1
    step = pattern[(i - 1) % len(pattern)]
    if step == "E":
        raise RuntimeError("boom")
    if step == "S":
        pytest.skip("not today")
    assert step == "P", f"wrong answer for {key}"
'''


def _function(name, cases, mark=None, extra=""):
    """A bench function over ``cases``: (id, pattern) pairs."""
    params = "\n".join(f'    pytest.param("{c}", "{p}", id="{c}"),' for c, p in cases)
    marks = f"@pytest.mark.probability({mark})\n" if mark else ""
    return f"""

{marks}{extra}@pytest.mark.parametrize("case,pattern", [
{params}
])
def bench_{name}(case, pattern):
    _step(("{name}", case), pattern)
"""


def _bench(*functions):
    return _HEAD + "".join(functions)


# min_rate=0.9 over 10 runs (exact, 95%) can't PASS, so judge at 0.5:
# PASS needs 9 of 10 and FAIL is at most 1. Few failing runs: pytest
# takes a while to report each one.
CLASSIFY = _function(
    "classify",
    [("solid", "P"), ("half", "PF"), ("weak", "PFFFFFFFFF")],
    "min_rate=0.5, runs=10",
)
SMOKE_FN = """

@pytest.mark.probability(min_passes=2, runs=3)
def bench_smoke():
    _step(("smoke", ""), "P")
"""
UNGATED = _function("plain", [("a", "P"), ("b", "P")], "runs=2")


def _run(pytester, *args):
    return pytester.runpytest(
        "--tb=short", "-p", "no:cacheprovider", "-W", "ignore::pytest.PytestWarning",
        *args,
    )


def _items(result, prefix="bench_s.py::"):
    """The ``-v`` result lines, as ``(id, outcome)``."""
    out = []
    for line in result.stdout.lines:
        if line.startswith(prefix):
            parts = line.split()
            out.append((parts[0], parts[1]))
    return out


def _collected(result):
    return [ln.strip() for ln in result.stdout.lines if ln.startswith("bench_s.py::")]


def _json(pytester, name="r.json"):
    return json.loads((pytester.path / name).read_text())


def _verdicts(data):
    return {r["case"]: r["gate"]["verdict"] for r in data["rows"] if r["gate"]}


def _section(result, title):
    lines = result.stdout.lines
    start = next(i for i, ln in enumerate(lines) if f"= {title} =" in ln)
    out = []
    for ln in lines[start + 1 :]:
        if not ln.strip() or ln.startswith("="):
            break
        out.append(ln)
    return out


# ---------------------------------------------------------------------------
# Ids and order
# ---------------------------------------------------------------------------


def test_ids_follow_each_case_last_run(pytester):
    pytester.makepyfile(bench_s=_bench(CLASSIFY, SMOKE_FN, UNGATED))
    result = _run(pytester, "--collect-only", "-q")
    ids = _collected(result)
    gates = [i for i in ids if "gate" in i.split("::")[-1]]
    assert gates == [
        "bench_s.py::bench_classify::solid[gate]",
        "bench_s.py::bench_classify::half[gate]",
        "bench_s.py::bench_classify::weak[gate]",
        "bench_s.py::bench_smoke::gate",
    ]
    for gate in gates:
        case_prefix = gate.removesuffix("[gate]").removesuffix("gate")
        runs = [n for n, i in enumerate(ids) if i.startswith(case_prefix) and i != gate]
        assert ids.index(gate) == max(runs) + 1
    # ungated cases get none
    assert not any("plain" in i and "gate" in i for i in ids)
    assert len(ids) == 30 + 3 + 4 + 4


def test_single_run_case_gets_its_gate_after_it(pytester):
    pytester.makepyfile(
        bench_s=_bench(_function("one", [("x", "P")], "min_passes=1"))
    )
    result = _run(pytester, "--collect-only", "-q")
    assert _collected(result) == [
        "bench_s.py::bench_one::x",
        "bench_s.py::bench_one::x[gate]",
    ]


def test_transpose_puts_each_gate_after_its_last_round(pytester):
    pytester.makepyfile(
        bench_s=_bench(
            _function(
                "mix",
                [
                    ("short", "P"),
                    ("long", "P"),
                ],
                "min_passes=1, runs=2",
            )
        )
    )
    result = _run(pytester, "--collect-only", "-q", "--prob-transpose")
    assert _collected(result) == [
        "bench_s.py::bench_mix::short[run1]",
        "bench_s.py::bench_mix::long[run1]",
        "bench_s.py::bench_mix::short[run2]",
        "bench_s.py::bench_mix::short[gate]",
        "bench_s.py::bench_mix::long[run2]",
        "bench_s.py::bench_mix::long[gate]",
    ]


def test_reordered_items_keep_gates_after_their_runs(pytester):
    # A plugin that reverses the order, as a shuffling one might.
    pytester.makeconftest(
        """
import pytest

@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(items):
    items.reverse()
"""
    )
    pytester.makepyfile(bench_s=_bench(CLASSIFY))
    result = _run(pytester, "-v")
    items = _items(result)
    names = [i for i, _ in items]
    for case in ("solid", "half", "weak"):
        gate = names.index(f"bench_s.py::bench_classify::{case}[gate]")
        runs = [n for n, i in enumerate(names) if f"::{case}[run" in i]
        assert len(runs) == 10 and gate > max(runs)
    # and they judge every run, as in collection order
    assert dict(items)["bench_s.py::bench_classify::solid[gate]"] == "PASSED"
    assert dict(items)["bench_s.py::bench_classify::weak[gate]"] == "FAILED"


# ---------------------------------------------------------------------------
# Verdicts and messages
# ---------------------------------------------------------------------------


def test_items_pass_and_fail_with_the_verdicts(pytester):
    pytester.makepyfile(bench_s=_bench(CLASSIFY, SMOKE_FN))
    result = _run(pytester, "-v", "--prob-json=r.json")
    outcomes = {i: o for i, o in _items(result) if "gate" in i}
    assert outcomes == {
        "bench_s.py::bench_classify::solid[gate]": "PASSED",
        "bench_s.py::bench_classify::half[gate]": "FAILED",
        "bench_s.py::bench_classify::weak[gate]": "FAILED",
        "bench_s.py::bench_smoke::gate": "PASSED",
    }
    # the same verdicts as the summary's, and one failed item per
    # failing verdict: 2 failed, like the gates block's two lines
    assert _verdicts(_json(pytester)) == {
        "classify::solid": "pass",
        "classify::half": "undecided",
        "classify::weak": "fail",
        "smoke": "pass",
    }
    assert len(_section(result, "probability: gates")) == 2
    result.assert_outcomes(passed=10 + 5 + 1 + 3 + 2, failed=2, xfailed=14)
    assert result.ret == pytest.ExitCode.TESTS_FAILED


def test_failure_message_names_interval_bar_and_verdict(pytester):
    pytester.makepyfile(bench_s=_bench(CLASSIFY))
    result = _run(pytester, "-rf", "-vv")
    out = result.stdout.str()
    # the header, the gates block's line, then the plain-language reading
    result.stdout.fnmatch_lines(
        [
            "*_ gate: classify::weak _*",
            "classify::weak  1/10  [1%, 45%]  ≥50%  FAIL",
            "1 of 10 runs passed. The true pass rate is probably between 1% and"
            " 45%*",
            "Next: look at the failing runs*",
        ]
    )
    result.stdout.fnmatch_lines(
        [
            "*_ gate: classify::half _*",
            "classify::half  5/10  [19%, 81%]  ≥50%  UNDECIDED",
            "*Until that's settled, the case fails the test session*",
        ]
    )
    # the short summary carries the verdict's line (pytest < 8 truncates
    # it even under -vv), no traceback
    result.stdout.fnmatch_lines(
        [
            "FAILED bench_s.py::bench_classify::half[[]gate[]] - classify::half"
            "  5/10  [[]19%*",
            "FAILED bench_s.py::bench_classify::weak[[]gate[]] - classify::weak"
            "  1/10  [[]1%*",
        ]
    )
    assert "plugin.py" not in out
    # the gates block lists the same two lines
    assert [re.sub(r"\s+", " ", ln.strip()) for ln in _section(
        result, "probability: gates"
    )] == [
        "classify::half 5/10 [19%, 81%] ≥50% UNDECIDED",
        "classify::weak 1/10 [ 1%, 45%] ≥50% FAIL",
    ]


def test_undecided_allowed_passes(pytester):
    pytester.makepyfile(bench_s=_bench(CLASSIFY))
    result = _run(pytester, "-v", "--prob-undecided=pass")
    outcomes = dict(_items(result))
    assert outcomes["bench_s.py::bench_classify::half[gate]"] == "PASSED"
    assert outcomes["bench_s.py::bench_classify::weak[gate]"] == "FAILED"
    result.assert_outcomes(passed=10 + 5 + 1 + 2, failed=1, xfailed=14)


def test_latency_gate_and_rate_gate_share_the_case_item(pytester):
    # Any measurable run is slower than a nanosecond: the latency gate
    # FAILs once the p50's lower bound exists; the rate gate PASSes.
    pytester.makepyfile(
        bench_s=_bench(
            _function(
                "api",
                [("slow", "P")],
                "min_rate=0.5, runs=10, latency_quantile=0.5, max_latency=1e-9",
            )
        )
    )
    result = _run(pytester, "-v")
    assert dict(_items(result))["bench_s.py::bench_api::slow[gate]"] == "FAILED"
    text = result.stdout.str()
    # the failing latency verdict first, then the passing rate verdict
    assert re.search(r"\napi::slow  10 runs  p50 .*≤1e-09s  FAIL\n", text)
    assert "\napi::slow  10/10  [69%, 100%]  ≥50%  PASS\n" in text


def test_gated_case_skipped_by_mark_skips_its_gate(pytester):
    pytester.makepyfile(
        bench_s=_HEAD
        + """

@pytest.mark.probability(min_passes=1, runs=2)
@pytest.mark.parametrize("case", [
    pytest.param("on", id="on"),
    pytest.param("off", id="off", marks=pytest.mark.skip(reason="later")),
])
def bench_maybe(case):
    _step(("maybe", case), "P")
"""
    )
    result = _run(pytester, "-v")
    outcomes = dict(_items(result))
    assert outcomes["bench_s.py::bench_maybe::on[gate]"] == "PASSED"
    assert outcomes["bench_s.py::bench_maybe::off[gate]"] == "SKIPPED"
    assert result.ret == pytest.ExitCode.OK


def test_all_runs_skipping_themselves_leave_nothing_to_judge(pytester):
    pytester.makepyfile(
        bench_s=_bench(_function("gone", [("x", "S")], "min_passes=1, runs=2"))
    )
    result = _run(pytester, "-rs")
    result.assert_outcomes(skipped=3)
    result.stdout.fnmatch_lines(
        ["SKIPPED [[]1[]] bench_s.py:1: probability: no runs of gone::x were recorded"]
    )
    assert result.ret == pytest.ExitCode.OK


# ---------------------------------------------------------------------------
# JUnit XML and --lf
# ---------------------------------------------------------------------------


def test_junit_xml_records_the_failed_gate(pytester):
    pytester.makepyfile(bench_s=_bench(CLASSIFY))
    _run(pytester, "--junitxml=j.xml")
    suite = ET.parse(pytester.path / "j.xml").getroot().find("testsuite")
    assert suite.get("failures") == "2"
    cases = {c.get("name"): c for c in suite.iter("testcase")}
    weak = cases["weak[gate]"]
    assert weak.get("classname") == "bench_s.bench_classify"
    failure = weak.find("failure")
    assert failure.get("message") == "classify::weak  1/10  [1%, 45%]  ≥50%  FAIL"
    assert failure.text.startswith("classify::weak  1/10  [1%, 45%]  ≥50%  FAIL\n")
    assert "The true pass rate is probably" in failure.text
    (prop,) = weak.find("properties")
    assert (prop.get("name"), prop.get("value")) == (
        "probability_verdict",
        "classify::weak  1/10  [1%, 45%]  ≥50%  FAIL",
    )
    # a passing gate is a passing testcase, with its verdict as a property
    solid = cases["solid[gate]"]
    assert solid.find("failure") is None
    assert solid.find("properties")[0].get("value").endswith("PASS")
    # failing runs of a gated case stay skipped (xfailed) testcases
    assert cases["weak[run2]"].find("skipped").get("type") == "pytest.xfail"


def test_lf_reruns_failed_gates_with_their_runs(pytester):
    pytester.makepyfile(bench_s=_bench(CLASSIFY, SMOKE_FN))
    first = pytester.runpytest("--tb=no", "-q")
    assert first.ret == pytest.ExitCode.TESTS_FAILED
    result = pytester.runpytest("--tb=no", "--lf", "-v")
    result.stdout.fnmatch_lines(
        [
            "*collected 37 items / 15 deselected / 22 selected",
            "run-last-failure: rerun previous 22 failures",
            "probability: rerunning 20 runs with the 2 failed verdict items that"
            " need them",
        ]
    )
    names = [i for i, _ in _items(result)]
    assert names[-1] == "bench_s.py::bench_classify::weak[gate]"
    assert names[10] == "bench_s.py::bench_classify::half[gate]"
    assert all("half" in n or "weak" in n for n in names)
    # judged on all their runs again: the same verdicts
    result.assert_outcomes(passed=6, failed=2, xfailed=14)
    assert len(_section(result, "probability")) == 2

    # once the gates pass, --lf has nothing left to rerun
    fixed = _function(
        "classify", [("solid", "P"), ("half", "P"), ("weak", "P")],
        "min_rate=0.5, runs=10",
    )
    pytester.makepyfile(bench_s=_bench(fixed, SMOKE_FN))
    fixed = pytester.runpytest("--tb=no", "--lf", "-q")
    assert fixed.ret == pytest.ExitCode.OK
    again = pytester.runpytest("--tb=no", "--lf")
    again.stdout.fnmatch_lines(["*no previously failed tests*"])


def test_ff_runs_failed_gates_first_after_their_runs(pytester):
    pytester.makepyfile(bench_s=_bench(CLASSIFY))
    pytester.runpytest("--tb=no", "-q")
    result = pytester.runpytest("--tb=no", "--ff", "-v")
    names = [i for i, _ in _items(result)]
    # half and weak first, each gate after its runs; then solid
    assert names[10] == "bench_s.py::bench_classify::half[gate]"
    assert names[21] == "bench_s.py::bench_classify::weak[gate]"
    assert names[-1] == "bench_s.py::bench_classify::solid[gate]"
    result.assert_outcomes(passed=17, failed=2, xfailed=14)


def test_lf_keeps_real_failures_with_the_gates(pytester):
    # an errored run is a failure of its own, alongside its case's gate
    pytester.makepyfile(
        bench_s=_bench(_function("api", [("flaky", "PPEP")], "min_rate=0.9, runs=4"))
    )
    pytester.runpytest("--tb=no", "-q")
    result = pytester.runpytest("--tb=no", "--lf", "-q")
    result.stdout.fnmatch_lines(
        ["probability: rerunning 3 runs with the 1 failed verdict item that needs them"]
    )
    result.assert_outcomes(passed=3, failed=2)


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------


def test_k_selecting_a_gate_alone_brings_its_runs(pytester):
    pytester.makepyfile(bench_s=_bench(CLASSIFY))
    result = _run(pytester, "-v", "-k", "weak and gate")
    result.stdout.fnmatch_lines(
        [
            "*collected 33 items / 22 deselected / 11 selected",
            "probability: selected 10 runs for 1 verdict item selected without them",
        ]
    )
    names = [i for i, _ in _items(result)]
    weak = "bench_s.py::bench_classify::weak"
    assert names == [f"{weak}[run{n}]" for n in range(1, 11)] + [f"{weak}[gate]"]
    result.assert_outcomes(passed=1, failed=1, xfailed=9)
    result.stdout.fnmatch_lines(
        ["*= 1 failed, 1 passed, 22 deselected, 9 xfailed in *"]
    )


def test_k_without_gates_judges_at_session_end(pytester):
    pytester.makepyfile(bench_s=_bench(CLASSIFY))
    result = _run(pytester, "-k", "not gate")
    result.assert_outcomes(passed=16, xfailed=14)
    # the verdicts still decide the exit status
    assert result.ret == pytest.ExitCode.TESTS_FAILED


def test_partial_selection_is_judged_on_the_runs_that_ran(pytester):
    # Deselecting some runs leaves the gate selected: it judges what ran,
    # like the summary.
    pytester.makepyfile(bench_s=_bench(CLASSIFY))
    result = _run(
        pytester, "-v", "--deselect", "bench_s.py::bench_classify::solid[run10]",
        "--prob-json=r.json",
    )
    outcomes = dict(_items(result))
    assert outcomes["bench_s.py::bench_classify::solid[gate]"] == "PASSED"
    assert "bench_s.py::bench_classify::solid[run10]" not in outcomes
    rows = {r["case"]: r for r in _json(pytester)["rows"]}
    assert rows["classify::solid"]["total"] == 9


# ---------------------------------------------------------------------------
# pytest-xdist
# ---------------------------------------------------------------------------


def test_gate_items_xdist_loadgroup_parity(pytester):
    pytest.importorskip("xdist")
    pytester.makepyfile(bench_s=_bench(CLASSIFY, SMOKE_FN, UNGATED))
    serial = _run(pytester, "--prob-json=serial.json", "-v")
    dist = _run(
        pytester, "--prob-json=dist.json", "-n", "2", "--dist", "loadgroup", "-v"
    )
    assert serial.parseoutcomes() == dist.parseoutcomes()
    assert serial.ret == dist.ret == pytest.ExitCode.TESTS_FAILED
    assert _verdicts(_json(pytester, "serial.json")) == _verdicts(
        _json(pytester, "dist.json")
    )
    # each gated case and its gate item are one group: one worker
    out = dist.stdout.str()
    assert "bench_s.py::bench_classify::weak[gate]@classify::weak" in out
    assert "bench_s.py::bench_classify::weak[run3]@classify::weak" in out
    assert "bench_s.py::bench_smoke::gate@smoke" in out
    # ungated cases are not grouped
    assert "bench_s.py::bench_plain::a[run1]" in out
    assert "bench_s.py::bench_plain::a[run1]@" not in out
    gate_rows = lambda r: sorted(_section(r, "probability: gates"))  # noqa: E731
    assert gate_rows(serial) == gate_rows(dist)


def test_lf_under_xdist_loadgroup(pytester):
    pytest.importorskip("xdist")
    pytester.makepyfile(bench_s=_bench(CLASSIFY))
    args = ("--tb=no", "-q", "-n", "2", "--dist", "loadgroup")
    pytester.runpytest(*args).assert_outcomes(passed=17, failed=2, xfailed=14)
    # the ids carry their @group, and so do the runs brought back
    result = pytester.runpytest(*args, "--lf")
    result.assert_outcomes(passed=6, failed=2, xfailed=14)


def test_gate_items_xdist_other_dist_skip_and_judge_at_session_end(pytester):
    pytest.importorskip("xdist")
    pytester.makepyfile(bench_s=_bench(CLASSIFY))
    # loadfile keeps the per-process patterns deterministic; any --dist
    # but loadgroup skips the gate items
    result = _run(
        pytester, "-n", "2", "--dist", "loadfile", "-rs", "--prob-json=r.json"
    )
    result.assert_outcomes(passed=16, skipped=3, xfailed=14)
    result.stdout.fnmatch_lines(
        [
            "SKIPPED [[]3[]] bench_s.py:1: probability: judged at session end;"
            " under pytest-xdist a verdict item needs --dist loadgroup to run on"
            " the worker that ran its runs",
        ]
    )
    # the session still fails on the verdicts, as without gate items
    assert result.ret == pytest.ExitCode.TESTS_FAILED
    assert _verdicts(_json(pytester))["classify::weak"] == "fail"
    # no groups are added
    assert "@classify" not in result.stdout.str()


# ---------------------------------------------------------------------------
# Curtailment
# ---------------------------------------------------------------------------


def test_curtailed_case_gate_judges_the_runs_that_ran(pytester):
    pytester.makepyfile(
        bench_s=_bench(_function("cur", [("down", "F")], "min_rate=0.5, runs=10"))
    )
    result = _run(pytester, "-v", "--prob-stop=curtail")
    items = _items(result)
    # down FAILs after 9 fails: its tenth run is skipped, then its gate
    # judges the nine
    assert items[-2:] == [
        ("bench_s.py::bench_cur::down[run10]", "SKIPPED"),
        ("bench_s.py::bench_cur::down[gate]", "FAILED"),
    ]
    result.stdout.fnmatch_lines(
        ["cur::down  0/9  [[]0%, 34%[]]  ≥50%  FAIL  decided after 9/10"]
    )
    result.assert_outcomes(failed=1, skipped=1, xfailed=9)


def test_curtailment_under_loadgroup_with_gate_items(pytester):
    pytest.importorskip("xdist")
    pytester.makepyfile(bench_s=_bench(CLASSIFY))
    serial = _run(pytester, "--prob-stop=curtail")
    dist = _run(pytester, "--prob-stop=curtail", "-n", "2", "--dist", "loadgroup")
    assert serial.parseoutcomes() == dist.parseoutcomes()
    assert _section(serial, "probability: gates") == _section(
        dist, "probability: gates"
    )


# ---------------------------------------------------------------------------
# Margin items
# ---------------------------------------------------------------------------

# Inputs 0-11; the arms pass on these inputs (one run each).
_PAIRED = '''
import pytest

PASS = {
    "alpha": lambda i: True,
    "same": lambda i: True,
    "never": lambda i: False,
}


@pytest.mark.probability(compare="arm", margin=0.05)
@pytest.mark.parametrize("arm", ["alpha", "same", "never"])
@pytest.mark.parametrize("i", range(12))
def bench_pair(i, arm):
    assert PASS[arm](i)
'''


def test_compare_items_per_arm(pytester):
    pytester.makepyfile(bench_s=_PAIRED)
    result = _run(pytester, "-v", "--prob-undecided=pass")
    items = _items(result)
    names = [i for i, _ in items]
    assert names[-2:] == [
        "bench_s.py::bench_pair[compare:same]",
        "bench_s.py::bench_pair[compare:never]",
    ]
    assert dict(items)["bench_s.py::bench_pair[compare:same]"] == "PASSED"
    assert dict(items)["bench_s.py::bench_pair[compare:never]"] == "FAILED"
    result.stdout.fnmatch_lines(
        [
            "*_ compare: pair never _*",
            "pair[[]arm[]]  never − alpha  −100.0 pp [[]−100.0, −100.0[]]  12 paired"
            "  ≥−5 pp  FAIL",
            "Your margin is 5 points: never passes if it is at most 5 points worse"
            " than alpha*",
        ]
    )
    assert result.ret == pytest.ExitCode.TESTS_FAILED
    # the comparisons block has the same verdicts
    block = [ln for ln in _section(result, "probability: comparisons") if "pair[" in ln]
    assert [ln.split()[-1] for ln in block] == ["PASS", "FAIL"]


def test_compare_item_selected_alone_brings_the_function(pytester):
    pytester.makepyfile(bench_s=_PAIRED)
    result = _run(pytester, "-v", "-k", "compare and same")
    names = [i for i, _ in _items(result)]
    assert len(names) == 36 + 1
    assert names[-1] == "bench_s.py::bench_pair[compare:same]"
    assert dict(_items(result))[names[-1]] == "PASSED"


def test_compare_items_xdist_loadgroup_group_the_function(pytester):
    pytest.importorskip("xdist")
    pytester.makepyfile(bench_s=_PAIRED)
    serial = _run(pytester)
    dist = _run(pytester, "-n", "2", "--dist", "loadgroup", "-v")
    assert serial.parseoutcomes() == dist.parseoutcomes()
    out = dist.stdout.str()
    assert "bench_s.py::bench_pair[compare:never]@pair" in out
    assert "bench_s.py::bench_pair::3-same@pair" in out


def _baseline(pytester, rows):
    (pytester.path / "main.json").write_text(json.dumps({"rows": rows}))


def test_baseline_item(pytester):
    pytester.makepyfile(
        bench_s=_bench(
            _function("pair", [(str(i), "F" if i < 4 else "P") for i in range(12)]),
            _function("other", [("x", "P")]),
        )
    )
    _baseline(
        pytester,
        [{"case": f"pair::{i}", "passes": 1, "total": 1} for i in range(12)],
    )
    result = _run(pytester, "-v", "--prob-baseline=main.json", "--prob-margin=0.02")
    outcomes = dict(_items(result))
    assert outcomes["bench_s.py::bench_pair[baseline]"] == "FAILED"
    # a function with no case in the baseline has nothing to judge
    assert "bench_s.py::bench_other[baseline]" not in outcomes
    result.stdout.fnmatch_lines(
        [
            "*_ baseline: pair _*",
            "pair  current − baseline  −33.3 pp*  12 paired  ≥−2 pp  FAIL",
            "Your margin (--prob-margin) is 2 points*",
        ]
    )
    # without --prob-margin a baseline only reports: no item
    result = _run(pytester, "--collect-only", "-q", "--prob-baseline=main.json")
    assert not any("[baseline]" in ln for ln in result.stdout.lines)


# ---------------------------------------------------------------------------
# Off switch, ungated suites, --prob-plan
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "how", [["--prob-no-gate-items"], ["-o", "prob_gate_items=false"]]
)
def test_off_switch(pytester, how):
    pytester.makepyfile(bench_s=_bench(CLASSIFY, SMOKE_FN) + _PAIRED.split("\n", 2)[2])
    result = _run(pytester, *how, "--collect-only", "-q")
    assert not any(
        "gate" in ln or "compare:" in ln for ln in _collected(result)
    )
    result = _run(pytester, *how, "--prob-undecided=pass")
    # verdicts at session end, as in #3: the runs alone in the counts
    result.assert_outcomes(passed=16 + 3 + 24, xfailed=14 + 12)
    assert result.ret == pytest.ExitCode.TESTS_FAILED


def test_ungated_suite_is_unchanged(pytester):
    pytester.makepyfile(bench_s=_bench(UNGATED))
    on = _run(pytester, "-v")
    off = _run(pytester, "-v", "--prob-no-gate-items")
    strip = lambda r: [  # noqa: E731
        ln for ln in r.stdout.lines if not re.search(r" in \d+\.\d+s", ln)
        and not ln.startswith(("rootdir", "platform", "plugins", "cachedir"))
    ]
    assert strip(on) == strip(off)
    on.assert_outcomes(passed=4)


def test_plan_counts_no_gate_items(pytester):
    pytester.makepyfile(bench_s=_bench(CLASSIFY, SMOKE_FN))
    on = _run(pytester, "--prob-plan")
    off = _run(pytester, "--prob-plan", "--prob-no-gate-items")
    assert "collected 33 items" in on.stdout.str()
    assert _section(on, "probability: plan") == _section(off, "probability: plan")
    assert on.ret == pytest.ExitCode.OK
