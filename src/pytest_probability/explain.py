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
  line: ``gate_reading``, ``row_reading`` and ``aggregate_reading``
  (which also reads ρ and the runs-vs-inputs projection) today; later
  features add their own (``comparison_reading``, …) next to them.
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

import textwrap
from dataclasses import dataclass
from typing import Any, Callable, Hashable, Iterable

from .plugin import FAIL, PASS, UNDECIDED, _change, _pct, _pct1, _pct_bar

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
