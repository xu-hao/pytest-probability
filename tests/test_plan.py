"""Tests for --prob-plan: the power and flake arithmetic, and the table
it prints instead of running anything."""

import json
import math

import pytest

from pytest_probability import explain, stats
from pytest_probability.plugin import PASS, Gate, StatsConfig

# ---------------------------------------------------------------------------
# Catching a rare failure
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "rate, runs", [(0.1, 29), (0.01, 299), (0.05, 59), (0.5, 5), (0.001, 2995)]
)
def test_runs_to_see_failure_known_values(rate, runs):
    assert stats.runs_to_see_failure(rate) == runs
    # the closed form the docs quote
    assert runs == math.ceil(math.log(0.05) / math.log(1 - rate))


@pytest.mark.parametrize("level", [0.8, 0.9, 0.95, 0.99])
def test_runs_to_see_failure_is_the_smallest_n(level):
    for rate in [i / 200 for i in range(1, 200)]:
        n = stats.runs_to_see_failure(rate, level)
        assert 1 - (1 - rate) ** n >= level - 1e-12
        assert n == 1 or 1 - (1 - rate) ** (n - 1) < level + 1e-12


def test_runs_to_see_failure_edges():
    assert stats.runs_to_see_failure(1.0) == 1
    for bad in (0.0, -0.1, 1.5):
        with pytest.raises(ValueError):
            stats.runs_to_see_failure(bad)
    with pytest.raises(ValueError):
        stats.runs_to_see_failure(0.1, level=1.0)


def test_detection_chance():
    for rate in (0.001, 0.1, 0.5, 0.9):
        for n in (0, 1, 7, 100):
            assert stats.detection_chance(rate, n) == pytest.approx(
                1 - (1 - rate) ** n, abs=1e-15
            )
    assert stats.detection_chance(1.0, 0) == 0.0
    assert stats.detection_chance(1.0, 3) == 1.0
    with pytest.raises(ValueError):
        stats.detection_chance(0.1, -1)


# ---------------------------------------------------------------------------
# Runs for 80% power
# ---------------------------------------------------------------------------


def _gate(rule="rate", bar=0.9, need=None, **cfg):
    sc = StatsConfig(**cfg)
    if rule == "count":
        return Gate(rule="count", stats=sc, min_passes=need)
    return Gate(rule="rate", stats=sc, min_rate=bar)


GATES = [
    _gate(bar=0.9),
    _gate(bar=0.8, method="wilson"),
    _gate(bar=0.7, method="bayes", prior=(0.5, 0.5)),
    _gate(bar=0.9, level=0.8),
    _gate(rule="count", need=7),
]


def _brute_power(gate, n, rate, binom):
    # Every pass count the gate's own verdict calls PASS, weighted by
    # its binomial probability — no monotonicity assumed.
    passing = [x for x in range(n + 1) if gate.verdict(x, n) == PASS]
    return sum(binom.pmf(x, n, rate) for x in passing)


@pytest.mark.parametrize("gate", GATES, ids=lambda g: g.describe())
def test_critical_passes_is_the_pass_boundary(gate):
    for n in range(1, 60):
        passing = [x for x in range(n + 1) if gate.verdict(x, n) == PASS]
        if not passing:
            assert gate.critical_passes(n) is None
        else:
            # monotone in x: the passing counts are one tail
            assert passing == list(range(passing[0], n + 1))
            assert gate.critical_passes(n) == passing[0]


@pytest.mark.parametrize("gate", GATES, ids=lambda g: g.describe())
@pytest.mark.parametrize("rate", [0.5, 0.95, 0.99])
def test_power_matches_brute_force(gate, rate):
    binom = pytest.importorskip("scipy.stats").binom
    for n in (1, 5, 13, 40, 77):
        assert gate.power(n, rate) == pytest.approx(
            _brute_power(gate, n, rate, binom), abs=1e-12
        )


@pytest.mark.parametrize("gate", GATES, ids=lambda g: g.describe())
@pytest.mark.parametrize("rate", [0.85, 0.97, 0.995])
def test_runs_for_power_is_the_smallest_n(gate, rate):
    binom = pytest.importorskip("scipy.stats").binom
    cap = 400
    got = gate.runs_for_power(rate, 0.8, cap)
    want = next(
        (n for n in range(1, cap + 1) if _brute_power(gate, n, rate, binom) >= 0.8),
        None,
    )
    if gate.rule == "rate" and rate <= gate.min_rate:
        want = None
    assert got == want


