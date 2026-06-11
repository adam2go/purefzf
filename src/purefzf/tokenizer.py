"""Field tokenization for --nth / --delimiter, ported from fzf's tokenizer.go."""

import re

from .algo import is_space

RANGE_ELLIPSIS = 0


class Range:
    """nth-expression: a 1-based field range. 0 stands for the open end."""

    __slots__ = ("begin", "end")

    def __init__(self, begin, end):
        # newRange in fzf
        if begin == 1 and end != 1:
            begin = RANGE_ELLIPSIS
        if end == -1:
            end = RANGE_ELLIPSIS
        self.begin = begin
        self.end = end

    def is_full(self):
        return self.begin == RANGE_ELLIPSIS and self.end == RANGE_ELLIPSIS

    def __eq__(self, other):
        return isinstance(other, Range) and \
            self.begin == other.begin and self.end == other.end

    def __repr__(self):
        return "Range(%d, %d)" % (self.begin, self.end)


def _atoi(s):
    # strconv.Atoi: optional sign followed by digits only
    if re.fullmatch(r"[+-]?[0-9]+", s):
        return int(s)
    return None


def parse_range(s):
    """Parse a single nth-expression. Returns Range or None. Port of ParseRange."""
    if s == "..":
        return Range(RANGE_ELLIPSIS, RANGE_ELLIPSIS)
    if s.startswith(".."):
        end = _atoi(s[2:])
        if end is None or end == 0:
            return None
        return Range(RANGE_ELLIPSIS, end)
    if s.endswith(".."):
        begin = _atoi(s[:-2])
        if begin is None or begin == 0:
            return None
        return Range(begin, RANGE_ELLIPSIS)
    if ".." in s:
        ns = s.split("..")
        if len(ns) != 2:
            return None
        begin, end = _atoi(ns[0]), _atoi(ns[1])
        if begin is None or end is None or begin == 0 or end == 0 or \
                (begin < 0 and end > 0):
            return None
        return Range(begin, end)
    n = _atoi(s)
    if n is None or n == 0:
        return None
    return Range(n, n)


def parse_ranges(s):
    """Parse a comma-separated list of nth-expressions. Raises ValueError."""
    ranges = []
    for token in s.split(","):
        r = parse_range(token)
        if r is None:
            raise ValueError("invalid format: " + s)
        ranges.append(r)
    return ranges


class Delimiter:
    """Delimiter for tokenizing the input: AWK-style (both None), a literal
    string, or a regular expression."""

    __slots__ = ("string", "regex")

    def __init__(self, string=None, regex=None):
        self.string = string
        self.regex = regex

    def is_awk(self):
        return self.string is None and self.regex is None

    @classmethod
    def parse(cls, s):
        """Port of fzf's delimiterRegexp."""
        s = s.replace("\\t", "\t")
        if re.escape(s) == s:
            return cls(string=s)
        try:
            return cls(regex=re.compile(s))
        except re.error:
            return cls(string=s)


class Token:
    __slots__ = ("text", "prefix_length")

    def __init__(self, text, prefix_length):
        self.text = text
        self.prefix_length = prefix_length

    def __repr__(self):
        return "Token(%r, %d)" % (self.text, self.prefix_length)


def _awk_tokenizer(text):
    # AWK-style: \S+\s* chunks; whitespace is tab, space, or newline,
    # matching the byte-level loop in fzf
    ret = []
    prefix_length = 0
    state = 0  # 0: nil, 1: black, 2: white
    begin = 0
    end = 0
    for idx, ch in enumerate(text):
        white = ch == " " or ch == "\t" or ch == "\n"
        if state == 0:
            if white:
                prefix_length += 1
            else:
                state, begin, end = 1, idx, idx + 1
        elif state == 1:
            end = idx + 1
            if white:
                state = 2
        else:
            if white:
                end = idx + 1
            else:
                ret.append(text[begin:end])
                state, begin, end = 1, idx, idx + 1
    if begin < end:
        ret.append(text[begin:end])
    return ret, prefix_length


def _with_prefix_lengths(tokens, begin):
    ret = []
    prefix_length = begin
    for tok in tokens:
        ret.append(Token(tok, prefix_length))
        prefix_length += len(tok)
    return ret


def tokenize(text, delimiter):
    """Tokenize the given string with the delimiter. Port of Tokenize."""
    if delimiter.is_awk():
        tokens, prefix_length = _awk_tokenizer(text)
        return _with_prefix_lengths(tokens, prefix_length)

    if delimiter.string is not None:
        # strings.SplitAfter
        parts = text.split(delimiter.string)
        tokens = [p + delimiter.string for p in parts[:-1]] + [parts[-1]]
        return _with_prefix_lengths(tokens, 0)

    tokens = []
    begin = 0
    for m in delimiter.regex.finditer(text):
        tokens.append(text[begin:m.end()])
        begin = m.end()
    if begin < len(text):
        tokens.append(text[begin:])
    return _with_prefix_lengths(tokens, 0)


def strip_last_delimiter(s, delimiter):
    """Remove the trailing delimiter. Port of StripLastDelimiter."""
    if delimiter.string is not None:
        if s.endswith(delimiter.string):
            return s[:len(s) - len(delimiter.string)]
        return s
    if delimiter.regex is not None:
        last = None
        for m in delimiter.regex.finditer(s):
            last = m
        if last is not None and last.end() == len(s):
            return s[:last.start()]
        return s
    end = len(s)
    while end > 0 and is_space(s[end - 1]):
        end -= 1
    return s[:end]


def join_tokens(tokens):
    return "".join(tok.text for tok in tokens)


def transform(tokens, with_nth):
    """Build the token list for the given ranges. Port of Transform."""
    trans_tokens = []
    num_tokens = len(tokens)
    for r in with_nth:
        parts = []
        min_idx = 0
        if r.begin == r.end:
            idx = r.begin
            if idx == RANGE_ELLIPSIS:
                parts.append(join_tokens(tokens))
            else:
                if idx < 0:
                    idx += num_tokens + 1
                if 1 <= idx <= num_tokens:
                    min_idx = idx - 1
                    parts.append(tokens[idx - 1].text)
        else:
            if r.begin == RANGE_ELLIPSIS:  # ..N
                begin, end = 1, r.end
                if end < 0:
                    end += num_tokens + 1
            elif r.end == RANGE_ELLIPSIS:  # N..
                begin, end = r.begin, num_tokens
                if begin < 0:
                    begin += num_tokens + 1
            else:
                begin, end = r.begin, r.end
                if begin < 0:
                    begin += num_tokens + 1
                if end < 0:
                    end += num_tokens + 1
            min_idx = max(0, begin - 1)
            for idx in range(begin, end + 1):
                if 1 <= idx <= num_tokens:
                    parts.append(tokens[idx - 1].text)

        merged = "".join(parts)
        if min_idx < num_tokens:
            prefix_length = tokens[min_idx].prefix_length
        else:
            prefix_length = 0
        trans_tokens.append(Token(merged, prefix_length))
    return trans_tokens
