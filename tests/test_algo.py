"""Port of fzf's src/algo/algo_test.go (v0.73.1).

Every assertion in the upstream test file is reproduced here with the same
inputs and expected scores. Cases are tagged with the upstream test name.
"""

import pytest

from purefzf.algo import (
    BONUS_BOUNDARY,
    BONUS_CAMEL123,
    BONUS_CONSECUTIVE,
    BONUS_FIRST_CHAR_MULTIPLIER,
    BONUS_NON_WORD,
    DEFAULT_SCHEME,
    SCORE_GAP_EXTENSION,
    SCORE_GAP_START,
    SCORE_MATCH,
    equal_match,
    exact_match_naive,
    fuzzy_match_v1,
    fuzzy_match_v2,
    prefix_match,
    suffix_match,
)

BOUNDARY_WHITE = DEFAULT_SCHEME.bonus_boundary_white
BOUNDARY_DELIMITER = DEFAULT_SCHEME.bonus_boundary_delimiter


def assert_match(fun, case_sensitive, forward, text, pattern, sidx, eidx,
                 score, normalize=False):
    if not case_sensitive:
        pattern = pattern.lower()
    res, pos = fun(case_sensitive, normalize, forward, text, pattern, True)
    if pos:
        pos = sorted(pos)
        start, end = pos[0], pos[-1] + 1
    else:
        start, end = res[0], res[1]
    assert (start, end, res[2]) == (sidx, eidx, score), \
        "%s(%r, %r) = %r, want %r" % (fun.__name__, text, pattern,
                                      (start, end, res[2]), (sidx, eidx, score))


FUZZY_CASES = [
    # (case_sensitive, input, pattern, sidx, eidx, score)
    (False, "fooBarbaz1", "oBZ", 2, 9,
     SCORE_MATCH * 3 + BONUS_CAMEL123 + SCORE_GAP_START + SCORE_GAP_EXTENSION * 3),
    (False, "foo bar baz", "fbb", 0, 9,
     SCORE_MATCH * 3 + BOUNDARY_WHITE * BONUS_FIRST_CHAR_MULTIPLIER +
     BOUNDARY_WHITE * 2 + 2 * SCORE_GAP_START + 4 * SCORE_GAP_EXTENSION),
    (False, "/AutomatorDocument.icns", "rdoc", 9, 13,
     SCORE_MATCH * 4 + BONUS_CAMEL123 + BONUS_CONSECUTIVE * 2),
    (False, "/man1/zshcompctl.1", "zshc", 6, 10,
     SCORE_MATCH * 4 + BOUNDARY_DELIMITER * BONUS_FIRST_CHAR_MULTIPLIER +
     BOUNDARY_DELIMITER * 3),
    (False, "/.oh-my-zsh/cache", "zshc", 8, 13,
     SCORE_MATCH * 4 + BONUS_BOUNDARY * BONUS_FIRST_CHAR_MULTIPLIER +
     BONUS_BOUNDARY * 2 + SCORE_GAP_START + BOUNDARY_DELIMITER),
    # Non-word character at start of input is treated as a strong boundary
    (False, ".vimrc", ".vimrc", 0, 6,
     SCORE_MATCH * 6 + BOUNDARY_WHITE * (BONUS_FIRST_CHAR_MULTIPLIER + 5)),
    # Non-word character right after a delimiter inherits the delimiter
    # boundary
    (False, "/.vimrc", ".vimrc", 1, 7,
     SCORE_MATCH * 6 + BOUNDARY_DELIMITER * (BONUS_FIRST_CHAR_MULTIPLIER + 5)),
    # Non-word character in the middle of a word stays at BONUS_NON_WORD
    (False, "a.vimrc", ".vimrc", 1, 7,
     SCORE_MATCH * 6 + BONUS_BOUNDARY * (BONUS_FIRST_CHAR_MULTIPLIER + 5)),
    (False, "ab0123 456", "12356", 3, 10,
     SCORE_MATCH * 5 + BONUS_CONSECUTIVE * 3 + SCORE_GAP_START +
     SCORE_GAP_EXTENSION),
    (False, "abc123 456", "12356", 3, 10,
     SCORE_MATCH * 5 + BONUS_CAMEL123 * BONUS_FIRST_CHAR_MULTIPLIER +
     BONUS_CAMEL123 * 2 + BONUS_CONSECUTIVE + SCORE_GAP_START +
     SCORE_GAP_EXTENSION),
    (False, "foo/bar/baz", "fbb", 0, 9,
     SCORE_MATCH * 3 + BOUNDARY_WHITE * BONUS_FIRST_CHAR_MULTIPLIER +
     BOUNDARY_DELIMITER * 2 + 2 * SCORE_GAP_START + 4 * SCORE_GAP_EXTENSION),
    (False, "fooBarBaz", "fbb", 0, 7,
     SCORE_MATCH * 3 + BOUNDARY_WHITE * BONUS_FIRST_CHAR_MULTIPLIER +
     BONUS_CAMEL123 * 2 + 2 * SCORE_GAP_START + 2 * SCORE_GAP_EXTENSION),
    (False, "foo barbaz", "fbb", 0, 8,
     SCORE_MATCH * 3 + BOUNDARY_WHITE * BONUS_FIRST_CHAR_MULTIPLIER +
     BOUNDARY_WHITE + SCORE_GAP_START * 2 + SCORE_GAP_EXTENSION * 3),
    (False, "fooBar Baz", "foob", 0, 4,
     SCORE_MATCH * 4 + BOUNDARY_WHITE * BONUS_FIRST_CHAR_MULTIPLIER +
     BOUNDARY_WHITE * 3),
    (False, "xFoo-Bar Baz", "foo-b", 1, 6,
     SCORE_MATCH * 5 + BONUS_CAMEL123 * BONUS_FIRST_CHAR_MULTIPLIER +
     BONUS_CAMEL123 * 2 + BONUS_NON_WORD + BONUS_BOUNDARY),
    (True, "fooBarbaz", "oBz", 2, 9,
     SCORE_MATCH * 3 + BONUS_CAMEL123 + SCORE_GAP_START +
     SCORE_GAP_EXTENSION * 3),
    (True, "Foo/Bar/Baz", "FBB", 0, 9,
     SCORE_MATCH * 3 + BOUNDARY_WHITE * BONUS_FIRST_CHAR_MULTIPLIER +
     BOUNDARY_DELIMITER * 2 + SCORE_GAP_START * 2 + SCORE_GAP_EXTENSION * 4),
    (True, "FooBarBaz", "FBB", 0, 7,
     SCORE_MATCH * 3 + BOUNDARY_WHITE * BONUS_FIRST_CHAR_MULTIPLIER +
     BONUS_CAMEL123 * 2 + SCORE_GAP_START * 2 + SCORE_GAP_EXTENSION * 2),
    (True, "FooBar Baz", "FooB", 0, 4,
     SCORE_MATCH * 4 + BOUNDARY_WHITE * BONUS_FIRST_CHAR_MULTIPLIER +
     BOUNDARY_WHITE * 2 + max(BONUS_CAMEL123, BOUNDARY_WHITE)),
    # Consecutive bonus updated
    (True, "foo-bar", "o-ba", 2, 6, SCORE_MATCH * 4 + BONUS_BOUNDARY * 3),
    # Non-match
    (True, "fooBarbaz", "oBZ", -1, -1, 0),
    (True, "Foo Bar Baz", "fbb", -1, -1, 0),
    (True, "fooBarbaz", "fooBarbazz", -1, -1, 0),
]


