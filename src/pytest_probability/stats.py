"""Statistical building blocks for pytest-probability.

Pure functions over plain numbers: no pytest import, no state, and
only the standard library (``math``, ``statistics``, ``random``) —
pytest stays the plugin's only runtime dependency. Everything that
renders an interval, decides a gate or compares two arms is built from
the pieces here, so they are kept small and individually checkable
against reference implementations (the test suite cross-checks them
against scipy/statsmodels, which are test-only extras).

Contents:

- **Binomial tails** — ``binom_cdf``/``binom_sf``, exact at any n,
  summed in log space outward from the mode.
- **Regularized incomplete beta** — ``betainc`` (continued fraction)
  and its inverse ``betaincinv`` (bisection-safeguarded Newton). Every
  exact interval below is a beta quantile.
- **One proportion** — ``clopper_pearson`` (the default "exact"
  method), ``wilson`` and ``beta_credible``, plus the
  ``proportion_interval`` dispatcher keyed by method name.
- **Two proportions** — ``newcombe`` (interval on p1 − p2) and
  ``fisher_exact`` (p-value).
- **Resampling** — ``bootstrap`` (seeded) and ``percentile_interval``,
  with ``normal_interval`` on a mean as the cross-check.
- **Clustered runs** — ``icc`` (intraclass correlation of per-run
  pass/fail outcomes, by one-way ANOVA), ``width_factor`` and
  ``projected_width`` (how an interval over inputs scales with runs
  and inputs).

Conventions:

- ``level`` is always the two-sided confidence (or credible) level in
  the open interval (0, 1); an interval at 0.95 leaves 2.5% in each
  tail. One level drives both a printed interval and any verdict built
  on it, so the two can never disagree.
- Intervals are ``(low, high)`` tuples of floats in [0, 1] (or
  [-1, 1] for a difference).
- Bad input raises ``ValueError`` with a message naming the argument;
  nothing is silently clamped.
"""
from __future__ import annotations

import math
import random
import statistics
from typing import Callable, Sequence, TypeVar

T = TypeVar("T")

# Interval methods understood by ``proportion_interval``. "exact" is the
# default everywhere: Clopper-Pearson never under-covers.
METHODS = ("exact", "wilson", "bayes")

# Relative accuracy targets. Double precision is ~2.2e-16; the
# continued fraction stops a little above that so rounding noise cannot
# keep it iterating forever.
_CF_EPS = 1e-15
_TINY = 1e-300

# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------


def _check_int(name: str, value: object) -> int:
    # bool is an int subclass; True/False as a count is almost certainly
    # a bug at the call site.
    if isinstance(value, bool) or not hasattr(value, "__index__"):
        raise ValueError(f"{name} must be an integer, got {value!r}")
    return value.__index__()  # type: ignore[attr-defined]


def _check_counts(
    x: object, n: object, xname: str = "x", nname: str = "n"
) -> tuple[int, int]:
    x, n = _check_int(xname, x), _check_int(nname, n)
    if n < 1:
        raise ValueError(f"{nname} must be at least 1, got {n}")
    if not 0 <= x <= n:
        raise ValueError(f"{xname} must be between 0 and {nname}={n}, got {x}")
    return x, n


def _check_level(level: float) -> float:
    level = float(level)
    # Written as a positive test so NaN fails it too.
    if not 0.0 < level < 1.0:
        raise ValueError(f"level must be strictly between 0 and 1, got {level}")
    return level


def _check_positive(name: str, value: float) -> float:
    value = float(value)
    if not (value > 0.0 and math.isfinite(value)):
        raise ValueError(f"{name} must be a positive finite number, got {value}")
    return value


def _check_unit(name: str, value: float) -> float:
    value = float(value)
    if not 0.0 <= value <= 1.0:
        raise ValueError(f"{name} must be between 0 and 1, got {value}")
    return value


# ---------------------------------------------------------------------------
# Binomial distribution
# ---------------------------------------------------------------------------


def _log_comb(n: int, k: int) -> float:
    return math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1)


def binom_logpmf(k: int, n: int, p: float) -> float:
    """Log of P(X = k) for X ~ Binomial(n, p); ``-inf`` off the support.

    Computed from ``lgamma`` so it neither overflows nor underflows at
    any n. Relative accuracy degrades gently with n (about 1e-9 at
    n = 10**6) because ``lgamma`` values grow like n·log n.
    """
    k, n = _check_int("k", k), _check_int("n", n)
    if n < 0:
        raise ValueError(f"n must be non-negative, got {n}")
    p = _check_unit("p", p)
    if not 0 <= k <= n:
        return -math.inf
    # The degenerate p's put all mass on one point; log(0) would raise.
    if p == 0.0:
        return 0.0 if k == 0 else -math.inf
    if p == 1.0:
        return 0.0 if k == n else -math.inf
    return _log_comb(n, k) + k * math.log(p) + (n - k) * math.log1p(-p)