def test_runs_for_power_known_values():
    # min_rate 0.9 at 95% (exact) needs 36 runs to pass at all, and 100
    # for an 80% chance when the true rate is 97%
    gate = _gate(bar=0.9)
    assert gate.min_runs() == 36
    assert gate.runs_for_power(0.97) == 100
    assert gate.power(99, 0.97) < 0.8 <= gate.power(100, 0.97)
    # min_passes=19: P(Binomial(n, 0.97) >= 19) first reaches 80% at 20
    assert _gate(rule="count", need=19).runs_for_power(0.97) == 20


def test_runs_for_power_unreachable():
    gate = _gate(bar=0.97)
    # a true rate at or below the bar never passes reliably
    assert gate.runs_for_power(0.97) is None
    assert gate.runs_for_power(0.5) is None
    # reachable, but not within the cap
    assert _gate(bar=0.96).runs_for_power(0.97, cap=500) is None
    assert _gate(rule="count", need=600).runs_for_power(0.97, cap=500) is None


# ---------------------------------------------------------------------------
# Column notes
# ---------------------------------------------------------------------------


def test_plan_notes_snapshot():
    notes = explain.plan_notes(
        assume=0.97,
        power=0.8,
        flakes={0.1: 29, 0.01: 299},
        level=0.95,
        gated=True,
        report="last.json",
    )
    assert notes == [
        (
            "runs",
            "Runs planned for the case (--prob-runs, a runs= mark or"
            " prob_runs), counting only the runs -k/-m selected.",
        ),
        (
            "min runs",
            "Fewest runs with which the gate can pass at all, and then only"
            " if every one of them passes.",
        ),
        (
            "runs for 80%",
            "Runs with which the gate passes 80% of the time if the case"
            " really passes 97% of its runs (--prob-plan-assume); never, if"
            " 97% is not above the gate's bar.",
        ),
        (
            "chance now",
            "The chance the gate passes with the planned runs, if the case"
            " really passes 97% of its runs.",
        ),
        (
            "catch 10%",
            "The chance the planned runs show at least one failure if 10% of"
            " runs fail; 29 runs make it 95%.",
        ),
        (
            "catch 1%",
            "The chance the planned runs show at least one failure if 1% of"
            " runs fail; 299 runs make it 95%.",
        ),
        (
            "cost",
            "Planned runs times the case's cost per run in last.json; blank"
            " for a case it doesn't have.",
        ),
    ]
    ungated = explain.plan_notes(
        assume=0.97, power=0.8, flakes={0.1: 29}, level=0.95, gated=False, report=None
    )
    assert [label for label, _ in ungated] == ["runs", "catch 10%"]


def test_render_notes_hangs_and_wraps():
    lines = explain.render_notes([("a", "word " * 30), ("long label", "short")], 50)
    assert lines[0].startswith("  a           word")
    assert all(len(ln) <= 50 for ln in lines)
    assert lines[1].startswith(" " * 14 + "word")
    assert lines[-1] == "  long label  short"


# ---------------------------------------------------------------------------
# End to end
# ---------------------------------------------------------------------------

# Every body records that it ran, so a test can prove nothing did.
BENCH_PLAN = """
import pathlib
import pytest
from pytest_probability import record_cost

def _ran():
    with open(pathlib.Path(__file__).with_name("ran.txt"), "a") as f:
        f.write("x")

@pytest.mark.probability(min_rate=0.9)
@pytest.mark.parametrize("q", ["refund", "billing"])
def bench_classify(q):
    _ran()
    record_cost(0.002)

@pytest.mark.probability(min_passes=19, runs=20)
def bench_smoke():
    _ran()
    record_cost(0.01)

@pytest.mark.slow
def bench_ungated():
    _ran()
"""

EXPECTED_TABLE = [
    "  case               runs  min runs  runs for 80%  chance now  catch 10%  catch 1%",
    "  classify::refund     40        36           100         30%        99%       33%",
    "  classify::billing    40        36           100         30%        99%       33%",
    "  smoke                20        19            20         88%        88%       18%",
    "  ungated              40         —             —           —        99%       33%",
]


@pytest.fixture
def columns(monkeypatch):
    monkeypatch.setenv("COLUMNS", "80")


def _run(pytester, *args):
    return pytester.runpytest(
        "--tb=no", "-p", "no:cacheprovider", "-W", "ignore", *args
    )


