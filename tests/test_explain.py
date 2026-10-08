"""Tests for --prob-explain: the plain-language templates and their
rendering.

The template tests are snapshots on purpose: the wording is the
product, so any change to it should show up as a reviewed diff here.
"""

import json
import math

import pytest

from pytest_probability import explain
from pytest_probability.plugin import (
    _SETTLE_CAP,
    UNDECIDED,
    CaseStats,
    Gate,
    StatsConfig,
)

# ---------------------------------------------------------------------------
# Template snapshots
# ---------------------------------------------------------------------------


def _gate(rule="rate", bar=0.9, need=None, errors="count", **stats):
    cfg = StatsConfig(**stats)
    if rule == "count":
        return Gate(rule="count", stats=cfg, min_passes=need, errors=errors)
    return Gate(rule="rate", stats=cfg, min_rate=bar, errors=errors)


def _read(gate, passes, fails=0, errors=0, undecided_fails=True):
    """A gate reading, built the way the aggregator builds one."""
    s = CaseStats("classify::x", passes, fails, errors, gate=gate)
    result = gate.evaluate(s)
    settle = None
    if result.verdict == UNDECIDED:
        settle = gate.runs_to_settle(result.passes, result.total, _SETTLE_CAP)
    return explain.gate_reading(
        result,
        errors=errors,
        undecided_fails=undecided_fails,
        settle=settle,
        cap=_SETTLE_CAP,
    )


UNDECIDED_FAILS = (
    "Until that's settled, the case fails the test session"
    " (--prob-undecided=pass would let it through)."
)
LOOK = (
    "look at the failing runs: -rx lists them with their assert messages,"
    " and --xfail-tb shows their tracebacks."
)
STRADDLES = (
    "Your bar is {}, and that range has values both above and below it, so"
    " there isn't enough data yet to tell whether this case meets the bar."
)


def test_rate_pass():
    r = _read(_gate(), 40)
    assert r.heading == "classify::x  40/40  [91%, 100%]  ≥90%  PASS"
    assert r.text() == (
        "40 of 40 runs passed. The true pass rate is probably between 91% and"
        " 100% (95% confidence). Your bar is 90%, and that whole range is"
        " above it, so this case meets the bar."
    )
    assert r.tone == "pass"
    assert r.terms == (("interval", ("exact", 0.95, None)), ("verdict", None))


def test_rate_undecided_above_the_bar():
    r = _read(_gate(), 37, 3)
    assert r.heading == "classify::x  37/40  [80%, 98%]  ≥90%  UNDECIDED"
    assert r.paragraphs == (
        "37 of 40 runs passed. The true pass rate is probably between 80% and"
        " 98% (95% confidence). " + STRADDLES.format("90%") + " " + UNDECIDED_FAILS,
        "Next: run more. If it keeps passing at today's rate (92.5%), about"
        " 570 runs in total would settle it.",
    )


def test_rate_undecided_below_the_bar():
    r = _read(_gate(), 33, 7)
    assert r.paragraphs[1] == (
        "Next: today's rate (82.5%) is below the bar; if it holds, about 78"
        " runs in total would confirm that it falls short. Meanwhile, " + LOOK
    )


def test_rate_undecided_too_close_to_settle():
    # 9/10 is exactly the bar: no number of runs would settle it
    r = _read(_gate(), 9, 1)
    assert r.paragraphs[1] == (
        "Next: today's rate (90%) is so close to the bar that even 10,000 runs"
        " might not settle it. Decide whether 90% is the right bar, and look at"
        " the failing runs (-rx) to see what goes wrong."
    )


def test_undecided_pass_policy():
    r = _read(_gate(), 37, 3, undecided_fails=False)
    assert r.paragraphs[0].endswith(
        STRADDLES.format("90%") + " This session lets undecided cases through"
        " (--prob-undecided=pass), so it doesn't fail the test session."
    )
    assert "Until that's settled" not in r.text()


