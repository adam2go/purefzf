"""Index (session API) correctness: for any sequence of queries, every
Index result must equal the corresponding one-shot purefzf.filter result.
The narrowing cache must be semantically invisible."""

import random
import string

import pytest

import purefzf
from purefzf import core
from purefzf.index import Index

WORDS = ["alpha", "Beta", "GAMMA", "delta_one", "epsilon/two", "Zeta.three",
         "Danço", "café", "naïve", "test", "  padded  ", "", "UPPER lower",
         "foo bar baz", "a|b", "x'y", "z$w", "zshell", "zsh cache",
         "abc", "aBc", "ABC", "japanese 日本語"]


def random_corpus(rng, n):
    out = []
    for _ in range(n):
        kind = rng.random()
        if kind < 0.5:
            out.append(rng.choice(WORDS))
        elif kind < 0.9:
            out.append("".join(rng.choice(string.ascii_letters +
                                          string.digits + " /._-")
                               for _ in range(rng.randint(0, 25))))
        else:
            out.append(rng.choice(WORDS) + " " + rng.choice(WORDS))
    return out


def typing_session(rng):
    """A realistic query session: incremental typing with edits, term
    additions, operator flips, and non-cacheable shapes mixed in."""
    queries = []
    base = rng.choice(["a", "z", "t", "d", "'a", "^a", "b"])
    queries.append(base)
    cur = base
    for _ in range(rng.randint(4, 10)):
        action = rng.random()
        if action < 0.45:  # extend the query
            cur += rng.choice("abcdeszh1B ç")
        elif action < 0.6:  # backspace
            cur = cur[:-1] if cur else rng.choice("az")
        elif action < 0.7:  # add an AND term
            cur += " " + rng.choice(["b", "'c", "^d", "e$", "!f"])
        elif action < 0.8:  # operator flip / non-cacheable shapes
            cur = rng.choice(["'", "^"]) + cur.lstrip("'^!")
        elif action < 0.9:  # OR group
            cur += " | " + rng.choice(["zz", "^ab", "qq$"])
        else:  # suffix anchor (never cacheable)
            cur = cur.rstrip("$") + "$"
        queries.append(cur)
    return queries


@pytest.mark.parametrize("seed", range(12))
def test_session_equivalence(seed):
    rng = random.Random(seed)
    corpus = random_corpus(rng, 300)
    idx = Index(corpus)
    for opts in ({}, {"tac": True}, {"sort": False}, {"case": "respect"},
                 {"fuzzy": False}, {"tiebreak": "end"}):
        for _ in range(3):
            for query in typing_session(rng):
                got = idx.filter(query, **opts)
                want = purefzf.filter(query, corpus, **opts)
                assert got == want, (
                    "seed=%d query=%r opts=%r" % (seed, query, opts))


@pytest.mark.parametrize("seed", range(4))
def test_session_equivalence_with_prefilter(seed, monkeypatch):
    # Force both the bulk prefilter and the cache to engage on a small
    # corpus so all paths interact
    monkeypatch.setattr(core, "_MIN_PREFILTER_LINES", 0)
    rng = random.Random(1000 + seed)
    corpus = random_corpus(rng, 500)
    idx = Index(corpus)
    for _ in range(2):
        for query in typing_session(rng):
            got = idx.filter(query)
            want = purefzf.filter(query, corpus)
            assert got == want, "seed=%d query=%r" % (seed, query)


def test_incremental_typing_narrowing():
    corpus = ["zsh cache", "zshell", "zoo show", "nothing", "Zsh Plugin"]
    idx = Index(corpus)
    for q in ("z", "zs", "zsh", "zsh c", "zsh cache"):
        assert idx.filter(q) == purefzf.filter(q, corpus), q
    # backspace back down
    for q in ("zsh", "zs", "z"):
        assert idx.filter(q) == purefzf.filter(q, corpus), q


def test_case_flip_narrowing():
    corpus = ["aBc", "abc", "ABC", "a_b_c", "xx"]
    idx = Index(corpus)
    for q in ("a", "ab", "aB", "aBc"):  # smart-case turns sensitive at 'aB'
        assert idx.filter(q) == purefzf.filter(q, corpus), q


def test_type_flips_are_safe():
    corpus = ["foo bar", "foobar", "barfoo", "ofo", "xbarx"]
    idx = Index(corpus)
    # 'foo (exact) -> 'foo' (boundary) changes semantics; ^foo -> ^foo$
    # (equal) changes semantics; both must bypass/refresh correctly
    for q in ("'fo", "'foo", "'foo'", "^foo", "^foo$", "foo", "foo$"):
        assert idx.filter(q) == purefzf.filter(q, corpus), q


def test_unicode_normalization_session():
    corpus = ["Dança", "danca", "Danco", "dxnxa", "plain"]
    idx = Index(corpus)
    for q in ("d", "da", "dan", "danc", "danco", "dança"):
        assert idx.filter(q) == purefzf.filter(q, corpus), q


def test_nth_and_inverse_bypass_cache():
    corpus = ["a:x", "b:y", "c:x", "ax:q"]
    idx = Index(corpus)
    for q, opts in [("x", {"nth": "2", "delimiter": ":"}),
                    ("x", {}),
                    ("!x", {}),
                    ("x | y", {})]:
        assert idx.filter(q, **opts) == purefzf.filter(q, corpus, **opts), q


def test_options_partition_cache():
    corpus = ["Ab", "ab", "AB", "aub"]
    idx = Index(corpus)
    # Same query text under different semantic options must not share
    # cached match sets
    for opts in ({}, {"case": "respect"}, {"case": "ignore"},
                 {"normalize": False}, {"fuzzy": False}, {"algo": "v1"}):
        for q in ("a", "ab", "AB"):
            assert idx.filter(q, **opts) == \
                purefzf.filter(q, corpus, **opts), (q, opts)


def test_snapshot_semantics():
    src = ["one", "two"]
    idx = Index(src)
    src.append("three")
    assert idx.filter("") == ["one", "two"]
    assert len(idx) == 2


def test_cache_disabled_equivalence():
    rng = random.Random(7)
    corpus = random_corpus(rng, 200)
    idx = Index(corpus, cache=False)
    for q in ("a", "ab", "abc", "'ab", "^a", "a$"):
        assert idx.filter(q) == purefzf.filter(q, corpus), q
    assert idx._buckets == {}


def test_cache_eviction_bounds():
    from purefzf import index as index_mod
    corpus = ["line %d with stuff" % i for i in range(100)]
    idx = Index(corpus)
    for i in range(200):
        idx.filter("li%d" % (i % 50))
    for bucket in idx._buckets.values():
        assert len(bucket) <= index_mod._MAX_BUCKET_ENTRIES
    assert idx._cached_indices <= index_mod._MAX_CACHED_INDICES


def test_matches_objects_and_positions():
    corpus = ["foobar", "nope", "f o o b"]
    idx = Index(corpus)
    one_shot = purefzf.matches("foob", corpus, with_positions=True)
    for _ in range(2):  # second call goes through the cache
        ms = idx.matches("foob", with_positions=True)
        assert [(m.text, m.index, m.score, m.positions) for m in ms] == \
            [(m.text, m.index, m.score, m.positions) for m in one_shot]


def test_unknown_option_raises():
    idx = Index(["a"])
    with pytest.raises(TypeError):
        idx.filter("a", bogus=True)
