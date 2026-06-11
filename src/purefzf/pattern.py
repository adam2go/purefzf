"""Search pattern parsing and matching, ported from fzf's pattern.go.

Extended-search syntax:

    fuzzy           'exact          'boundary-exact'
    ^prefix-exact   suffix-exact$   ^equal-exact$
    !inverse-exact  !'inverse-fuzzy !^inverse-prefix-exact
    sbtrkt | strkbt (OR)            term1 term2 (AND)
"""

import re

from . import algo
from .normalize import normalize_str
from .tokenizer import tokenize, transform, strip_last_delimiter

# Term types
TERM_FUZZY = 0
TERM_EXACT = 1
TERM_EXACT_BOUNDARY = 2
TERM_PREFIX = 3
TERM_SUFFIX = 4
TERM_EQUAL = 5

# Case matching modes
CASE_SMART = 0
CASE_IGNORE = 1
CASE_RESPECT = 2

_SPLIT_REGEX = re.compile(" +")


class Term:
    __slots__ = ("typ", "inv", "text", "case_sensitive", "normalize")

    def __init__(self, typ, inv, text, case_sensitive, normalize):
        self.typ = typ
        self.inv = inv
        self.text = text
        self.case_sensitive = case_sensitive
        self.normalize = normalize

    def __repr__(self):
        return "Term(typ=%d, inv=%r, text=%r, case_sensitive=%r)" % (
            self.typ, self.inv, self.text, self.case_sensitive)


class Item:
    """A single input line."""

    __slots__ = ("text", "index", "transformed")

    def __init__(self, text, index):
        self.text = text
        self.index = index
        self.transformed = None


def parse_terms(fuzzy, case_mode, normalize, s):
    """Port of parseTerms. Returns a list of term sets (OR groups)."""
    s = s.replace("\\ ", "\t")
    tokens = _SPLIT_REGEX.split(s)
    sets = []
    cur = []
    switch_set = False
    after_bar = False
    for token in tokens:
        typ, inv, text = TERM_FUZZY, False, token.replace("\t", " ")
        lower_text = text.lower()
        case_sensitive = case_mode == CASE_RESPECT or \
            (case_mode == CASE_SMART and text != lower_text)
        normalize_term = normalize and lower_text == normalize_str(lower_text)
        if not case_sensitive:
            text = lower_text
        if not fuzzy:
            typ = TERM_EXACT

        if len(cur) > 0 and not after_bar and text == "|":
            switch_set = False
            after_bar = True
            continue
        after_bar = False

        if text.startswith("!"):
            inv = True
            typ = TERM_EXACT
            text = text[1:]

        if text != "$" and text.endswith("$"):
            typ = TERM_SUFFIX
            text = text[:-1]

        if len(text) > 2 and text.startswith("'") and text.endswith("'"):
            typ = TERM_EXACT_BOUNDARY
            text = text[1:-1]
        elif text.startswith("'"):
            # Flip exactness
            if fuzzy and not inv:
                typ = TERM_EXACT
            else:
                typ = TERM_FUZZY
            text = text[1:]
        elif text.startswith("^"):
            if typ == TERM_SUFFIX:
                typ = TERM_EQUAL
            else:
                typ = TERM_PREFIX
            text = text[1:]

        if len(text) > 0:
            if switch_set:
                sets.append(cur)
                cur = []
            if normalize_term:
                text = normalize_str(text)
            cur.append(Term(typ, inv, text, case_sensitive, normalize_term))
            switch_set = True
    if len(cur) > 0:
        sets.append(cur)
    return sets