def test_rate_fail():
    r = _read(_gate(), 3, 37)
    assert r.heading == "classify::x  3/40  [2%, 20%]  ≥90%  FAIL"
    assert r.paragraphs == (
        "3 of 40 runs passed. The true pass rate is probably between 2% and 20%"
        " (95% confidence). Your bar is 90%, and that whole range is below it,"
        " so this case falls short of the bar.",
        "Next: " + LOOK + " If a lower pass rate is acceptable for this case,"
        " lower the bar.",
    )


def test_rate_fail_with_counted_errors():
    r = _read(_gate(), 3, 30, 7)
    assert r.paragraphs == (
        "3 of 40 runs passed. 7 of the runs that didn't pass errored (raised an"
        " exception other than a failed assert); they count as non-passes. The"
        " true pass rate is probably between 2% and 20% (95% confidence). Your"
        " bar is 90%, and that whole range is below it, so this case falls"
        " short of the bar.",
        "Next: " + LOOK + " Fix what raised the errors too. If a lower pass"
        " rate is acceptable for this case, lower the bar.",
    )


def test_count_rule_pass():
    r = _read(_gate("count", need=19), 19, 1)
    assert r.heading == "classify::x  19/20  [75%, 99%]  ≥19 passes  PASS"
    assert r.text() == (
        "19 of 20 runs passed. This gate counts passes and needs at least 19."
        " That's enough, so this case meets the bar. (The range [75%, 99%]"
        " beside it is for information only: this gate judges the count.)"
    )
    assert ("count", None) in r.terms and ("verdict", None) not in r.terms


def test_count_rule_fail():
    r = _read(_gate("count", need=19), 17, 3)
    assert r.paragraphs == (
        "17 of 20 runs passed. This gate counts passes and needs at least 19."
        " It is 2 short, so this case falls short of the bar. (The range"
        " [62%, 97%] beside it is for information only: this gate judges the"
        " count.)",
        "Next: " + LOOK,
    )


def test_count_rule_too_few_runs():
    r = _read(_gate("count", need=19), 9, 1)
    assert r.paragraphs == (
        "9 of 10 runs passed. This gate counts passes and needs at least 19."
        " Only 10 runs counted, so it couldn't reach 19 passes however they"
        " went. (The range [55%, 99%] beside it is for information only: this"
        " gate judges the count.)",
        "Next: run it at least 19 times (runs=19 on the mark, or --prob-runs).",
    )


def test_errors_excluded():
    r = _read(_gate(bar=0.5, errors="exclude"), 7, 1, 4)
    assert r.heading == (
        "classify::x  7/8  [47%, 99%]  ≥50%  UNDECIDED  (4 errored, excluded)"
    )
    assert r.paragraphs == (
        "7 of 8 runs passed. 4 more errored (raised an exception other than a"
        " failed assert) and were left out of this gate (prob_errors ="
        " exclude). The true pass rate is probably between 47% and 99% (95%"
        " confidence). " + STRADDLES.format("50%") + " " + UNDECIDED_FAILS,
        "Next: run more. If it keeps passing at today's rate (87.5%), about 9"
        " runs in total, not counting errored ones, would settle it.",
    )
    assert ("excluded", None) in r.terms


def test_every_run_excluded():
    r = _read(_gate(bar=0.5, errors="exclude"), 0, 0, 12)
    assert r.heading == "classify::x  0/0  ≥50%  UNDECIDED  (12 errored, excluded)"
    assert r.paragraphs == (
        "All 12 runs errored (raised an exception other than a failed assert),"
        " and this gate leaves errored runs out (prob_errors = exclude), so"
        " there are no runs to judge it on: the verdict is UNDECIDED. "
        + UNDECIDED_FAILS,
        "Next: fix what raised the errors (-rx lists each one with its"
        " exception), then run again.",
    )
    # no interval, so no interval entry in the glossary
    assert r.terms == (("verdict", None), ("excluded", None))


def test_bayes_gate_says_probability():
    r = _read(_gate(method="bayes", prior=(0.5, 0.5)), 40)
    assert "(95% probability)" in r.text()
    assert r.terms[0] == ("interval", ("bayes", 0.95, (0.5, 0.5)))


