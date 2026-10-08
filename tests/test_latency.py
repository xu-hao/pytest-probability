"""Latency quantiles and gates (#10), end to end via pytester.

Run times come from a fake clock: the plugin times every run with
``plugin._clock``, and ``CONFTEST_CLOCK`` swaps in a clock that only
moves when a bench body advances it. That makes every ``elapsed``
exact and the suite fast; nothing sleeps.
"""
import json

import pytest

from pytest_probability import stats
from pytest_probability.plugin import (
    FAIL,
    PASS,
    UNDECIDED,
    CaseStats,
    LatencySpec,
    _secs,
    latency_verdict,
)

# A conftest rather than a monkeypatch in the test, so that xdist
# workers get the fake clock too; restored at unconfigure, since
# pytester runs in-process.
CONFTEST_CLOCK = """
import pytest_probability.plugin as plugin


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def pytest_configure(config):
    config._saved_clock = plugin._clock
    plugin._clock = FakeClock()


def pytest_unconfigure(config):
    plugin._clock = config._saved_clock
"""

# Each case's run times cycle through its list (dyadic, so every elapsed
# is exact). With 80 runs against max_latency=1: fast's p95 interval
# lies below 1 (PASS), slow's above (FAIL), and edge's — 1 run in 10
# takes 1.5s — straddles it (UNDECIDED). ping is ungated.
TIMES = {
    "fast": [0.25, 0.5, 0.375, 0.75],
    "slow": [1.5, 2.0, 1.75, 2.5],
    "edge": [0.5] * 9 + [1.5],
    "ping": [0.0078125] * 19 + [0.03125],
}

BENCH = """
import pytest
from pytest_probability import plugin

TIMES = {times!r}
_calls = {{}}


def tick(case):
    i = _calls.get(case, 0)
    _calls[case] = i + 1
    plugin._clock.advance(TIMES[case][i % len(TIMES[case])])


@pytest.mark.probability({marker})
@pytest.mark.parametrize("case", {cases!r})
def bench_api(case):
    tick(case)
    {body}


def bench_ping():
    tick("ping")
"""


def _suite(pytester, marker="max_latency=1.0", cases=("fast", "slow", "edge"),
           body="pass", clock=True):
    if clock:
        pytester.makeconftest(CONFTEST_CLOCK)
    pytester.makepyfile(
        bench_lat=BENCH.format(
            times=TIMES, marker=marker, cases=list(cases), body=body
        )
    )


def _run(pytester, *args):
    return pytester.runpytest("--tb=no", "-p", "no:cacheprovider", *args)


def _times(case, runs):
    seq = TIMES[case]
    return [seq[i % len(seq)] for i in range(runs)]


def _section(result, title):
    """The lines of a ``probability: ...`` section, up to the next one or
    the short summary ([] when absent)."""
    lines = result.stdout.lines
    try:
        start = next(i for i, ln in enumerate(lines) if f"= {title} =" in ln)
    except StopIteration:
        return []
    out = []
    for ln in lines[start + 1 :]:
        if ln.startswith("="):
            break
        out.append(ln)
    return out


def _tally(result):
    return next(ln for ln in result.stdout.lines if ln.startswith("  Gates:"))


def _json(pytester, name="r.json"):
    return json.loads((pytester.path / name).read_text())


def _rows(data):
    return {r["case"]: r for r in data["rows"]}


# ---------------------------------------------------------------------------
# Terminal, JSON, verdicts and exit status
# ---------------------------------------------------------------------------