class Pattern:
    """Port of fzf's Pattern. Build with build_pattern()."""

    def __init__(self, fuzzy, fuzzy_algo, extended, case_mode, normalize,
                 forward, with_pos, nth, delimiter, query, scheme):
        if extended:
            as_string = query.lstrip(" ")
            while as_string.endswith(" ") and not as_string.endswith("\\ "):
                as_string = as_string[:-1]
        else:
            as_string = query

        case_sensitive = True
        sortable = True
        term_sets = []

        if extended:
            term_sets = parse_terms(fuzzy, case_mode, normalize, as_string)
            # We should not sort the result if there are only inverse search
            # terms
            sortable = any(not term.inv
                           for term_set in term_sets for term in term_set)
        else:
            lower_string = as_string.lower()
            normalize = normalize and lower_string == normalize_str(lower_string)
            case_sensitive = case_mode == CASE_RESPECT or \
                (case_mode == CASE_SMART and lower_string != as_string)
            if not case_sensitive:
                as_string = lower_string

        self.fuzzy = fuzzy
        self.fuzzy_algo = fuzzy_algo
        self.extended = extended
        self.case_sensitive = case_sensitive
        self.normalize = normalize
        self.forward = forward
        self.with_pos = with_pos
        self.text = as_string
        self.term_sets = term_sets
        self.sortable = sortable
        self.nth = nth
        self.delimiter = delimiter
        self.scheme = scheme
        self._proc_fun = {
            TERM_FUZZY: fuzzy_algo,
            TERM_EQUAL: algo.equal_match,
            TERM_EXACT: algo.exact_match_naive,
            TERM_EXACT_BOUNDARY: algo.exact_match_boundary,
            TERM_PREFIX: algo.prefix_match,
            TERM_SUFFIX: algo.suffix_match,
        }

    def is_empty(self):
        if not self.extended:
            return len(self.text) == 0
        return len(self.term_sets) == 0

    def match_item(self, item, slab=None):
        """Returns (offsets, total_score, positions) if the item matches,
        otherwise None. Port of MatchItem."""
        if self.extended:
            offsets, bonus, positions = self._extended_match(item, slab)
            if len(offsets) == len(self.term_sets):
                return offsets, bonus, positions
            return None
        offset, bonus, positions = self._basic_match(item, slab)
        if offset[0] >= 0:
            return [offset], bonus, positions
        return None

    def _input_tokens(self, item):
        if not self.nth:
            return None  # match against the whole line
        if item.transformed is not None:
            return item.transformed
        tokens = tokenize(item.text, self.delimiter)
        ret = transform(tokens, self.nth)
        # Strip the last delimiter to allow suffix match
        if len(ret) > 0 and not self.delimiter.is_awk():
            ret[-1].text = strip_last_delimiter(ret[-1].text, self.delimiter)
        item.transformed = ret
        return ret

    def _basic_match(self, item, slab):
        tokens = self._input_tokens(item)
        pfun = self.fuzzy_algo if self.fuzzy else algo.exact_match_naive
        return self._iter(pfun, item, tokens, self.case_sensitive,
                          self.normalize, self.text, slab)

    def _extended_match(self, item, slab):
        tokens = self._input_tokens(item)
        offsets = []
        total_score = 0
        all_pos = [] if self.with_pos else None
        for term_set in self.term_sets:
            offset = None
            current_score = 0
            matched = False
            for term in term_set:
                pfun = self._proc_fun[term.typ]
                off, score, pos = self._iter(pfun, item, tokens,
                                             term.case_sensitive,
                                             term.normalize, term.text, slab)
                if off[0] >= 0:
                    if term.inv:
                        continue
                    offset, current_score = off, score
                    matched = True
                    if self.with_pos:
                        if pos is not None:
                            all_pos.extend(pos)
                        else:
                            all_pos.extend(range(off[0], off[1]))
                    break
                elif term.inv:
                    offset, current_score = (0, 0), 0
                    matched = True
                    continue
            if matched:
                offsets.append(offset)
                total_score += current_score
        return offsets, total_score, all_pos

    def _iter(self, pfun, item, tokens, case_sensitive, normalize, pattern,
              slab):
        """Match the pattern against each token; first match wins.
        Port of Pattern.iter."""
        if tokens is None:
            res, pos = pfun(case_sensitive, normalize, self.forward,
                            item.text, pattern, self.with_pos, slab,
                            self.scheme)
            if res[0] >= 0:
                return (res[0], res[1]), res[2], pos
            return (-1, -1), 0, None
        for part in tokens:
            res, pos = pfun(case_sensitive, normalize, self.forward,
                            part.text, pattern, self.with_pos, slab,
                            self.scheme)
            if res[0] >= 0:
                sidx = res[0] + part.prefix_length
                eidx = res[1] + part.prefix_length
                if pos is not None:
                    pos = [p + part.prefix_length for p in pos]
                return (sidx, eidx), res[2], pos
        return (-1, -1), 0, None


def build_pattern(query, fuzzy=True, fuzzy_algo=algo.fuzzy_match_v2,
                  extended=True, case_mode=CASE_SMART, normalize=True,
                  forward=True, with_pos=False, nth=None, delimiter=None,
                  scheme=algo.DEFAULT_SCHEME):
    from .tokenizer import Delimiter
    if delimiter is None:
        delimiter = Delimiter()
    return Pattern(fuzzy, fuzzy_algo, extended, case_mode, normalize, forward,
                   with_pos, nth or [], delimiter, query, scheme)