def _log_tail(k: int, n: int, p: float, step: int) -> float:
    """Log of the sum of P(X = j) for j = k, k+step, k+2·step, ...

    Only called walking *away* from the mode, where terms shrink
    monotonically (the binomial is log-concave): the sum is accumulated
    relative to its first, largest term — so nothing under- or
    overflows — and stops once a term no longer moves the total.
    """
    log_first = binom_logpmf(k, n, p)
    # Ratio of successive pmf terms: P(j+1)/P(j) = (n-j)/(j+1) · p/q.
    odds = p / (1.0 - p)
    total, term, j = 1.0, 1.0, k
    while 0 <= j + step <= n:
        if step > 0:
            term *= (n - j) / (j + 1) * odds
        else:
            term *= j / (n - j + 1) / odds
        j += step
        total += term
        if term <= total * 1e-17:
            break
    return log_first + math.log(total)


def _mode(n: int, p: float) -> int:
    return min(n, math.floor((n + 1) * p))


def binom_cdf(k: int, n: int, p: float) -> float:
    """P(X ≤ k) for X ~ Binomial(n, p) — the lower tail, exactly.

    Whichever tail lies away from the mode is summed directly in log
    space; the other comes from its complement, which is then at least
    a sizeable fraction of 1 so the subtraction loses nothing that
    matters. Cost is O(√n) terms, not O(n).
    """
    k, n = _check_int("k", k), _check_int("n", n)
    if n < 0:
        raise ValueError(f"n must be non-negative, got {n}")
    p = _check_unit("p", p)
    if k < 0:
        return 0.0
    if k >= n or p == 0.0:
        return 1.0
    if p == 1.0:
        return 0.0
    if k < _mode(n, p):
        return math.exp(_log_tail(k, n, p, -1))
    return max(0.0, 1.0 - math.exp(_log_tail(k + 1, n, p, +1)))


def binom_sf(k: int, n: int, p: float) -> float:
    """P(X > k) for X ~ Binomial(n, p) — the upper tail, exactly.

    The same convention as ``scipy.stats.binom.sf``: strictly greater
    than k, so ``binom_cdf(k) + binom_sf(k) == 1``. For P(X ≥ k) use
    ``binom_sf(k - 1, n, p)``.
    """
    k, n = _check_int("k", k), _check_int("n", n)
    if n < 0:
        raise ValueError(f"n must be non-negative, got {n}")
    p = _check_unit("p", p)
    if k < 0:
        return 1.0
    if k >= n or p == 0.0:
        return 0.0
    if p == 1.0:
        return 1.0
    if k >= _mode(n, p):
        return math.exp(_log_tail(k + 1, n, p, +1))
    return max(0.0, 1.0 - math.exp(_log_tail(k, n, p, -1)))


# ---------------------------------------------------------------------------
# Regularized incomplete beta function
# ---------------------------------------------------------------------------


def _log_beta(a: float, b: float) -> float:
    return math.lgamma(a) + math.lgamma(b) - math.lgamma(a + b)


def _beta_cf(a: float, b: float, x: float) -> float:
    """Continued fraction for I_x(a, b), evaluated by modified Lentz.

    Converges fast for x < (a + 1) / (a + b + 2); callers use the
    symmetry I_x(a, b) = 1 − I_{1−x}(b, a) to stay in that region.
    Iterations needed grow like √max(a, b), so the cap scales with it.
    """
    max_iter = 200 + int(10 * math.sqrt(max(a, b)))
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > _TINY else _TINY)
    h = d
    for m in range(1, max_iter + 1):
        m2 = 2 * m
        # Even step of the fraction.
        num = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + num * d
        d = 1.0 / (d if abs(d) > _TINY else _TINY)
        c = 1.0 + num / c
        c = c if abs(c) > _TINY else _TINY
        h *= d * c
        # Odd step.
        num = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + num * d
        d = 1.0 / (d if abs(d) > _TINY else _TINY)
        c = 1.0 + num / c
        c = c if abs(c) > _TINY else _TINY
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < _CF_EPS:
            return h
    # Not reachable for finite positive a, b with the scaled cap; failing
    # loudly beats returning a silently wrong probability.
    raise ArithmeticError(
        f"incomplete beta continued fraction did not converge (a={a}, b={b}, x={x})"
    )