@pytest.mark.parametrize("fun", [fuzzy_match_v1, fuzzy_match_v2],
                         ids=["v1", "v2"])
@pytest.mark.parametrize("forward", [True, False], ids=["fwd", "bwd"])
@pytest.mark.parametrize("case", FUZZY_CASES,
                         ids=["%s/%s" % (c[1], c[2]) for c in FUZZY_CASES])
def test_fuzzy_match(fun, forward, case):
    case_sensitive, text, pattern, sidx, eidx, score = case
    assert_match(fun, case_sensitive, forward, text, pattern, sidx, eidx, score)


def test_fuzzy_match_backward():
    assert_match(fuzzy_match_v1, False, True, "foobar fb", "fb", 0, 4,
                 SCORE_MATCH * 2 + BOUNDARY_WHITE * BONUS_FIRST_CHAR_MULTIPLIER +
                 SCORE_GAP_START + SCORE_GAP_EXTENSION)
    assert_match(fuzzy_match_v1, False, False, "foobar fb", "fb", 7, 9,
                 SCORE_MATCH * 2 + BOUNDARY_WHITE * BONUS_FIRST_CHAR_MULTIPLIER +
                 BOUNDARY_WHITE)


@pytest.mark.parametrize("forward", [True, False], ids=["fwd", "bwd"])
def test_exact_match_naive(forward):
    assert_match(exact_match_naive, True, forward, "fooBarbaz", "oBA", -1, -1, 0)
    assert_match(exact_match_naive, True, forward, "fooBarbaz", "fooBarbazz",
                 -1, -1, 0)
    assert_match(exact_match_naive, False, forward, "fooBarbaz", "oBA", 2, 5,
                 SCORE_MATCH * 3 + BONUS_CAMEL123 + BONUS_CONSECUTIVE)
    assert_match(exact_match_naive, False, forward, "/AutomatorDocument.icns",
                 "rdoc", 9, 13,
                 SCORE_MATCH * 4 + BONUS_CAMEL123 + BONUS_CONSECUTIVE * 2)
    assert_match(exact_match_naive, False, forward, "/man1/zshcompctl.1",
                 "zshc", 6, 10,
                 SCORE_MATCH * 4 +
                 BOUNDARY_DELIMITER * (BONUS_FIRST_CHAR_MULTIPLIER + 3))
    assert_match(exact_match_naive, False, forward, "/.oh-my-zsh/cache",
                 "zsh/c", 8, 13,
                 SCORE_MATCH * 5 +
                 BONUS_BOUNDARY * (BONUS_FIRST_CHAR_MULTIPLIER + 3) +
                 BOUNDARY_DELIMITER)


