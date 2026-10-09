"""Anytime-valid sequential testing (--prob-stop=sequential).

Two claims are under test. The statistics: the confidence sequence is
the beta-binomial mixture boundary, computed right, and it keeps its
coverage however often it is checked — exactly, by a forward pass over
every path of runs, and by a seeded simulation — where a fixed-run
interval checked after every run does not. The plugin: a rate gate
stops as soon as its sequence is decided, a case that reaches its
budget is judged on the sequence (never on a fixed-run interval), and
errors, xdist, hidden intervals, JSON, explain and plan follow.
"""

import functools
import itertools
import json
import math
import random
from collections import Counter

import pytest

from pytest_probability import stats
from pytest_probability.plugin import (
    FAIL,
    PASS,
    UNDECIDED,
    CaseStats,
    Gate,
    StatsConfig,
    _first_true,
)

ALPHAS = {0.8: 0.2, 0.9: 0.1, 0.95: 0.05, 0.99: 0.01}

# ---------------------------------------------------------------------------
# The boundary: mixture_log_wealth
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("prior", [(0.5, 0.5), (1.0, 1.0), (2.0, 0.7)])
def test_log_wealth_is_the_beta_binomial_ratio(prior):
    scipy_stats = pytest.importorskip("scipy.stats")
    a, b = prior
    for n, x, p in itertools.product((1, 7, 40, 300), (0, 1, 3), (0.05, 0.5, 0.93)):
        for k in {min(x, n), n - min(x, n)}:
            # One sequence of k passes in n runs has mixture probability
            # betabinom.pmf / C(n, k).
            mix = scipy_stats.betabinom.logpmf(k, n, a, b) - math.log(math.comb(n, k))
            direct = mix - k * math.log(p) - (n - k) * math.log1p(-p)
            got = stats.mixture_log_wealth(k, n, p, prior)
            assert got == pytest.approx(direct, rel=1e-10, abs=1e-10), (k, n, p)


def test_log_wealth_is_a_product_of_one_run_bets():
    # The martingale form: each run multiplies the wealth by the
    # mixture's prediction over p's — the same whatever the order.
    rng = random.Random(7)
    for _ in range(200):
        n = rng.randint(1, 60)
        runs = [rng.random() < 0.7 for _ in range(n)]
        p = rng.uniform(0.01, 0.99)
        log_wealth, passes = 0.0, 0
        for i, passed in enumerate(runs):
            predict = (0.5 + passes) / (1.0 + i)
            if passed:
                log_wealth += math.log(predict / p)
                passes += 1
            else:
                log_wealth += math.log((1.0 - predict) / (1.0 - p))
        assert stats.mixture_log_wealth(passes, n, p) == pytest.approx(
            log_wealth, rel=1e-11, abs=1e-11
        )


def test_log_wealth_matches_numerical_integration():
    # Jeffreys' density is uniform in θ with q = sin²θ: integrate the
    # likelihood ratio over θ by the midpoint rule.
    steps = 200_000
    for x, n, p in [(0, 5, 0.3), (3, 10, 0.5), (9, 10, 0.6), (20, 25, 0.95)]:
        total = 0.0
        for i in range(steps):
            q = math.sin((i + 0.5) / steps * math.pi / 2) ** 2
            total += q**x * (1 - q) ** (n - x)
        integral = total / steps / (p**x * (1 - p) ** (n - x))
        assert math.exp(stats.mixture_log_wealth(x, n, p)) == pytest.approx(
            integral, rel=1e-6
        )


def test_log_wealth_edges_and_validation():
    assert stats.mixture_log_wealth(0, 0, 0.3) == 0.0  # M₀ = 1
    assert stats.mixture_log_wealth(1, 3, 0.0) == math.inf
    assert stats.mixture_log_wealth(2, 3, 1.0) == math.inf
    assert stats.mixture_log_wealth(0, 3, 0.0) < 0.0
    for bad in [(-1, 3, 0.5), (4, 3, 0.5), (1, 3, 1.5), (1.5, 3, 0.5)]:
        with pytest.raises(ValueError):
            stats.mixture_log_wealth(*bad)
    with pytest.raises(ValueError):
        stats.mixture_log_wealth(1, 3, 0.5, prior=(0.0, 1.0))


