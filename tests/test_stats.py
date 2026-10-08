"""Unit tests for pytest_probability.stats.

Unlike the end-to-end plugin tests, these call the functions directly.
Reference values come from scipy/statsmodels (test-only extras:
``uv pip install -e '.[test]'``); the cross-check tests skip cleanly
when those are absent, while the property tests — validation,
determinism, coverage — need nothing beyond the standard library.
"""
from __future__ import annotations

import ast
import math
import random
import sys

import pytest

from pytest_probability import stats

LEVELS = [0.5, 0.8, 0.9, 0.95, 0.99, 0.999]
# (x, n) pairs: every edge case (x = 0, x = n, n = 1) plus interior
# points at small, medium and large n.
COUNTS = [
    (0, 1), (1, 1),
    (0, 2), (1, 2), (2, 2),
    (0, 5), (1, 5), (4, 5), (5, 5),
    (0, 10), (3, 10), (7, 10), (10, 10),
    (0, 37), (18, 37), (36, 37), (37, 37),
    (1, 100), (50, 100), (99, 100),
    (0, 1000), (123, 1000), (1000, 1000),
]


def close(a: float, b: float, rel: float = 1e-9, abs_: float = 1e-13) -> bool:
    return math.isclose(a, b, rel_tol=rel, abs_tol=abs_)


@pytest.fixture(scope="module")
def scipy_stats():
    return pytest.importorskip("scipy.stats")


@pytest.fixture(scope="module")
def scipy_special():
    return pytest.importorskip("scipy.special")


@pytest.fixture(scope="module")
def sm_proportion():
    return pytest.importorskip("statsmodels.stats.proportion")


# ---------------------------------------------------------------------------
# Binomial tails
# ---------------------------------------------------------------------------

BINOM_NS = [1, 2, 5, 10, 37, 100, 1000, 100_000]
BINOM_PS = [0.0, 1e-4, 0.1, 0.5, 0.73, 0.999, 1.0]