def test_exact_match_naive_backward():
    assert_match(exact_match_naive, False, True, "foobar foob", "oo", 1, 3,
                 SCORE_MATCH * 2 + BONUS_CONSECUTIVE)
    assert_match(exact_match_naive, False, False, "foobar foob", "oo", 8, 10,
                 SCORE_MATCH * 2 + BONUS_CONSECUTIVE)


@pytest.mark.parametrize("forward", [True, False], ids=["fwd", "bwd"])
def test_prefix_match(forward):
    score = SCORE_MATCH * 3 + BOUNDARY_WHITE * BONUS_FIRST_CHAR_MULTIPLIER + \
        BOUNDARY_WHITE * 2

    assert_match(prefix_match, True, forward, "fooBarbaz", "Foo", -1, -1, 0)
    assert_match(prefix_match, False, forward, "fooBarBaz", "baz", -1, -1, 0)
    assert_match(prefix_match, False, forward, "fooBarbaz", "Foo", 0, 3, score)
    assert_match(prefix_match, False, forward, "foOBarBaZ", "foo", 0, 3, score)
    assert_match(prefix_match, False, forward, "f-oBarbaz", "f-o", 0, 3, score)
    assert_match(prefix_match, False, forward, " fooBar", "foo", 1, 4, score)
    assert_match(prefix_match, False, forward, " fooBar", " fo", 0, 3, score)
    assert_match(prefix_match, False, forward, "     fo", "foo", -1, -1, 0)


@pytest.mark.parametrize("forward", [True, False], ids=["fwd", "bwd"])
def test_suffix_match(forward):
    assert_match(suffix_match, True, forward, "fooBarbaz", "Baz", -1, -1, 0)
    assert_match(suffix_match, False, forward, "fooBarbaz", "Foo", -1, -1, 0)
    assert_match(suffix_match, False, forward, "fooBarbaz", "baz", 6, 9,
                 SCORE_MATCH * 3 + BONUS_CONSECUTIVE * 2)
    assert_match(suffix_match, False, forward, "fooBarBaZ", "baz", 6, 9,
                 (SCORE_MATCH + BONUS_CAMEL123) * 3 +
                 BONUS_CAMEL123 * (BONUS_FIRST_CHAR_MULTIPLIER - 1))
    # Strip trailing white space from the string
    assert_match(suffix_match, False, forward, "fooBarbaz ", "baz", 6, 9,
                 SCORE_MATCH * 3 + BONUS_CONSECUTIVE * 2)
    # Only when the pattern doesn't end with a space
    assert_match(suffix_match, False, forward, "fooBarbaz ", "baz ", 6, 10,
                 SCORE_MATCH * 4 + BONUS_CONSECUTIVE * 2 + BOUNDARY_WHITE)


@pytest.mark.parametrize("forward", [True, False], ids=["fwd", "bwd"])
def test_empty_pattern(forward):
    assert_match(fuzzy_match_v1, True, forward, "foobar", "", 0, 0, 0)
    assert_match(fuzzy_match_v2, True, forward, "foobar", "", 0, 0, 0)
    assert_match(exact_match_naive, True, forward, "foobar", "", 0, 0, 0)
    assert_match(prefix_match, True, forward, "foobar", "", 0, 0, 0)
    assert_match(suffix_match, True, forward, "foobar", "", 6, 6, 0)


NORMALIZE_CASES = [
    ("Só Danço Samba", "So", 0, 2, 62,
     [fuzzy_match_v1, fuzzy_match_v2, prefix_match, exact_match_naive]),
    ("Só Danço Samba", "sodc", 0, 7, 97, [fuzzy_match_v1, fuzzy_match_v2]),
    ("Danço", "danco", 0, 5, 140,
     [fuzzy_match_v1, fuzzy_match_v2, prefix_match, suffix_match,
      exact_match_naive, equal_match]),
]


@pytest.mark.parametrize("case", NORMALIZE_CASES, ids=[c[1] for c in NORMALIZE_CASES])
def test_normalize(case):
    text, pattern, sidx, eidx, score, funs = case
    for fun in funs:
        assert_match(fun, False, True, text, pattern, sidx, eidx, score,
                     normalize=True)


def test_long_string():
    max_uint16 = 0xFFFF
    text = "x" * max_uint16 + "z" + "x" * (max_uint16 - 1)
    assert_match(fuzzy_match_v2, True, True, text, "zx",
                 max_uint16, max_uint16 + 2,
                 SCORE_MATCH * 2 + BONUS_CONSECUTIVE)


def test_long_string_with_normalize():
    text = "x" * 30000 + " Minímal example"
    assert_match(fuzzy_match_v1, False, False, text, "minim", 30001, 30006, 140,
                 normalize=True)