def _betainc(a: float, b: float, x: float, log_beta: float) -> float:
    # Unchecked core shared with the inverse, which calls it many times
    # with the same (a, b) and precomputed log B(a, b).
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    log_front = a * math.log(x) + b * math.log1p(-x) - log_beta
    if x < (a + 1.0) / (a + b + 2.0):
        return math.exp(log_front) * _beta_cf(a, b, x) / a
    return 1.0 - math.exp(log_front) * _beta_cf(b, a, 1.0 - x) / b


def betainc(a: float, b: float, x: float) -> float:
    """Regularized incomplete beta function I_x(a, b).

    The CDF of a Beta(a, b) distribution at x, and the identity behind
    every exact interval here: binomial tails are incomplete beta
    values, so Clopper-Pearson bounds and Bayesian credible bounds are
    both beta quantiles. Same argument order as
    ``scipy.special.betainc``. Accurate to ~1e-14 absolute for the
    parameter sizes run counts produce (a, b up to ~10**4).
    """
    a, b = _check_positive("a", a), _check_positive("b", b)
    x = _check_unit("x", x)
    return _betainc(a, b, x, _log_beta(a, b))


def betaincinv(a: float, b: float, y: float) -> float:
    """Inverse of ``betainc`` in x: the y-quantile of Beta(a, b).

    Newton's method on I_x(a, b) − y, kept inside a bisection bracket:
    every evaluation narrows [lo, hi], and any step that leaves the
    bracket — or fails to halve the previous step — is replaced by a
    bisection. That keeps bisection's guarantee (the root is always
    bracketed, I_x is monotone) at Newton's speed, typically under ten
    evaluations; plain bisection would need 50+ per bound, which adds
    up across a summary with many rows.
    """
    a, b = _check_positive("a", a), _check_positive("b", b)
    y = _check_unit("y", y)
    if y == 0.0:
        return 0.0
    if y == 1.0:
        return 1.0
    log_beta = _log_beta(a, b)
    lo, hi = 0.0, 1.0
    x = a / (a + b)  # the mean: a start inside the bulk of the mass
    prev_step = 1.0
    for _ in range(1100):  # 1074 halvings exhaust the doubles in (0, 1)
        f = _betainc(a, b, x, log_beta) - y
        if f == 0.0:
            return x
        if f < 0.0:
            lo = x
        else:
            hi = x
        log_pdf = (a - 1.0) * math.log(x) + (b - 1.0) * math.log1p(-x) - log_beta
        pdf = math.exp(log_pdf) if log_pdf < 700.0 else math.inf
        nxt = x - f / pdf if pdf > 0.0 else math.nan
        # Fall back to bisection when Newton leaves the bracket (NaN
        # fails this test too) or is converging no faster than it.
        if not lo < nxt < hi or abs(nxt - x) > 0.5 * prev_step:
            nxt = 0.5 * (lo + hi)
        step = abs(nxt - x)
        if nxt in (lo, hi) or step <= 4e-16 * nxt:
            return nxt
        prev_step, x = step, nxt
    return x


# ---------------------------------------------------------------------------
# One proportion: intervals
# ---------------------------------------------------------------------------


def _z(level: float) -> float:
    # Two-sided standard-normal critical value: 1.959963... at 0.95.
    return statistics.NormalDist().inv_cdf(0.5 + level / 2.0)


def clopper_pearson(x: int, n: int, level: float = 0.95) -> tuple[float, float]:
    """Clopper-Pearson ("exact") interval for a binomial proportion.

    The set of p not rejected by either one-sided binomial test at
    (1 − level)/2. Its coverage is never below ``level`` for any true p
    and any n — the property that makes it the default: a gate built on
    it may be conservative, but it never passes more often than the
    level promises. The bounds are beta quantiles; the bound at the
    edge of the data is exact (0 when x = 0, 1 when x = n).
    """
    x, n = _check_counts(x, n)
    alpha = 1.0 - _check_level(level)
    low = 0.0 if x == 0 else betaincinv(x, n - x + 1, alpha / 2.0)
    # Upper bound via the mirrored lower quantile: better accuracy than
    # asking for a quantile a hair below 1.
    high = 1.0 if x == n else 1.0 - betaincinv(n - x, x + 1, alpha / 2.0)
    return low, high