def _row(passes, fails=0, errors=0, gated=False, cfg=StatsConfig()):
    s = CaseStats("c", passes, fails, errors)
    return explain.row_reading(s, cfg.interval(s.passes, s.total), cfg, gated)


ROW_TEMPLATES = {
    "pass": (
        (10,),
        "c  10/10  [69%, 100%]",
        "10 of 10 runs passed. The true pass rate is probably between 69% and"
        " 100% (95% confidence).",
    ),
    "flaky": (
        (7, 3),
        "c  7/10  [35%, 93%]  FLAKY",
        "7 of 10 runs passed; the others failed, so the outcome changes from"
        " run to run. The true pass rate is probably between 35% and 93% (95%"
        " confidence).\n"
        "Next: look at the failures above to see what differs between runs. To"
        " hold the case to a pass rate instead of every run, gate it:"
        " @pytest.mark.probability(min_rate=...).",
    ),
    "flaky_with_errors": (
        (6, 2, 2),
        "c  6/10  [26%, 88%]  FLAKY",
        "6 of 10 runs passed; the others failed, so the outcome changes from"
        " run to run. 2 of the runs that didn't pass errored (raised an"
        " exception other than a failed assert). The true pass rate is probably"
        " between 26% and 88% (95% confidence). Errored runs count as"
        " non-passes in that range.\n"
        "Next: look at the failures above to see what differs between runs. To"
        " hold the case to a pass rate instead of every run, gate it:"
        " @pytest.mark.probability(min_rate=...).",
    ),
    "fail": (
        (0, 10),
        "c  0/10  [0%, 31%]  FAIL",
        "None of the 10 runs passed. The true pass rate is probably between 0%"
        " and 31% (95% confidence).\n"
        "Next: look at the failures above.",
    ),
    "errored": (
        (8, 0, 2),
        "c  8/10  [44%, 97%]  ERRORED",
        "8 of 10 runs passed; the others errored (raised an exception other"
        " than a failed assert), which usually means the harness or a service"
        " broke, not that the code answered wrong. The true pass rate is"
        " probably between 44% and 97% (95% confidence). Errored runs count as"
        " non-passes in that range.\n"
        "Next: fix what raised the errors (see the tracebacks above), then run"
        " again.",
    ),
    "error": (
        (0, 0, 10),
        "c  0/10  [0%, 31%]  ERROR",
        "All 10 runs errored (raised an exception other than a failed assert),"
        " which usually means the harness or a service broke, not that the"
        " code answered wrong.\n"
        "Next: fix what raised the errors (see the tracebacks above), then run"
        " again.",
    ),
    "single_run": (
        (0, 1),
        "c  0/1  FAIL",
        "The one run failed. A single run says little about how often this"
        " case passes.\n"
        "Next: run it more times (for example --prob-runs=10) to see a pass"
        " rate.",
    ),
}


@pytest.mark.parametrize("name", ROW_TEMPLATES)
def test_row_templates(name):
    counts, heading, text = ROW_TEMPLATES[name]
    r = _row(*counts)
    assert (r.heading, r.text()) == (heading, text)


def test_gated_row_has_no_next_step():
    # the gate's own reading carries the next step
    r = _row(7, 3, gated=True)
    assert len(r.paragraphs) == 1
    assert "Next:" not in r.text()


def test_row_terms():
    assert _row(7, 3).terms == (("interval", ("exact", 0.95, None)),)
    # no range is described for all-error or single-run rows
    assert _row(0, 0, 10).terms == ()
    assert _row(1).terms == ()


@pytest.mark.parametrize(
    "p, text", [(0.925, "92.5%"), (0.9, "90%"), (0.875, "87.5%"), (1.0, "100%")]
)
def test_rate_format(p, text):
    assert explain.rate(p) == text


@pytest.mark.parametrize(
    "n, text", [(9, "9"), (99, "99"), (100, "100"), (537, "540"), (1012, "1,100")]
)
def test_about_rounds_up_to_two_figures(n, text):
    assert explain.about(n) == text