def _ks(n: int) -> list[int]:
    if n <= 40:
        return list(range(-1, n + 2))
    return sorted({-1, 0, 1, n // 10, n // 2 - 1, n // 2, n // 2 + 1,
                   (9 * n) // 10, n - 1, n, n + 1})


@pytest.mark.parametrize("n", BINOM_NS)
@pytest.mark.parametrize("p", BINOM_PS)
def test_binom_tails_match_scipy(scipy_stats, n, p):
    for k in _ks(n):
        want_cdf = float(scipy_stats.binom.cdf(k, n, p))
        want_sf = float(scipy_stats.binom.sf(k, n, p))
        assert close(stats.binom_cdf(k, n, p), want_cdf, rel=1e-8, abs_=1e-14), (k, n, p)
        assert close(stats.binom_sf(k, n, p), want_sf, rel=1e-8, abs_=1e-14), (k, n, p)


@pytest.mark.parametrize(
    "k, n, p",
    [(0, 1000, 0.5), (10, 1000, 0.5), (2, 10**4, 0.01), (800, 10**5, 0.01),
     (990, 1000, 0.5)],
)
def test_binom_far_tails_keep_relative_accuracy(scipy_stats, k, n, p):
    # Far tails are summed directly in log space, so even values near
    # 1e-300 keep their relative accuracy instead of rounding to 0.
    want_cdf = float(scipy_stats.binom.cdf(k, n, p))
    want_sf = float(scipy_stats.binom.sf(k - 1, n, p))  # P(X >= k)
    if want_cdf < 0.5:
        assert want_cdf > 0.0
        assert math.isclose(stats.binom_cdf(k, n, p), want_cdf, rel_tol=1e-8)
    if want_sf < 0.5:
        assert want_sf > 0.0
        assert math.isclose(stats.binom_sf(k - 1, n, p), want_sf, rel_tol=1e-8)


def test_binom_logpmf_matches_scipy(scipy_stats):
    for n in (1, 7, 100, 10**5):
        for p in (0.0, 0.03, 0.5, 0.97, 1.0):
            for k in _ks(n):
                want = float(scipy_stats.binom.logpmf(k, n, p))
                got = stats.binom_logpmf(k, n, p)
                if want == -math.inf:
                    assert got == -math.inf, (k, n, p)
                else:
                    assert close(got, want, rel=1e-10, abs_=1e-9), (k, n, p)


def test_binom_cdf_and_sf_are_complements():
    for n in (1, 3, 50):
        for p in (0.2, 0.5, 0.9):
            for k in range(-1, n + 1):
                assert close(stats.binom_cdf(k, n, p) + stats.binom_sf(k, n, p), 1.0)


def test_binom_tail_is_an_incomplete_beta():
    # P(X <= k) = I_{1-p}(n - k, k + 1): the identity that ties the
    # binomial code to the interval code.
    for n, k, p in [(10, 3, 0.4), (100, 70, 0.66), (2000, 5, 0.001)]:
        assert close(stats.binom_cdf(k, n, p), stats.betainc(n - k, k + 1, 1 - p), rel=1e-9)


# ---------------------------------------------------------------------------
# Incomplete beta and its inverse
# ---------------------------------------------------------------------------

BETA_PARAMS = [0.5, 1.0, 2.5, 10.0, 100.0, 1001.0]
BETA_XS = [0.0, 1e-8, 0.01, 0.3, 0.5, 0.9, 0.999999, 1.0]
BETA_YS = [0.0, 1e-10, 0.0005, 0.025, 0.5, 0.975, 0.9995, 1.0]


@pytest.mark.parametrize("a", BETA_PARAMS)
@pytest.mark.parametrize("b", BETA_PARAMS)
def test_betainc_matches_scipy(scipy_special, a, b):
    for x in BETA_XS:
        want = float(scipy_special.betainc(a, b, x))
        assert close(stats.betainc(a, b, x), want, rel=1e-10, abs_=1e-14), (a, b, x)


@pytest.mark.parametrize("a", BETA_PARAMS)
@pytest.mark.parametrize("b", BETA_PARAMS)
def test_betaincinv_matches_scipy(scipy_special, a, b):
    for y in BETA_YS:
        want = float(scipy_special.betaincinv(a, b, y))
        got = stats.betaincinv(a, b, y)
        assert close(got, want, rel=1e-9, abs_=1e-14), (a, b, y)


def test_betaincinv_round_trips():
    for a, b in [(0.5, 0.5), (3, 8), (40, 2), (0.7, 250)]:
        for y in (1e-12, 0.01, 0.4, 0.99):
            x = stats.betaincinv(a, b, y)
            assert close(stats.betainc(a, b, x), y, rel=1e-9, abs_=1e-15)


def test_betainc_large_parameters_converge(scipy_special):
    # Iterations grow like sqrt(max(a, b)); the cap scales with it.
    for a, b, x in [(1e6, 1e6, 0.5), (2e5, 5.0, 0.99998), (3.0, 1e6, 3e-6)]:
        want = float(scipy_special.betainc(a, b, x))
        # lgamma cancellation costs ~1e-9 relative at these sizes.
        assert close(stats.betainc(a, b, x), want, rel=1e-7, abs_=1e-9)


# ---------------------------------------------------------------------------
# One-proportion intervals
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("level", LEVELS)
def test_clopper_pearson_matches_references(scipy_stats, sm_proportion, level):
    for x, n in COUNTS:
        got = stats.clopper_pearson(x, n, level)
        sm = sm_proportion.proportion_confint(x, n, alpha=1 - level, method="beta")
        sp = scipy_stats.binomtest(x, n).proportion_ci(level, method="exact")
        for want in (sm, (sp.low, sp.high)):
            assert close(got[0], float(want[0])), (x, n, level, got, want)
            assert close(got[1], float(want[1])), (x, n, level, got, want)


@pytest.mark.parametrize("level", LEVELS)
def test_wilson_matches_references(scipy_stats, sm_proportion, level):
    for x, n in COUNTS:
        got = stats.wilson(x, n, level)
        sm = sm_proportion.proportion_confint(x, n, alpha=1 - level, method="wilson")
        sp = scipy_stats.binomtest(x, n).proportion_ci(level, method="wilson")
        for want in (sm, (sp.low, sp.high)):
            assert close(got[0], float(want[0]), abs_=1e-12), (x, n, level)
            assert close(got[1], float(want[1]), abs_=1e-12), (x, n, level)


@pytest.mark.parametrize("prior", [(1.0, 1.0), (0.5, 0.5), (2.0, 7.0), (0.3, 1.7)])
@pytest.mark.parametrize("level", [0.8, 0.95, 0.99])
def test_beta_credible_matches_scipy(scipy_stats, prior, level):
    a, b = prior
    for x, n in COUNTS:
        got = stats.beta_credible(x, n, level, prior)
        want = scipy_stats.beta.interval(level, a + x, b + n - x)
        assert close(got[0], float(want[0])), (x, n, prior, level)
        assert close(got[1], float(want[1])), (x, n, prior, level)


@pytest.mark.parametrize("level", [0.9, 0.95])
def test_jeffreys_matches_statsmodels(sm_proportion, level):
    for x, n in COUNTS:
        got = stats.beta_credible(x, n, level, prior=(0.5, 0.5))
        want = sm_proportion.proportion_confint(x, n, alpha=1 - level, method="jeffreys")
        assert close(got[0], float(want[0])) and close(got[1], float(want[1])), (x, n)


def test_exact_bounds_at_the_edges():
    # The bound at the data's edge is exact, not approximately 0 or 1.
    for n in (1, 2, 10, 500):
        for fn in (stats.clopper_pearson, stats.wilson):
            assert fn(0, n)[0] == 0.0
            assert fn(n, n)[1] == 1.0
    # x = n has a closed form: the lower bound is (alpha/2) ** (1/n).
    for n in (1, 5, 36):
        assert close(stats.clopper_pearson(n, n, 0.95)[0], 0.025 ** (1 / n))


def test_intervals_are_ordered_and_nested_by_level():
    for x, n in COUNTS:
        for method in stats.METHODS:
            lo90, hi90 = stats.proportion_interval(x, n, 0.90, method)
            lo99, hi99 = stats.proportion_interval(x, n, 0.99, method)
            assert 0.0 <= lo99 <= lo90 < hi90 <= hi99 <= 1.0, (x, n, method)
            if method != "bayes":  # a posterior interval can exclude x/n
                assert lo90 <= x / n <= hi90, (x, n, method)


def test_proportion_interval_dispatches_by_method():
    assert stats.proportion_interval(7, 10) == stats.clopper_pearson(7, 10)
    assert stats.proportion_interval(7, 10, 0.9, "wilson") == stats.wilson(7, 10, 0.9)
    assert stats.proportion_interval(7, 10, 0.9, "bayes", (0.5, 0.5)) == (
        stats.beta_credible(7, 10, 0.9, (0.5, 0.5))
    )


@pytest.mark.parametrize(
    "min_rate, runs", [(0.80, 17), (0.90, 36), (0.95, 72), (0.99, 368)]
)
def test_gate_feasibility_minimums(min_rate, runs):
    # The run counts #3's collection-time check quotes: the fewest runs
    # for which an all-pass case's 95% exact interval clears min_rate.
    assert stats.clopper_pearson(runs, runs, 0.95)[0] > min_rate
    assert stats.clopper_pearson(runs - 1, runs - 1, 0.95)[0] <= min_rate


# ---------------------------------------------------------------------------
# Two proportions
# ---------------------------------------------------------------------------

TWO_SAMPLE = [
    (10, 10, 8, 10), (8, 10, 10, 10), (0, 1, 1, 1), (1, 1, 1, 1), (0, 5, 0, 5),
    (5, 5, 5, 5), (3, 7, 6, 9), (0, 20, 20, 20), (17, 40, 31, 40),
    (2, 3, 40, 60), (450, 1000, 500, 1000),
]


@pytest.mark.parametrize("level", [0.8, 0.95, 0.99])
def test_newcombe_matches_statsmodels(level):
    smp = pytest.importorskip("statsmodels.stats.proportion")
    for x1, n1, x2, n2 in TWO_SAMPLE:
        got = stats.newcombe(x1, n1, x2, n2, level)
        want = smp.confint_proportions_2indep(
            x1, n1, x2, n2, method="newcomb", compare="diff", alpha=1 - level
        )
        assert close(got[0], float(want[0]), abs_=1e-12), (x1, n1, x2, n2)
        assert close(got[1], float(want[1]), abs_=1e-12), (x1, n1, x2, n2)


@pytest.mark.parametrize("alternative", ["two-sided", "less", "greater"])
def test_fisher_exact_matches_scipy(scipy_stats, alternative):
    for x1, n1, x2, n2 in TWO_SAMPLE:
        table = [[x1, n1 - x1], [x2, n2 - x2]]
        want = float(scipy_stats.fisher_exact(table, alternative=alternative).pvalue)
        got = stats.fisher_exact(x1, n1, x2, n2, alternative)
        assert close(got, want, rel=1e-9, abs_=1e-14), (x1, n1, x2, n2)


def test_fisher_exact_exhaustive_small_tables(scipy_stats):
    # Every table with arms up to 6 runs: ties between equally likely
    # tables are where two-sided p-values most often go wrong.
    for n1 in range(1, 7):
        for n2 in range(1, 7):
            for x1 in range(n1 + 1):
                for x2 in range(n2 + 1):
                    table = [[x1, n1 - x1], [x2, n2 - x2]]
                    want = float(scipy_stats.fisher_exact(table).pvalue)
                    assert close(stats.fisher_exact(x1, n1, x2, n2), want), table


def test_readme_ab_example():
    # The epic's motivating example: chain_of_thought 10/10 vs terse
    # 8/10 is +20 pp, 95% interval [-11, +51] pp, Fisher p = 0.47.
    low, high = stats.newcombe(10, 10, 8, 10, 0.95)
    assert (round(low * 100), round(high * 100)) == (-11, 51)
    assert round(stats.fisher_exact(10, 10, 8, 10), 2) == 0.47


# ---------------------------------------------------------------------------
# Resampling
# ---------------------------------------------------------------------------


def test_bootstrap_is_deterministic_and_seeded():
    data = [i % 7 / 6 for i in range(50)]
    a = stats.bootstrap(data, resamples=200, seed=3)
    assert a == stats.bootstrap(data, resamples=200, seed=3)
    assert a != stats.bootstrap(data, resamples=200, seed=4)
    assert len(a) == 200


def test_bootstrap_leaves_global_random_alone():
    random.seed(12345)
    expected = random.random()
    random.seed(12345)
    stats.bootstrap([0.0, 1.0], resamples=50, seed=0)
    assert random.random() == expected


def test_bootstrap_resamples_whole_units():
    # Pairs stay together: the statistic sees len(data) intact tuples.
    pairs = [(0.2, 0.5), (0.9, 0.9), (0.4, 0.1)]
    seen: list[list[tuple[float, float]]] = []

    def mean_diff(sample):
        seen.append(sample)
        return sum(b - a for a, b in sample) / len(sample)

    out = stats.bootstrap(pairs, mean_diff, resamples=20, seed=1)
    assert len(out) == 20
    assert all(len(s) == 3 and set(s) <= set(pairs) for s in seen)


def test_percentile_interval_matches_numpy():
    np = pytest.importorskip("numpy")
    rng = random.Random(0)
    for size in (1, 2, 10, 999, 5000):
        samples = [rng.gauss(0, 1) for _ in range(size)]
        for level in (0.5, 0.9, 0.95):
            lo, hi = stats.percentile_interval(samples, level)
            want = np.quantile(samples, [(1 - level) / 2, (1 + level) / 2])
            assert close(lo, float(want[0])) and close(hi, float(want[1]))


def test_normal_interval_matches_scipy(scipy_stats):
    rng = random.Random(4)
    for size in (2, 3, 10, 400):
        data = [rng.randint(0, 10) / 10 for _ in range(size)]
        for level in LEVELS:
            low, high = stats.normal_interval(data, level)
            want = scipy_stats.norm.interval(
                level,
                loc=sum(data) / size,
                scale=scipy_stats.sem(data),
            )
            assert close(low, want[0], rel=1e-12, abs_=1e-14)
            assert close(high, want[1], rel=1e-12, abs_=1e-14)


def test_normal_interval_is_unclipped():
    # One of 20 inputs passes: the lower bound goes below 0, which is the
    # cross-check's way of saying the approximation is poor there.
    low, high = stats.normal_interval([1.0] + [0.0] * 19)
    assert low < 0.0 < 0.05 < high
    assert stats.normal_interval([0.5, 0.5, 0.5]) == (0.5, 0.5)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "call",
    [
        lambda: stats.clopper_pearson(1, 0),
        lambda: stats.clopper_pearson(-1, 5),
        lambda: stats.clopper_pearson(6, 5),
        lambda: stats.clopper_pearson(2.5, 5),
        lambda: stats.clopper_pearson(True, 5),
        lambda: stats.wilson(1, 5, level=0.0),
        lambda: stats.wilson(1, 5, level=1.0),
        lambda: stats.wilson(1, 5, level=95),
        lambda: stats.wilson(1, 5, level=math.nan),
        lambda: stats.beta_credible(1, 5, prior=(0, 1)),
        lambda: stats.beta_credible(1, 5, prior=(1, math.inf)),
        lambda: stats.beta_credible(1, 5, prior=(1, 1, 1)),
        lambda: stats.proportion_interval(1, 5, method="wald"),
        lambda: stats.newcombe(1, 5, 6, 5),
        lambda: stats.fisher_exact(1, 5, 2, 5, alternative="two_sided"),
        lambda: stats.betainc(0, 1, 0.5),
        lambda: stats.betainc(1, 1, 1.5),
        lambda: stats.betaincinv(1, 1, -0.1),
        lambda: stats.binom_cdf(1, 5, 1.2),
        lambda: stats.binom_sf(1, -1, 0.5),
        lambda: stats.bootstrap([], resamples=10),
        lambda: stats.bootstrap([1.0], resamples=0),
        lambda: stats.bootstrap([1.0], seed=1.5),
        lambda: stats.percentile_interval([]),
        lambda: stats.normal_interval([0.5]),
        lambda: stats.normal_interval([0.5, 1.0], level=1.0),
        lambda: stats.sign_flip_test([0.1], resamples=0),
        lambda: stats.sign_flip_test([0.1], seed=0.5),
        lambda: stats.sign_flip_test([math.nan]),
        lambda: stats.adjust_pvalues([0.1], "fdr"),
        lambda: stats.adjust_pvalues([1.5], "holm"),
    ],
)
def test_invalid_input_raises_value_error(call):
    with pytest.raises(ValueError):
        call()


# ---------------------------------------------------------------------------
# Coverage
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_interval_coverage(record_property):
    """Clopper-Pearson never under-covers; record Wilson's mean coverage.

    Coverage at a true p is computed exactly rather than sampled: sum
    the binomial probability of every outcome x whose interval contains
    p. That is the limit of an infinitely long simulation, so the
    "never below nominal" check has no Monte Carlo noise to flake on.
    """
    level = 0.95
    ps = [i / 200 for i in range(1, 200)]  # 0.005 ... 0.995
    cp_min, wilson_cov = 1.0, []
    for n in range(1, 101):
        cp = [stats.clopper_pearson(x, n, level) for x in range(n + 1)]
        wi = [stats.wilson(x, n, level) for x in range(n + 1)]
        for p in ps:
            pmf = [math.comb(n, x) * p**x * (1 - p) ** (n - x) for x in range(n + 1)]
            cov_cp = math.fsum(w for w, (lo, hi) in zip(pmf, cp) if lo <= p <= hi)
            cov_wi = math.fsum(w for w, (lo, hi) in zip(pmf, wi) if lo <= p <= hi)
            assert cov_cp >= level - 1e-12, (n, p, cov_cp)
            cp_min = min(cp_min, cov_cp)
            wilson_cov.append(cov_wi)
    wilson_mean = math.fsum(wilson_cov) / len(wilson_cov)
    record_property("clopper_pearson_min_coverage", round(cp_min, 4))
    record_property("wilson_mean_coverage", round(wilson_mean, 4))
    record_property("wilson_min_coverage", round(min(wilson_cov), 4))
    print(f"Clopper-Pearson min coverage {cp_min:.4f}; Wilson mean"
          f" {wilson_mean:.4f}, min {min(wilson_cov):.4f} (n = 1..100, level {level})")
    # Wilson trades guaranteed coverage for width: close to nominal on
    # average, below it at some p.
    assert 0.93 < wilson_mean < 0.96
    assert min(wilson_cov) < level


def _clustered_cases(rng, n_inputs, runs, theta, concentration):
    """Correlated pass/fail data with a known true rate.

    Each input's own pass probability is drawn from a Beta with mean
    ``theta``; its runs are Bernoulli draws at that probability. So runs
    of one input are correlated (intraclass correlation 1 / (1 +
    concentration)), and the mean of the per-input probabilities over
    the population is exactly ``theta``.
    """
    from pytest_probability.plugin import CaseStats

    a, b = theta * concentration, (1 - theta) * concentration
    cases = []
    for i in range(n_inputs):
        p = rng.betavariate(a, b)
        passes = sum(rng.random() < p for _ in range(runs))
        cases.append(CaseStats(f"f::{i}", passes=passes, fails=runs - passes))
    return cases


# (inputs, runs per input, true rate, Beta concentration): strongly and
# weakly correlated runs, and a rate near 1 with more inputs.
COVERAGE_SCENARIOS = [
    (100, 10, 0.8, 2.0),
    (100, 5, 0.6, 10.0),
    (200, 10, 0.9, 1.0),
]


@pytest.mark.slow
def test_cluster_bootstrap_coverage(record_property):
    """#4: coverage within 1.5 pp of nominal for N ≥ 100 on clustered
    data with a known true rate.

    Goes through ``plugin.aggregate`` — the code the summary uses — with
    1,000 resamples instead of the default 5,000 to keep the run time
    reasonable. The data stream and every bootstrap are seeded, so the
    coverage figure is a fixed number, not a flaky one.
    """
    from pytest_probability.plugin import StatsConfig, aggregate

    level, reps = 0.95, 1000
    rng = random.Random(2026)
    for n_inputs, runs, theta, concentration in COVERAGE_SCENARIOS:
        hits = 0
        for rep in range(reps):
            cases = _clustered_cases(rng, n_inputs, runs, theta, concentration)
            cfg = StatsConfig(level=level, resamples=1000, seed=rep)
            low, high = aggregate("function", "f", cases, cfg).ci
            hits += low <= theta <= high
        coverage = hits / reps
        label = f"N={n_inputs} k={runs} theta={theta} conc={concentration}"
        record_property(f"coverage {label}", coverage)
        print(f"cluster bootstrap coverage {coverage:.3f} ({label}, level {level})")
        assert abs(coverage - level) <= 0.015, (label, coverage)


def test_cluster_bootstrap_performance():
    """#4: 1,000 cases × 5,000 resamples in under 0.5 s of plain Python.

    Best of three, in process CPU time, so a busy machine (or ``-n``
    workers on the same cores) doesn't make it flaky.
    """
    import time

    from pytest_probability.plugin import StatsConfig, aggregate

    cases = _clustered_cases(random.Random(0), 1000, 10, 0.8, 2.0)
    cfg = StatsConfig(resamples=5000)
    best = math.inf
    for _ in range(3):
        start = time.process_time()
        agg = aggregate("function", "f", cases, cfg)
        best = min(best, time.process_time() - start)
    assert agg.ci is not None and agg.inputs == 1000
    assert best < 0.5, f"{best:.3f} s"


# ---------------------------------------------------------------------------
# Intraclass correlation and interval width (#5)
# ---------------------------------------------------------------------------

# (passes, runs) per input: equal and unequal run counts, inputs that
# are all-or-nothing, inputs that look alike, and a negative raw ρ.
ICC_FIXTURES = {
    "equal k": [(i % 5 * 2 + 2, 10) for i in range(12)],
    "unequal k": [(3, 4), (0, 2), (5, 5), (1, 7), (6, 10), (2, 3), (0, 1)],
    "mostly k=1": [(10, 10)] + [(0, 1)] * 9,
    "all-or-nothing": [(5, 5), (0, 5), (5, 5), (0, 5)],
    "alike inputs": [(5, 10), (5, 10), (5, 10), (4, 10), (6, 10)],
    "negative raw": [(1, 2), (1, 2), (1, 2), (2, 2)],
    "large": [(i * 37 % 51, 50 + i % 7) for i in range(40)],
}


def _icc_reference(counts):
    """ρ by the textbook route, independently of the stats module:
    statsmodels fits a one-way ANOVA to one 0/1 row per run, and k₀ is
    computed from the group sizes."""
    pd = pytest.importorskip("pandas")
    smf = pytest.importorskip("statsmodels.formula.api")
    sm_anova = pytest.importorskip("statsmodels.stats.anova")
    rows = [
        {"input": f"i{i}", "y": float(r < x)}
        for i, (x, n) in enumerate(counts)
        for r in range(n)
    ]
    table = sm_anova.anova_lm(smf.ols("y ~ C(input)", pd.DataFrame(rows)).fit())
    msb = table.loc["C(input)", "mean_sq"]
    msw = table.loc["Residual", "mean_sq"]
    sizes = [n for _, n in counts]
    m = sum(sizes)
    k0 = (m - sum(n * n for n in sizes) / m) / (len(sizes) - 1)
    return (msb - msw) / (msb + (k0 - 1) * msw)


@pytest.mark.parametrize("name", ICC_FIXTURES)
def test_icc_matches_anova_reference(name):
    counts = ICC_FIXTURES[name]
    want = _icc_reference(counts)
    got = stats.icc(counts, clip=False)
    assert close(got, want, rel=1e-9, abs_=1e-12), (got, want)
    assert close(stats.icc(counts), min(1.0, max(0.0, want)), rel=1e-9, abs_=1e-12)


def test_icc_hand_computed():
    # Two inputs, 2 runs each, 1/2 and 2/2: M = 4, ȳ = 3/4.
    # SSB = 2·(1/2 − 3/4)² + 2·(1 − 3/4)² = 1/4 on 1 df; SSW = 1/2 on 2.
    # MSB = MSW = 1/4, so ρ = 0 exactly.
    assert stats.icc([(1, 2), (2, 2)], clip=False) == pytest.approx(0.0, abs=1e-15)
    # Equal k = 4, inputs 2/4 and 4/4: M = 8, ȳ = 3/4. SSB = 4·(1/16)·2
    # = 1/2 (1 df); SSW = 1 (6 df). ρ = (1/2 − 1/6)/(1/2 + 3/6) = 1/3.
    assert stats.icc([(2, 4), (4, 4)]) == pytest.approx(1 / 3)
    # Unequal sizes 2 and 3, inputs 1/2 and 3/3: M = 5, k₀ = (5 − 13/5)/1
    # = 2.4. SSB = 1/2 + 3 − 16/5 = 0.3; SSW = 1/2 (3 df), MSW = 1/6.
    # ρ = (0.3 − 1/6)/(0.3 + 1.4/6) = 0.25.
    assert stats.icc([(1, 2), (3, 3)]) == pytest.approx(0.25)
    # Each input all-pass or all-fail: no variation within inputs, ρ = 1.
    assert stats.icc([(4, 4), (0, 4)]) == 1.0
    assert stats.icc([(2, 2), (0, 3)]) == 1.0


def test_icc_clips_to_unit_interval():
    raw = stats.icc(ICC_FIXTURES["negative raw"], clip=False)
    assert raw < 0
    assert stats.icc(ICC_FIXTURES["negative raw"]) == 0.0
    # never above 1, even unclipped: k₀ ≥ 1 keeps the denominator ≥ MSB
    for counts in ICC_FIXTURES.values():
        assert stats.icc(counts, clip=False) <= 1.0 + 1e-12


@pytest.mark.parametrize(
    "counts, why",
    [
        ([(1, 1), (0, 1), (1, 1)], "every input run once"),
        ([(3, 3)], "a single input"),
        ([], "no inputs"),
        ([(5, 5), (10, 10), (2, 2)], "every run passed"),
        ([(0, 5), (0, 10), (0, 2)], "every run failed"),
    ],
)
def test_icc_undefined(counts, why):
    assert stats.icc(counts) is None, why
    assert stats.icc(counts, clip=False) is None, why


def test_icc_order_independent():
    counts = ICC_FIXTURES["unequal k"]
    assert stats.icc(counts) == stats.icc(list(reversed(counts)))


@pytest.mark.parametrize("bad", [[(3, 2)], [(1, 0)], [(-1, 2)], [(1.5, 2)]])
def test_icc_validates(bad):
    with pytest.raises(ValueError):
        stats.icc(bad + [(1, 2)])


@pytest.mark.parametrize("concentration", [0.5, 2.0, 10.0, 50.0])
def test_icc_recovers_a_known_correlation(concentration):
    # Per-input rates from a Beta with concentration c give ρ = 1/(1+c).
    cases = _clustered_cases(random.Random(5), 3000, 10, 0.7, concentration)
    rho = stats.icc([(s.passes, s.total) for s in cases])
    assert rho == pytest.approx(1 / (1 + concentration), abs=0.02)


def test_width_factor():
    assert stats.width_factor(1, 0.37) == 1.0
    assert stats.width_factor(10, 1.0) == 1.0
    assert stats.width_factor(16, 0.0) == pytest.approx(0.25)
    assert stats.width_factor(10, 0.6) == pytest.approx(0.8)
    assert stats.width_factor(10, 0.025) == pytest.approx(0.35)
    # the harmonic-mean k is exact for an equally weighted average:
    # mean over inputs of (ρ + (1 − ρ)/kᵢ)
    ks, rho = [2, 5, 10, 10, 40], 0.2
    k_h = len(ks) / sum(1 / k for k in ks)
    per_input = sum(rho + (1 - rho) / k for k in ks) / len(ks)
    assert stats.width_factor(k_h, rho) == pytest.approx(math.sqrt(per_input))


@pytest.mark.parametrize(
    "k, rho", [(0.5, 0.2), (0, 0.2), (math.inf, 0.2), (10, -0.1), (10, 1.1)]
)
def test_width_factor_validates(k, rho):
    with pytest.raises(ValueError):
        stats.width_factor(k, rho)


@pytest.mark.parametrize("rho, shrink", [(0.025, 22), (0.3, 5), (0.6, 2)])
def test_projection_matches_issue_table(rho, shrink):
    # #5: going from k = 10 to 20 runs per input.
    change = stats.projected_width(10, rho, runs=2) - 1
    assert round(-change * 100) == shrink
    # doubling the inputs is 1/√2 whatever ρ is: about −29%
    inputs = stats.projected_width(10, rho, inputs=2) - 1
    assert inputs == pytest.approx(1 / math.sqrt(2) - 1)
    assert round(-inputs * 100) == 29


def test_projection_limits():
    # ρ = 0: runs are as good as inputs; ρ = 1: runs add nothing.
    assert stats.projected_width(10, 0.0, runs=2) == pytest.approx(1 / math.sqrt(2))
    assert stats.projected_width(10, 1.0, runs=2) == 1.0
    assert stats.projected_width(10, 0.4) == 1.0
    # more runs never beat the same factor of fresh inputs
    for rho in (0.0, 0.01, 0.2, 0.9):
        assert stats.projected_width(7, rho, runs=3) >= stats.projected_width(
            7, rho, inputs=3
        ) - 1e-15


# ---------------------------------------------------------------------------
# Module hygiene
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Paired differences (#6)
# ---------------------------------------------------------------------------


def _brute_sign_flip(diffs):
    """P(|Σ ±dᵢ| ≥ |Σ dᵢ|) by listing every sign pattern, on exact
    fractions so ties are exact."""
    import itertools
    from fractions import Fraction

    fr = [Fraction(d).limit_denominator(10**6) for d in diffs]
    observed = abs(sum(fr))
    hits = sum(
        abs(sum(s * f for s, f in zip(signs, fr))) >= observed
        for signs in itertools.product((1, -1), repeat=len(fr))
    )
    return hits / 2 ** len(fr)


def _fraction_diffs(rng, n, k):
    return [rng.randint(0, k) / k - rng.randint(0, k) / k for _ in range(n)]


def test_sign_flip_exact_matches_enumeration():
    rng = random.Random(6)
    for _ in range(60):
        k = rng.choice([1, 3, 5, 10])
        diffs = _fraction_diffs(rng, rng.randint(1, 12), k)
        p, exact = stats.sign_flip_test(diffs)
        assert exact
        assert close(p, _brute_sign_flip(diffs), rel=1e-12), diffs


def test_sign_flip_matches_scipy_permutation_test(scipy_stats):
    np = pytest.importorskip("numpy")
    rng = random.Random(7)
    for _ in range(20):
        diffs = _fraction_diffs(rng, rng.randint(2, 10), 10)
        if not any(diffs):
            continue
        want = scipy_stats.permutation_test(
            (np.array(diffs),),
            np.sum,
            permutation_type="samples",
            n_resamples=np.inf,
            alternative="two-sided",
        ).pvalue
        got, exact = stats.sign_flip_test(diffs)
        assert exact and close(got, float(want), rel=1e-9), diffs


def test_sign_flip_with_one_run_per_arm_is_mcnemar():
    # k = 1: differences are -1, 0 or +1, and the exact sign-flip test is
    # McNemar's exact (binomial) test on the discordant pairs.
    mcnemar = pytest.importorskip("statsmodels.stats.contingency_tables").mcnemar
    for b, c, ties in [(0, 3, 4), (1, 6, 0), (5, 5, 2), (2, 9, 20), (40, 70, 300)]:
        diffs = [1.0] * b + [-1.0] * c + [0.0] * ties
        p, exact = stats.sign_flip_test(diffs)
        want = mcnemar([[ties, b], [c, 0]], exact=True).pvalue
        assert exact and close(p, float(want), rel=1e-9), (b, c)


def test_sign_flip_no_differences():
    assert stats.sign_flip_test([]) == (1.0, True)
    assert stats.sign_flip_test([0.0, 0.0, 0.0]) == (1.0, True)
    # equal fractions with different run counts are no difference
    assert stats.sign_flip_test([5 / 10 - 1 / 2, 3 / 10 - 6 / 20]) == (1.0, True)


def test_sign_flip_exact_for_moderate_suites():
    rng = random.Random(8)
    assert stats.sign_flip_test(_fraction_diffs(rng, 300, 10))[1]
    assert stats.sign_flip_test([rng.choice((-1.0, 0.0, 1.0)) for _ in range(1000)])[1]
    assert not stats.sign_flip_test(_fraction_diffs(rng, 2000, 10))[1]


def test_sign_flip_monte_carlo_is_seeded_and_close():
    rng = random.Random(9)
    diffs = _fraction_diffs(rng, 14, 10)
    diffs[0] += 0.5  # push p away from 1
    exact, _ = stats.sign_flip_test(diffs)
    mc = stats.sign_flip_test(diffs, max_work=1, resamples=20_000, seed=3)
    assert mc[1] is False
    assert mc == stats.sign_flip_test(diffs, max_work=1, resamples=20_000, seed=3)
    assert mc != stats.sign_flip_test(diffs, max_work=1, resamples=20_000, seed=4)
    # three standard errors of a Monte Carlo proportion
    assert abs(mc[0] - exact) < 3 * math.sqrt(exact * (1 - exact) / 20_000) + 1e-4
    # never 0: (1 + hits) / (1 + resamples)
    huge = stats.sign_flip_test([1.0] * 40, max_work=1, resamples=99)
    assert huge == (0.01, False)


def test_sign_flip_leaves_global_random_alone():
    random.seed(12345)
    expected = random.random()
    random.seed(12345)
    stats.sign_flip_test([0.1, -0.3, 0.5] * 10, max_work=1, resamples=50)
    assert random.random() == expected


@pytest.mark.parametrize("method, reference", [
    ("holm", "holm"), ("bonferroni", "bonferroni"), ("bh", "fdr_bh"),
])
def test_adjust_pvalues_matches_statsmodels(method, reference):
    multitest = pytest.importorskip("statsmodels.stats.multitest")
    rng = random.Random(10)
    cases = [[0.04], [0.01, 0.04, 0.03, 0.2], [0.5, 0.5, 0.01, 0.01, 1.0]]
    cases += [[rng.random() ** 3 for _ in range(rng.randint(2, 30))] for _ in range(50)]
    for ps in cases:
        want = multitest.multipletests(ps, method=reference)[1]
        got = stats.adjust_pvalues(ps, method)
        assert all(close(g, float(w), rel=1e-12) for g, w in zip(got, want)), ps


def test_adjust_pvalues_none_and_empty():
    assert stats.adjust_pvalues([0.3, 0.01], "none") == [0.3, 0.01]
    assert stats.adjust_pvalues([], "holm") == []
    # Holm is never larger than Bonferroni, BH never larger than Holm
    ps = [0.001, 0.02, 0.03, 0.2, 0.6]
    holm = stats.adjust_pvalues(ps, "holm")
    assert all(
        h <= b for h, b in zip(holm, stats.adjust_pvalues(ps, "bonferroni"))
    )
    assert all(x <= h for x, h in zip(stats.adjust_pvalues(ps, "bh"), holm))


def _paired_cases(rng, n_inputs, runs, effect):
    """Pairs of (baseline, arm) counts: each input has its own
    difficulty, and the arm's pass probability is ``effect`` higher."""
    from pytest_probability.plugin import Pair

    pairs = []
    for i in range(n_inputs):
        p = rng.betavariate(3.0, 1.5) * (1 - abs(effect))
        q = p + effect
        base = sum(rng.random() < p for _ in range(runs))
        arm = sum(rng.random() < q for _ in range(runs))
        pairs.append(Pair(f"{i:03}", (base, runs), (arm, runs)))
    return pairs


@pytest.mark.parametrize("effect", [0.0, 0.15])
def test_permutation_and_bootstrap_agree(effect, record_property):
    """#6: the sign-flip p and the paired bootstrap interval reach the
    same conclusion — the interval leaves out 0 exactly when p is small
    — on simulated data with and without a real difference."""
    from pytest_probability.plugin import CompareSpec, StatsConfig, compare_pairs

    rng = random.Random(2026 + int(effect * 100))
    spec = CompareSpec(axis="style", baseline="a")
    agree = found = 0
    reps = 200
    for rep in range(reps):
        pairs = _paired_cases(rng, 30, 10, effect)
        cfg = StatsConfig(resamples=1000, seed=rep)
        cmp = compare_pairs("f", spec, "b", pairs, cfg)
        excludes_zero = not cmp.ci[0] <= 0 <= cmp.ci[1]
        agree += excludes_zero == (cmp.p < 0.05)
        found += excludes_zero
    record_property(f"agreement effect={effect}", agree / reps)
    assert agree / reps >= 0.95
    if effect:
        assert found / reps >= 0.9
    else:
        assert found / reps <= 0.1


def test_stats_imports_only_the_standard_library():
    # The epic's first principle: pytest stays the only runtime
    # dependency, and the stats module doesn't even need pytest.
    tree = ast.parse(open(stats.__file__, encoding="utf-8").read())
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(tree) if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module.split(".")[0]
        for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module
    }
    assert "pytest" not in imported
    assert imported <= set(sys.stdlib_module_names) | {"__future__"}, imported
