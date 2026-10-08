"""Plain-language readings of the results, for ``--prob-explain``.

Most people who read the summary are not statisticians. This module
holds every sentence the explain section prints, as templates kept
apart from the math: the plugin computes intervals, verdicts and
estimates, and the functions here only turn them into words. That
keeps the wording reviewable — and snapshot-testable — on its own.

Writing rules, from the issue that introduced it:

- No unexplained jargon: never "null hypothesis", "reject",
  "significant" or "alpha". Say what a number means for the decision.
- Round to what matters: whole-percent bounds (as printed in the
  table), one decimal for an observed rate, two significant figures
  for an estimated run count.
- End with a concrete next step when one exists, on its own
  ``Next:`` line.

How it fits together:

- A *reading* is one notable line of output explained: a ``Reading``
  with the line itself as its ``heading``, a few ``paragraphs`` of
  body, a ``tone`` (a verdict or status, for coloring) and the
  glossary ``terms`` it relied on. There is one function per kind of
  line: ``gate_reading``, ``row_reading``, ``aggregate_reading``
  (which also reads ρ and the runs-vs-inputs projection),
  ``metric_reading`` (pass^k and pass@k) and ``comparison_reading``;
  later features add their own next to them.
- The *glossary* ("Methods used") lists only the terms the readings
  used. Each entry is a function registered with ``@glossary_entry``
  under a key; a reading names ``(key, detail)`` pairs in ``terms``,
  and the entry receives the distinct details — for example every
  (method, level, prior) an interval was computed with — so it names
  exactly the methods that appeared. Entries print in registration
  order.
- ``render_section`` lays readings and glossary out for the terminal,
  wrapped to its width; ``Reading.text()`` is the unwrapped form the
  JSON report carries.

Everything is duck-typed on the plugin's objects (``GateResult``,
``CaseStats``, ``StatsConfig``, ``Aggregate``). The plugin imports this
module only when it needs it, so the dependency runs one way.
"""
from __future__ import annotations

import math
import textwrap
from dataclasses import dataclass
from typing import Any, Callable, Hashable, Iterable

from .plugin import (
    FAIL,
    PASS,
    UNDECIDED,
    _change,
    _p,
    _pct,
    _pct1,
    _pct_bar,
    _pp,
    _pp_decimals,
    _ratio,
)

#: The last line of the normal summary when something deserves a
#: closer look and ``--prob-explain`` is off.
HINT = "Run with --prob-explain for a plain-language reading."

#: Where the glossary sends readers for the full story.
GUIDE_URL = "https://pytest-probability.readthedocs.io/en/latest/reference.html#gates"

# Layout: headings sit where table rows do, bodies one step in.
_HEAD_INDENT = "  "
_BODY_INDENT = "    "
# Below this many columns of text, wrapping hurts more than it helps.
_MIN_TEXT_WIDTH = 30


@dataclass(frozen=True)
class Reading:
    """One notable line of output, explained.

    ``heading`` repeats the line being explained; ``paragraphs`` are
    wrapped separately (the last is usually the ``Next:`` step);
    ``tone`` is a verdict (``"pass"``/``"fail"``/``"undecided"``) or a
    row status, for coloring; ``terms`` are the ``(key, detail)``
    glossary entries the wording relied on.
    """

    heading: str
    paragraphs: tuple[str, ...]
    tone: str | None = None
    terms: tuple[tuple[str, Hashable], ...] = ()

    def text(self) -> str:
        """The body as one string — paragraphs on their own lines — for
        the JSON report."""
        return "\n".join(self.paragraphs)


# ---------------------------------------------------------------------------
# Number formatting
# ---------------------------------------------------------------------------


def rate(p: float) -> str:
    """An observed rate: one decimal, ``.0`` dropped — ``92.5%``, ``90%``."""
    text = f"{p * 100:.1f}".removesuffix(".0")
    return f"{text}%"