# ---------------------------------------------------------------------------
# Glossary
# ---------------------------------------------------------------------------


def _entries(*terms):
    return dict(explain.glossary(terms))


INTERVAL = (
    "The range the true pass rate probably falls in, given the runs so far."
    " More runs make it narrower."
)


@pytest.mark.parametrize(
    "key, note",
    [
        (
            ("exact", 0.95, None),
            "(Clopper-Pearson, 95% confidence: errs on the side of a wider"
            " range.)",
        ),
        (
            ("wilson", 0.9, None),
            "(Wilson score, 90% confidence: a close approximation, slightly"
            " narrower.)",
        ),
        (
            ("bayes", 0.95, (1.0, 1.0)),
            "(Bayesian, 95% probability, from a Beta(1, 1) prior, which means"
            " no starting preference.)",
        ),
        (
            ("bayes", 0.975, (0.5, 0.5)),
            "(Bayesian, 97.5% probability, from a Beta(0.5, 0.5) prior: a"
            " starting belief worth roughly 0.5 passes and 0.5 fails.)",
        ),
    ],
)
def test_glossary_names_the_method_used(key, note):
    assert _entries(("interval", key)) == {"[low, high]": f"{INTERVAL} {note}"}


def test_glossary_lists_each_method_once_in_first_seen_order():
    text = _entries(
        ("interval", ("wilson", 0.95, None)),
        ("interval", ("exact", 0.95, None)),
        ("interval", ("wilson", 0.95, None)),
    )["[low, high]"]
    assert text.count("Wilson") == 1
    assert text.index("Wilson") < text.index("Clopper-Pearson")


def test_glossary_only_lists_terms_used():
    assert explain.glossary([]) == []
    assert [label for label, _ in explain.glossary([("count", None)])] == [
        "≥N passes"
    ]
    # registration order, whatever order the terms came in
    labels = [
        label
        for label, _ in explain.glossary(
            [
                ("excluded", None),
                ("verdict", None),
                ("interval", ("exact", 0.95, None)),
                ("count", None),
            ]
        )
    ]
    assert labels == ["[low, high]", "Verdict", "≥N passes", "excluded"]


def test_glossary_entries_text():
    entries = _entries(("verdict", None), ("count", None), ("excluded", None))
    assert entries == {
        "Verdict": "PASS: the whole range is above the bar. FAIL: the whole"
        " range is below it. UNDECIDED: the range crosses the bar, so more"
        " runs are needed.",
        "≥N passes": "A count bar (min_passes): PASS when at least N runs"
        " passed, FAIL otherwise. Any range beside it is for information"
        " only.",
        "excluded": "Runs that errored (raised an exception other than a"
        " failed assert). Under prob_errors = exclude a gate leaves them out,"
        " so they count neither for nor against the case.",
    }


def test_glossary_registry_is_extensible():
    # how later features add their own entries
    @explain.glossary_entry("_test_term", "p")
    def _p(details):
        return f"p entry {details}"

    try:
        assert explain.glossary([("_test_term", 1), ("_test_term", 2)]) == [
            ("p", "p entry [1, 2]")
        ]
    finally:
        del explain._GLOSSARY["_test_term"]


# ---------------------------------------------------------------------------
# The settle estimate
# ---------------------------------------------------------------------------


def _settle_band(gate, passes, total, cap):
    """The first n that settles, and the first from which every n does.

    Rounding p·n makes the verdict flicker in between, so any n in that
    band is a fair answer.
    """
    rate = passes / total
    settled = [
        gate.verdict(math.floor(rate * n + 0.5), n) != UNDECIDED
        for n in range(total, cap + 1)
    ]
    first = settled.index(True) + total
    last_unsettled = max(i for i, v in enumerate(settled) if not v) + total
    return first, last_unsettled + 1


