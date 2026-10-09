"""Early stopping by curtailment (--prob-stop=curtail).

The claim under test is exactness: a stopped case gets the verdict all
its planned runs would have given. ``Gate.settled`` is checked against
every way the remaining runs could go, and whole sessions against the
same sessions run in full.
"""

import functools
import itertools
import json

import pytest

from pytest_probability.plugin import (
    FAIL,
    PASS,
    UNDECIDED,
    CaseStats,
    Gate,
    StatsConfig,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# The i-th run of a case follows its pattern, repeated: P passes, F
# fails an assert, E raises, S calls pytest.skip. Counters are per
# process, which is why xdist needs every run of a case on one worker.
_BODY = """
_calls = {{}}


def _step(key, pattern):
    i = _calls[key] = _calls.get(key, 0) + 1
    step = pattern[(i - 1) % len(pattern)]
    record_cost(0.001)
    if step == "E":
        raise RuntimeError("boom")
    if step == "S":
        pytest.skip("not today")
    assert step == "P", f"wrong answer for {{key}}"
"""

_HEAD = "import pytest\nfrom pytest_probability import record_cost\n"


def _function(name, cases, mark=None, extra=""):
    """A bench function over ``cases``: (id, pattern) pairs."""
    params = "\n".join(
        f'    pytest.param("{c}", "{p}", id="{c}"),' for c, p in cases
    )
    marks = f"@pytest.mark.probability({mark})\n" if mark else ""
    return f"""

{marks}{extra}@pytest.mark.parametrize("case,pattern", [
{params}
])
def bench_{name}(case, pattern):
    _step(("{name}", case), pattern)
"""


def _bench(*functions):
    return _HEAD + _BODY.format() + "".join(functions)


# Every verdict over 10 runs, with early and late failures, errors and
# skips — and few failing runs, since pytest takes a while to report
# each one. At min_rate=0.9 (exact, 95%) 10 runs can't PASS and FAIL
# is at most 6 passes; at 0.5, PASS is at least 9 and FAIL at most 1.
STRICT_CASES = [
    ("solid", "P"),  # UNDECIDED once 7 passes are in
    ("down", "FFFFPPPPPP"),  # FAIL after 4 fails
    ("late", "PPPPPPFFFF"),  # FAIL, but only on the last run
    ("errs", "PEPPPPPPPP"),
    ("skips", "SPPPPPPPPP"),
]
LOOSE_CASES = [
    ("solid", "P"),  # PASS after 9
    ("one_off", "PPPPFPPPPP"),  # PASS, on the last run
    ("half", "PF"),  # UNDECIDED after 4
]
COUNT_CASES = [
    ("steady", "P"),  # PASS after 9 of 9 needed
    ("broken", "FFPPPPPPPP"),  # FAIL after 2 fails
    ("flaky_err", "PPPPPEPPPP"),
    ("skippy", "SPPPPPPPPP"),
]
PATTERNS = {
    f"{fn}::{case}": pattern
    for fn, cases in (
        ("strict", STRICT_CASES), ("loose", LOOSE_CASES), ("count", COUNT_CASES)
    )
    for case, pattern in cases
}


def _suite():
    return _bench(
        _function("strict", STRICT_CASES, "min_rate=0.9, runs=10"),
        _function("loose", LOOSE_CASES, "min_rate=0.5, runs=10"),
        _function("count", COUNT_CASES, "min_passes=9, runs=10"),
    )


def _run(pytester, *args):
    return pytester.runpytest(
        "--tb=no", "-p", "no:cacheprovider", "-W", "ignore::pytest.PytestWarning",
        *args,
    )


def _json(pytester, name="r.json"):
    return json.loads((pytester.path / name).read_text())


def _verdicts(data):
    return {r["case"]: r["gate"]["verdict"] for r in data["rows"] if r["gate"]}


def _section(result, title):
    lines = result.stdout.lines
    start = next(i for i, ln in enumerate(lines) if f"= {title} =" in ln)
    out = []
    for ln in lines[start + 1 :]:
        if ln.startswith("=") or not ln.strip():
            break
        out.append(ln)
    return out


def _line(result, prefix):
    return next(ln for ln in result.stdout.lines if ln.startswith(prefix))


def _prefix_counts(pattern, runs):
    """(passes, fails, errors) of a pattern's first ``runs`` runs."""
    steps = [pattern[i % len(pattern)] for i in range(runs)]
    return steps.count("P"), steps.count("F"), steps.count("E")


# ---------------------------------------------------------------------------
# The rule: Gate.settled against every completion
# ---------------------------------------------------------------------------


def _compositions(n, k):
    if k == 1:
        yield (n,)
        return
    for i in range(n + 1):
        for rest in _compositions(n - i, k - 1):
            yield (i, *rest)


GATES = [
    Gate(rule, StatsConfig(method=method, level=level, prior=prior), errors=errors,
         **({"min_rate": bar} if rule == "rate" else {"min_passes": bar}))
    for method, prior in (
        ("exact", (1.0, 1.0)),
        ("wilson", (1.0, 1.0)),
        ("bayes", (1.0, 1.0)),
        # A prior that can make 0 of n PASS a low bar: the "no runs left
        # to judge" corner has to be handled, not assumed away.
        ("bayes", (3.0, 0.2)),
    )
    for level in (0.8, 0.95)
    for errors in ("count", "exclude")
    for rule, bars in (("rate", (0.01, 0.5, 0.85)), ("count", (1, 4, 9)))
    for bar in bars
]


@pytest.mark.parametrize("planned", [2, 5, 8])
def test_settled_matches_every_completion(planned):
    # For every way the runs so far can have gone and every way the
    # rest could go (pass, fail, error, or no sample): when settled()
    # names a verdict, every completion ends on it and so do the runs so
    # far; when it doesn't, at least two verdicts are still possible —
    # it stops as soon as it can, and never sooner.
    stops = 0
    for gate in GATES:

        @functools.lru_cache(maxsize=None)
        def final(p, f, e, gate=gate):
            return gate.evaluate(CaseStats("c", p, f, e)).verdict

        for seen in range(1, planned):
            left = planned - seen
            completions = list(_compositions(left, 4))
            for p, f, e, _skips in _compositions(seen, 4):
                got = gate.settled(p, f, e, left)
                if p + f + e == 0:
                    assert got is None  # no row yet: never stop
                    continue
                reachable = {
                    final(p + p2, f + f2, e + e2) for p2, f2, e2, _ in completions
                }
                if got is None:
                    assert len(reachable) > 1, (gate, p, f, e, left)
                else:
                    stops += 1
                    assert reachable == {got}, (gate, p, f, e, left)
                    assert final(p, f, e) == got
    assert stops > 50 * planned


@pytest.mark.parametrize("method", ["exact", "wilson", "bayes"])
def test_cutoffs_match_a_scan(method):
    # The bisection relies on the verdict only improving with passes.
    for bar, total in itertools.product((0.3, 0.75, 0.95), range(0, 61, 3)):
        gate = Gate("rate", StatsConfig(method=method), min_rate=bar)
        verdicts = [gate.verdict(x, total) for x in range(total + 1)]
        pass_at = next((x for x, v in enumerate(verdicts) if v == PASS), total + 1)
        fail_at = max((x for x, v in enumerate(verdicts) if v == FAIL), default=-1)
        assert gate.cutoffs(total) == (pass_at, fail_at)
        assert all(v == UNDECIDED for v in verdicts[fail_at + 1 : pass_at])


def test_count_cutoffs():
    gate = Gate("count", StatsConfig(), min_passes=12)
    assert gate.cutoffs(20) == (12, 11)
    assert gate.cutoffs(5) == (6, 5)  # can't reach 12: everything FAILs
    assert gate.settled(12, 0, 0, 8) == PASS
    assert gate.settled(11, 0, 0, 8) is None
    assert gate.settled(3, 6, 0, 2) == FAIL  # at most 5 of 12
    assert gate.settled(0, 0, 0, 20) is None  # nothing recorded yet


# ---------------------------------------------------------------------------
# Exactness: curtailed sessions against full ones
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("transpose", [False, True], ids=["case-major", "run-major"])
@pytest.mark.parametrize("errors", ["count", "exclude"])
def test_verdicts_match_full_runs(pytester, transpose, errors):
    pytester.makepyfile(bench_s=_suite())
    args = ["-o", f"prob_errors={errors}"] + (["--prob-transpose"] if transpose else [])
    full = _run(pytester, *args, "--prob-json=full.json")
    stop = _run(pytester, *args, "--prob-json=stop.json", "--prob-stop=curtail")
    full_data, stop_data = _json(pytester, "full.json"), _json(pytester, "stop.json")
    # the same verdicts and the same exit status
    assert _verdicts(stop_data) == _verdicts(full_data)
    assert set(_verdicts(full_data).values()) == {PASS, FAIL, UNDECIDED}
    assert stop.ret == full.ret
    assert stop_data["exit_status"] == full_data["exit_status"]
    # ... from fewer runs
    stopped = {r["case"]: r for r in stop_data["rows"] if r["stopped"]}
    assert len(stopped) >= 8
    skipped = sum(r["stopped"]["skipped"] for r in stopped.values())
    assert stop_data["stopping"]["runs_skipped"] == skipped >= 20
    for case, row in stopped.items():
        info = row["stopped"]
        assert info["verdict"] == row["gate"]["verdict"]
        assert info["planned"] == 10 and info["after"] + info["skipped"] == 10
        # the runs that ran are the pattern's first ones, the rest skipped
        pattern = PATTERNS[case]
        assert (row["passes"], row["fails"], row["errors"]) == _prefix_counts(
            pattern, info["after"]
        )
        # ... and one run fewer would not have settled it
        gate = Gate.from_record(row["gate"])
        before = _prefix_counts(pattern, info["after"] - 1)
        assert gate.settled(*before, 10 - info["after"] + 1) is None
    # skipped runs are not samples: records hold only the runs that ran
    assert len(stop_data["records"]) == sum(r["total"] for r in stop_data["rows"])


@pytest.mark.parametrize("method", ["wilson", "bayes"])
def test_verdicts_match_full_runs_for_each_method(pytester, method):
    pytester.makepyfile(bench_s=_suite())
    _run(pytester, f"--prob-method={method}", "--prob-json=full.json")
    _run(pytester, f"--prob-method={method}", "--prob-json=stop.json",
         "--prob-stop=curtail")
    full, stop = _json(pytester, "full.json"), _json(pytester, "stop.json")
    assert _verdicts(stop) == _verdicts(full)
    assert stop["stopping"]["cases"] >= 8


# ---------------------------------------------------------------------------
# Each verdict, the output and the skips
# ---------------------------------------------------------------------------

# min_rate=0.9 over 40 runs (exact, 95%): PASS needs 40/40 and FAIL at
# most 31 passes, so close (37 then 3 fails) is UNDECIDED once its first
# fail lands and weak (1 in 4 fails) FAILs once 9 fails are in.
CLASSIFY = _function(
    "classify",
    [("solid", "P"), ("close", "P" * 37 + "FFF"), ("weak", "PPPF")],
    "min_rate=0.9, runs=40",
)
SMOKE = _function(
    "smoke", [("steady", "P"), ("broken", "PPFF")], "min_passes=19, runs=20"
)


def test_each_verdict_stops_with_its_runs(pytester):
    pytester.makepyfile(bench_s=_bench(CLASSIFY, SMOKE))
    result = _run(pytester, "--prob-stop=curtail", "-rs")
    assert result.ret == pytest.ExitCode.TESTS_FAILED
    # solid's and steady's [gate] items pass; close's, weak's, broken's fail
    result.assert_outcomes(passed=127, failed=3, skipped=23, xfailed=12)
    # broken (PPFF) FAILs once 2 fails leave at most 18 of 19 passes
    assert _section(result, "probability") == [
        "  classify::solid  40/40  [91%, 100%]  $0.0400",
        "  classify::close  37/38  [86%,  99%]  $0.0380  FLAKY  decided after 38/40",
        "  classify::weak   27/36  [58%,  88%]  $0.0360  FLAKY  decided after 36/40",
        "  smoke::steady    19/19  [82%, 100%]  $0.0190  decided after 19/20",
        "  smoke::broken      2/4  [ 7%,  93%]  $0.0040  FLAKY  decided after 4/20",
    ]
    assert _line(result, "  Stopped:") == (
        "  Stopped: 4 cases early, saving 23 of 160 runs (14%) and about $0.0230"
    )
    assert _line(result, "  Gates:") == "  Gates:   2 passed, 2 failed, 1 undecided"
    assert _section(result, "probability: gates") == [
        "  classify::close  37/38  [86%, 99%]  ≥90%        UNDECIDED"
        "  decided after 38/40",
        "  classify::weak   27/36  [58%, 88%]  ≥90%        FAIL  decided after 36/40",
        "  smoke::broken      2/4  [ 7%, 93%]  ≥19 passes  FAIL  decided after 4/20",
    ]
    # the skipped runs say why, at the bench file
    result.stdout.fnmatch_lines([
        "SKIPPED [[]2[]] bench_s.py: probability gate: decided after 38/40 runs"
        " (UNDECIDED)",
        "SKIPPED [[]4[]] bench_s.py: probability gate: decided after 36/40 runs"
        " (FAIL)",
        "SKIPPED [[]1[]] bench_s.py: probability gate: decided after 19/20 runs"
        " (PASS)",
        "SKIPPED [[]16[]] bench_s.py: probability gate: decided after 4/20 runs"
        " (FAIL)",
    ])
    assert "Run with --prob-explain" in result.stdout.str()


def test_skipped_runs_are_reported_per_item(pytester):
    pytester.makepyfile(bench_s=_bench(SMOKE))
    result = _run(pytester, "--prob-stop=curtail", "-v")
    # (-v shortens the reason to fit; -rs prints it whole)
    result.stdout.fnmatch_lines([
        "*bench_smoke::steady[[]run19[]] PASSED*",
        "*bench_smoke::steady[[]run20[]] SKIPPED (probability gate: de*",
        "*bench_smoke::broken[[]run4[]] XFAIL*",
        "*bench_smoke::broken[[]run5[]] SKIPPED*",
    ])


def test_errors_count_or_leave_the_sample(pytester):
    # Every run errors. Counted, errors are non-passes: the rate gate
    # FAILs once at most 1 pass of 10 is left (after 9 runs). Excluded,
    # they leave nothing to judge: once 5 runs or fewer remain, 5 of 5
    # couldn't PASS at 0.5 nor 0 of 5 FAIL, so it stays UNDECIDED.
    bench = _bench(
        _function("rate", [("down", "E")], "min_rate=0.5, runs=10"),
        _function("count", [("down", "E")], "min_passes=3, runs=6"),
    )
    pytester.makepyfile(bench_s=bench)
    counted = _run(pytester, "--prob-stop=curtail", "--prob-json=r.json")
    rows = {r["case"]: r for r in _json(pytester)["rows"]}
    assert rows["rate::down"]["stopped"]["after"] == 9
    assert rows["rate::down"]["gate"]["verdict"] == FAIL
    assert rows["count::down"]["stopped"]["after"] == 4  # 2 left < 3 needed
    assert counted.ret == pytest.ExitCode.TESTS_FAILED  # errors still fail

    excluded = _run(
        pytester, "--prob-stop=curtail", "--prob-json=r.json",
        "-o", "prob_errors=exclude",
    )
    rows = {r["case"]: r for r in _json(pytester)["rows"]}
    assert rows["rate::down"]["stopped"]["after"] == 5
    assert rows["rate::down"]["gate"]["verdict"] == UNDECIDED
    assert rows["rate::down"]["gate"]["total"] == 0
    assert rows["count::down"]["stopped"]["after"] == 4
    assert rows["count::down"]["gate"]["verdict"] == FAIL
    excluded.assert_outcomes(xfailed=9, skipped=7, failed=2)  # the [gate]s


def test_user_skips_are_not_samples(pytester):
    # A run that skips itself is no sample but still one of the planned
    # runs: count the remaining ones, not the samples.
    pytester.makepyfile(
        bench_s=_bench(_function("smoke", [("half", "SP")], "min_passes=3, runs=10"))
    )
    _run(pytester, "--prob-stop=curtail", "--prob-json=r.json")
    (row,) = _json(pytester)["rows"]
    assert (row["passes"], row["total"]) == (3, 3)
    assert row["stopped"] == {
        "after": 6, "planned": 10, "skipped": 4, "verdict": "pass",
        "cost_avoided": pytest.approx(0.004),
    }


def test_nothing_recorded_never_stops(pytester):
    # Every run skips itself: there is no row to stop, and no run to save.
    pytester.makepyfile(
        bench_s=_bench(_function("smoke", [("gone", "S")], "min_passes=3, runs=4"))
    )
    result = _run(pytester, "--prob-stop=curtail", "-rs")
    result.assert_outcomes(skipped=5)  # the [gate] item has nothing to judge
    result.stdout.fnmatch_lines([
        "SKIPPED [[]4[]] *not today",
        "SKIPPED [[]1[]] bench_s.py:1: probability: no runs of smoke::gone were"
        " recorded",
    ])
    assert "= probability =" not in result.stdout.str()


def test_transpose_interleaves_the_skips(pytester):
    pytester.makepyfile(bench_s=_bench(SMOKE))
    result = _run(pytester, "--prob-stop=curtail", "--prob-transpose", "-v")
    lines = [ln for ln in result.stdout.lines if ln.startswith("bench_s.py::")]
    # run-major: steady and broken alternate; broken settles after its
    # 4th run and steady after its 19th
    assert "broken[run4] XFAIL" in lines[7]
    assert "steady[run5] PASSED" in lines[8] and "broken[run5] SKIPPED" in lines[9]
    # each [gate] item follows its case's last run, and judges from the
    # runs recorded before the skips
    assert "steady[run20] SKIPPED" in lines[-4]
    assert "steady[gate] PASSED" in lines[-3]
    assert "broken[run20] SKIPPED" in lines[-2]
    assert "broken[gate] FAILED" in lines[-1]


def test_only_cases_no_other_verdict_needs_stop(pytester):
    # Ungated cases run every run, and so do gated cases whose runs
    # another verdict needs: a comparison margin's, or a latency gate's
    # (skipping runs would shrink its sample of run times).
    down = [("x", "FFFFPPPPPP")]
    margin = _function(
        "ab", down, 'compare="arm", margin=0.1, min_rate=0.9',
        extra='@pytest.mark.parametrize("arm", ["a", "b"])\n',
    ).replace("def bench_ab(case, pattern):", "def bench_ab(case, pattern, arm):")
    bench = _bench(
        _function("free", down, "runs=10"),
        _function("gated", down, "min_rate=0.9, runs=10"),
        _function("timed", down, "min_rate=0.9, max_latency=60, runs=10"),
        margin.replace('("ab", case)', '("ab", case, arm)'),
    )
    pytester.makepyfile(bench_s=bench)
    _run(pytester, "--prob-stop=curtail", "--prob-json=r.json", "--prob-runs=10")
    rows = {r["case"]: r for r in _json(pytester)["rows"]}
    # FAIL at 0.9 over 10 runs is at most 6 passes: settled by 4 fails
    assert rows["gated::x"]["stopped"]["after"] == 4
    for case in ("free::x", "timed::x", "ab::x-a", "ab::x-b"):
        assert rows[case]["total"] == 10 and rows[case]["stopped"] is None, case


def test_skip_without_the_skipping_plugin(pytester):
    # -p no:skipping ignores skip marks; the run itself skips instead.
    pytester.makepyfile(bench_s=_bench(SMOKE))
    result = _run(pytester, "--prob-stop=curtail", "-p", "no:skipping")
    assert _line(result, "  Stopped:").startswith("  Stopped: 2 cases early, saving 17")


# ---------------------------------------------------------------------------
# Unchanged without the option
# ---------------------------------------------------------------------------


def test_output_unchanged_without_the_option(pytester):
    pytester.makepyfile(bench_s=_suite())
    base = _run(pytester, "--prob-json=a.json")
    off = _run(pytester, "--prob-stop=off", "--prob-json=b.json")
    strip = lambda r: [  # noqa: E731
        ln for ln in r.stdout.lines if " in " not in ln and "Report:" not in ln
    ]
    assert strip(base) == strip(off)
    assert "Stopped:" not in base.stdout.str()
    a, b = _json(pytester, "a.json"), _json(pytester, "b.json")
    assert a["stopping"] == b["stopping"] == {
        "mode": "off", "active": False, "cases": 0, "runs_skipped": 0,
        "runs_planned": sum(r["total"] for r in a["rows"]), "cost_avoided": None,
    }
    assert all(r["stopped"] is None for r in a["rows"])
    assert all(x["stopped"] == 0 for x in a["aggregates"])


def test_ini_and_bad_values(pytester):
    pytester.makepyfile(bench_s=_bench(SMOKE))
    result = _run(pytester, "-o", "prob_stop=curtail")
    assert "Stopped: 2 cases early" in result.stdout.str()
    result = _run(pytester, "-o", "prob_stop=sometimes")
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(["*prob_stop must be one of off, curtail*"])
    result = _run(pytester, "--prob-stop=sometimes")
    assert result.ret == pytest.ExitCode.USAGE_ERROR


# ---------------------------------------------------------------------------
# What a stopped case hides
# ---------------------------------------------------------------------------

# Six inputs over 10 runs, enough for a function-level line with
# prob_min_inputs=3. Gated at 0.5, P and PPPPPPPPPF PASS after 9 runs and
# stop; PPPPFPPPPP needs all 10.
MANY = [
    (f"q{i}", p) for i, p in enumerate(["P", "PPPPFPPPPP", "PPPPPPPPPF"] * 2)
]
FEW_INPUTS = ("--prob-runs=10", "-o", "prob_min_inputs=3")


def test_aggregates_hide_their_interval(pytester):
    bench = _bench(
        _function("gated", MANY, "min_rate=0.5"),
        _function("free", MANY),
    )
    pytester.makepyfile(bench_s=bench)
    result = _run(
        pytester, "--prob-stop=curtail", *FEW_INPUTS, "--prob-json=r.json",
        "--prob-metric=pass^2",
    )
    block = result.stdout.lines
    gated = next(ln for ln in block if ln.startswith("  gated  "))
    assert gated.endswith("interval hidden: 4 cases stopped early")
    assert "  N=6 inputs × k=9–10  " in gated
    overall = next(ln for ln in block if ln.startswith("  Overall  N="))
    assert overall.endswith("interval hidden: 4 cases stopped early")
    free = next(ln for ln in block if ln.startswith("  free   "))
    assert "[" in free and "ρ=" in free  # untouched
    data = _json(pytester)
    aggs = {a["name"]: a for a in data["aggregates"]}
    assert aggs["gated"]["ci"] is None and aggs["gated"]["normal_ci"] is None
    assert aggs["gated"]["suppressed"] == "4 cases stopped early"
    assert aggs["gated"]["stopped"] == 4
    assert aggs["gated"]["estimate"] is not None  # kept, as data
    assert aggs["free"]["ci"] is not None and aggs["free"]["stopped"] == 0
    # pass^k averages hide the same way
    metric = aggs["gated"]["metrics"]["pass^2"]
    assert metric["ci"] is None and metric["stopped"] == 4
    assert aggs["free"]["metrics"]["pass^2"]["ci"] is not None
    lines = _section(result, "probability: metrics")
    assert lines[0].startswith("  free ") and "[" in lines[0]
    assert lines[1].split() == [
        "gated", "pass^2", "N=6", "inputs", "hidden:", "4", "cases", "stopped",
        "early",
    ]


def test_comparisons_hide_their_interval_and_p(pytester):
    arms = '@pytest.mark.parametrize("arm", ["a", "b"])\n'

    def compared(name, mark):
        return (
            _function(name, MANY, mark, extra=arms)
            .replace(
                f"def bench_{name}(case, pattern):",
                f"def bench_{name}(case, pattern, arm):",
            )
            .replace(f'("{name}", case)', f'("{name}", case, arm)')
        )

    bench = _bench(
        compared("stops", 'compare="arm", min_rate=0.5'),
        compared("runs", 'compare="arm"'),
    )
    pytester.makepyfile(bench_s=bench)
    result = _run(
        pytester, "--prob-stop=curtail", *FEW_INPUTS, "--prob-json=r.json",
        "--prob-adjust=holm",
    )
    lines = _section(result, "probability: comparisons")
    assert lines[0].startswith("  runs[arm]   b − a") and "[" in lines[0]
    assert lines[1].split() == [
        "stops[arm]", "b", "−", "a", "6", "paired", "cost", "×1.0", "interval",
        "hidden:", "8", "cases", "stopped", "early",
    ]
    cmps = {c["function"]: c for c in _json(pytester)["comparisons"]}
    stops = cmps["stops"]
    assert (stops["ci"], stops["p"], stops["p_adjusted"]) == (None, None, None)
    assert stops["suppressed"] == "8 cases stopped early"
    assert stops["stopped"] == 8 and stops["verdict"] is None
    # the family is the comparisons that keep a p-value
    assert cmps["runs"]["family"] == 1 and cmps["runs"]["p"] is not None


def test_baseline_margin_cases_run_every_run(pytester):
    pytester.makepyfile(bench_s=_bench(SMOKE))
    _run(pytester, "--prob-json=base.json")
    # With a margin, the baseline verdict needs every run: no stopping.
    _run(pytester, "--prob-stop=curtail", "--prob-baseline=base.json",
         "--prob-margin=0.1", "--prob-json=r.json")
    data = _json(pytester)
    assert data["stopping"]["cases"] == 0
    assert all(r["total"] == 20 for r in data["rows"])
    # Without one it only reports: cases stop, and the line hides.
    result = _run(pytester, "--prob-stop=curtail", "--prob-baseline=base.json",
                  "--prob-json=r.json")
    (cmp,) = _json(pytester)["baseline"]["comparisons"]
    assert cmp["ci"] is None and cmp["p"] is None and cmp["stopped"] == 2
    line = next(
        ln for ln in _section(result, "probability: baseline") if "smoke" in ln
    )
    assert line.endswith("interval hidden: 2 cases stopped early")


# ---------------------------------------------------------------------------
# JSON
# ---------------------------------------------------------------------------


def test_json_fields(pytester):
    pytester.makepyfile(bench_s=_bench(CLASSIFY, SMOKE))
    _run(pytester, "--prob-stop=curtail", "--prob-json=r.json")
    data = _json(pytester)
    assert data["stopping"] == {
        "mode": "curtail",
        "active": True,
        "cases": 4,
        "runs_skipped": 23,
        "runs_planned": 160,
        "cost_avoided": pytest.approx(0.023),
    }
    rows = {r["case"]: r for r in data["rows"]}
    assert rows["classify::solid"]["stopped"] is None
    assert rows["classify::weak"]["stopped"] == {
        "after": 36, "planned": 40, "skipped": 4, "verdict": "fail",
        "cost_avoided": pytest.approx(0.004),
    }
    # the gate's own sample is the runs that ran
    assert rows["classify::weak"]["gate"]["total"] == 36
    assert not any("explanation" in r for r in data["rows"])


# ---------------------------------------------------------------------------
# xdist
# ---------------------------------------------------------------------------


def test_xdist_loadgroup_stops_the_same(pytester):
    pytest.importorskip("xdist")
    pytester.makepyfile(bench_s=_suite())
    _run(pytester, "--prob-stop=curtail", "--prob-json=serial.json")
    dist = _run(
        pytester, "--prob-stop=curtail", "--prob-json=dist.json", "-n", "2",
        "--dist", "loadgroup", "-v",
    )
    serial, spread = _json(pytester, "serial.json"), _json(pytester, "dist.json")
    key = lambda r: r["case"]  # noqa: E731
    pick = lambda data: [  # noqa: E731
        (r["case"], r["passes"], r["total"], r["stopped"], r["gate"]["verdict"])
        for r in sorted(data["rows"], key=key)
    ]
    assert pick(spread) == pick(serial)
    assert spread["stopping"] == serial["stopping"]
    # each case is its own xdist group
    dist.stdout.fnmatch_lines(["*bench_strict::solid[[]run1[]]@strict::solid*"])


def test_xdist_without_loadgroup_warns_and_runs_everything(pytester):
    pytest.importorskip("xdist")
    pytester.makepyfile(bench_s=_bench(SMOKE))
    result = pytester.runpytest(
        "--tb=no", "-p", "no:cacheprovider", "--prob-stop=curtail",
        "--prob-json=r.json", "-n", "2",
    )
    result.stdout.fnmatch_lines([
        "*CurtailmentWarning: --prob-stop=curtail needs --dist loadgroup under"
        " pytest-xdist*every case runs all its runs",
    ])
    data = _json(pytester)
    assert data["stopping"]["active"] is False
    assert data["stopping"]["cases"] == 0
    assert sum(r["total"] for r in data["rows"]) == 40
    assert "Stopped:" not in result.stdout.str()


# ---------------------------------------------------------------------------
# --prob-explain
# ---------------------------------------------------------------------------


def _explained(result):
    lines = result.stdout.lines
    start = next(i for i, ln in enumerate(lines) if "= probability: explained =" in ln)
    return "\n".join(lines[start + 1 :])


def test_explain_says_why_runs_were_skipped(pytester):
    pytester.makepyfile(bench_s=_bench(CLASSIFY, SMOKE))
    result = _run(
        pytester, "--prob-stop=curtail", "--prob-explain", "--prob-json=r.json"
    )
    text = " ".join(_explained(result).split())
    assert (
        "classify::weak 27/36 [58%, 88%] ≥90% FAIL decided after 36/40" in text
    )
    assert (
        "It stopped after 36 of its 40 planned runs (--prob-stop=curtail) because"
        " by then it would fall short of the bar even if every remaining run"
        " passed; the other 4 runs were skipped. The verdict is the one all 40"
        " runs would give." in text
    )
    assert "it would stay UNDECIDED however the remaining runs went" in text
    assert "it would meet the bar even if every remaining run failed" in text
    # a stopped count gate: the runs it skipped are why it fell short
    assert "With 16 runs left it could have reached at most 18" in text
    assert "run it at least 19 times" not in text
    assert "decided after With --prob-stop=curtail a gated case stops" in text
    rows = {r["case"]: r for r in _json(pytester)["rows"]}
    weak = rows["classify::weak"]
    assert "It stopped after 36 of its 40 planned runs" in weak["gate"]["explanation"]
    assert "can lean toward the verdict" in weak["explanation"]
    assert "stopped" not in rows["classify::solid"]["explanation"]


def test_explain_says_why_intervals_are_hidden(pytester):
    bench = _bench(_function("gated", MANY, "min_rate=0.5"), _function("free", MANY))
    pytester.makepyfile(bench_s=bench)
    result = _run(
        pytester, "--prob-stop=curtail", *FEW_INPUTS, "--prob-explain",
        "--prob-json=r.json",
    )
    text = " ".join(_explained(result).split())
    assert (
        "gated N=6 inputs × k=9–10 interval hidden: 4 cases stopped early"
        " No average or range is shown: 4 of gated's 6 inputs stopped early,"
        " once their gates' verdicts were certain (--prob-stop=curtail)."
    ) in text
    assert (
        "Overall N=12 inputs × k=9–10 interval hidden: 4 cases stopped early"
        " No average or range is shown: 4 of the 12 inputs in this session"
        " stopped early"
    ) in text
    assert "Next: to see this line, run without --prob-stop=curtail." in text
    aggs = {a["name"]: a for a in _json(pytester)["aggregates"]}
    assert aggs["gated"]["explanation"].startswith("No average or range is shown")


def test_stop_paragraph_for_each_verdict():
    from pytest_probability import explain
    from pytest_probability.plugin import Stop

    stop = Stop("c", after=12, planned=40, verdict=FAIL, skipped=28)
    assert explain.stop_paragraph(stop, FAIL) == (
        "It stopped after 12 of its 40 planned runs (--prob-stop=curtail)"
        " because by then it would fall short of the bar even if every"
        " remaining run passed; the other 28 runs were skipped. The verdict"
        " is the one all 40 runs would give. The fraction and range come from"
        " the 12 runs that ran; because the case stopped as soon as its"
        " verdict was certain, they can lean toward that verdict."
    )
    one = Stop("c", after=19, planned=20, verdict=PASS, skipped=1)
    assert "even if every remaining run failed; the other 1 run was skipped" in (
        explain.stop_paragraph(one, PASS)
    )
    assert explain.stop_paragraph(one, UNDECIDED, numbers=False).endswith(
        "it would stay UNDECIDED however the remaining runs went; the other 1"
        " run was skipped. The verdict is the one all 20 runs would give."
    )


def test_gate_reading_of_a_stopped_case_with_nothing_judged():
    # Every run errored and was excluded: no fraction to speak of.
    from pytest_probability import explain
    from pytest_probability.plugin import Stop

    gate = Gate("count", StatsConfig(), min_passes=3, errors="exclude")
    result = gate.evaluate(CaseStats("smoke::down", 0, 0, 4))
    stop = Stop("smoke::down", after=4, planned=6, verdict=FAIL, skipped=2)
    reading = explain.gate_reading(result, errors=4, stop=stop)
    assert reading.heading == (
        "smoke::down  0/0  ≥3 passes  FAIL  (4 errored, excluded)  decided after 4/6"
    )
    text = reading.text()
    assert "It stopped after 4 of its 6 planned runs" in text
    assert "The fraction and range" not in text
    assert ("stopped", None) in reading.terms


def test_comparison_and_metric_readings_say_why_they_are_hidden():
    from pytest_probability import explain
    from pytest_probability.plugin import (
        CompareSpec,
        Metric,
        Pair,
        aggregate,
        compare_pairs,
        suppress_stopped,
        suppress_stopped_comparison,
    )

    cfg = StatsConfig(min_inputs=3)
    pairs = [Pair(f"q{i}", (9, 10), (i, 10)) for i in range(5)]
    cmp = compare_pairs("triage", CompareSpec("style", "a"), "b", pairs, cfg)
    hidden = suppress_stopped_comparison(cmp, 2)
    assert (hidden.ci, hidden.p, hidden.stopped) == (None, None, 2)
    reading = explain.comparison_reading(hidden, min_inputs=3)
    assert reading.heading.endswith(
        "5 paired  interval hidden: 2 cases stopped early"
    )
    assert reading.paragraphs[0].startswith(
        "No difference, range or p-value is shown: 2 cases behind this"
        " comparison of b with a stopped early"
    )
    assert reading.paragraphs[-1] == (
        "Next: to compare the arms, run without --prob-stop=curtail."
    )

    cases = [CaseStats(f"f::{i}", passes=i + 5, fails=5 - i) for i in range(5)]
    agg = suppress_stopped(
        aggregate("function", "f", cases, cfg, metrics=[Metric("^", 2)]), {"f::0"}
    )
    assert agg.ci is None and agg.stopped == 1
    (m,) = agg.metrics
    reading = explain.metric_reading(agg, m, 3)
    assert reading.heading.split() == [
        "f", "pass^2", "N=5", "inputs", "hidden:", "1", "case", "stopped", "early",
    ]
    assert "No value is shown: 1 of f's 5 inputs stopped early" in reading.text()
    assert explain.aggregate_reading(agg, 3).paragraphs[0].startswith(
        "No average or range is shown: 1 of f's 5 inputs stopped early"
    )
    # A stopped case outside an aggregate leaves it alone.
    assert suppress_stopped(agg, {"g::0"}) is agg
