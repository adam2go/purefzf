"""The bulk regex prefilter must be invisible: run_filter output with the
prefilter enabled has to be identical to the output with it disabled, for
any corpus and query."""

import random
import string

import pytest

import purefzf
from purefzf import core

WORDS = ["alpha", "Beta", "GAMMA", "delta_one", "epsilon/two", "Zeta.three",
         "Danço", "café", "naïve", "test", "  padded  ", "", "UPPER lower",
         "foo bar baz", "a|b", "x'y", "z$w", "^caret", "!bang", "日本語"]

QUERIES = ["a", "al", "be", "ta", "'test", "^al", "one$", "!bar", "a b",
           "^al | ^be", "^Al | ^be", "Beta | GAMMA", "danco", "Danço",
           "cafe", "ÇÃ", "z", "qqq", "'", "  ", "a\\ b", "ä"]


def random_corpus(rng, n):
    out = []
    for _ in range(n):
        kind = rng.random()
        if kind < 0.6:
            out.append(rng.choice(WORDS))
        elif kind < 0.9:
            out.append("".join(rng.choice(string.ascii_letters +
                                          string.digits + " /._-")
                               for _ in range(rng.randint(0, 30))))
        else:
            out.append(rng.choice(WORDS) + rng.choice(WORDS))
    return out


@pytest.mark.parametrize("seed", range(10))
def test_prefilter_equivalence(seed, monkeypatch):
    monkeypatch.setattr(core, "_MIN_PREFILTER_LINES", 0)
    rng = random.Random(seed)
    corpus = random_corpus(rng, 400)
    for query in QUERIES:
        for opts in ({}, {"fuzzy": False}, {"extended": False},
                     {"case": "respect"}, {"tac": True},
                     {"sort": False}, {"normalize": False}):
            with_pf = purefzf.filter(query, corpus, **opts)
            sub = pytest.MonkeyPatch()
            sub.setattr(core, "_bulk_candidates",
                        lambda lines, pattern: None)
            without_pf = purefzf.filter(query, corpus, **opts)
            sub.undo()
            assert with_pf == without_pf, (
                "query=%r opts=%r seed=%d" % (query, opts, seed))


def test_prefilter_skips_embedded_newlines(monkeypatch):
    # --read0 records may contain newlines; the prefilter must bail out
    monkeypatch.setattr(core, "_MIN_PREFILTER_LINES", 0)
    lines = ["first\nsecond", "third b"]
    assert purefzf.filter("second", lines) == ["first\nsecond"]
    assert purefzf.filter("first b", lines) == []


def test_prefilter_non_ascii_lines_survive(monkeypatch):
    # ASCII query that only matches through normalization of accented text;
    # equal scores keep input order (verified against fzf)
    monkeypatch.setattr(core, "_MIN_PREFILTER_LINES", 0)
    lines = ["Dança", "danca", "nothing"]
    assert purefzf.filter("danca", lines) == ["Dança", "danca"]


def test_prefilter_linear_on_degenerate_lines():
    # A 200KB single-character line must not blow up the prefilter scan
    # (the canary hangs for minutes if the regex backtracks quadratically)
    import time
    lines = ["a" * 200000] + ["filler %d" % i for i in range(8000)]
    t0 = time.perf_counter()
    out = purefzf.filter("ab", lines)
    elapsed = time.perf_counter() - t0
    assert out == []
    assert elapsed < 5.0


def test_prefilter_anchored_chain_semantics(monkeypatch):
    monkeypatch.setattr(core, "_MIN_PREFILTER_LINES", 0)
    # Greedy chain must not miss subsequences that need a later first char
    lines = ["xaxxb", "ab", "ba", "axb", "b a"]
    assert purefzf.filter("ab", lines) == ["ab", "axb", "xaxxb"]