def about(n: int) -> str:
    """A run-count estimate rounded up to what matters: exact below
    100, else two significant figures — ``37``, ``540``, ``1,100``."""
    if n >= 100:
        step = 10 ** (len(str(n)) - 2)
        n = -(-n // step) * step
    return f"{n:,}"


def _range(interval: tuple[float, float]) -> str:
    # The same whole percents the table prints.
    return f"between {_pct(interval[0])} and {_pct(interval[1])}"


def _level(stats: Any) -> str:
    # "95% confidence", or "95% probability" for a Bayesian range.
    word = "probability" if stats.method == "bayes" else "confidence"
    return f"{_pct_bar(stats.level)} {word}"


def stats_key(stats: Any) -> tuple:
    # The prior only matters to the Bayesian method.
    prior = tuple(stats.prior) if stats.method == "bayes" else None
    return (stats.method, stats.level, prior)


def _runs(n: int) -> str:
    return f"{n} run" if n == 1 else f"{n} runs"


# ---------------------------------------------------------------------------
# Readings
# ---------------------------------------------------------------------------

_ERRORED = "raised an exception other than a failed assert"

_RATE_VERDICTS = {
    PASS: "Your bar is {bar}, and that whole range is above it, so this case"
    " meets the bar.",
    FAIL: "Your bar is {bar}, and that whole range is below it, so this case"
    " falls short of the bar.",
    UNDECIDED: "Your bar is {bar}, and that range has values both above and"
    " below it, so there isn't enough data yet to tell whether this case"
    " meets the bar.",
}

_UNDECIDED_POLICY = {
    True: "Until that's settled, the case fails the test session"
    " (--prob-undecided=pass would let it through).",
    False: "This session lets undecided cases through"
    " (--prob-undecided=pass), so it doesn't fail the test session.",
}

_LOOK_AT_FAILURES = (
    "look at the failing runs: -rx lists them with their assert messages,"
    " and --xfail-tb shows their tracebacks."
)


def gate_reading(
    result: Any,
    *,
    errors: int = 0,
    undecided_fails: bool = True,
    settle: int | None = None,
    cap: int = 0,
) -> Reading:
    """A gate's verdict explained.

    ``result`` is a ``GateResult``; ``errors`` the case's errored runs
    (counted as non-passes unless the gate excluded them);
    ``undecided_fails`` the session's UNDECIDED policy; ``settle`` the
    gate's ``runs_to_settle()`` estimate and ``cap`` the limit it
    searched up to.
    """
    gate = result.gate
    passes, total, excluded = result.passes, result.total, result.excluded
    verdict, interval = result.verdict, result.interval
    terms: list[tuple[str, Hashable]] = []
    if interval is not None:
        terms.append(("interval", stats_key(gate.stats)))
    terms.append(("verdict", None) if gate.rule == "rate" else ("count", None))
    if excluded:
        terms.append(("excluded", None))

    heading = f"{result.case}  {passes}/{total}"
    if interval is not None:
        heading += f"  [{_pct(interval[0])}, {_pct(interval[1])}]"
    heading += f"  {gate.bar()}  {verdict.upper()}"
    if excluded:
        heading += f"  ({excluded} errored, excluded)"

    counted_errors = 0 if excluded else errors
    paras: list[str] = []
    if not total:
        # Every run errored and the gate left them all out.
        what = (
            "the verdict is UNDECIDED"
            if verdict == UNDECIDED
            else f"the verdict is {verdict.upper()}"
        )
        body = (
            f"All {_runs(excluded)} errored ({_ERRORED}), and this gate leaves"
            f" errored runs out (prob_errors = exclude), so there are no runs"
            f" to judge it on: {what}."
        )
        if verdict == UNDECIDED:
            body += " " + _UNDECIDED_POLICY[undecided_fails]
        paras.append(body)
        paras.append(
            "Next: fix what raised the errors (-rx lists each one with its"
            " exception), then run again."
        )
        return Reading(heading, tuple(paras), verdict, tuple(terms))

    first = f"{passes} of {_runs(total)} passed."
    if excluded:
        first += (
            f" {excluded} more errored ({_ERRORED}) and were left out of"
            " this gate (prob_errors = exclude)."
        )
    elif counted_errors:
        first += (
            f" {counted_errors} of the runs that didn't pass errored"
            f" ({_ERRORED}); they count as non-passes."
        )

    if gate.rule == "count":
        need = gate.min_passes
        body = f"{first} This gate counts passes and needs at least {need}."
        if verdict == PASS:
            body += " That's enough, so this case meets the bar."
        elif total < need:
            body += (
                f" Only {_runs(total)} counted, so it couldn't reach"
                f" {need} passes however they went."
            )
        else:
            body += (
                f" It is {need - passes} short, so this case falls short of"
                " the bar."
            )
        if interval is not None:
            body += (
                f" (The range [{_pct(interval[0])}, {_pct(interval[1])}] beside"
                " it is for information only: this gate judges the count.)"
            )
        paras.append(body)
        if verdict != PASS:
            if total < need and not excluded:
                paras.append(
                    f"Next: run it at least {need} times (runs={need} on the"
                    " mark, or --prob-runs)."
                )
            else:
                paras.append(f"Next: {_LOOK_AT_FAILURES}")
        return Reading(heading, tuple(paras), verdict, tuple(terms))

    bar = _pct_bar(gate.min_rate)
    body = (
        f"{first} The true pass rate is probably {_range(interval)}"
        f" ({_level(gate.stats)}). {_RATE_VERDICTS[verdict].format(bar=bar)}"
    )
    if verdict == UNDECIDED:
        body += " " + _UNDECIDED_POLICY[undecided_fails]
    paras.append(body)

    observed = passes / total
    # The estimate is in judged runs: errored ones don't move it.
    in_total = " in total, not counting errored ones," if excluded else " in total"
    if verdict == FAIL:
        step = f"Next: {_LOOK_AT_FAILURES}"
        if counted_errors:
            step += " Fix what raised the errors too."
        step += " If a lower pass rate is acceptable for this case, lower the bar."
        paras.append(step)
    elif verdict == UNDECIDED:
        if settle is None:
            paras.append(
                f"Next: today's rate ({rate(observed)}) is so close to the bar"
                f" that even {cap:,} runs might not settle it. Decide whether"
                f" {bar} is the right bar, and look at the failing runs (-rx)"
                " to see what goes wrong."
            )
        elif observed > gate.min_rate:
            paras.append(
                f"Next: run more. If it keeps passing at today's rate"
                f" ({rate(observed)}), about {about(settle)} runs{in_total}"
                " would settle it."
            )
        else:
            paras.append(
                f"Next: today's rate ({rate(observed)}) is below the bar; if it"
                f" holds, about {about(settle)} runs{in_total} would confirm"
                f" that it falls short. Meanwhile, {_LOOK_AT_FAILURES}"
            )
    return Reading(heading, tuple(paras), verdict, tuple(terms))


# The first sentence of a row reading, by status.
_ROW_STATUS = {
    "pass": "{passes} of {total} runs passed.",
    "flaky": "{passes} of {total} runs passed; the others failed, so the"
    " outcome changes from run to run.",
    "fail": "None of the {total} runs passed.",
    "errored": "{passes} of {total} runs passed; the others errored"
    " ({errored}), which usually means the harness or a service broke, not"
    " that the code answered wrong.",
    "error": "All {total} runs errored ({errored}), which usually means the"
    " harness or a service broke, not that the code answered wrong.",
}

_ROW_NEXT = {
    "pass": None,
    "flaky": "Next: look at the failures above to see what differs between"
    " runs. To hold the case to a pass rate instead of every run, gate it:"
    " @pytest.mark.probability(min_rate=...).",
    "fail": "Next: look at the failures above.",
    "errored": "Next: fix what raised the errors (see the tracebacks above),"
    " then run again.",
    "error": "Next: fix what raised the errors (see the tracebacks above),"
    " then run again.",
}


def row_reading(
    s: Any, interval: tuple[float, float] | None, stats: Any, gated: bool = False
) -> Reading:
    """A summary row explained: what its fraction and interval say.

    ``s`` is the row's ``CaseStats``, ``interval`` the session's
    interval over every run (``None`` with no runs) and ``stats`` the
    ``StatsConfig`` it came from. A gated row gets no ``Next:`` step:
    its gate's reading carries it.
    """
    status = s.status
    heading = f"{s.case}  {s.passes}/{s.total}"
    terms: list[tuple[str, Hashable]] = []
    if s.total > 1 and interval is not None:
        heading += f"  [{_pct(interval[0])}, {_pct(interval[1])}]"
    if status != "pass":
        heading += f"  {status.upper()}"

    paras: list[str] = []
    if s.total == 1:
        outcome = {
            "pass": "passed",
            "fail": "failed",
            "error": f"errored ({_ERRORED})",
        }.get(status, status)
        paras.append(
            f"The one run {outcome}. A single run says little about how often"
            " this case passes."
        )
        if not gated:
            paras.append(
                "Next: run it more times (for example --prob-runs=10) to see a"
                " pass rate."
            )
        return Reading(heading, tuple(paras), status, tuple(terms))

    body = _ROW_STATUS[status].format(
        passes=s.passes, total=s.total, errored=_ERRORED
    )
    if s.errors and status in ("flaky", "fail"):
        body += (
            f" {s.errors} of the runs that didn't pass errored ({_ERRORED})."
        )
    if status != "error" and interval is not None:
        terms.append(("interval", stats_key(stats)))
        body += f" The true pass rate is probably {_range(interval)} ({_level(stats)})."
        if s.errors:
            body += " Errored runs count as non-passes in that range."
    paras.append(body)
    step = _ROW_NEXT.get(status)
    if step and not gated:
        paras.append(step)
    return Reading(heading, tuple(paras), status, tuple(terms))


def aggregate_reading(agg: Any, min_inputs: int) -> Reading:
    """A function-level (or Overall) line explained.

    ``agg`` is an ``Aggregate``; ``min_inputs`` the session's
    ``prob_min_inputs``. An aggregate without an interval (too few
    inputs) only reaches the JSON report, and its reading says why.
    """
    n, est = agg.inputs, _pct1(agg.estimate)
    heading = f"{agg.name}  {agg.size()}  {est}"
    if agg.ci is not None:
        heading += f"  [{_pct1(agg.ci[0])}, {_pct1(agg.ci[1])}]"
    if agg.scope == "overall":
        whose, subject = f"All {n} inputs in this session", "your code"
    else:
        whose, subject = f"{agg.name}'s {n} inputs", agg.name
    terms = (("aggregate", (agg.level, agg.resamples, agg.seed, min_inputs)),)

    first = (
        f"{whose} passed {est} of their runs on average, each input counting"
        " equally"
    )
    low, _, high = agg.runs
    if low != high:
        first += f" however many runs it had ({low} to {high})"
    first += "."
    if agg.ci is None:
        paras = (
            f"{first} No range is shown: with fewer than {min_inputs} inputs"
            " (prob_min_inputs), a range worked out from the inputs alone comes"
            " out too narrow.",
            f"Next: add inputs (more parametrize cases) to reach {min_inputs}.",
        )
        return Reading(heading, paras, None, terms)
    paras = [
        f"{first} The true average pass rate is probably between"
        f" {_pct1(agg.ci[0])} and {_pct1(agg.ci[1])}"
        f" ({_pct_bar(agg.level)} confidence).",
        f"That range treats these {n} inputs as a random sample of the inputs"
        f" {subject} will meet, so it allows for other inputs doing better or"
        " worse, not only for runs varying. If you picked the inputs by hand"
        " rather than at random, it measures the cases you chose, not inputs"
        " in general.",
    ]
    projection = agg.projection()
    if projection is None:
        paras.append(
            "Next: to narrow the range, add inputs. More runs of the same inputs"
            " narrow it less, often much less, because they can't show how"
            " other inputs would do."
        )
        return Reading(heading, tuple(paras), None, terms)
    heading += f"  ρ={agg.icc:.2f}"
    terms += (("icc", None),)
    paras.extend(_budget(agg, projection))
    return Reading(heading, tuple(paras), None, terms)


# More runs count as helping when doubling them buys at least this
# share of what doubling the inputs would.
_RUNS_HELP = 0.5


def _budget(agg: Any, projection: tuple[float, float]) -> list[str]:
    """The ρ and runs-vs-inputs paragraphs of an aggregate reading.

    Doubling the inputs always narrows the range more than doubling the
    runs (or as much, at ρ = 0), so the advice turns on whether runs
    still buy a fair share of it.
    """
    runs, inputs = projection
    runs_help = runs <= _RUNS_HELP * inputs
    if runs_help:
        reading = (
            "Here it is low enough that the outputs vary noticeably from run"
            " to run, so more runs of an input still tell you more about it."
        )
    else:
        reading = (
            "Here it is high enough that each input tends to be consistently"
            " right or consistently wrong, so more runs of an input mostly"
            " repeat what you already know about it."
        )
    body = (
        f"ρ = {agg.icc:.2f} measures how alike the runs of one input are, from"
        " 0 (an input's runs differ as much as runs of different inputs) to 1"
        f" (every run of an input gives the same result). {reading} With twice"
        f" the runs of every input the range would be {_narrower(runs)}; with"
        f" twice as many inputs (and as many runs each), {_narrower(inputs)}."
    )
    if agg.cost:
        body += (
            " Either way the number of runs doubles, so each option would cost"
            f" about ${agg.cost:.4f} more, assuming new inputs cost as much per"
            " run as these did."
        )
    if runs_help:
        step = (
            "Next: to narrow the range, add runs or inputs: both help here, and"
            " more runs are often easier, since they need no new inputs."
        )
    else:
        step = (
            "Next: to narrow the range, add inputs. More runs of the same inputs"
            " would help much less."
        )
    return [body, step]


def _narrower(change: float) -> str:
    # The same rounding as the summary's continuation line.
    text = _change(change)
    if text == "±0%":
        return "no narrower"
    if text == "−<1%":
        return "less than 1% narrower"
    return f"about {text.removeprefix('−')} narrower"


# ---------------------------------------------------------------------------
# Metrics: pass^k and pass@k
# ---------------------------------------------------------------------------


def _metric_meaning(metric: Any) -> str:
    """What a metric measures, in one sentence."""
    name, k = metric.name, metric.k
    if k == 1:
        return f"{name} is the chance that one run of an input passes: the pass rate."
    if metric.kind == "^":
        return (
            f"{name} is the chance that {k} runs of the same input all pass:"
            " reliability, for code that has to work every time it is called."
        )
    return (
        f"{name} is the chance that at least one of {k} runs of the same input"
        f" passes: best-of-{k}, for when a failed attempt can be retried, or the"
        f" best of {k} answers kept."
    )


def _inputs(n: int) -> str:
    return f"{n} input" if n == 1 else f"{n} inputs"


def metric_reading(
    agg: Any, m: Any, min_inputs: int, *, intervals: bool = True
) -> Reading:
    """A line of the metrics block explained: what the metric means, its
    average over inputs and range, how many inputs were too short for
    it, and a next step.

    ``agg`` is the ``Aggregate`` the line belongs to and ``m`` its
    ``MetricAggregate``; ``min_inputs`` the session's
    ``prob_min_inputs``; ``intervals`` whether the block shows ranges.
    """
    from .plugin import _metric_lines

    heading = _metric_lines([(agg, m)], intervals)[0].strip()
    metric, k = m.metric, m.metric.k
    kind = "pass^k" if metric.kind == "^" else "pass@k"
    terms: list[tuple[str, Hashable]] = [(kind, None)]
    meaning = _metric_meaning(metric)
    step = (
        f"Next: give every case at least {k} runs (--prob-runs={k} or more, or"
        " runs= on its mark)"
    )
    if m.aggregate is None:
        where = "in this session" if agg.scope == "overall" else f"of {agg.name}"
        paras = (
            f"{meaning} No input {where} has {k} runs, so it can't be worked"
            f" out yet: an input needs at least {k} runs for it.",
            f"{step}.",
        )
        return Reading(heading, paras, None, tuple(terms))

    inner, n = m.aggregate, m.inputs
    if agg.scope == "overall":
        whose, subject = f"the {_inputs(n)} in this session", "your code"
    else:
        whose, subject = f"{agg.name}'s {_inputs(n)}", agg.name
    est = _pct1(inner.estimate)
    body = (
        f"{meaning} Averaged over {whose}, each input counting equally, it is"
        f" {est}."
    )
    if m.left_out:
        body += (
            f" {_inputs(m.left_out).capitalize()} had fewer than {k} runs and"
            f" {'was' if m.left_out == 1 else 'were'} left out."
        )
    if k > 1:
        mean_rate = sum(c / t for c, t in inner.counts) / n
        body += (
            f" For comparison, the same inputs passed {_pct1(mean_rate)} of"
            " their runs"
        )
        if metric.kind == "^" and inner.estimate < mean_rate:
            every = "both runs" if k == 2 else f"all {k} runs"
            body += f"; {metric.name} is lower because {every} have to pass."
        elif metric.kind == "@" and inner.estimate > mean_rate:
            body += f"; {metric.name} is higher because one pass in {k} runs is enough."
        else:
            body += "."
    if inner.ci is not None:
        setup = (inner.level, inner.resamples, inner.seed, min_inputs)
        terms.append(("aggregate", setup))
        body += (
            f" The true average is probably between {_pct1(inner.ci[0])} and"
            f" {_pct1(inner.ci[1])} ({_pct_bar(inner.level)} confidence),"
            f" treating these inputs as a random sample of the inputs {subject}"
            " will meet."
        )
    else:
        body += (
            f" No range is shown: with fewer than {min_inputs} inputs"
            " (prob_min_inputs), a range worked out from the inputs alone comes"
            " out too narrow."
        )
    paras = [body]
    if m.left_out:
        paras.append(f"{step}, so that no input is left out.")
    elif inner.ci is None:
        paras.append(
            f"Next: add inputs (more parametrize cases) to reach {min_inputs}."
        )
    return Reading(heading, tuple(paras), None, tuple(terms))


# ---------------------------------------------------------------------------
# Comparisons
# ---------------------------------------------------------------------------


def chance(p: float) -> str:
    """A p-value as a frequency: ``about 5 times in 10``, ``about 4 times
    in 1,000``; ``less than 1 time in 1,000`` where the table prints
    ``p<0.001``."""
    if p >= 0.945:  # prints as 0.95 or more
        return "nearly every time"
    if p < 0.001:
        return "less than 1 time in 1,000"
    for scale in (10, 100, 1_000):
        times = math.floor(p * scale + 0.5)
        if times >= 1:
            word = "time" if times == 1 else "times"
            return f"about {times} {word} in {scale:,}"
    return "about 1 time in 1,000"


def p_is(p: float) -> str:
    # "p = 0.47", "p < 0.001": the table's rounding, in a sentence.
    text = _p(p)
    return f"p < {text[1:]}" if text.startswith("<") else f"p = {text}"


def points(d: float, decimals: int) -> str:
    # "+20 points", "−11 points": the table's rounding, spelled out.
    return f"{_pp(d, decimals)} points"


_ADJUST_NAMES = {
    "holm": "Holm",
    "bonferroni": "Bonferroni",
    "bh": "Benjamini-Hochberg",
}


def _direction(cmp: Any) -> str:
    """What the range says about which arm is better."""
    low, high = cmp.ci
    if low > 0:
        return f"so {cmp.arm} probably does better than {cmp.baseline}."
    if high < 0:
        return f"so {cmp.arm} probably does worse than {cmp.baseline}."
    return (
        "so the data can't tell yet which arm is better: the range includes"
        " no difference at all."
    )


def _p_paragraph(cmp: Any) -> str:
    p = cmp.shown_p
    if cmp.adjustment != "none" and cmp.family > 1:
        name = _ADJUST_NAMES[cmp.adjustment]
        text = (
            f"{p_is(p)}, adjusted for the {cmp.family} comparisons in this"
            f" session ({name}; {p_is(cmp.p)} before adjusting): if there were no"
            f" real difference, a gap this big would turn up by chance"
            f" {chance(p)}, even allowing for making {cmp.family} comparisons."
        )
    else:
        text = (
            f"{p_is(p)}: if there were no real difference, a gap this big would"
            f" turn up by chance {chance(p)}."
        )
    if cmp.exploratory:
        text += (
            f" This session makes {cmp.family} comparisons and their p-values"
            " are not adjusted for that, so read them as exploratory: with"
            " many comparisons, some small p-values turn up by chance"
            " (--prob-adjust=holm adjusts them)."
        )
    return text


def _margin_paragraph(cmp: Any, undecided_fails: bool) -> str:
    spec, verdict = cmp.spec, cmp.verdict
    number = f"{spec.margin * 100:g}"
    size = f"{number} points"
    if spec.equivalence:
        body = (
            f"Your margin is ±{size}: the arms count as equivalent when the"
            f" whole range sits between −{number} and +{size}."
        )
        if verdict == PASS:
            body += " It does, so they are equivalent within the margin."
        elif verdict == FAIL:
            body += (
                " The whole range is outside it, so the arms differ by more"
                " than the margin."
            )
    else:
        body = (
            f"Your margin is {size}: {cmp.arm} passes if it is at most {size}"
            f" worse than {cmp.baseline}, which needs the whole range above"
            f" −{size}."
        )
        if verdict == PASS:
            body += f" It is, so {cmp.arm} is no worse than the margin allows."
        elif verdict == FAIL:
            body += (
                f" The whole range is below it, so {cmp.arm} is worse than the"
                " margin allows."
            )
    if verdict == UNDECIDED:
        if cmp.ci is None:
            body += " There is no range yet, so the verdict is UNDECIDED."
        else:
            body += (
                " The range has values on both sides of that, so there isn't"
                " enough data yet to tell: the verdict is UNDECIDED."
            )
        body += " " + (
            "Until that's settled, the comparison fails the test session"
            " (--prob-undecided=pass would let it through)."
            if undecided_fails
            else "This session lets undecided verdicts through"
            " (--prob-undecided=pass), so it doesn't fail the test session."
        )
    return body


def comparison_reading(
    cmp: Any, *, undecided_fails: bool = True, min_inputs: int = 10
) -> Reading:
    """A comparison explained: the difference, its range, the p-value
    and any margin verdict.

    ``cmp`` is a ``Comparison``; ``undecided_fails`` the session's
    UNDECIDED policy and ``min_inputs`` its ``prob_min_inputs``.
    """
    from .plugin import _comparison_lines

    heading = _comparison_lines([cmp])[0][0].strip()
    dec = _pp_decimals(cmp)
    arm, base = cmp.arm, cmp.baseline
    terms: list[tuple[str, Hashable]] = []
    paras: list[str] = []
    unpaired = ""
    if cmp.unpaired:
        n = len(cmp.unpaired)
        unpaired = (
            f" {n} {'input' if n == 1 else 'inputs'} ran in only one of the two"
            " arms (for example after -k or -m) and"
            f" {'was' if n == 1 else 'were'} left out."
        )

    if cmp.pairs == 0:
        paras.append(
            f"No input ran in both {arm} and {base}, so there is nothing to"
            f" compare.{unpaired}"
        )
        if cmp.verdict is not None:
            paras.append(_margin_paragraph(cmp, undecided_fails))
            terms.append(("margin", None))
        paras.append(
            "Next: run both arms on the same inputs (check what -k or -m"
            " selected)."
        )
        return Reading(heading, tuple(paras), cmp.verdict, tuple(terms))

    if cmp.pairs == 1:
        only = cmp.inputs[0]
        (xa, na), (xb, nb) = only.arm, only.baseline
        where = f" ({only.input})" if only.input else ""
        body = (
            f"On the one input both arms ran{where}, {arm} passed {xa} of"
            f" {na} runs and {base} {xb} of {nb}: a difference of"
            f" {points(cmp.estimate, dec)}."
        )
    else:
        body = (
            f"Over the {cmp.pairs} inputs both arms ran, {arm} passed"
            f" {_pp(abs(cmp.estimate), dec).lstrip('+')} points"
            f" {'fewer' if cmp.estimate < 0 else 'more'} of its runs than {base}"
            " on average, each input counting equally."
        )
    body += unpaired
    setup = (cmp.level, cmp.resamples, cmp.seed, min_inputs)
    if cmp.ci is not None:
        terms.append(("difference", (cmp.ci_method, *setup)))
        low, high = (_pp(v, dec) for v in cmp.ci)
        body += (
            f" The true difference is probably between {low} and {high} points"
            f" ({_pct_bar(cmp.level)} confidence), {_direction(cmp)}"
        )
    else:
        terms.append(("difference", ("suppressed", *setup)))
        body += (
            f" No range is shown: with fewer than {min_inputs} paired inputs"
            " (prob_min_inputs), a range worked out from the inputs alone"
            " comes out too narrow. Each input's own difference and range are"
            " listed with it instead."
        )
    paras.append(body)
    if cmp.pairs == 1:
        paras.append(
            "With a single input, this compares the arms on that input only:"
            " it says nothing about how they would do on other inputs."
        )
    elif cmp.ci is not None:
        paras.append(
            f"That range treats these {cmp.pairs} inputs as a random sample of"
            f" the inputs {cmp.function} will meet, keeping each input's runs"
            " of both arms together, so it allows for other inputs doing"
            " better or worse, not only for runs varying."
        )
    if cmp.p is not None:
        terms.append(("p", (cmp.p_method, cmp.exact, cmp.resamples, cmp.seed)))
        if cmp.adjustment != "none" or cmp.exploratory:
            terms.append(("adjust", cmp.adjustment))
        paras.append(_p_paragraph(cmp))
    if cmp.cost_ratio is not None:
        times = _ratio(cmp.cost_ratio).lstrip("×")
        paras.append(
            f"Per run, {arm} cost about the same as {base}."
            if times == "1.0"
            else f"Per run, {arm} cost {times} times as much as {base}."
        )
    if cmp.verdict is not None:
        terms.append(("margin", None))
        paras.append(_margin_paragraph(cmp, undecided_fails))

    if cmp.ci is None:
        paras.append(
            f"Next: add inputs (more parametrize cases) to reach {min_inputs}"
            " paired inputs."
        )
    elif cmp.verdict == FAIL:
        paras.append(
            f"Next: look at {arm}'s failing runs (-rx lists them), or keep"
            f" {base}."
        )
    elif cmp.ci[0] <= 0 <= cmp.ci[1] or cmp.verdict == UNDECIDED:
        if cmp.pairs == 1:
            paras.append(
                "Next: to tell the arms apart, add inputs (more parametrize"
                " cases); more runs of this one narrow its range too."
            )
        else:
            paras.append(
                "Next: to narrow the range, add inputs (more parametrize"
                " cases); more runs help less when each input is consistently"
                " right or wrong."
            )
    return Reading(heading, tuple(paras), cmp.verdict, tuple(terms))


# ---------------------------------------------------------------------------
# Glossary ("Methods used")
# ---------------------------------------------------------------------------

# key -> (label, entry function); dicts keep registration order, which
# is the print order.
_GLOSSARY: dict[str, tuple[str, Callable[[list[Any]], str]]] = {}


def glossary_entry(key: str, label: str):
    """Register a glossary entry: ``fn(details) -> text``, where
    ``details`` are the distinct details readings gave for ``key``, in
    first-seen order."""

    def register(fn: Callable[[list[Any]], str]):
        _GLOSSARY[key] = (label, fn)
        return fn

    return register


_METHOD_NAMES = {
    "exact": "Clopper-Pearson",
    "wilson": "Wilson score",
    "bayes": "Bayesian",
}

_METHOD_NOTES = {
    "exact": "errs on the side of a wider range",
    "wilson": "a close approximation, slightly narrower",
}


def _method_text(key: tuple) -> str:
    method, level, prior = key
    if method == "bayes":
        a, b = prior
        if (a, b) == (1.0, 1.0):
            note = "from a Beta(1, 1) prior, which means no starting preference"
        else:
            note = (
                f"from a Beta({a:g}, {b:g}) prior: a starting belief worth"
                f" roughly {a:g} passes and {b:g} fails"
            )
        return f"(Bayesian, {_pct_bar(level)} probability, {note}.)"
    return (
        f"({_METHOD_NAMES[method]}, {_pct_bar(level)} confidence:"
        f" {_METHOD_NOTES[method]}.)"
    )


@glossary_entry("interval", "[low, high]")
def _interval_entry(keys: list[tuple]) -> str:
    return " ".join(
        [
            "The range the true pass rate probably falls in, given the runs so"
            " far. More runs make it narrower.",
            *(_method_text(k) for k in keys),
        ]
    )


@glossary_entry("verdict", "Verdict")
def _verdict_entry(_: list) -> str:
    return (
        "PASS: the whole range is above the bar. FAIL: the whole range is"
        " below it. UNDECIDED: the range crosses the bar, so more runs are"
        " needed."
    )


@glossary_entry("count", "≥N passes")
def _count_entry(_: list) -> str:
    return (
        "A count bar (min_passes): PASS when at least N runs passed, FAIL"
        " otherwise. Any range beside it is for information only."
    )


@glossary_entry("excluded", "excluded")
def _excluded_entry(_: list) -> str:
    return (
        f"Runs that errored ({_ERRORED}). Under prob_errors = exclude a gate"
        " leaves them out, so they count neither for nor against the case."
    )


@glossary_entry("aggregate", "N inputs × k")
def _aggregate_entry(keys: list[tuple]) -> str:
    return " ".join(
        [
            "An average over inputs: each input's share of passing runs,"
            " averaged so every input counts equally. N is the number of"
            " inputs and k the runs each had (a range when they differ). The"
            " range beside it comes from re-drawing the set of inputs at random"
            " many times, keeping each input's runs together, and seeing how"
            " far the average moves.",
            *(
                f"(A bootstrap over inputs: {resamples:,} re-draws, seed {seed},"
                f" {_pct_bar(level)} confidence. Shown only with {min_inputs} or"
                " more inputs; with fewer, the range comes out too narrow.)"
                for level, resamples, seed, min_inputs in keys
            ),
        ]
    )


@glossary_entry("icc", "ρ")
def _icc_entry(_: list) -> str:
    return (
        "How alike the runs of one input are (the intraclass correlation),"
        " from 0 (an input's runs differ as much as runs of different inputs)"
        " to 1 (every run of an input gives the same result). Low: outputs"
        " vary from run to run, so more runs help. High: each input is"
        " consistently right or wrong, so add inputs instead. \"runs ×2\" and"
        " \"inputs ×2\" are how much the range would narrow with twice the"
        " runs of every input, or twice as many inputs; each doubles the"
        " number of runs, and the cost shown assumes new inputs cost as much"
        " per run as these did. With few inputs ρ is itself rough, so read"
        " them as a direction, not a promise. (One-way analysis of variance"
        " on every run's pass or fail, adjusted for inputs with different run"
        " counts, kept between 0 and 1; the range scales with"
        " √((1+(k−1)ρ)/k) for k runs per input.)"
    )


_METRIC_TAIL = (
    " Each input's value comes from its own runs, on average neither too"
    " high nor too low, and the line averages it over the inputs, each"
    " counting equally; inputs with fewer than k runs are left out. k is"
    " the number of runs the metric draws, not the runs each input had."
)


@glossary_entry("pass^k", "pass^k")
def _pass_hat_entry(_: list) -> str:
    return (
        "The chance that k runs of the same input all pass: reliability, for"
        " code that has to work every time it is called. It can only fall as"
        " k grows; pass^1 is the pass rate." + _METRIC_TAIL + " (Of all the"
        " ways to pick k of an input's n runs, the share in which all k"
        " passed: C(c,k)/C(n,k) for c passes.)"
    )


@glossary_entry("pass@k", "pass@k")
def _pass_at_entry(_: list) -> str:
    return (
        "The chance that at least one of k runs of the same input passes:"
        " best-of-k, for when a failed attempt can be retried, or the best of"
        " k answers kept. It can only rise as k grows; pass@1 is the pass"
        " rate." + _METRIC_TAIL + " (Of all the ways to pick k of an input's"
        " n runs, the share with at least one pass: 1 − C(n−c,k)/C(n,k)"
        " for c passes.)"
    )


@glossary_entry("difference", "+N pp")
def _difference_entry(keys: list[tuple]) -> str:
    notes = []
    for method, level, resamples, seed, min_inputs in keys:
        if method == "newcombe":
            notes.append(
                f"(One input: Newcombe's hybrid score range, {_pct_bar(level)}"
                " confidence, built from each arm's Wilson range; the two arms'"
                " runs are separate samples.)"
            )
        elif method == "bootstrap":
            notes.append(
                f"(Over inputs: a paired bootstrap, {resamples:,} re-draws of"
                f" the inputs with both arms' runs kept together, seed {seed},"
                f" {_pct_bar(level)} confidence.)"
            )
        else:
            notes.append(
                f"(Over inputs, a range is shown only with {min_inputs} or more"
                " paired inputs; with fewer, it comes out too narrow, so each"
                " input gets its own range instead.)"
            )
    return " ".join(
        [
            "A difference in pass rate, the arm's minus the baseline's, in"
            " percentage points (pp): +20 pp means the arm passed 20 more runs"
            " in every 100. Over several inputs it is the average of each"
            " input's difference, every input counting equally. The range"
            " after it is where the true difference probably falls; only"
            " inputs that ran in both arms count.",
            *notes,
        ]
    )


@glossary_entry("p", "p")
def _p_entry(keys: list[tuple]) -> str:
    notes = []
    for method, exact, resamples, seed in keys:
        if method == "fisher":
            note = "(One input: Fisher's exact test on the two arms' runs.)"
        elif exact:
            note = (
                "(Over inputs: a sign-flip test on the per-input differences,"
                " exact: it tries every way of swapping the arms within inputs."
                " With one run per arm it is McNemar's exact test.)"
            )
        else:
            note = (
                "(Over inputs: a sign-flip test on the per-input differences,"
                f" from {resamples:,} random ways of swapping the arms within"
                f" inputs, seed {seed}.)"
            )
        if note not in notes:
            notes.append(note)
    return " ".join(
        [
            "How easily luck alone could explain the gap: if both arms really"
            " passed equally often, the chance of a gap at least this big"
            " between them. Small p: luck is an unlikely explanation. Large p:"
            " the data can't tell the arms apart. It is not the chance that"
            " the arms are equal, and the verdict, when there is a margin,"
            " comes from the range, not from p.",
            *notes,
        ]
    )


@glossary_entry("margin", "Margin")
def _margin_entry(_: list) -> str:
    return (
        "≥−N pp (margin=, non-inferiority): PASS when the whole range is above"
        " −N points, so the arm is at most N points worse than the baseline;"
        " FAIL when the whole range is below it. ±N pp (equivalence=True):"
        " PASS when the whole range is between −N and +N points; FAIL when it"
        " is entirely outside them. Otherwise, or with no range yet,"
        " UNDECIDED."
    )


@glossary_entry("adjust", "adjusted")
def _adjust_entry(methods: list[str]) -> str:
    texts = []
    for method in methods:
        if method == "none":
            texts.append(
                "Exploratory: p-values not adjusted for making several"
                " comparisons. The more comparisons, the more likely some"
                " small p turns up by luck alone; --prob-adjust=holm,"
                " bonferroni or bh adjusts them."
            )
        elif method == "bh":
            texts.append(
                "Benjamini-Hochberg: p-values raised so that, among the"
                " comparisons with a small p, only a small share are expected"
                " to be luck."
            )
        else:
            text = (
                f"{_ADJUST_NAMES[method]}: p-values raised so that the chance"
                " of even one comparison's small p being luck stays small."
            )
            if method == "holm":
                text += " Holm is never stricter than Bonferroni."
            texts.append(text)
    return " ".join(texts)


def glossary(terms: Iterable[tuple[str, Hashable]]) -> list[tuple[str, str]]:
    """``(label, text)`` for every registered entry the terms use, in
    registration order; each entry sees its distinct details."""
    details: dict[str, list[Any]] = {}
    for key, detail in terms:
        seen = details.setdefault(key, [])
        if detail not in seen:
            seen.append(detail)
    return [
        (label, fn(details[key]))
        for key, (label, fn) in _GLOSSARY.items()
        if key in details
    ]


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------


def _wrap(text: str, width: int, indent: str, hang: str | None = None) -> list[str]:
    hang = indent if hang is None else hang
    return textwrap.wrap(
        text,
        width=max(width, len(hang) + _MIN_TEXT_WIDTH),
        initial_indent=indent,
        subsequent_indent=hang,
        break_long_words=False,
        break_on_hyphens=False,
    )


def render_section(
    readings: list[Reading],
    width: int,
    extra_terms: Iterable[tuple[str, Hashable]] = (),
    empty: str = "Every case passed all its runs; nothing needs a closer look.",
) -> list[tuple[str, str | None]]:
    """The explain section's lines (without its ``===`` title), each
    with the tone to color it by (``None``: plain).

    ``extra_terms`` are glossary terms the rest of the output uses
    without a reading — for example the interval column of the main
    table.
    """
    lines: list[tuple[str, str | None]] = []
    for reading in readings:
        lines.append((_HEAD_INDENT + reading.heading, reading.tone))
        for para in reading.paragraphs:
            lines.extend((ln, None) for ln in _wrap(para, width, _BODY_INDENT))
        lines.append(("", None))
    if not readings:
        lines.extend((ln, None) for ln in _wrap(empty, width, _HEAD_INDENT))
        lines.append(("", None))
    terms = [t for r in readings for t in r.terms] + list(extra_terms)
    entries = glossary(terms)
    if entries:
        lines.append((_HEAD_INDENT + "Methods used", None))
        label_w = max(len(label) for label, _ in entries)
        for label, text in entries:
            head = f"{_BODY_INDENT}{label:<{label_w}}  "
            hang = " " * len(head)
            if width - len(hang) < _MIN_TEXT_WIDTH:
                # Too narrow for a hanging column: label on its own line.
                lines.append((_BODY_INDENT + label, None))
                body = _wrap(text, width, _BODY_INDENT + "  ")
                lines.extend((ln, None) for ln in body)
            else:
                lines.extend((ln, None) for ln in _wrap(text, width, head, hang))
    # Never wrapped: a broken URL can't be clicked.
    lines.append((f"{_HEAD_INDENT}Full guide: {GUIDE_URL}", None))
    return lines
