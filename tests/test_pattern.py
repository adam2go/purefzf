"""Port of fzf's src/pattern_test.go (v0.73.1).

The chunk-cache tests (TestCacheKey, TestCacheable, TestBitmapCacheBenefit)
are not ported: the cache exists to speed up incremental typing in the
interactive UI, which purefzf does not include. Everything else is
reproduced with the same inputs and expectations.
"""

from purefzf import algo
from purefzf.pattern import (CASE_IGNORE, CASE_RESPECT, CASE_SMART, Item,
                             TERM_EQUAL, TERM_EXACT, TERM_FUZZY, TERM_PREFIX,
                             TERM_SUFFIX, build_pattern, parse_terms)
from purefzf.tokenizer import Delimiter, Range, tokenize, transform


def test_parse_terms_extended():
    terms = parse_terms(
        True, CASE_SMART, False,
        "aaa 'bbb ^ccc ddd$ !eee !'fff !^ggg !hhh$ | ^iii$ ^xxx | 'yyy | zzz$ | !ZZZ |")
    assert len(terms) == 9
    assert terms[0][0].typ == TERM_FUZZY and not terms[0][0].inv
    assert terms[1][0].typ == TERM_EXACT and not terms[1][0].inv
    assert terms[2][0].typ == TERM_PREFIX and not terms[2][0].inv
    assert terms[3][0].typ == TERM_SUFFIX and not terms[3][0].inv
    assert terms[4][0].typ == TERM_EXACT and terms[4][0].inv
    assert terms[5][0].typ == TERM_FUZZY and terms[5][0].inv
    assert terms[6][0].typ == TERM_PREFIX and terms[6][0].inv
    assert terms[7][0].typ == TERM_SUFFIX and terms[7][0].inv
    assert terms[7][1].typ == TERM_EQUAL and not terms[7][1].inv
    assert terms[8][0].typ == TERM_PREFIX and not terms[8][0].inv
    assert terms[8][1].typ == TERM_EXACT and not terms[8][1].inv
    assert terms[8][2].typ == TERM_SUFFIX and not terms[8][2].inv
    assert terms[8][3].typ == TERM_EXACT and terms[8][3].inv
    for term_set in terms[:8]:
        assert len(term_set[0].text) == 3


def test_parse_terms_extended_exact():
    terms = parse_terms(False, CASE_SMART, False,
                        "aaa 'bbb ^ccc ddd$ !eee !'fff !^ggg !hhh$")
    assert len(terms) == 8
    expected = [
        (TERM_EXACT, False), (TERM_FUZZY, False), (TERM_PREFIX, False),
        (TERM_SUFFIX, False), (TERM_EXACT, True), (TERM_FUZZY, True),
        (TERM_PREFIX, True), (TERM_SUFFIX, True),
    ]
    for term_set, (typ, inv) in zip(terms, expected):
        assert term_set[0].typ == typ
        assert term_set[0].inv == inv
        assert len(term_set[0].text) == 3


def test_parse_terms_empty():
    terms = parse_terms(True, CASE_SMART, False, "' ^ !' !^")
    assert len(terms) == 0


def test_exact():
    pattern = build_pattern("'abc", fuzzy=True, extended=True,
                            case_mode=CASE_SMART, normalize=False)
    term = pattern.term_sets[0][0]
    res, pos = algo.exact_match_naive(
        pattern.case_sensitive, pattern.normalize, pattern.forward,
        "aabbcc abc", term.text, True)
    assert (res[0], res[1]) == (7, 10)
    assert pos is None


def test_equal():
    pattern = build_pattern("^AbC$", fuzzy=True, extended=True,
                            case_mode=CASE_SMART, normalize=False)
    term = pattern.term_sets[0][0]

    def match(s, sidx_expected, eidx_expected):
        res, pos = algo.equal_match(
            pattern.case_sensitive, pattern.normalize, pattern.forward,
            s, term.text, True)
        assert (res[0], res[1]) == (sidx_expected, eidx_expected)
        assert pos is None

    match("ABC", -1, -1)
    match("AbC", 0, 3)
    match("AbC  ", 0, 3)
    match(" AbC ", 1, 4)
    match("  AbC", 2, 5)


def test_case_sensitivity():
    cases = [
        # query, case_mode, expected text, expected case_sensitive
        ("abc", CASE_SMART, "abc", False),
        ("Abc", CASE_SMART, "Abc", True),
        ("abc", CASE_IGNORE, "abc", False),
        ("Abc", CASE_IGNORE, "abc", False),
        ("abc", CASE_RESPECT, "abc", True),
        ("Abc", CASE_RESPECT, "Abc", True),
    ]
    for query, case_mode, text, case_sensitive in cases:
        pat = build_pattern(query, fuzzy=True, extended=False,
                            case_mode=case_mode, normalize=False)
        assert pat.text == text, query
        assert pat.case_sensitive == case_sensitive, query


def test_orig_text_and_transformed():
    # Adapted: purefzf matches transformed tokens through the nth option
    # rather than pre-populated chunk items.
    pattern = build_pattern("jg", fuzzy=True, extended=True,
                            case_mode=CASE_SMART, normalize=False,
                            with_pos=True, nth=[Range(1, 1)])
    tokens = tokenize("junegunn", Delimiter())
    trans = transform(tokens, [Range(1, 1)])

    for extended in (False, True):
        pattern.extended = extended
        item = Item("junegunn", 0)
        item.transformed = trans
        matched = pattern.match_item(item)
        assert matched is not None
        offsets, _bonus, pos = matched
        assert offsets[0] == (0, 5)
        assert pos == [4, 0]