@pytest.mark.parametrize(
    "gate, passes, total",
    [
        (_gate(), 37, 40),
        (_gate(), 33, 40),
        (_gate(bar=0.5, method="wilson"), 7, 12),
        (_gate(bar=0.8, method="bayes", prior=(0.5, 0.5)), 15, 20),
    ],
)
def test_runs_to_settle_matches_a_linear_search(gate, passes, total):
    passes = min(passes, total)
    got = gate.runs_to_settle(passes, total, 3000)
    first, stays = _settle_band(gate, passes, total, 3000)
    assert first <= got <= stays
    assert gate.verdict(math.floor(passes / total * got + 0.5), got) != UNDECIDED


def test_runs_to_settle_all_pass_is_min_runs():
    gate = _gate()
    assert gate.runs_to_settle(30, 30, 3000) == gate.min_runs()


def test_runs_to_settle_none_cases():
    assert _gate().runs_to_settle(9, 10, 10_000) is None  # exactly at the bar
    assert _gate().runs_to_settle(0, 0, 10_000) is None  # nothing to go on
    assert _gate("count", need=19).runs_to_settle(9, 10, 10_000) is None
    assert _gate().runs_to_settle(91, 100, 500) is None  # beyond the cap


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------


def test_render_wraps_to_width():
    readings = [_read(_gate(), 37, 3), _row(7, 3)]
    for width in (50, 60, 80, 120):
        lines = [ln for ln, _ in explain.render_section(readings, width)]
        for ln in lines:
            if ln.startswith("  Full guide:") or ln in (
                "  " + r.heading for r in readings
            ):
                continue  # headings and the URL are never wrapped
            assert len(ln) <= width, (width, ln)
        # bodies hang under their heading, glossary text under its column
        body = lines[lines.index("  " + readings[0].heading) + 1]
        assert body.startswith("    37 of 40")
        glossary_at = lines.index("  Methods used")
        assert lines[glossary_at + 1].startswith("    [low, high]  The range")
        assert lines[glossary_at + 2].startswith(" " * 17)


def test_render_very_narrow_puts_glossary_labels_on_their_own_line():
    lines = [ln for ln, _ in explain.render_section([_read(_gate(), 37, 3)], 40)]
    at = lines.index("  Methods used")
    assert lines[at + 1] == "    [low, high]"
    assert lines[at + 2].startswith("      The range")


def test_render_tones():
    rendered = explain.render_section([_read(_gate(), 3, 37)], 80)
    assert rendered[0] == ("  classify::x  3/40  [2%, 20%]  ≥90%  FAIL", "fail")
    assert all(tone is None for _, tone in rendered[1:])


def test_render_nothing_notable():
    lines = [
        ln
        for ln, _ in explain.render_section(
            [], 80, [("interval", ("exact", 0.95, None))]
        )
    ]
    assert lines[0] == (
        "  Every case passed all its runs; nothing needs a closer look."
    )
    assert "  Methods used" in lines
    assert lines[-1] == f"  Full guide: {explain.GUIDE_URL}"


# ---------------------------------------------------------------------------
# End to end
# ---------------------------------------------------------------------------

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

def bench_plain():
    _calls["p"] = _calls.get("p", 0) + 1
    assert _calls["p"] % 4
"""

BENCH_ALL_PASS = """
import pytest

@pytest.mark.probability(min_rate=0.5)
def bench_fine():
    assert True

def bench_ungated():
    assert True
"""

BENCH_FLAKY = """
_calls = {"n": 0}

def bench_wobbly():
    _calls["n"] += 1
    assert _calls["n"] % 2 == 0