def _section(result, title="probability: plan"):
    lines = result.stdout.lines
    start = next(i for i, ln in enumerate(lines) if f"= {title} =" in ln)
    out = []
    for ln in lines[start + 1 :]:
        if ln.startswith("="):
            break
        out.append(ln)
    return out


def _table(result):
    """The header and the rows: the section up to its first blank line."""
    section = _section(result)
    return section[: section.index("")]


def _footer(result):
    """The lines between the table and the column notes."""
    section = _section(result)
    start = section.index("") + 1
    return section[start : section.index("", start)]


def test_plan_prints_table_and_runs_nothing(pytester, columns):
    pytester.makepyfile(bench_plan=BENCH_PLAN)
    result = _run(pytester, "--prob-plan", "--prob-runs=40")
    assert result.ret == 0
    assert not (pytester.path / "ran.txt").exists()
    result.assert_outcomes()  # nothing passed, failed or was skipped
    out = result.stdout.str()
    assert "collected 160 items" in out
    assert "= probability =" not in out
    # --prob-runs beats the runs=20 mark, as when running
    assert _table(result)[3].split()[:2] == ["smoke", "40"]
    assert _footer(result) == ["  Plan:    4 cases, 160 runs; nothing was run."]


def test_plan_table_snapshot(pytester, columns):
    pytester.makeini("[pytest]\nprob_runs = 40\n")
    pytester.makepyfile(bench_plan=BENCH_PLAN)
    result = _run(pytester, "--prob-plan")
    assert result.ret == 0
    assert _table(result) == EXPECTED_TABLE
    assert _footer(result) == ["  Plan:    4 cases, 140 runs; nothing was run."]
    section = _section(result)
    notes = section[section.index("", len(EXPECTED_TABLE) + 1) + 1 :]
    labels = [ln.split("  ")[1] for ln in notes if not ln.startswith("    ")]
    assert labels == [
        "runs", "min runs", "runs for 80%", "chance now", "catch 10%", "catch 1%",
    ]
    assert any("29 runs make it 95%." in ln for ln in notes)
    assert any("299 runs make it" in ln for ln in notes)
    assert not (pytester.path / "ran.txt").exists()


def test_plan_respects_selection(pytester, columns):
    pytester.makeini("[pytest]\nprob_runs = 40\n")
    pytester.makepyfile(bench_plan=BENCH_PLAN)
    result = _run(pytester, "--prob-plan", "-k", "refund or smoke")
    names = [ln.split()[0] for ln in _table(result)[1:]]
    assert names == ["classify::refund", "smoke"]
    result = _run(pytester, "--prob-plan", "-m", "not slow")
    assert "ungated" not in "\n".join(_section(result))
    # selecting single runs plans exactly those runs
    result = _run(
        pytester, "--prob-plan", "--prob-runs=9", "-k", "refund and (run1 or run2)"
    )
    assert _table(result)[1].split()[:2] == ["classify::refund", "2"]
    # nothing selected: pytest's own "no tests" exit, and no section
    result = _run(pytester, "--prob-plan", "-k", "nothing_matches")
    assert result.ret == pytest.ExitCode.NO_TESTS_COLLECTED
    assert "probability: plan" not in result.stdout.str()


def test_plan_without_gates_shows_flake_columns_only(pytester, columns):
    pytester.makepyfile(bench_plain="def bench_one():\n    pass\n")
    result = _run(pytester, "--prob-plan", "--prob-runs=29", "--prob-plan-flake=0.1")
    table = _table(result)
    assert table[0] == "  case  runs  catch 10%"
    assert table[1] == "  one     29        95%"
    assert "min runs" not in "\n".join(_section(result))


def test_plan_never_and_beyond_cap(pytester, columns):
    pytester.makepyfile(
        bench_hard="""
import pytest

@pytest.mark.probability(min_rate=0.97)
def bench_at_bar():
    pass

@pytest.mark.probability(min_passes=20000)
def bench_huge():
    pass
"""
    )
    result = _run(pytester, "--prob-plan", "--prob-runs=10")
    rows = {ln.split()[0]: ln.split() for ln in _table(result)[1:]}
    assert rows["at_bar"][3] == "never"
    assert rows["at_bar"][4] == "0%"
    assert rows["huge"][2:5] == ["20,000", ">10,000", "0%"]
    # a different assumed rate moves the answer
    result = _run(pytester, "--prob-plan", "--prob-runs=10", "--prob-plan-assume=0.995")
    row = _table(result)[1].split()
    assert row[0] == "at_bar" and row[3].replace(",", "").isdigit()


