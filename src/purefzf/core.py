"""Non-interactive filter pipeline: the engine behind `purefzf --filter`
and the purefzf.filter() / purefzf.matches() library API."""

from . import algo as _algo
from .pattern import (CASE_IGNORE, CASE_RESPECT, CASE_SMART, Item,
                      TERM_FUZZY, build_pattern)
from .result import (BY_BEGIN, BY_CHUNK, BY_END, BY_PATHNAME,
                     build_rank, criteria_for_scheme, parse_tiebreak)
from .tokenizer import Delimiter, parse_ranges

_CASE_MODES = {
    "smart": CASE_SMART,
    "ignore": CASE_IGNORE,
    "respect": CASE_RESPECT,
}

_ALGOS = {
    "v1": _algo.fuzzy_match_v1,
    "v2": _algo.fuzzy_match_v2,
}


class Match:
    """A matched line."""

    __slots__ = ("text", "index", "score", "positions")

    def __init__(self, text, index, score, positions):
        self.text = text
        self.index = index
        self.score = score
        self.positions = positions

    def __repr__(self):
        return "Match(text=%r, index=%d, score=%d)" % (
            self.text, self.index, self.score)


def _prepare(query, fuzzy, extended, case, normalize, algo, scheme, nth,
             delimiter, tiebreak, with_positions):
    if case not in _CASE_MODES:
        raise ValueError("case must be one of: smart, ignore, respect")
    if algo not in _ALGOS:
        raise ValueError("algo must be one of: v1, v2")
    scheme_name = scheme or "default"
    scheme_obj = _algo.get_scheme(scheme_name)

    if tiebreak:
        if not isinstance(tiebreak, str):
            tiebreak = ",".join(tiebreak)
        criteria = parse_tiebreak(tiebreak)
    else:
        criteria = criteria_for_scheme(scheme_name)

    forward = True
    with_pos = with_positions
    for idx in range(len(criteria) - 1, 0, -1):
        criterion = criteria[idx]
        if criterion == BY_CHUNK:
            with_pos = True
        elif criterion == BY_END:
            forward = False
        elif criterion == BY_BEGIN:
            forward = True
        elif criterion == BY_PATHNAME:
            with_pos = True
            forward = False

    ranges = None
    if nth:
        if isinstance(nth, str):
            ranges = parse_ranges(nth)
        else:
            ranges = parse_ranges(",".join(str(n) for n in nth))

    if delimiter is None:
        delim = Delimiter()
    elif isinstance(delimiter, Delimiter):
        delim = delimiter
    else:
        delim = Delimiter.parse(delimiter)

    pattern = build_pattern(query, fuzzy=fuzzy, fuzzy_algo=_ALGOS[algo],
                            extended=extended, case_mode=_CASE_MODES[case],
                            normalize=normalize, forward=forward,
                            with_pos=with_pos, nth=ranges, delimiter=delim,
                            scheme=scheme_obj)
    return pattern, criteria


def run_filter(lines, query, fuzzy=True, extended=True, case="smart",
               normalize=True, algo="v2", scheme=None, nth=None,
               delimiter=None, tiebreak=None, sort=True, tac=False,
               with_positions=False):
    """Match lines against the query exactly like `fzf --filter`.

    Returns the list of Match objects in fzf's output order.
    """
    pattern, criteria = _prepare(query, fuzzy, extended, case, normalize,
                                 algo, scheme, nth, delimiter, tiebreak,
                                 with_positions)
    slab = _algo.Slab()

    # fzf streams (and therefore never sorts) only when sorting is disabled
    # and --tac is not given; otherwise the sortedness of the output is
    # decided by the pattern alone (a quirk faithfully replicated here:
    # `--no-sort --tac` still sorts unless the query is unsortable).
    streaming = not sort and not tac
    effective_sort = pattern.sortable and not streaming

    results = []
    if pattern.is_empty():
        for index, text in enumerate(lines):
            results.append((None, Match(text, index, 0, None)))
    else:
        scheme_obj = pattern.scheme
        forward = pattern.forward
        with_pos = pattern.with_pos

        # Fast path mirroring fzf's directAlgo: a single non-inverse fuzzy
        # term and no --nth lets us call the match function directly.
        direct_term = None
        if pattern.extended and not pattern.nth and \
                len(pattern.term_sets) == 1 and \
                len(pattern.term_sets[0]) == 1:
            term = pattern.term_sets[0][0]
            if not term.inv and term.typ == TERM_FUZZY:
                direct_term = term

        if direct_term is not None:
            pfun = pattern.fuzzy_algo
            term_cs = direct_term.case_sensitive
            term_norm = direct_term.normalize
            term_text = direct_term.text
            for index, text in enumerate(lines):
                res, pos = pfun(term_cs, term_norm, forward, text, term_text,
                                with_pos, slab, scheme_obj)
                start = res[0]
                if start >= 0:
                    score = res[2]
                    rank = build_rank(text, [(start, res[1])], score,
                                      criteria) if effective_sort else None
                    results.append((rank, Match(text, index, score, pos)))
        else:
            for index, text in enumerate(lines):
                item = Item(text, index)
                matched = pattern.match_item(item, slab)
                if matched is not None:
                    offsets, score, pos = matched
                    rank = build_rank(text, offsets, score,
                                      criteria) if effective_sort else None
                    results.append((rank, Match(text, index, score, pos)))

    if effective_sort:
        if tac:
            results.sort(key=lambda r: (r[0], -r[1].index))
        else:
            results.sort(key=lambda r: r[0])
    elif tac and not streaming:
        results.reverse()

    return [match for _, match in results]


def filter_lines(query, lines, **options):
    """Return the matching lines (str) in fzf's output order."""
    return [m.text for m in run_filter(lines, query, **options)]