"""

HINT = "  " + explain.HINT


@pytest.fixture
def columns(monkeypatch):
    def set_width(n):
        monkeypatch.setenv("COLUMNS", str(n))

    set_width(80)
    return set_width


def _run(pytester, *args):
    return pytester.runpytest("--tb=no", "-p", "no:cacheprovider", *args)


def _section(result, title="probability: explained"):
    """The lines of a ``probability: ...`` section, up to the next one."""
    lines = result.stdout.lines
    start = next(i for i, ln in enumerate(lines) if f"= {title} =" in ln)
    out = []
    for ln in lines[start + 1 :]:
        if ln.startswith("="):
            break
        out.append(ln)
    return out


EXPLAINED = [
    "  classify::solid  40/40  [91%, 100%]  ≥90%  PASS",
    "    40 of 40 runs passed. The true pass rate is probably between 91% and 100%",
    "    (95% confidence). Your bar is 90%, and that whole range is above it, so this",
    "    case meets the bar.",
    "",
    "  classify::close  37/40  [80%, 98%]  ≥90%  UNDECIDED",
    "    37 of 40 runs passed. The true pass rate is probably between 80% and 98%",
    "    (95% confidence). Your bar is 90%, and that range has values both above and",
    "    below it, so there isn't enough data yet to tell whether this case meets the",
    "    bar. Until that's settled, the case fails the test session",
    "    (--prob-undecided=pass would let it through).",
    "    Next: run more. If it keeps passing at today's rate (92.5%), about 570 runs",
    "    in total would settle it.",
    "",
    "  classify::weak  3/40  [2%, 20%]  ≥90%  FAIL",
    "    3 of 40 runs passed. The true pass rate is probably between 2% and 20% (95%",
    "    confidence). Your bar is 90%, and that whole range is below it, so this case",
    "    falls short of the bar.",
    "    Next: look at the failing runs: -rx lists them with their assert messages,",
    "    and --xfail-tb shows their tracebacks. If a lower pass rate is acceptable",
    "    for this case, lower the bar.",
    "",
    "  plain  30/40  [59%, 87%]  FLAKY",
    "    30 of 40 runs passed; the others failed, so the outcome changes from run to",
    "    run. The true pass rate is probably between 59% and 87% (95% confidence).",
    "    Next: look at the failures above to see what differs between runs. To hold",
    "    the case to a pass rate instead of every run, gate it:",
    "    @pytest.mark.probability(min_rate=...).",
    "",
    "  Methods used",
    "    [low, high]  The range the true pass rate probably falls in, given the runs",
    "                 so far. More runs make it narrower. (Clopper-Pearson, 95%",
    "                 confidence: errs on the side of a wider range.)",
    "    Verdict      PASS: the whole range is above the bar. FAIL: the whole range",
    "                 is below it. UNDECIDED: the range crosses the bar, so more runs",
    "                 are needed.",
    f"  Full guide: {explain.GUIDE_URL}",
]


def test_explained_section(pytester, columns):
    pytester.makepyfile(bench_g=BENCH_GATED)
    result = _run(pytester, "--prob-runs=40", "--prob-explain")
    assert _section(result) == EXPLAINED
    # the section replaces the hint, and comes after the gates block
    assert HINT not in result.stdout.lines
    out = result.stdout.str()
    assert out.index("probability: gates") < out.index("probability: explained")


def test_ini_turns_it_on(pytester, columns):
    pytester.makepyfile(bench_g=BENCH_GATED)
    pytester.makeini("[pytest]\nprob_explain = true\n")
    result = _run(pytester, "--prob-runs=40")
    assert _section(result) == EXPLAINED


def test_section_wraps_to_terminal_width(pytester, columns):
    columns(52)
    pytester.makepyfile(bench_g=BENCH_GATED)
    result = _run(pytester, "--prob-runs=40", "--prob-explain")
    lines = _section(result)
    headings = {ln for ln in lines if ln.startswith("  ") and "  ≥" in ln}
    for ln in lines:
        if ln in headings or ln.startswith("  Full guide:"):
            continue
        assert len(ln) <= 52, ln
    assert "    37 of 40 runs passed. The true pass rate is" in lines


def test_errors_excluded_and_count_rule_end_to_end(pytester, columns):
    pytester.makeini("[pytest]\nprob_errors = exclude\n")
    pytester.makepyfile(
        bench_e="""
import pytest

_calls = {"n": 0, "c": 0}

@pytest.mark.probability(min_rate=0.5)
def bench_down():
    raise ConnectionError("API timeout")