def test_plan_cost_from_report(pytester, columns):
    pytester.makepyfile(bench_plan=BENCH_PLAN)
    run = _run(pytester, "--prob-runs=5", "--prob-json=last.json", "-k", "not billing")
    assert run.ret == pytest.ExitCode.TESTS_FAILED  # 5 runs can't pass the gates
    (pytester.path / "ran.txt").unlink()
    result = _run(
        pytester, "--prob-plan", "--prob-plan-report=last.json", "--prob-plan-flake=0.1"
    )
    assert result.ret == 0
    assert not (pytester.path / "ran.txt").exists()
    table = _table(result)
    assert table[0].endswith("catch 10%     cost")
    cells = {ln.split()[0]: ln.split() for ln in table[1:]}
    assert cells["classify::refund"][-1] == "$0.0020"
    assert cells["classify::billing"][-1] == "10%"  # not in the report: blank
    assert cells["smoke"][-1] == "$0.2000"  # 20 runs × $0.01
    assert cells["ungated"][-1] == "$0.0000"
    assert _footer(result)[1] == (
        "  Cost:    $0.2020 projected from last.json (1 case not in it)"
    )
    assert any(ln.startswith("  cost ") for ln in _section(result))
    # the report it read is never overwritten by the plan
    before = (pytester.path / "last.json").read_text()
    _run(
        pytester,
        "--prob-plan",
        "--prob-plan-report=last.json",
        "--prob-json=last.json",
    )
    assert (pytester.path / "last.json").read_text() == before


@pytest.mark.parametrize(
    "args, message",
    [
        (["--prob-plan-assume=1.5"], "--prob-plan-assume must be strictly between"),
        (["--prob-plan-assume=0"], "--prob-plan-assume must be strictly between"),
        (["--prob-plan-flake=0.1,abc"], "--prob-plan-flake must be a number"),
        (["--prob-plan-flake=0.1,"], "--prob-plan-flake must be comma-separated rates"),
        (["--prob-plan-flake=10"], "did you mean 0.1?"),
        (["--prob-plan", "--prob-plan-report=missing.json"], "cannot read missing"),
        (["--prob-plan", "--prob-plan-report=bad.json"], "bad.json is not JSON"),
        (["--prob-plan", "--prob-plan-report=list.json"], "not a pytest-probability"),
        (["--prob-plan", "--prob-plan-report=rowless.json"], "has a row without case"),
    ],
)
def test_plan_validation(pytester, args, message):
    pytester.makepyfile(bench_plain="def bench_one():\n    pass\n")
    (pytester.path / "bad.json").write_text("{nope")
    (pytester.path / "list.json").write_text("[]")
    (pytester.path / "rowless.json").write_text(json.dumps({"rows": [{"case": "x"}]}))
    result = _run(pytester, *args)
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines([f"*{message}*"])


def test_plan_stops_on_collection_errors(pytester):
    pytester.makepyfile(
        bench_plain="def bench_one():\n    pass\n", bench_broken="import nope\n"
    )
    result = _run(pytester, "--prob-plan")
    assert result.ret == pytest.ExitCode.INTERRUPTED
    assert "probability: plan" not in result.stdout.str()


def test_plan_ignores_xdist(pytester, columns):
    pytest.importorskip("xdist")
    pytester.makeini("[pytest]\nprob_runs = 40\n")
    pytester.makepyfile(bench_plan=BENCH_PLAN)
    serial = _run(pytester, "--prob-plan")
    dist = _run(pytester, "--prob-plan", "-n", "2")
    assert dist.ret == 0
    assert _section(dist) == _section(serial)
    # no workers started, so nothing ran anywhere
    assert "workers" not in dist.stdout.str()
    assert not (pytester.path / "ran.txt").exists()


def test_output_unchanged_without_plan(pytester, columns):
    pytester.makepyfile(bench_plain="def bench_one():\n    pass\n")
    plain = _run(pytester, "--prob-runs=3")
    with_options = _run(
        pytester, "--prob-runs=3", "--prob-plan-assume=0.9", "--prob-plan-flake=0.2"
    )
    assert with_options.ret == plain.ret == 0
    assert "probability: plan" not in with_options.stdout.str()
    strip = lambda r: [ln for ln in r.stdout.lines if " in " not in ln]  # noqa: E731
    assert strip(with_options) == strip(plain)