def test_latency_block_gates_and_json(pytester):
    _suite(pytester)
    result = _run(pytester, "--prob-runs=80", "--prob-latency", "--prob-json=r.json")
    result.assert_outcomes(passed=320)
    assert result.ret == pytest.ExitCode.TESTS_FAILED
    assert _section(result, "probability: latency") == [
        "  api::fast  80 runs  p95   750ms  [ 750ms,  750ms]  ≤1s  PASS",
        "  api::slow  80 runs  p95   2.50s  [ 2.50s,  2.50s]  ≤1s  FAIL",
        "  api::edge  80 runs  p95   1.50s  [ 500ms,  1.50s]  ≤1s  UNDECIDED",
        "  ping       80 runs  p95  7.81ms  [7.81ms, 31.3ms]",
    ]
    # every gate verdict is tallied; the non-PASS ones are listed
    assert _tally(result) == "  Gates:   1 passed, 1 failed, 1 undecided"
    assert _section(result, "probability: gates") == [
        "  api::slow  80 runs  p95  2.50s  [2.50s, 2.50s]  ≤1s  FAIL",
        "  api::edge  80 runs  p95  1.50s  [500ms, 1.50s]  ≤1s  UNDECIDED",
        "",
        "  Run with --prob-explain for a plain-language reading.",
    ]
    rows = _rows(_json(pytester))
    for case, verdict in [
        ("api::fast", PASS), ("api::slow", FAIL), ("api::edge", UNDECIDED),
        ("ping", None),
    ]:
        lat = rows[case]["latency"]
        times = _times(case.removeprefix("api::"), 80)
        r, s = stats.quantile_ranks(80, 0.95)
        assert lat == {
            "quantile": 0.95,
            "total": 80,
            "excluded": 0,
            "estimate": stats.sample_quantile(times, 0.95),
            "ci": {
                "method": "order-statistic",
                "level": 0.95,
                "low": sorted(times)[r - 1],
                "high": sorted(times)[s - 1],
                "ranks": [r, s],
                "coverage": stats.quantile_coverage(80, 0.95, r, s),
            },
            "min_runs": 72,
            "max_latency": 1.0 if verdict else None,
            "verdict": verdict,
        }
        assert (lat["ci"]["low"], lat["ci"]["high"]) == stats.quantile_interval(
            times, 0.95
        )


def test_records_keep_elapsed_and_drop_the_latency_spec(pytester):
    _suite(pytester)
    _run(pytester, "--prob-runs=4", "--prob-json=r.json")
    records = _json(pytester)["records"]
    assert all("latency" not in rec for rec in records)
    fast = [rec["elapsed"] for rec in records if rec["case"] == "api::fast"]
    assert fast == TIMES["fast"]


@pytest.mark.parametrize(
    "cases, args, ret",
    [
        (("fast",), (), pytest.ExitCode.OK),
        (("fast", "slow"), (), pytest.ExitCode.TESTS_FAILED),
        (("fast", "edge"), (), pytest.ExitCode.TESTS_FAILED),
        (("fast", "edge"), ("--prob-undecided=pass",), pytest.ExitCode.OK),
    ],
)
def test_exit_status(pytester, cases, args, ret):
    _suite(pytester, cases=cases)
    result = _run(pytester, "--prob-runs=80", *args)
    assert result.ret == ret
    if ret == pytest.ExitCode.OK and "edge" in cases:
        assert _tally(result) == "  Gates:   1 passed, 1 undecided (allowed)"


def test_too_few_runs_is_undecided_and_warns(pytester):
    _suite(pytester, cases=("fast", "slow"))
    result = _run(pytester, "--prob-runs=20", "--prob-latency", "-rw")
    # no upper bound below 72 runs: fast can't PASS yet, but slow's
    # lower bound already clears the limit
    assert _section(result, "probability: latency")[:2] == [
        "  api::fast  20 runs  p95   750ms  [ 750ms, —]  ≤1s  UNDECIDED",
        "  api::slow  20 runs  p95   2.50s  [ 2.50s, —]  ≤1s  FAIL",
    ]
    result.stdout.fnmatch_lines(
        ["*InfeasibleGateWarning: api (2 cases): max_latency=1 (p95) at 95%"
         " needs ≥72 runs; each has 20"]
    )
    assert result.ret == pytest.ExitCode.TESTS_FAILED


def test_no_warning_with_enough_runs(pytester):
    _suite(pytester, cases=("fast",))
    result = _run(pytester, "--prob-runs=72", "-W", "error::pytest.PytestWarning")
    assert result.ret == pytest.ExitCode.OK


def test_latency_gate_does_not_xfail_failing_runs(pytester):
    # A latency gate judges speed, not answers: a failing assert still
    # fails the session, whatever the latency verdict.
    body = "assert _calls[case] % 4 != 0, 'wrong'"
    _suite(pytester, cases=("fast",), body=body)
    result = _run(pytester, "--prob-runs=80")
    result.assert_outcomes(passed=60 + 80, failed=20)  # ping: 80 passes
    assert _tally(result) == "  Gates:   1 passed"
    assert result.ret == pytest.ExitCode.TESTS_FAILED
    # with a rate gate too, the rate gate's verdict judges the answers
    _suite(pytester, marker="max_latency=1.0, min_rate=0.5", cases=("fast",),
           body=body)
    result = _run(pytester, "--prob-runs=80")
    result.assert_outcomes(passed=60 + 80, xfailed=20)
    assert _tally(result) == "  Gates:   2 passed"
    assert result.ret == pytest.ExitCode.OK