@pytest.mark.probability(min_passes=19)
def bench_counted():
    _calls["c"] += 1
    assert _calls["c"] <= 17
"""
    )
    result = _run(pytester, "--prob-runs=20", "--prob-explain")
    lines = _section(result)
    assert "  down  0/0  ≥50%  UNDECIDED  (20 errored, excluded)" in lines
    assert "  counted  17/20  [62%, 97%]  ≥19 passes  FAIL" in lines
    glossary = lines[lines.index("  Methods used") + 1 :]
    labels = [
        ln[4:].split("  ")[0]
        for ln in glossary
        if ln.startswith("    ") and ln[4] != " "
    ]
    assert labels == ["[low, high]", "Verdict", "≥N passes", "excluded"]


def test_nothing_notable(pytester, columns):
    pytester.makepyfile(bench_ok=BENCH_ALL_PASS)
    result = _run(pytester, "--prob-runs=10", "--prob-explain")
    lines = _section(result)
    # the passing gate is still read; the all-pass ungated row is not
    assert lines[0] == "  fine  10/10  [69%, 100%]  ≥50%  PASS"
    assert not any(ln.startswith("  ungated") for ln in lines)
    assert result.ret == pytest.ExitCode.OK


def test_ungated_suite_explains_the_interval_column(pytester, columns):
    pytester.makepyfile(bench_f=BENCH_FLAKY)
    result = _run(pytester, "--prob-runs=10", "--prob-explain", "--prob-method=wilson")
    lines = _section(result)
    assert lines[0] == "  wobbly  5/10  [24%, 76%]  FLAKY"
    assert "(Wilson score, 95% confidence" in " ".join(ln.strip() for ln in lines)
    assert not any("Verdict" in ln for ln in lines)


@pytest.mark.parametrize(
    "source, args, shown",
    [
        (BENCH_GATED, ["--prob-runs=40"], True),  # FAIL and UNDECIDED
        (BENCH_GATED.replace("min_rate=0.9", "min_rate=0.6").replace(
            '"weak", 3', '"weak", 40'
        ), ["--prob-runs=40"], False),  # every gate passes
        (BENCH_FLAKY, ["--prob-runs=10"], False),  # no gates
        (BENCH_GATED, ["--prob-runs=40", "--prob-explain"], False),  # flag on
        (
            BENCH_GATED.replace(
                '"close", 37', '"close", 40'
            ).replace('"weak", 3', '"weak", 34'),
            ["--prob-runs=36", "-W", "ignore::pytest.PytestWarning"],
            True,  # UNDECIDED only
        ),
    ],
)
def test_hint(pytester, columns, source, args, shown):
    pytester.makepyfile(bench_g=source)
    result = _run(pytester, *args)
    if shown:
        # the last line of the plugin's output, after a blank line
        lines = result.stdout.lines
        at = lines.index(HINT)
        assert lines[at - 1] == ""
        assert lines[at + 1].startswith("=")
    else:
        assert HINT not in result.stdout.lines


def test_hint_with_undecided_allowed(pytester, columns):
    # an UNDECIDED that doesn't fail the session still deserves a reading
    pytester.makepyfile(bench_g=BENCH_GATED.replace('"weak", 3', '"weak", 40'))
    result = _run(pytester, "--prob-runs=40", "--prob-undecided=pass")
    assert result.ret == pytest.ExitCode.TESTS_FAILED  # bench_plain fails
    assert HINT in result.stdout.lines


def test_json_explanations(pytester, columns):
    pytester.makepyfile(bench_g=BENCH_GATED)
    _run(pytester, "--prob-runs=40", "--prob-explain", "--prob-json=r.json")
    data = json.loads((pytester.path / "r.json").read_text())
    rows = {r["case"]: r for r in data["rows"]}
    close = rows["classify::close"]
    assert close["gate"]["explanation"] == (
        "37 of 40 runs passed. The true pass rate is probably between 80% and"
        " 98% (95% confidence). " + STRADDLES.format("90%") + " " + UNDECIDED_FAILS
        + "\nNext: run more. If it keeps passing at today's rate (92.5%), about"
        " 570 runs in total would settle it."
    )
    # the row's own reading: every run, the session's interval, no next step
    assert close["explanation"] == (
        "37 of 40 runs passed; the others failed, so the outcome changes from"
        " run to run. The true pass rate is probably between 80% and 98% (95%"
        " confidence)."
    )
    assert rows["plain"]["gate"] is None
    assert rows["plain"]["explanation"].endswith(
        "@pytest.mark.probability(min_rate=...)."
    )
    # every row has one, passing rows included
    assert all(isinstance(r["explanation"], str) for r in rows.values())


def test_flag_off_changes_nothing(pytester, columns):
    pytester.makepyfile(bench_g=BENCH_GATED)
    off = _run(pytester, "--prob-runs=40", "--prob-json=off.json")
    ini_off = _run(
        pytester, "--prob-runs=40", "--prob-json=ini.json", "-o", "prob_explain=false"
    )
    strip = lambda r: [  # noqa: E731
        ln for ln in r.stdout.lines if " in " not in ln and "Report:" not in ln
    ]
    assert strip(off) == strip(ini_off)
    assert "probability: explained" not in off.stdout.str()
    data = json.loads((pytester.path / "off.json").read_text())
    assert not any("explanation" in r for r in data["rows"])
    assert not any("explanation" in (r["gate"] or {}) for r in data["rows"])


def test_ungated_output_unchanged_without_flag(pytester, columns):
    # no gate, nothing to hint at: byte-for-byte the pre-explain output
    pytester.makepyfile(bench_f=BENCH_FLAKY)
    result = _run(pytester, "--prob-runs=10")
    assert "--prob-explain" not in result.stdout.str()


def test_regular_suite_unchanged(pytester, columns):
    pytester.makepyfile(test_plain="def test_ok():\n    assert True\n")
    base = _run(pytester)
    explained = _run(pytester, "--prob-explain")
    strip = lambda r: [ln for ln in r.stdout.lines if " in " not in ln]  # noqa: E731
    assert strip(base) == strip(explained)
    assert "= probability" not in explained.stdout.str()


def _blocks(lines):
    """Split a section into blank-line-separated blocks."""
    blocks, current = [], []
    for ln in lines:
        if ln:
            current.append(ln)
        elif current:
            blocks.append(tuple(current))
            current = []
    if current:
        blocks.append(tuple(current))
    return blocks


def test_xdist_parity(pytester, columns):
    pytest.importorskip("xdist")
    pytester.makeini("[pytest]\nprob_errors = exclude\n")
    pytester.makepyfile(
        bench_g=BENCH_GATED,
        bench_f=BENCH_FLAKY,
        bench_e="""
