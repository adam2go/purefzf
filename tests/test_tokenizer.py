"""Port of fzf's src/tokenizer_test.go (v0.73.1)."""

from purefzf.tokenizer import (RANGE_ELLIPSIS, Delimiter, join_tokens,
                               parse_range, parse_ranges, tokenize, transform)


def test_parse_range():
    r = parse_range("..")
    assert (r.begin, r.end) == (RANGE_ELLIPSIS, RANGE_ELLIPSIS)

    r = parse_range("3..")
    assert (r.begin, r.end) == (3, RANGE_ELLIPSIS)

    r = parse_range("3..5")
    assert (r.begin, r.end) == (3, 5)

    r = parse_range("-3..-5")
    assert (r.begin, r.end) == (-3, -5)

    r = parse_range("3")
    assert (r.begin, r.end) == (3, 3)

    assert parse_range("1..3..5") is None
    assert parse_range("-3..3") is None


def test_tokenize():
    # AWK-style
    text = "  abc: \n\t def:  ghi  "
    tokens = tokenize(text, Delimiter())
    assert tokens[0].text == "abc: \n\t " and tokens[0].prefix_length == 2

    # With delimiter
    tokens = tokenize(text, Delimiter.parse(":"))
    assert tokens[0].text == "  abc:" and tokens[0].prefix_length == 0

    # With delimiter regex
    tokens = tokenize(text, Delimiter.parse("\\s+"))
    assert tokens[0].text == "  " and tokens[0].prefix_length == 0
    assert tokens[1].text == "abc: \n\t " and tokens[1].prefix_length == 2
    assert tokens[2].text == "def:  " and tokens[2].prefix_length == 10
    assert tokens[3].text == "ghi  " and tokens[3].prefix_length == 16


def test_transform():
    text = "  abc:  def:  ghi:  jkl"

    # AWK-style tokens
    tokens = tokenize(text, Delimiter())

    tx = transform(tokens, parse_ranges("1,2,3"))
    assert join_tokens(tx) == "abc:  def:  ghi:  "

    tx = transform(tokens, parse_ranges("1..2,3,2..,1"))
    assert join_tokens(tx) == "abc:  def:  ghi:  def:  ghi:  jklabc:  "
    assert len(tx) == 4
    assert tx[0].text == "abc:  def:  " and tx[0].prefix_length == 2
    assert tx[1].text == "ghi:  " and tx[1].prefix_length == 14
    assert tx[2].text == "def:  ghi:  jkl" and tx[2].prefix_length == 8
    assert tx[3].text == "abc:  " and tx[3].prefix_length == 2

    # String-delimiter tokens
    tokens = tokenize(text, Delimiter.parse(":"))
    tx = transform(tokens, parse_ranges("1..2,3,2..,1"))
    assert join_tokens(tx) == "  abc:  def:  ghi:  def:  ghi:  jkl  abc:"
    assert len(tx) == 4
    assert tx[0].text == "  abc:  def:" and tx[0].prefix_length == 0
    assert tx[1].text == "  ghi:" and tx[1].prefix_length == 12
    assert tx[2].text == "  def:  ghi:  jkl" and tx[2].prefix_length == 6
    assert tx[3].text == "  abc:" and tx[3].prefix_length == 0


def test_transform_index_out_of_bounds():
    transform([], parse_ranges("1"))