def test_errored_runs(pytester):
    # Every 4th run errors (at that run's 0.75s). Ungated and gated cases
    # count them, unless a gated case runs under prob_errors = exclude.
    body = "if _calls[case] % 4 == 0: raise RuntimeError('down')"
    _suite(pytester, marker="latency_quantile=0.5", cases=("fast",), body=body)
    _run(pytester, "--prob-runs=8", "--prob-json=r.json", "-o", "prob_errors=exclude")
    lat = _rows(_json(pytester))["api::fast"]["latency"]
    assert (lat["total"], lat["excluded"], lat["estimate"]) == (8, 0, 0.375)

    _suite(pytester, marker="latency_quantile=0.5, max_latency=1.0",
           cases=("fast",), body=body)
    result = _run(
        pytester, "--prob-runs=8", "--prob-latency", "--prob-json=r.json",
        "-o", "prob_errors=exclude",
    )
    lat = _rows(_json(pytester))["api::fast"]["latency"]
    assert (lat["total"], lat["excluded"], lat["estimate"]) == (6, 2, 0.375)
    assert _section(result, "probability: latency")[0].endswith(
        "(2 errored, excluded)"
    )
    # errors still fail the session: only a rate gate xfails them (ping
    # adds 8 passes)
    result.assert_outcomes(passed=14, failed=2)


def test_case_mark_overrides_and_quantile_settings(pytester):
    pytester.makeconftest(CONFTEST_CLOCK)
    pytester.makepyfile(
        bench_lat=f"""
import pytest
from pytest_probability import plugin

TIMES = {TIMES!r}

def tick(case, i=[0]):
    i[0] += 1
    plugin._clock.advance(TIMES[case][i[0] % len(TIMES[case])])

@pytest.mark.probability(latency_quantile=0.5, max_latency=1.0)
@pytest.mark.parametrize("case", [
    "fast",
    pytest.param("slow", marks=pytest.mark.probability(max_latency=3.0)),
    pytest.param("edge", marks=pytest.mark.probability(latency_quantile=0.9)),
])
def bench_api(case):
    tick(case)

@pytest.mark.probability(max_latency=1.0, confidence=0.8)
def bench_loose():
    tick("fast")

def bench_ping():
    tick("ping")
"""
    )
    _run(pytester, "--prob-runs=40", "--prob-json=r.json",
         "-o", "prob_latency_quantile=0.99")
    rows = _rows(_json(pytester))
    lat = {case: row["latency"] for case, row in rows.items()}
    # the function's quantile, the case's limit
    assert (lat["api::slow"]["quantile"], lat["api::slow"]["max_latency"]) == (0.5, 3.0)
    # the case's quantile, the function's limit
    assert (lat["api::edge"]["quantile"], lat["api::edge"]["max_latency"]) == (0.9, 1.0)
    # confidence= sets a latency gate's level, as it does a rate gate's
    assert lat["loose"]["ci"]["level"] == 0.8
    assert lat["api::fast"]["ci"]["level"] == 0.95
    # unmarked cases report prob_latency_quantile
    assert lat["ping"]["quantile"] == 0.99
    assert lat["ping"]["verdict"] is None


@pytest.mark.parametrize(
    "marker, message",
    [
        ("latency_quantile=95", "latency_quantile must be strictly between 0 and 1,"
         " got 95 (did you mean 0.95?)"),
        ("latency_quantile='p95'", "latency_quantile must be a number, got 'p95'"),
        ("max_latency=0", "max_latency must be a positive number of seconds, got 0"),
        ("max_latency=float('inf')", "max_latency must be a positive number"),
        ("max_latency='2s'", "max_latency must be a number of seconds, got '2s'"),
        ("max_latency=True", "max_latency must be a number of seconds, got True"),
    ],
)
def test_invalid_latency_marks(pytester, marker, message):
    _suite(pytester, marker=marker, cases=("fast",))
    result = pytester.runpytest()
    result.stdout.fnmatch_lines([f"*api::fast: invalid probability mark: {message}*"])
    assert result.ret == pytest.ExitCode.INTERRUPTED