# ---------------------------------------------------------------------------
# The interval: confidence_sequence
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("level", [0.5, 0.8, 0.95, 0.99])
def test_bounds_are_where_the_wealth_reaches_one_over_alpha(level):
    threshold = -math.log1p(-level)
    for n in (1, 2, 5, 13, 40, 100, 1000):
        for x in sorted({0, 1, n // 3, n // 2, n - 1, n}):
            low, high = stats.confidence_sequence(x, n, level)
            assert 0.0 <= low <= x / n <= high <= 1.0
            for bound, inside in ((low, x > 0), (high, x < n)):
                if inside:
                    wealth = stats.mixture_log_wealth(x, n, bound)
                    assert wealth == pytest.approx(threshold, abs=1e-8), (x, n)
            # just inside the bounds the rate is kept, just outside it isn't
            width = high - low
            for p in (low + width * 1e-6, high - width * 1e-6):
                assert stats.mixture_log_wealth(x, n, p) < threshold
            if x:
                assert stats.mixture_log_wealth(x, n, low * (1 - 1e-6)) >= threshold
            if x < n:
                outside = 1 - (1 - high) * (1 - 1e-6)
                assert stats.mixture_log_wealth(x, n, outside) >= threshold


def test_bounds_match_a_brute_force_scan():
    # The interval is {p : M(p) < 1/α}: scan a grid of p and compare.
    grid = [i / 20_000 for i in range(1, 20_000)]
    for x, n, level in [(7, 10, 0.95), (53, 53, 0.95), (3, 12, 0.8), (70, 100, 0.99)]:
        threshold = -math.log1p(-level)
        kept = [p for p in grid if stats.mixture_log_wealth(x, n, p) < threshold]
        low, high = stats.confidence_sequence(x, n, level)
        assert kept[0] - 1 / 20_000 <= low <= kept[0]
        assert kept[-1] <= high <= kept[-1] + 1 / 20_000


def test_closed_forms_and_edges():
    assert stats.confidence_sequence(0, 0) == (0.0, 1.0)
    # One pass: M₁(p) = ½/p, so the bound is α/2.
    assert stats.confidence_sequence(1, 1, 0.95) == pytest.approx((0.025, 1.0))
    assert stats.confidence_sequence(0, 1, 0.95) == pytest.approx((0.0, 0.975))
    # n of n: exp(−(log(1/α) − log mix)/n), mirrored for 0 of n.
    for n in (3, 53, 400):
        log_mix = math.lgamma(n + 0.5) - math.lgamma(n + 1) - 0.5 * math.log(math.pi)
        low = math.exp(-(math.log(20) - log_mix) / n)
        assert stats.confidence_sequence(n, n, 0.95)[0] == pytest.approx(low, rel=1e-14)
        assert stats.confidence_sequence(0, n, 0.95)[1] == pytest.approx(
            1 - low, rel=1e-12
        )
    # Symmetric prior: mirrored counts give mirrored bounds.
    low, high = stats.confidence_sequence(9, 40, 0.9)
    assert stats.confidence_sequence(31, 40, 0.9) == pytest.approx((1 - high, 1 - low))
    # Tiny and near-1 bounds keep their precision.
    low, high = stats.confidence_sequence(1, 10_000)
    assert 0 < low < 1e-7 and high < 0.002
    assert 1 - stats.confidence_sequence(9_999, 10_000)[1] > 0


def test_dispatch_and_validation():
    assert stats.proportion_interval(7, 10, 0.9, "sequential", (0.5, 0.5)) == (
        stats.confidence_sequence(7, 10, 0.9)
    )
    assert "sequential" not in stats.METHODS  # not an option value
    for bad in [(-1, 3), (4, 3), (1, -1), (True, 3)]:
        with pytest.raises(ValueError):
            stats.confidence_sequence(*bad)
    for level in (0.0, 1.0, float("nan")):
        with pytest.raises(ValueError):
            stats.confidence_sequence(1, 3, level)
    with pytest.raises(ValueError):
        stats.confidence_sequence(1, 3, 0.95, prior=(1.0,))
    with pytest.raises(ValueError, match="sequential"):
        stats.proportion_interval(1, 3, 0.95, "nope")


@pytest.mark.parametrize("level", [0.5, 0.8, 0.95, 0.99])
def test_wider_than_a_fixed_run_interval(level):
    # The price of validity at every run: never narrower than
    # Clopper-Pearson at the same level, and clearly wider by 100 runs.
    for n in [*range(1, 61), 100, 333]:
        for x in range(n + 1):
            low, high = stats.confidence_sequence(x, n, level)
            cp_low, cp_high = stats.clopper_pearson(x, n, level)
            assert low <= cp_low + 1e-12 and high >= cp_high - 1e-12, (x, n)
    low, high = stats.confidence_sequence(70, 100, 0.95)
    cp_low, cp_high = stats.clopper_pearson(70, 100, 0.95)
    assert 1.5 < (high - low) / (cp_high - cp_low) < 1.7


@pytest.mark.parametrize("level", [stats.SEQUENCE_MONOTONE_LEVEL, 0.5, 0.8, 0.95, 0.99])
def test_a_run_moves_both_bounds_one_way(level):
    # The property early stopping leans on, from the monotone level up:
    # a pass never lowers a bound, a non-pass never raises one.
    tol = 1e-12
    cs = functools.lru_cache(maxsize=None)(
        lambda x, n: stats.confidence_sequence(x, n, level)
    )
    for n in range(0, 90):
        for x in range(n + 1):
            low, high = cs(x, n)
            passed, failed = cs(x + 1, n + 1), cs(x, n + 1)
            assert passed[0] >= low - tol and passed[1] >= high - tol, (x, n)
            assert failed[0] <= low + tol and failed[1] <= high + tol, (x, n)


# ---------------------------------------------------------------------------
# Validity under continuous monitoring
# ---------------------------------------------------------------------------


def _bands(interval, p, horizon):
    """For n = 1 .. ``horizon``: the (first, last) pass counts x whose
    ``interval(x, n)`` keeps p. Coverage is monotone in x — more passes
    move both bounds up — so each end is a search, started from the last
    n's (a run moves it by at most one)."""
    bands, first, last = [], 0, 0
    for n in range(1, horizon + 1):
        # p above the interval below ``first``; below it past ``last``.
        first = _first_true(lambda x: interval(x, n)[1] >= p, n + 1, first)
        last = _first_true(lambda x: interval(x, n)[0] > p, n + 1, last + 1) - 1
        bands.append((first, last))
    return bands


def ever_left_out(interval, p, horizon):
    """The exact chance that ``interval``, checked after every one of
    ``horizon`` runs that each pass with probability p, leaves p out at
    least once: a forward pass over the pass counts still covered."""
    alive, out = {0: 1.0}, 0.0
    for first, last in _bands(interval, p, horizon):
        step = {}
        for x, w in alive.items():
            step[x + 1] = step.get(x + 1, 0.0) + w * p
            step[x] = step.get(x, 0.0) + w * (1 - p)
        alive = {}
        for x, w in step.items():
            if first <= x <= last:
                alive[x] = w
            else:
                out += w
    return out


RATES = (0.01, 0.1, 0.3, 0.5, 0.75, 0.9, 0.97, 0.995)


@pytest.mark.parametrize("level", [0.8, 0.95, 0.99])
def test_never_leaves_the_rate_out_more_than_alpha(level):
    # Exact, not simulated: over every path of 300 runs.
    seq = functools.partial(stats.confidence_sequence, level=level)
    for p in RATES:
        assert ever_left_out(seq, p, 300) <= ALPHAS[level], p


def test_peeking_at_a_fixed_run_interval_is_not_valid():
    # The same check on Clopper-Pearson, which is only valid for a run
    # count fixed in advance: looked at after every run, it leaves the
    # truth out far more often than 5%.
    exact = functools.partial(stats.clopper_pearson, level=0.95)
    seq = functools.partial(stats.confidence_sequence, level=0.95)
    for p in (0.3, 0.5, 0.9):
        naive = ever_left_out(exact, p, 300)
        assert naive > 0.2, p
        assert ever_left_out(seq, p, 300) < naive / 5, p


@pytest.mark.slow
@pytest.mark.parametrize("level", [0.8, 0.9, 0.95, 0.99])
def test_never_leaves_the_rate_out_more_than_alpha_over_2000_runs(level):
    seq = functools.partial(stats.confidence_sequence, level=level)
    for p in RATES:
        assert ever_left_out(seq, p, 2000) <= ALPHAS[level], p


def _simulate(interval, p, level, horizon, streams, seed):
    """Seeded continuous monitoring: the share of ``streams`` Bernoulli(p)
    runs of length ``horizon`` whose interval ever leaves p out."""
    bands = _bands(interval, p, horizon)
    rng = random.Random(seed)
    hits = 0
    for _ in range(streams):
        x = 0
        for first, last in bands:
            x += rng.random() < p
            if not first <= x <= last:
                hits += 1
                break
    return hits / streams


@pytest.mark.slow
@pytest.mark.parametrize("level", [0.8, 0.95])
def test_simulated_streams_keep_their_coverage(level):
    # 2,000 seeded streams of 1,000 runs per rate. The sequence's
    # ever-left-out share stays within Monte Carlo slack (three standard
    # errors) of α; the fixed-run interval's is several times α.
    streams, horizon, alpha = 2000, 1000, ALPHAS[level]
    slack = 3 * math.sqrt(alpha * (1 - alpha) / streams)
    seq = functools.partial(stats.confidence_sequence, level=level)
    exact = functools.partial(stats.clopper_pearson, level=level)
    for i, p in enumerate((0.05, 0.3, 0.5, 0.9, 0.98)):
        got = _simulate(seq, p, level, horizon, streams, seed=i)
        assert got <= alpha + slack, (p, got)
        naive = _simulate(exact, p, level, horizon, streams, seed=100 + i)
        assert naive > 2.5 * alpha, (p, naive)


# ---------------------------------------------------------------------------
# The stopping rule: Gate.decided against every continuation
# ---------------------------------------------------------------------------


def _seq(bar, level=0.95, errors="count", runs=1):
    return Gate(
        "rate", StatsConfig(level=level), min_rate=bar, errors=errors, runs=runs
    ).as_sequential()


SEQ_GATES = [
    _seq(bar, level, errors)
    for bar in (0.3, 0.75, 0.9)
    for level in (0.8, 0.95, 0.3)  # 0.3: below the monotone level
    for errors in ("count", "exclude")
]


def _compositions(n, k):
    if k == 1:
        yield (n,)
        return
    for i in range(n + 1):
        for rest in _compositions(n - i, k - 1):
            yield (i, *rest)


@pytest.mark.parametrize("planned", [4, 9, 12])
def test_decided_stops_exactly_when_it_should(planned):
    # For every way the runs so far can have gone (pass, fail, error,
    # skip): PASS or FAIL is the verdict on them; UNDECIDED means no
    # state the remaining runs can reach — at any run, not only the
    # last — is PASS or FAIL; and keeping on means the verdict is
    # UNDECIDED now and (from the monotone level up) one of them is.
    stops = Counter()
    for gate in SEQ_GATES:
        verdict = functools.lru_cache(maxsize=None)(gate.verdict)
        for seen in range(1, planned):
            left = planned - seen
            for p, f, e, _skips in _compositions(seen, 4):
                got = gate.decided(p, f, e, left)
                judged = p + f + (e if gate.errors == "count" else 0)
                now = verdict(p, judged) if judged else UNDECIDED
                if p + f + e == 0:
                    assert got is None
                    continue
                # Reachable: i more passes among k more judged runs.
                ahead = {
                    verdict(p + i, judged + k)
                    for k in range(left + 1)
                    for i in range(k + 1)
                    if judged + k
                }
                if got in (PASS, FAIL):
                    assert got == now
                elif got == UNDECIDED:
                    assert now == UNDECIDED and ahead <= {UNDECIDED}, (gate, p, f, e)
                else:
                    assert now == UNDECIDED
                    if gate.stats.level >= stats.SEQUENCE_MONOTONE_LEVEL:
                        assert ahead - {UNDECIDED}, (gate, p, f, e, left)
                stops[got] += 1
    assert stops[PASS] and stops[FAIL] and stops[UNDECIDED] and stops[None]


def test_fixed_run_gates_are_still_curtailed():
    gate = Gate("count", StatsConfig(), min_passes=12)
    assert gate.decided(12, 0, 0, 8) == gate.settled(12, 0, 0, 8) == PASS
    rate = Gate("rate", StatsConfig(), min_rate=0.9)
    assert not rate.sequential
    for state in [(5, 0, 0, 5), (3, 6, 0, 2), (40, 0, 0, 0)]:
        assert rate.decided(*state) == rate.settled(*state)


def test_sequential_gate_shape():
    gate = Gate("rate", StatsConfig(level=0.9, method="wilson"), min_rate=0.8)
    seq = gate.as_sequential()
    assert seq.sequential and not gate.sequential
    assert seq.stats.method == "sequential" and seq.stats.level == 0.9
    assert seq.stats.prior == stats.SEQUENCE_PRIOR
    assert seq.describe() == "min_rate=0.8 at 90% (sequential)"
    assert Gate.from_record(seq.to_record()) == Gate(
        "rate",
        StatsConfig(method="sequential", level=0.9, prior=(0.5, 0.5)),
        min_rate=0.8,
    )
    assert seq.judge(53, 53) == (stats.confidence_sequence(53, 53, 0.9), PASS)
    # 53 of 53 is where a 90% bar passes at 95%; 36 with a fixed count.
    assert _seq(0.9).min_runs() == 53
    assert Gate("rate", StatsConfig(), min_rate=0.9).min_runs() == 36


def test_cutoff_walk_matches_cutoffs():
    for gate in (_seq(0.9), _seq(0.4, level=0.8), _seq(0.75, level=0.3)):
        walk = itertools.islice(gate._cutoff_walk(), 250)
        for t, cut in enumerate(walk, start=1):
            assert cut == gate.cutoffs(t), (gate, t)


def test_first_true_finds_every_boundary():
    for end in range(0, 30):
        for boundary in range(0, end + 1):
            for guess in range(-2, end + 3):
                calls = []

                def holds(x, b=boundary):
                    calls.append(x)
                    return x >= b

                assert _first_true(holds, end, guess) == boundary
                assert all(0 <= x < end for x in calls)


# ---------------------------------------------------------------------------
# Planning numbers against every path
# ---------------------------------------------------------------------------


def _paths(gate, n, rate):
    """(P(PASS), E[runs]) by walking every pass/fail path of n runs with
    ``decided()`` itself: the reference for the forward passes."""
    decided = functools.lru_cache(maxsize=None)(gate.decided)
    verdict = functools.lru_cache(maxsize=None)(gate.verdict)
    chance = used = 0.0
    for path in itertools.product((True, False), repeat=n):
        weight, passes = 1.0, 0
        for t, passed in enumerate(path, start=1):
            weight *= rate if passed else 1 - rate
            passes += passed
            got = (
                verdict(passes, t)
                if t == n
                else decided(passes, t - passes, 0, n - t)
            )
            if got is not None:
                break
        # Paths that agree up to the stop are counted once each: share
        # the weight of the path's unseen tail out (it sums to 1).
        chance += weight * (got == PASS) / 2 ** (n - t)
        used += weight * t / 2 ** (n - t)
    return chance, used


@pytest.mark.parametrize(
    "gate",
    [
        _seq(0.5),
        _seq(0.3, level=0.8),
        _seq(0.6, level=0.3),
        Gate("count", StatsConfig(), min_passes=7),
    ],
    ids=["seq-0.5", "seq-0.3-at-80%", "seq-below-monotone", "count"],
)
@pytest.mark.parametrize("rate", [0.35, 0.8])
def test_expected_runs_and_power_match_every_path(gate, rate):
    for n in (1, 6, 12):
        chance, used = _paths(gate, n, rate)
        assert gate.expected_runs(n, rate) == pytest.approx(used, rel=1e-12)
        if gate.sequential:
            assert gate.power(n, rate) == pytest.approx(chance, rel=1e-12, abs=1e-15)


def test_runs_for_power_is_the_first_budget_that_gets_there():
    gate = _seq(0.9)
    n = gate.runs_for_power(0.97, 0.8, 10_000)
    assert n == 197
    assert gate.power(n, 0.97) >= 0.8 > gate.power(n - 1, 0.97)
    assert gate.runs_for_power(0.9, 0.8, 10_000) is None  # at the bar: never
    assert gate.runs_for_power(0.97, 0.8, 100) is None  # beyond the cap
    assert _seq(0.5).runs_for_power(0.6, 0.999, 300) is None


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------

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


def _run(pytester, *args):
    return pytester.runpytest(
        "--tb=no", "-p", "no:cacheprovider", "-W", "ignore::pytest.PytestWarning",
        *args,
    )


def _json(pytester, name="r.json"):
    return json.loads((pytester.path / name).read_text())


def _rows(data):
    return {r["case"]: r for r in data["rows"]}


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


def _counts(pattern, runs):
    steps = [pattern[i % len(pattern)] for i in range(runs)]
    return steps.count("P"), steps.count("F"), steps.count("E")


# min_rate=0.9 over at most 100 runs: solid PASSes at 53 of 53, close
# (one fail in 20) can't clear 90% by run 100 and stops UNDECIDED, weak
# (one in 4) FAILs at 60. smoke is a count gate: curtailed, as ever.
CLASSIFY = _function(
    "classify",
    [("solid", "P"), ("close", "P" * 19 + "F"), ("weak", "PPPF")],
    "min_rate=0.9, runs=100",
)
SMOKE = _function(
    "smoke", [("steady", "P"), ("broken", "PPFF")], "min_passes=19, runs=20"
)
PATTERNS = {
    "classify::solid": "P",
    "classify::close": "P" * 19 + "F",
    "classify::weak": "PPPF",
    "smoke::steady": "P",
    "smoke::broken": "PPFF",
}


def test_each_verdict_stops_as_soon_as_its_sequence_says(pytester):
    pytester.makepyfile(bench_s=_bench(CLASSIFY, SMOKE))
    result = _run(pytester, "--prob-stop=sequential", "-rs", "--prob-json=r.json")
    assert result.ret == pytest.ExitCode.TESTS_FAILED
    # (the gate items: 2 PASS, 2 FAIL and 1 UNDECIDED verdicts)
    result.assert_outcomes(passed=200, failed=3, skipped=121, xfailed=21)
    assert _section(result, "probability") == [
        "  classify::solid  53/53  [90%, 100%] seq  $0.0530  decided after 53/100",
        "  classify::close  79/83  [83%,  99%] seq  $0.0830  FLAKY"
        "  decided after 83/100",
        "  classify::weak   45/60  [55%,  90%] seq  $0.0600  FLAKY"
        "  decided after 60/100",
        "  smoke::steady    19/19  [82%, 100%]      $0.0190  decided after 19/20",
        "  smoke::broken      2/4  [ 7%,  93%]      $0.0040  FLAKY  decided after 4/20",
    ]
    assert _line(result, "  Stopped:") == (
        "  Stopped: 5 cases early, saving 121 of 340 runs (36%) and about $0.1210"
    )
    assert _line(result, "  Gates:") == "  Gates:   2 passed, 2 failed, 1 undecided"
    assert _section(result, "probability: gates") == [
        "  classify::close  79/83  [83%, 99%] seq  ≥90%        UNDECIDED"
        "  decided after 83/100",
        "  classify::weak   45/60  [55%, 90%] seq  ≥90%        FAIL"
        "  decided after 60/100",
        "  smoke::broken      2/4  [ 7%, 93%]      ≥19 passes  FAIL"
        "  decided after 4/20",
    ]
    result.stdout.fnmatch_lines([
        "SKIPPED [[]47[]] bench_s.py: probability gate: decided after 53/100 runs"
        " (PASS, sequential)",
        "SKIPPED [[]17[]] bench_s.py: probability gate: decided after 83/100 runs"
        " (UNDECIDED, sequential)",
        "SKIPPED [[]1[]] bench_s.py: probability gate: decided after 19/20 runs"
        " (PASS)",
    ])
    # Each stop is the first run at which the sequence decided.
    for case, row in _rows(_json(pytester)).items():
        gate, info = Gate.from_record(row["gate"]), row["stopped"]
        assert gate.sequential == case.startswith("classify")
        after, planned = info["after"], info["planned"]
        assert info["verdict"] == row["gate"]["verdict"]
        assert gate.decided(*_counts(PATTERNS[case], after), planned - after) == (
            info["verdict"]
        )
        before = _counts(PATTERNS[case], after - 1)
        assert gate.decided(*before, planned - after + 1) is None


def test_run_major_order_stops_the_same(pytester):
    # The rule needs only each case's counts, so interleaving the cases'
    # runs changes nothing but the order.
    pytester.makepyfile(bench_s=_bench(CLASSIFY, SMOKE))
    _run(pytester, "--prob-stop=sequential", "--prob-json=a.json")
    _run(pytester, "--prob-stop=sequential", "--prob-transpose", "--prob-json=b.json")
    pick = lambda data: {  # noqa: E731
        c: (r["passes"], r["total"], r["stopped"], r["gate"])
        for c, r in _rows(data).items()
    }
    assert pick(_json(pytester, "a.json")) == pick(_json(pytester, "b.json"))


def test_sequential_stops_sooner_than_curtailment_on_clear_cases(pytester):
    pytester.makepyfile(bench_s=_bench(CLASSIFY, SMOKE))
    _run(pytester, "--prob-stop=curtail", "--prob-json=c.json")
    _run(pytester, "--prob-stop=sequential", "--prob-json=s.json")
    _run(pytester, "--prob-json=o.json")
    curtail, seq, off = (
        _rows(_json(pytester, f)) for f in ("c.json", "s.json", "o.json")
    )
    assert seq["classify::solid"]["total"] == 53
    assert curtail["classify::solid"]["total"] == 96
    def verdicts(rows):
        return {c: r["gate"]["verdict"] for c, r in rows.items()}

    assert verdicts(seq) == verdicts(curtail) == verdicts(off)
    assert sum(r["total"] for r in seq.values()) == 219
    assert sum(r["total"] for r in curtail.values()) == 287


def test_the_budget_is_judged_on_the_sequence(pytester):
    # 60 runs at a 90% bar. edge passes 46 of 60 while its sequence keeps
    # FAIL in reach to the last run, so it runs them all — and ends
    # UNDECIDED on the sequence, where the fixed-run interval would FAIL.
    # late (one fail, then passes) would PASS a fixed run count at 59 of
    # 60, but its sequence can't reach 90% within 60 runs: it stops as
    # UNDECIDED once FAIL is out of reach too.
    bench = _bench(
        _function(
            "g",
            [("edge", "PPPF" * 14 + "PPPP"), ("late", "F" + "P" * 59)],
            "min_rate=0.9, runs=60",
        )
    )
    pytester.makepyfile(bench_s=bench)
    _run(pytester, "--prob-stop=sequential", "--prob-json=seq.json")
    _run(pytester, "--prob-json=fixed.json")
    seq = _rows(_json(pytester, "seq.json"))
    fixed = _rows(_json(pytester, "fixed.json"))
    edge = seq["g::edge"]
    assert (edge["passes"], edge["total"], edge["stopped"]) == (46, 60, None)
    assert edge["gate"]["verdict"] == UNDECIDED
    assert (edge["gate"]["low"], edge["gate"]["high"]) == stats.confidence_sequence(
        46, 60
    )
    assert edge["ci"]["method"] == "sequential"
    assert fixed["g::edge"]["gate"]["verdict"] == FAIL
    late = seq["g::late"]
    assert late["stopped"]["after"] == 47
    assert late["gate"]["verdict"] == UNDECIDED
    assert fixed["g::late"]["gate"]["verdict"] == PASS


def test_errors_count_or_leave_the_sample(pytester):
    # Every run errors. Counted, they are non-passes: 0 of 7 is below a
    # 50% bar. Excluded, nothing is judged, and once 6 runs are left (7
    # are the fewest that could PASS or FAIL) it stops as UNDECIDED.
    bench = _bench(_function("rate", [("down", "E")], "min_rate=0.5, runs=10"))
    pytester.makepyfile(bench_s=bench)
    counted = _run(pytester, "--prob-stop=sequential", "--prob-json=r.json")
    (row,) = _json(pytester)["rows"]
    assert row["stopped"]["after"] == 7 and row["gate"]["verdict"] == FAIL
    assert counted.ret == pytest.ExitCode.TESTS_FAILED  # errors still fail
    excluded = _run(
        pytester, "--prob-stop=sequential", "--prob-json=r.json",
        "-o", "prob_errors=exclude",
    )
    (row,) = _json(pytester)["rows"]
    assert row["stopped"]["after"] == 4
    assert row["gate"]["verdict"] == UNDECIDED and row["gate"]["total"] == 0
    excluded.assert_outcomes(xfailed=4, skipped=6, failed=1)  # its gate item


def test_errors_excluded_mid_stream(pytester):
    # Excluded errors are no sample: the sequence counts judged runs
    # only, so a case with every other run erroring needs twice the runs.
    bench = _bench(
        _function("rate", [("clean", "P"), ("noisy", "PE")], "min_rate=0.5, runs=30")
    )
    pytester.makepyfile(bench_s=bench)
    _run(pytester, "--prob-stop=sequential", "--prob-json=r.json",
         "-o", "prob_errors=exclude")
    rows = _rows(_json(pytester))
    assert rows["rate::clean"]["stopped"]["after"] == 7
    noisy = rows["rate::noisy"]
    assert noisy["stopped"]["after"] == 13
    assert (noisy["gate"]["passes"], noisy["gate"]["total"]) == (7, 7)
    assert noisy["gate"]["excluded"] == 6


def test_only_stoppable_rate_gates_are_sequential(pytester):
    # Cases whose runs another verdict needs run every run, judged by
    # their own method as without --prob-stop; marks' method= gives way
    # to the sequence, confidence= stays.
    down = [("x", "FFFFPPPPPP")]
    margin = _function(
        "ab", down, 'compare="arm", margin=0.1, min_rate=0.9',
        extra='@pytest.mark.parametrize("arm", ["a", "b"])\n',
    ).replace("def bench_ab(case, pattern):", "def bench_ab(case, pattern, arm):")
    bench = _bench(
        _function("free", down, "runs=10"),
        _function("gated", down, 'min_rate=0.9, runs=10, method="wilson"'),
        _function("loose", down, "min_rate=0.9, runs=10, confidence=0.8"),
        _function("timed", down, "min_rate=0.9, max_latency=60, runs=10"),
        margin.replace('("ab", case)', '("ab", case, arm)'),
    )
    pytester.makepyfile(bench_s=bench)
    _run(pytester, "--prob-stop=sequential", "--prob-json=r.json", "--prob-runs=10")
    rows = _rows(_json(pytester))
    assert rows["gated::x"]["gate"]["method"] == "sequential"
    assert rows["gated::x"]["gate"]["confidence"] == 0.95
    assert rows["loose::x"]["gate"]["method"] == "sequential"
    assert rows["loose::x"]["gate"]["confidence"] == 0.8
    assert rows["gated::x"]["stopped"]["verdict"] == FAIL
    for case in ("free::x", "timed::x", "ab::x-a", "ab::x-b"):
        row = rows[case]
        assert row["total"] == 10 and row["stopped"] is None, case
        assert row["ci"]["method"] == "exact"
        if row["gate"] is not None:
            assert row["gate"]["method"] == "exact", case


def test_infeasible_warning_counts_the_sequence(pytester):
    pytester.makepyfile(
        bench_s=_bench(_function("g", [("x", "P")], "min_rate=0.9, runs=40"))
    )
    result = pytester.runpytest("-p", "no:cacheprovider", "--prob-stop=sequential")
    result.stdout.fnmatch_lines([
        "*InfeasibleGateWarning: g::x: min_rate=0.9 at 95% (sequential) needs"
        " ≥53 runs; this case has 40",
    ])


def test_no_intervals_keeps_the_gates_block_tag(pytester):
    pytester.makepyfile(bench_s=_bench(CLASSIFY))
    result = _run(pytester, "--prob-stop=sequential", "--prob-no-intervals")
    assert _section(result, "probability")[0] == (
        "  classify::solid  53/53  $0.0530  decided after 53/100"
    )
    assert _section(result, "probability: gates")[0].startswith(
        "  classify::close  79/83  [83%, 99%] seq  ≥90%"
    )


def test_ini_value(pytester):
    pytester.makepyfile(bench_s=_bench(CLASSIFY))
    result = _run(pytester, "-o", "prob_stop=sequential")
    assert "decided after 53/100" in result.stdout.str()
    bad = _run(pytester, "-o", "prob_stop=sometimes")
    assert bad.ret == pytest.ExitCode.USAGE_ERROR
    bad.stderr.fnmatch_lines(["*prob_stop must be one of off, curtail, sequential*"])


def _items(result, prefix="bench_s.py::"):
    """The ``-v`` result lines, as ``(id, outcome)``."""
    return [
        tuple(line.split()[:2])
        for line in result.stdout.lines
        if line.startswith(prefix)
    ]


def test_gate_items_judge_on_the_sequence(pytester):
    # A case's gate item comes after its skipped runs and reads the
    # verdict off the sequence — the case that reached its budget too.
    bench = _bench(
        _function("g", [("solid", "P")], "min_rate=0.9, runs=100"),
        _function("h", [("edge", "PPPF" * 14 + "PPPP")], "min_rate=0.9, runs=60"),
    )
    pytester.makepyfile(bench_s=bench)
    result = _run(pytester, "-v", "--prob-stop=sequential", "--junitxml=j.xml")
    items = dict(_items(result))
    assert items["bench_s.py::bench_g::solid[run53]"] == "PASSED"
    assert items["bench_s.py::bench_g::solid[run54]"] == "SKIPPED"
    assert items["bench_s.py::bench_g::solid[gate]"] == "PASSED"
    assert items["bench_s.py::bench_h::edge[run60]"] in ("PASSED", "XFAIL")
    assert items["bench_s.py::bench_h::edge[gate]"] == "FAILED"
    ids = [i for i, _ in _items(result)]
    assert ids.index("bench_s.py::bench_g::solid[gate]") == 100
    low, high = stats.confidence_sequence(46, 60)
    line = f"h::edge  46/60  [{round(low * 100)}%, {round(high * 100)}%] seq  ≥90%"
    summary = f"FAILED bench_s.py::bench_h::edge[gate] - {line}"
    result.stdout.fnmatch_lines([summary.replace("[", "[[]") + "*"])
    junit = (pytester.path / "j.xml").read_text()
    assert f'message="{line}  UNDECIDED"' in junit


def test_gate_items_under_loadgroup_match_a_serial_run(pytester):
    pytest.importorskip("xdist")
    pytester.makepyfile(bench_s=_bench(CLASSIFY, SMOKE))
    serial = _run(pytester, "--prob-stop=sequential")
    dist = _run(pytester, "--prob-stop=sequential", "-n", "2", "--dist", "loadgroup")
    assert serial.parseoutcomes() == dist.parseoutcomes()
    assert _section(serial, "probability: gates") == _section(
        dist, "probability: gates"
    )


# ---------------------------------------------------------------------------
# Unchanged elsewhere
# ---------------------------------------------------------------------------


def _strip(result):
    return [
        ln for ln in result.stdout.lines if " in " not in ln and "Report:" not in ln
    ]


def test_ungated_suites_are_unchanged(pytester):
    many = [(f"q{i}", p) for i, p in enumerate(["P", "PPPF", "PF"] * 4)]
    pytester.makepyfile(bench_s=_bench(_function("free", many, "runs=8")))
    off = _run(pytester, "--prob-json=a.json", "--prob-explain")
    seq = _run(
        pytester, "--prob-json=b.json", "--prob-explain", "--prob-stop=sequential"
    )
    assert _strip(off) == _strip(seq)
    assert off.ret == seq.ret
    a, b = _json(pytester, "a.json"), _json(pytester, "b.json")
    drop = lambda row: {k: v for k, v in row.items() if k != "latency"}  # noqa: E731
    assert list(map(drop, a["rows"])) == list(map(drop, b["rows"]))
    assert a["aggregates"] == b["aggregates"]
    assert b["stopping"]["mode"] == "sequential" and b["stopping"]["cases"] == 0


def test_curtail_is_unchanged(pytester):
    # Under curtail every gate keeps its fixed-run interval: no seq tag,
    # no sequential method, the same verdicts as a full run.
    pytester.makepyfile(bench_s=_bench(CLASSIFY, SMOKE))
    result = _run(pytester, "--prob-stop=curtail", "--prob-json=r.json")
    assert " seq" not in result.stdout.str()
    rows = _rows(_json(pytester))
    assert all(r["gate"]["method"] == "exact" for r in rows.values())
    assert all(r["ci"]["method"] == "exact" for r in rows.values())
    assert rows["classify::solid"]["stopped"]["after"] == 96
    assert "sequential" not in json.dumps(_json(pytester)["rows"])


# ---------------------------------------------------------------------------
# What a stopped case hides
# ---------------------------------------------------------------------------

# Six inputs over at most 20 runs at a 50% bar: P and PPPPPPPPPF pass
# at 7 of 7 and stop; PPPF needs longer.
MANY = [(f"q{i}", p) for i, p in enumerate(["P", "PPPF", "PPPPPPPPPF"] * 2)]
FEW_INPUTS = ("--prob-runs=20", "-o", "prob_min_inputs=3")


def test_aggregates_and_comparisons_hide_their_interval(pytester):
    arms = '@pytest.mark.parametrize("arm", ["a", "b"])\n'
    compared = (
        _function("cmp", MANY, 'compare="arm", min_rate=0.5', extra=arms)
        .replace("def bench_cmp(case, pattern):", "def bench_cmp(case, pattern, arm):")
        .replace('("cmp", case)', '("cmp", case, arm)')
    )
    bench = _bench(
        _function("gated", MANY, "min_rate=0.5"), _function("free", MANY), compared
    )
    pytester.makepyfile(bench_s=bench)
    result = _run(
        pytester, "--prob-stop=sequential", *FEW_INPUTS, "--prob-json=r.json",
        "--prob-metric=pass^2", "--prob-explain",
    )
    gated = next(ln for ln in result.stdout.lines if ln.startswith("  gated  "))
    assert gated.endswith("interval hidden: 6 cases stopped early")
    data = _json(pytester)
    aggs = {a["name"]: a for a in data["aggregates"]}
    assert aggs["gated"]["ci"] is None and aggs["gated"]["stopped"] == 6
    assert aggs["gated"]["metrics"]["pass^2"]["ci"] is None
    assert aggs["free"]["ci"] is not None and aggs["free"]["stopped"] == 0
    (cmp,) = data["comparisons"]
    assert cmp["ci"] is None and cmp["p"] is None and cmp["stopped"] == 12
    text = " ".join(result.stdout.str().split())
    assert (
        "No average or range is shown: all 6 of gated's inputs stopped early, once"
        " their gates' verdicts were decided (--prob-stop=sequential). A case that"
        " stops early has a fraction from fewer runs, and because it stopped as"
        " soon as its verdict was decided, that fraction can lean toward the"
        " verdict" in text
    )
    assert "Next: to see this line, run without --prob-stop=sequential." in text
    assert "Next: to compare the arms, run without --prob-stop=sequential." in text
    assert "--prob-stop=curtail" not in text


def test_baseline_margin_cases_run_every_run(pytester):
    pytester.makepyfile(bench_s=_bench(CLASSIFY))
    _run(pytester, "--prob-json=base.json")
    _run(pytester, "--prob-stop=sequential", "--prob-baseline=base.json",
         "--prob-margin=0.1", "--prob-json=r.json")
    data = _json(pytester)
    assert data["stopping"]["cases"] == 0
    assert all(r["total"] == 100 for r in data["rows"])
    assert all(r["gate"]["method"] == "exact" for r in data["rows"])
    _run(pytester, "--prob-stop=sequential", "--prob-baseline=base.json",
         "--prob-json=r.json")
    (cmp,) = _json(pytester)["baseline"]["comparisons"]
    assert cmp["ci"] is None and cmp["stopped"] == 3


# ---------------------------------------------------------------------------
# JSON, xdist, explain, plan
# ---------------------------------------------------------------------------


def test_json_fields(pytester):
    pytester.makepyfile(bench_s=_bench(CLASSIFY, SMOKE))
    _run(pytester, "--prob-stop=sequential", "--prob-json=r.json")
    data = _json(pytester)
    assert data["stopping"] == {
        "mode": "sequential",
        "active": True,
        "cases": 5,
        "runs_skipped": 121,
        "runs_planned": 340,
        "cost_avoided": pytest.approx(0.121),
    }
    rows = _rows(data)
    solid = rows["classify::solid"]
    low, high = stats.confidence_sequence(53, 53, 0.95)
    assert solid["ci"] == {
        "method": "sequential", "level": 0.95, "low": low, "high": high,
    }
    assert solid["gate"] == {
        "rule": "rate", "min_rate": 0.9, "confidence": 0.95, "method": "sequential",
        "prior": [0.5, 0.5], "errors": "count", "runs": 100, "passes": 53,
        "total": 53, "excluded": 0, "low": low, "high": high, "verdict": "pass",
    }
    assert solid["stopped"] == {
        "after": 53, "planned": 100, "skipped": 47, "verdict": "pass",
        "cost_avoided": pytest.approx(0.047),
    }
    # a count gate keeps its fixed-run interval
    assert rows["smoke::steady"]["gate"]["method"] == "exact"
    assert rows["smoke::steady"]["ci"]["method"] == "exact"


def test_xdist_loadgroup_stops_the_same(pytester):
    pytest.importorskip("xdist")
    pytester.makepyfile(bench_s=_bench(CLASSIFY, SMOKE))
    _run(pytester, "--prob-stop=sequential", "--prob-json=serial.json")
    dist = _run(
        pytester, "--prob-stop=sequential", "--prob-json=dist.json", "-n", "2",
        "--dist", "loadgroup", "-v",
    )
    serial, spread = _json(pytester, "serial.json"), _json(pytester, "dist.json")
    pick = lambda data: [  # noqa: E731
        (r["case"], r["passes"], r["total"], r["stopped"], r["gate"], r["ci"])
        for r in sorted(data["rows"], key=lambda r: r["case"])
    ]
    assert pick(spread) == pick(serial)
    assert spread["stopping"] == serial["stopping"]
    dist.stdout.fnmatch_lines(["*bench_classify::solid[[]run1[]]@classify::solid*"])


def test_xdist_without_loadgroup_warns_and_judges_as_usual(pytester):
    pytest.importorskip("xdist")
    pytester.makepyfile(bench_s=_bench(CLASSIFY))
    result = pytester.runpytest(
        "--tb=no", "-p", "no:cacheprovider", "--prob-stop=sequential",
        "--prob-json=r.json", "-n", "2",
    )
    result.stdout.fnmatch_lines([
        "*CurtailmentWarning: --prob-stop=sequential needs --dist loadgroup under"
        " pytest-xdist*every case runs all its runs",
    ])
    data = _json(pytester)
    assert data["stopping"]["active"] is False and data["stopping"]["cases"] == 0
    assert all(r["total"] == 100 for r in data["rows"])
    # Nothing could stop, so nothing needs the sequence's wider interval.
    assert all(r["gate"]["method"] == "exact" for r in data["rows"])
    assert " seq" not in result.stdout.str()


def _explained(result):
    lines = result.stdout.lines
    start = next(i for i, ln in enumerate(lines) if "= probability: explained =" in ln)
    return " ".join(" ".join(lines[start + 1 :]).split())


def test_explain_reads_the_sequence(pytester):
    pytester.makepyfile(bench_s=_bench(CLASSIFY, SMOKE))
    result = _run(
        pytester, "--prob-stop=sequential", "--prob-explain", "--prob-json=r.json"
    )
    text = _explained(result)
    assert (
        "classify::solid 53/53 [90%, 100%] seq ≥90% PASS decided after 53/100"
        " 53 of 53 runs passed. The true pass rate is probably between 90% and"
        " 100% (95% confidence, however early the case stops). Your bar is 90%,"
        " and that whole range is above it, so this case meets the bar. It"
        " stopped after 53 of its 100 planned runs (--prob-stop=sequential), as"
        " soon as its range was entirely above the bar; the other 47 runs were"
        " skipped. That range is built to hold however early a case stops, which"
        " is also why it is wider than a fixed-run range. The fraction comes from"
        " the 53 runs that ran and can lean toward the verdict, since the case"
        " stopped as soon as it was decided; the range allows for that."
    ) in text
    assert (
        "because by then its range could no longer get clear of the bar within"
        " its planned runs, however they went; the other 17 runs were skipped."
    ) in text
    assert "as soon as its range was entirely below the bar" in text
    assert (
        "Under --prob-stop=sequential runs= (or --prob-runs) is the most it may"
        " use: it still stops as soon as its range is clear of the bar." in text
    )
    # a count gate is curtailed: its paragraph says so, under this option
    assert (
        "It stopped after 4 of its 20 planned runs (--prob-stop=sequential)"
        " because by then it would fall short of the bar even if every remaining"
        " run passed" in text
    )
    # the glossary explains the tag and the stopping rule
    assert "(seq: an anytime-valid range, 95% confidence: the chance it ever" in text
    assert "With --prob-stop=sequential a case with a pass-rate bar stops" in text
    assert "With --prob-stop=curtail" not in text
    rows = _rows(_json(pytester))
    assert "as soon as its range was entirely above the bar" in (
        rows["classify::solid"]["gate"]["explanation"]
    )
    assert "the range allows for that" in rows["classify::weak"]["explanation"]


def test_row_reading_of_a_sequential_row():
    from pytest_probability import explain
    from pytest_probability.plugin import Stop

    seq = StatsConfig(method="sequential", prior=(0.5, 0.5))
    s = CaseStats("c", passes=45, fails=15)
    stop = Stop("c", after=60, planned=100, verdict=FAIL, skipped=40,
                sequential=True, mode="sequential")
    reading = explain.row_reading(
        s, stats.confidence_sequence(45, 60), seq, gated=True, stop=stop
    )
    assert reading.heading == "c  45/60  [55%, 90%] seq  FLAKY  decided after 60/100"
    assert "(95% confidence, however early the case stops)" in reading.text()
    assert (
        "It stopped after 60 of its 100 planned runs, as soon as its gate's range"
        " was clear of the bar (--prob-stop=sequential)" in reading.text()
    )
    undecided = Stop("c", 80, 100, UNDECIDED, 20, sequential=True, mode="sequential")
    assert "once its gate's range could no longer get clear of the bar" in (
        explain.row_reading(s, (0.1, 0.9), seq, gated=True, stop=undecided).text()
    )
    assert ("stopped", "sequential") in reading.terms


def test_plan_shows_the_sequential_budget(pytester):
    pytester.makepyfile(bench_s=_bench(CLASSIFY, SMOKE))
    result = _run(pytester, "--prob-stop=sequential", "--prob-plan")
    assert result.ret == pytest.ExitCode.OK
    lines = _section(result, "probability: plan")
    assert lines[0].split() == [
        "case", "runs", "min", "runs", "runs", "for", "80%", "chance", "now",
        "expected", "runs", "catch", "10%", "catch", "1%",
    ]
    assert lines[1].split() == [
        "classify::solid", "100", "53", "197", "33%", "76", "99%", "63%",
    ]
    assert lines[4].split() == [
        "smoke::steady", "20", "19", "20", "88%", "19", "88%", "18%",
    ]
    gate = _seq(0.9)
    assert round(gate.expected_runs(100, 0.97)) == 76
    assert "expected runs  Runs the case would use on average with" in (
        result.stdout.str()
    )
    # Without the option the plan is as before: fixed-run numbers, no column.
    plain = _run(pytester, "--prob-plan")
    assert "expected runs" not in plain.stdout.str()
    assert _section(plain, "probability: plan")[1].split()[:5] == [
        "classify::solid", "100", "36", "100", "82%",
    ]