import pytest

_calls = {"n": 0}

@pytest.mark.probability(min_rate=0.5)
def bench_shaky():
    _calls["n"] += 1
    if _calls["n"] % 3 == 0:
        raise ConnectionError("API timeout")
    assert _calls["n"] % 4
""",
    )
    args = ("--prob-runs=40", "--prob-explain")
    serial = _run(pytester, *args, "--prob-json=serial.json")
    dist = _run(
        pytester, *args, "--prob-json=dist.json", "-n", "4", "--dist", "loadfile"
    )
    # row order follows result arrival, so compare readings as a set;
    # the glossary (the last block) must match exactly
    s_blocks, d_blocks = _blocks(_section(serial)), _blocks(_section(dist))
    assert sorted(s_blocks[:-1]) == sorted(d_blocks[:-1])
    assert s_blocks[-1] == d_blocks[-1]
    key = lambda r: r["case"]  # noqa: E731
    s = sorted(json.loads((pytester.path / "serial.json").read_text())["rows"], key=key)
    d = sorted(json.loads((pytester.path / "dist.json").read_text())["rows"], key=key)
    assert [(r["explanation"], (r["gate"] or {}).get("explanation")) for r in s] == [
        (r["explanation"], (r["gate"] or {}).get("explanation")) for r in d
    ]