def test_invalid_latency_quantile_ini_is_a_usage_error(pytester):
    _suite(pytester)
    result = _run(pytester, "-o", "prob_latency_quantile=95")
    result.stderr.fnmatch_lines(
        ["*prob_latency_quantile must be strictly between 0 and 1, got '95'*"]
    )
    assert result.ret == pytest.ExitCode.USAGE_ERROR


def test_ini_turns_the_block_on(pytester):
    _suite(pytester)
    pytester.makeini("[pytest]\nprob_latency = true\n")
    result = _run(pytester, "--prob-runs=4")
    assert len(_section(result, "probability: latency")) == 4


def test_spec_travels_in_user_properties_as_plain_data(pytester):
    pytester.makeconftest(
        CONFTEST_CLOCK
        + """
import json

def pytest_runtest_logreport(report):
    if report.when == "call":
        for name, value in report.user_properties:
            if name == "probability":
                json.dumps(value)  # plain data only
                print("LATENCY", value["case"], value.get("latency"))
"""
    )
    pytester.makepyfile(
        bench_lat=BENCH.format(
            times=TIMES, marker="max_latency=1.0", cases=["fast"], body="pass"
        )
    )
    result = pytester.runpytest("-s", "-W", "ignore::pytest.PytestWarning")
    result.stdout.fnmatch_lines(
        [
            "*LATENCY api::fast {'quantile': 0.95, 'confidence': 0.95,"
            " 'max_latency': 1.0, 'errors': 'count', 'runs': 1}*",
            "*LATENCY ping None*",
        ]
    )


# ---------------------------------------------------------------------------
# Nothing changes without the options
# ---------------------------------------------------------------------------

BENCH_PLAIN = """
import pytest

@pytest.mark.parametrize("word", ["alpha", "beta"])
def bench_check(word):
    assert word != "beta"
"""


def test_unmarked_suite_output_unchanged(pytester):
    pytester.makepyfile(bench_plain=BENCH_PLAIN)
    base = _run(pytester, "--prob-runs=5")
    other = _run(pytester, "--prob-runs=5", "-o", "prob_latency_quantile=0.5")
    strip = lambda r: [ln for ln in r.stdout.lines if " in " not in ln]  # noqa: E731
    assert strip(base) == strip(other)
    assert "latency" not in base.stdout.str()
    assert "Gates:" not in base.stdout.str()
    assert base.ret == other.ret == pytest.ExitCode.TESTS_FAILED


def test_regular_suite_unchanged_by_latency_flag(pytester):
    pytester.makepyfile(test_plain="def test_ok():\n    assert True\n")
    base = _run(pytester)
    flagged = _run(pytester, "--prob-latency")
    strip = lambda r: [ln for ln in r.stdout.lines if " in " not in ln]  # noqa: E731
    assert strip(base) == strip(flagged)


def test_real_clock_still_times_runs(pytester):
    # Without the fake, elapsed comes from perf_counter.
    _suite(pytester, cases=("fast",), clock=False)
    _run(pytester, "--prob-runs=2", "--prob-json=r.json",
         "-W", "ignore::pytest.PytestWarning")
    records = _json(pytester)["records"]
    assert all(0 <= rec["elapsed"] < 5 for rec in records)


# ---------------------------------------------------------------------------
# Explanations
# ---------------------------------------------------------------------------


def _explained(result):
    return _section(result, "probability: explained")


def test_explained(pytester):
    _suite(pytester, cases=("fast", "slow", "edge"))
    result = _run(pytester, "--prob-runs=80", "--prob-explain", "--prob-json=r.json")
    text = "\n".join(_explained(result))
    assert "  api::slow  80 runs  p95  2.50s  [2.50s, 2.50s]  ≤1s  FAIL" in text
    assert "so this case is too slow." in " ".join(text.split())
    assert "Next: run more: more runs narrow the range." in " ".join(text.split())
    assert "≤Ns" in text and "pN [low, high]" in text
    # the hint is replaced by the section
    assert "Run with --prob-explain" not in result.stdout.str()
    rows = _rows(_json(pytester))
    assert rows["api::fast"]["latency"]["explanation"].startswith(
        "The 95th percentile of this case's run time"
    )
    assert "explanation" in rows["ping"]["latency"]