def wilson(x: int, n: int, level: float = 0.95) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion.

    Inverts the normal-approximation score test. Narrower than
    Clopper-Pearson and closer to nominal *on average*, but it can dip
    below nominal for particular p — the trade the user opts into with
    ``method="wilson"``.
    """
    x, n = _check_counts(x, n)
    z = _z(_check_level(level))
    z2 = z * z
    p_hat = x / n
    center = (p_hat + z2 / (2 * n)) / (1 + z2 / n)
    half = z / (1 + z2 / n) * math.sqrt(p_hat * (1 - p_hat) / n + z2 / (4 * n * n))
    # At x = 0 / x = n the bound is 0 / 1 analytically; pin it so float
    # rounding can't produce -1e-17 or 0.99999999999999989.
    low = 0.0 if x == 0 else max(0.0, center - half)
    high = 1.0 if x == n else min(1.0, center + half)
    return low, high


def beta_credible(
    x: int, n: int, level: float = 0.95, prior: tuple[float, float] = (1.0, 1.0)
) -> tuple[float, float]:
    """Equal-tailed Bayesian credible interval under a Beta prior.

    With prior Beta(a, b) the posterior is Beta(a + x, b + n − x); the
    interval cuts (1 − level)/2 from each tail of it. Any positive
    prior works, including non-integer ones: (1, 1) is uniform, (0.5,
    0.5) is Jeffreys. Improper priors such as Haldane's (0, 0) are
    rejected, because with x = 0 or x = n their posterior is improper
    too.
    """
    x, n = _check_counts(x, n)
    alpha = 1.0 - _check_level(level)
    if len(prior) != 2:
        raise ValueError(f"prior must be a pair (a, b), got {prior!r}")
    a = _check_positive("prior a", prior[0]) + x
    b = _check_positive("prior b", prior[1]) + (n - x)
    low = betaincinv(a, b, alpha / 2.0)
    high = 1.0 - betaincinv(b, a, alpha / 2.0)
    return low, high


def proportion_interval(
    x: int,
    n: int,
    level: float = 0.95,
    method: str = "exact",
    prior: tuple[float, float] = (1.0, 1.0),
) -> tuple[float, float]:
    """Interval for x successes in n trials by method name.

    ``method`` is one of ``METHODS``: ``"exact"`` (Clopper-Pearson),
    ``"wilson"``, or ``"bayes"`` (``beta_credible`` with ``prior``;
    ignored otherwise). The single entry point option values map onto,
    so rows, gates and the feasibility check all agree on what a
    method name means.
    """
    if method == "exact":
        return clopper_pearson(x, n, level)
    if method == "wilson":
        return wilson(x, n, level)
    if method == "bayes":
        return beta_credible(x, n, level, prior)
    raise ValueError(f"method must be one of {', '.join(METHODS)}; got {method!r}")


# ---------------------------------------------------------------------------
# Two proportions
# ---------------------------------------------------------------------------


def newcombe(
    x1: int, n1: int, x2: int, n2: int, level: float = 0.95
) -> tuple[float, float]:
    """Newcombe hybrid-score interval for the difference p1 − p2.

    Combines the two Wilson intervals (Newcombe's "method 10"). The
    samples are independent — two arms' runs of the same input are
    separate draws — and, unlike the Wald interval, it behaves at 0/n
    and n/n, which is exactly where benchmark arms tend to sit. For
    8/10 vs 10/10 at 0.95 it gives about (−0.51, +0.11).
    """
    x1, n1 = _check_counts(x1, n1, "x1", "n1")
    x2, n2 = _check_counts(x2, n2, "x2", "n2")
    l1, u1 = wilson(x1, n1, level)
    l2, u2 = wilson(x2, n2, level)
    p1, p2 = x1 / n1, x2 / n2
    diff = p1 - p2
    low = diff - math.sqrt((p1 - l1) ** 2 + (u2 - p2) ** 2)
    high = diff + math.sqrt((u1 - p1) ** 2 + (p2 - l2) ** 2)
    return max(-1.0, low), min(1.0, high)


def fisher_exact(
    x1: int, n1: int, x2: int, n2: int, alternative: str = "two-sided"
) -> float:
    """Fisher's exact test p-value for H0: p1 = p2.

    Conditions on the total number of successes, under which x1 is
    hypergeometric. ``alternative`` is ``"two-sided"`` (sum of every
    table no more likely than the observed one, with a 1e-7 relative
    tolerance so mathematically tied tables are not lost to rounding),
    ``"less"`` (p1 < p2) or ``"greater"`` (p1 > p2) — the same meaning
    as ``scipy.stats.fisher_exact`` on ``[[x1, n1-x1], [x2, n2-x2]]``.
    """
    x1, n1 = _check_counts(x1, n1, "x1", "n1")
    x2, n2 = _check_counts(x2, n2, "x2", "n2")
    if alternative not in ("two-sided", "less", "greater"):
        raise ValueError(
            "alternative must be 'two-sided', 'less' or 'greater';"
            f" got {alternative!r}"
        )
    successes = x1 + x2
    lo_k, hi_k = max(0, successes - n2), min(successes, n1)
    log_denominator = _log_comb(n1 + n2, successes)

    def log_pmf(k: int) -> float:
        return _log_comb(n1, k) + _log_comb(n2, successes - k) - log_denominator

    if alternative == "less":
        ks = range(lo_k, x1 + 1)
    elif alternative == "greater":
        ks = range(x1, hi_k + 1)
    else:
        cutoff = log_pmf(x1) + math.log1p(1e-7)
        ks = [k for k in range(lo_k, hi_k + 1) if log_pmf(k) <= cutoff]
    return min(1.0, math.fsum(math.exp(log_pmf(k)) for k in ks))


# ---------------------------------------------------------------------------
# Resampling
# ---------------------------------------------------------------------------


def bootstrap(
    data: Sequence[T],
    statistic: Callable[[list[T]], float] = statistics.fmean,
    *,
    resamples: int = 5000,
    seed: int = 0,
) -> list[float]:
    """Bootstrap distribution of ``statistic`` over ``data``, seeded.

    Each resample draws ``len(data)`` elements with replacement and
    applies ``statistic`` to them; the values come back in draw order.
    Elements can be anything — a per-case pass fraction for a
    function-level interval, an ``(arm_a, arm_b)`` pair to keep a
    paired comparison together — so resampling whole units is just a
    matter of what the caller puts in ``data``.

    The generator is a private ``random.Random(seed)``: the same data
    and seed give the same distribution in every process (xdist
    controller included) and never disturb the global ``random`` state
    the code under test may rely on.
    """
    if len(data) == 0:
        raise ValueError("data must not be empty")
    resamples = _check_int("resamples", resamples)
    if resamples < 1:
        raise ValueError(f"resamples must be at least 1, got {resamples}")
    seed = _check_int("seed", seed)
    rng = random.Random(seed)
    pool = list(data)
    k = len(pool)
    return [statistic(rng.choices(pool, k=k)) for _ in range(resamples)]


def _quantile(sorted_values: list[float], q: float) -> float:
    # Linear interpolation between order statistics (Hyndman & Fan type
    # 7, numpy's default), so results are easy to cross-check.
    pos = q * (len(sorted_values) - 1)
    i = math.floor(pos)
    if i + 1 >= len(sorted_values):
        return sorted_values[-1]
    frac = pos - i
    return sorted_values[i] + frac * (sorted_values[i + 1] - sorted_values[i])


def percentile_interval(
    samples: Sequence[float], level: float = 0.95
) -> tuple[float, float]:
    """Equal-tailed percentile interval of a bootstrap distribution.

    Cuts (1 − level)/2 from each end of ``samples``, interpolating
    linearly between order statistics.
    """
    alpha = 1.0 - _check_level(level)
    if len(samples) == 0:
        raise ValueError("samples must not be empty")
    ordered = sorted(samples)
    return _quantile(ordered, alpha / 2.0), _quantile(ordered, 1.0 - alpha / 2.0)


def normal_interval(data: Sequence[float], level: float = 0.95) -> tuple[float, float]:
    """Normal-approximation interval for the mean of ``data``.

    mean ± z·s/√N, with s the sample standard deviation (N − 1
    divisor). The textbook cross-check for a bootstrap interval on a
    mean: the two agree closely once N is moderate, and a gap between
    them says the bootstrap distribution is skewed — typically rates
    near 0 or 1. Not clipped, unlike the proportion intervals above: a
    bound past 0 or 1 is itself the sign that the approximation is
    poor there.
    """
    z = _z(_check_level(level))
    if len(data) < 2:
        raise ValueError(f"data must have at least 2 values, got {len(data)}")
    mean = statistics.fmean(data)
    half = z * statistics.stdev(data, mean) / math.sqrt(len(data))
    return mean - half, mean + half


# ---------------------------------------------------------------------------
# Clustered runs: intraclass correlation and interval width
# ---------------------------------------------------------------------------


def icc(counts: Sequence[tuple[int, int]], *, clip: bool = True) -> float | None:
    """Intraclass correlation ρ of per-run pass/fail outcomes, grouped
    by input: ``counts`` holds ``(passes, runs)`` per input.

    One-way random-effects ANOVA on the 0/1 outcome of every run, with
    inputs as groups — ICC(1). For binary outcomes the sums of squares
    follow from the counts alone (a run's square is the run itself):

    - between inputs: SSB = Σ xᵢ²/kᵢ − (Σ xᵢ)²/M, on N − 1 degrees of
      freedom;
    - within inputs: SSW = Σ xᵢ(kᵢ − xᵢ)/kᵢ, on M − N;

    with xᵢ passes in kᵢ runs, N inputs and M = Σ kᵢ runs. Unequal run
    counts use the adjusted average group size
    k₀ = (M − Σ kᵢ²/M)/(N − 1), which is k when every input has k runs
    (and never below 1), and

        ρ = (MSB − MSW) / (MSB + (k₀ − 1)·MSW).

    ρ near 0: an input's runs vary as much as runs of different inputs.
    ρ near 1: every run of an input gives the same result. The estimate
    can come out negative (inputs more alike than chance would allow);
    with ``clip`` (the default) it is clipped to [0, 1], the range the
    model allows. ``clip=False`` returns the raw value, for checking.

    ``None`` when ρ is not defined: fewer than 2 inputs, every input
    with a single run (nothing within an input to compare), or no
    variation at all — every run passed, or every run failed — when
    both mean squares are 0 and there is nothing to split.
    """
    pairs = [_check_counts(x, n, "passes", "runs") for x, n in counts]
    n_inputs = len(pairs)
    total = sum(n for _, n in pairs)
    if n_inputs < 2 or total == n_inputs:
        return None
    passes = sum(x for x, _ in pairs)
    ssb = math.fsum(x * x / n for x, n in pairs) - passes * passes / total
    ssw = math.fsum(x * (n - x) / n for x, n in pairs)
    # SSB is a difference of nearly equal sums when the inputs barely
    # differ; rounding must not turn "no spread" into a negative one.
    msb = max(0.0, ssb) / (n_inputs - 1)
    msw = ssw / (total - n_inputs)
    k0 = (total - math.fsum(n * n for _, n in pairs) / total) / (n_inputs - 1)
    denominator = msb + (k0 - 1.0) * msw
    if denominator <= 0.0:
        return None
    rho = (msb - msw) / denominator
    return min(1.0, max(0.0, rho)) if clip else rho


def width_factor(k: float, rho: float) -> float:
    """√((1 + (k − 1)ρ)/k): the standard deviation of one input's pass
    fraction over k runs, relative to that of a single run.

    An interval over N inputs has a width proportional to
    ``width_factor(k, ρ)/√N``. At ρ = 0 runs are as good as fresh inputs
    and the factor is 1/√k; at ρ = 1 an input's extra runs add nothing
    and it is 1. ``k`` may be fractional — for inputs with different run
    counts, the harmonic mean of the counts gives the factor of their
    equally weighted average exactly.
    """
    k = float(k)
    if not (k >= 1.0 and math.isfinite(k)):
        raise ValueError(f"k must be a finite number of at least 1, got {k}")
    rho = _check_unit("rho", rho)
    return math.sqrt((1.0 + (k - 1.0) * rho) / k)


def projected_width(
    k: float, rho: float, *, runs: float = 1.0, inputs: float = 1.0
) -> float:
    """How an interval's width over inputs would scale — new width over
    today's — with ``runs`` times as many runs per input and ``inputs``
    times as many inputs (new inputs alike to today's).

    ``width_factor(runs·k, ρ)/width_factor(k, ρ)/√inputs``: doubling the
    inputs always gives 1/√2 ≈ 0.71, while doubling the runs gives
    between that (ρ = 0) and 1 (ρ = 1). At k = 10, doubling the runs
    narrows the interval by about 22% at ρ = 0.025, 5% at ρ = 0.3 and 2%
    at ρ = 0.6.
    """
    runs = _check_positive("runs", runs)
    inputs = _check_positive("inputs", inputs)
    before = width_factor(k, rho)
    return width_factor(k * runs, rho) / before / math.sqrt(inputs)
