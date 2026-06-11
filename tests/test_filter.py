"""Behavioral tests for the filter pipeline (purefzf.filter / run_filter).

The expected outputs were all verified against `fzf --filter` v0.73.1.
"""

import purefzf


def test_sorted_by_score():
    # "bbb" scores higher (boundary bonus) than "ab"
    assert purefzf.filter("b", ["ab", "bbb"]) == ["bbb", "ab"]


def test_no_sort_keeps_input_order():
    assert purefzf.filter("b", ["ab", "bbb"], sort=False) == ["ab", "bbb"]


def test_no_sort_with_tac_still_sorts():
    # fzf quirk: --no-sort --tac goes through the non-streaming path where
    # sortedness is decided by the pattern alone
    assert purefzf.filter("b", ["ab", "bbb"], sort=False, tac=True) == \
        ["bbb", "ab"]


def test_tac_flips_ties():
    assert purefzf.filter("b", ["zb", "ab"]) == ["zb", "ab"]
    assert purefzf.filter("b", ["zb", "ab"], tac=True) == ["ab", "zb"]


def test_inverse_only_query_is_not_sorted():
    assert purefzf.filter("!a", ["bbb", "ab", "zz"]) == ["bbb", "zz"]


def test_empty_query_matches_all_in_input_order():
    lines = ["b", "a", "c"]
    assert purefzf.filter("", lines) == lines
    assert purefzf.filter("", lines, tac=True) == ["c", "a", "b"]


def test_and_terms():
    assert purefzf.filter("foo bar", ["foobar", "barfoo", "foo baz"]) == \
        ["foobar", "barfoo"]


def test_or_terms():
    assert sorted(purefzf.filter("^core | ^cli", ["core.py", "cli.py", "x.py"])) == \
        ["cli.py", "core.py"]


def test_exact_term():
    assert purefzf.filter("'oba", ["foobar", "fxoxbxa"]) == ["foobar"]


def test_prefix_suffix_terms():
    assert purefzf.filter("^foo", ["foobar", "xfoo"]) == ["foobar"]
    assert purefzf.filter("bar$", ["foobar", "barfoo"]) == ["foobar"]
    assert purefzf.filter("^foobar$", ["foobar", "foobarx"]) == ["foobar"]


def test_exact_boundary_term():
    lines = ["foo bar baz", "foobar baz"]
    assert purefzf.filter("'bar'", lines) == ["foo bar baz"]


def test_smart_case():
    # Lowercase query matches either case; equal scores keep input order
    assert purefzf.filter("abc", ["ABC", "abc"]) == ["ABC", "abc"]
    assert purefzf.filter("Abc", ["ABC", "abc", "Abc"]) == ["Abc"]
    assert purefzf.filter("abc", ["ABC", "xabc"], case="respect") == ["xabc"]
    assert purefzf.filter("ABC", ["ABC", "xabc"], case="ignore") == \
        ["ABC", "xabc"]


def test_exact_mode():
    # -e: terms are exact by default; 'term flips back to fuzzy
    assert purefzf.filter("ob", ["foobar", "oxbx"], fuzzy=False) == ["foobar"]
    assert sorted(purefzf.filter("'ob", ["foobar", "oxbx"], fuzzy=False)) == \
        ["foobar", "oxbx"]


def test_non_extended_mode():
    # +x: the query is a single fuzzy pattern; quotes have no meaning
    assert purefzf.filter("'ob", ["foobar", "'obx"], extended=False) == ["'obx"]


def test_literal_disables_normalization():
    assert purefzf.filter("danco", ["Danço"]) == ["Danço"]
    assert purefzf.filter("danco", ["Danço"], normalize=False) == []


def test_nth():
    lines = ["alpha beta", "beta alpha"]
    assert purefzf.filter("alpha", lines, nth="1") == ["alpha beta"]
    assert purefzf.filter("alpha", lines, nth="2") == ["beta alpha"]
    assert sorted(purefzf.filter("alpha", lines, nth="1,2")) == sorted(lines)


def test_nth_with_delimiter():
    lines = ["a:x:1", "b:y:2", "c:x:3"]
    assert purefzf.filter("x", lines, nth="2", delimiter=":") == \
        ["a:x:1", "c:x:3"]


def test_tiebreak_begin_end():
    lines = ["xxb", "bxx"]
    assert purefzf.filter("b", lines, tiebreak="begin") == ["bxx", "xxb"]
    # Score still dominates the end tiebreak ("bxx" gets the boundary bonus)
    assert purefzf.filter("b", lines, tiebreak="end") == ["bxx", "xxb"]
    # With equal scores, end tiebreak prefers matches closer to the end
    assert purefzf.filter("b", ["xbx xxx", "xxx xbx"], tiebreak="end") == \
        ["xxx xbx", "xbx xxx"]


def test_tiebreak_index_only():
    assert purefzf.filter("b", ["ab", "bbb"], tiebreak="index") == \
        ["bbb", "ab"]


def test_algo_v1():
    assert purefzf.filter("ob", ["foobar"], algo="v1") == ["foobar"]


def test_matches_objects():
    ms = purefzf.matches("ob", ["foobar", "nope"])
    assert len(ms) == 1
    assert ms[0].text == "foobar"
    assert ms[0].index == 0
    assert ms[0].score > 0


def test_matches_with_positions():
    ms = purefzf.matches("fb", ["foobar"], with_positions=True)
    assert sorted(ms[0].positions) == [0, 3]


def test_long_line_falls_back_to_v1():
    # fzf falls back to the greedy algorithm when N*M exceeds its slab
    # capacity (100*1024); the result must still be a match
    line = "x" * 60000 + "needle"
    assert purefzf.filter("ned", [line]) == [line]


def test_unicode_lines():
    lines = ["日本語のテキスト", "中文文本"]
    assert purefzf.filter("文本", lines) == ["中文文本"]


def test_invalid_options():
    import pytest
    with pytest.raises(ValueError):
        purefzf.filter("a", ["a"], tiebreak="bogus")
    with pytest.raises(ValueError):
        purefzf.filter("a", ["a"], nth="0")
    with pytest.raises(ValueError):
        purefzf.filter("a", ["a"], case="loud")