def test_explained_shown_latency_needing_runs(pytester):
    # Ungated lines are explained only when shown and missing a bound.
    _suite(pytester, marker="runs=5", cases=("fast",))
    result = _run(pytester, "--prob-explain")
    assert "95th percentile" not in "\n".join(_explained(result))
    result = _run(pytester, "--prob-explain", "--prob-latency")
    text = " ".join("\n".join(_explained(result)).split())
    assert "takes at least 72 runs" in text
    assert "pN [low, high]" in text


# ---------------------------------------------------------------------------
# xdist
# ---------------------------------------------------------------------------


def test_xdist_parity(pytester):
    pytest.importorskip("xdist")
    _suite(pytester)
    pytester.makepyfile(
        bench_more=BENCH.format(
            times=TIMES, marker="max_latency=0.5, latency_quantile=0.5",
            cases=["fast", "edge"], body="pass",
        ).replace("bench_api", "bench_more").replace("bench_ping", "bench_pong")
    )
    args = ("--prob-runs=80", "--prob-latency", "-o", "prob_errors=exclude")
    serial = _run(pytester, *args, "--prob-json=serial.json")
    dist = _run(pytester, *args, "--prob-json=dist.json", "-n", "4",
                "--dist", "loadfile")
    assert serial.ret == dist.ret == pytest.ExitCode.TESTS_FAILED
    assert sorted(_section(serial, "probability: latency")) == sorted(
        _section(dist, "probability: latency")
    )
    assert sorted(_section(serial, "probability: gates")) == sorted(
        _section(dist, "probability: gates")
    )
    assert _tally(serial) == _tally(dist)
    key = lambda r: r["case"]  # noqa: E731
    s, d = _json(pytester, "serial.json"), _json(pytester, "dist.json")
    assert [r["latency"] for r in sorted(s["rows"], key=key)] == [
        r["latency"] for r in sorted(d["rows"], key=key)
    ]


# ---------------------------------------------------------------------------
# Units
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "t, text",
    [
        (None, "—"), (0.0, "0s"), (2.4, "2.40s"), (12.345, "12.3s"),
        (123.4, "123s"), (0.5, "500ms"), (0.0123, "12.3ms"), (0.00123, "1.23ms"),
        (0.000850, "850µs"), (0.9996, "1.00s"), (0.99949, "999ms"),
        (9.996, "10.0s"), (0.03125, "31.3ms"),
    ],
)
def test_secs(t, text):
    assert _secs(t) == text


@pytest.mark.parametrize(
    "low, high, verdict",
    [
        (0.5, 0.9, PASS), (1.1, 2.0, FAIL), (0.5, 1.5, UNDECIDED),
        # bounds exactly on the limit are not past it
        (0.5, 1.0, UNDECIDED), (1.0, 2.0, UNDECIDED),
        # an open side: no upper bound can't PASS, no lower bound can't FAIL
        (0.5, None, UNDECIDED), (1.5, None, FAIL), (None, 0.5, PASS),
        (None, 1.5, UNDECIDED), (None, None, UNDECIDED),
    ],
)
def test_latency_verdict(low, high, verdict):
    assert latency_verdict(low, high, 1.0) == verdict


def test_evaluate_sorts_and_excludes():
    spec = LatencySpec(quantile=0.5, level=0.95, max_latency=1.0, errors="exclude")
    s = CaseStats("f", passes=5, errors=2, times=[0.5, 0.1, 0.4, 0.3, 0.2],
                  error_times=[9.0, 9.0])
    r = spec.evaluate(s)
    assert (r.total, r.excluded, r.estimate) == (5, 2, 0.3)
    assert r.interval == (None, None)  # the median needs 6 runs
    assert r.verdict == UNDECIDED
    # ungated, errors count
    r = LatencySpec(quantile=0.5, level=0.95, errors="exclude").evaluate(s)
    assert (r.total, r.excluded, r.estimate, r.verdict) == (7, 0, 0.4, None)
    # every run errored and excluded: nothing to judge
    empty = CaseStats("f", errors=2, error_times=[1.0, 1.0])
    r = spec.evaluate(empty)
    assert (r.total, r.estimate, r.verdict) == (0, None, UNDECIDED)
    assert r.to_json()["ci"] is None
