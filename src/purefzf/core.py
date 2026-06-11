"""Non-interactive filter pipeline: the engine behind `purefzf --filter`
and the purefzf.filter() / purefzf.matches() library API."""

import re

from . import algo as _algo
from .pattern import (CASE_IGNORE, CASE_RESPECT, CASE_SMART, Item,
                      TERM_FUZZY, build_pattern)
from .result import (BY_BEGIN, BY_CHUNK, BY_END, BY_LENGTH, BY_PATHNAME,
                     BY_SCORE, build_rank, criteria_for_scheme,
                     parse_tiebreak)
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


def _term_necessary_regex(term):
    """A regex over a single line that is a *necessary* condition for the
    term to match it (never a sufficient one -- candidates are always
    re-verified by the real match functions). Returns None when no useful
    condition exists."""
    text = term.text
    if "\n" in text:
        # Lines never contain a newline, and a pattern newline cannot
        # cross line boundaries in the joined corpus
        return "(?!)"
    if term.typ == TERM_FUZZY:
        # Anchored greedy chain: from the start of a line, skip to the
        # first c1, then to the first c2 after it, and so on. Every step
        # has exactly one possible outcome, so the scan never backtracks
        # and stays linear even on degenerate lines ('a' * 200000 made
        # the non-greedy form `c1[^\n]*?c2...` quadratic). Greedy chains
        # find a subsequence iff one exists.
        return "^" + "".join("[^\\n%s]*%s" % (re.escape(c), re.escape(c))
                             for c in text)
    return re.escape(text)


_MIN_PREFILTER_LINES = 4096


def _bulk_candidates(lines, pattern):
    """C-speed prefilter: scan the corpus joined into one string with a
    single regex built from one of the pattern's AND-sets, yielding only
    the lines that could possibly match. Returns a list of (index, line)
    pairs, or None when prefiltering is not applicable/profitable."""
    if pattern.nth:
        # --nth can reorder fields; a per-line condition no longer holds
        return None
    if len(lines) < _MIN_PREFILTER_LINES:
        # The per-line path is already fast on small inputs
        return None

    # Pick the most selective AND-set that has no inverse terms (every
    # line in the result must satisfy each AND-set, so any one of them is
    # a necessary condition)
    if pattern.extended:
        best = None
        best_score = -1
        for term_set in pattern.term_sets:
            if any(t.inv for t in term_set):
                continue
            score = min(
                len(t.text) * (1 if t.typ == TERM_FUZZY else 2)
                for t in term_set)
            if score > best_score:
                best, best_score = term_set, score
        if best is None:
            return None
        branches = [_term_necessary_regex(t) for t in best]
        case_sensitive = [t.case_sensitive for t in best]
        has_fuzzy = any(t.typ == TERM_FUZZY for t in best)
    else:
        if not pattern.text:
            return None

        class _T:  # minimal stand-in
            typ = TERM_FUZZY if pattern.fuzzy else -1
            text = pattern.text
        branches = [_term_necessary_regex(_T)]
        case_sensitive = [pattern.case_sensitive]
        has_fuzzy = pattern.fuzzy

    if all(case_sensitive):
        rx_src = "|".join(branches)
        lower_hay = False
    elif not any(case_sensitive):
        rx_src = "|".join(branches)
        lower_hay = True
    else:
        rx_src = "|".join(
            b if cs else "(?i:%s)" % b
            for b, cs in zip(branches, case_sensitive))
        lower_hay = False
    rx = re.compile(rx_src, re.M)

    # Sample before scanning everything: a subsequence regex scan is only
    # cheaper than the per-line prefilter when it rejects the vast
    # majority of lines; plain literal scans are near-free and pay off at
    # much lower rejection rates.
    if True:
        n_lines = len(lines)
        chunk = 512
        hits = seen = 0
        for start_line in (0, n_lines // 2, n_lines - chunk):
            lo = max(0, start_line)
            sample = lines[lo:lo + chunk]
            seen += len(sample)
            stext = "\n".join(sample)
            shay = stext.lower() if lower_hay else stext
            pos = 0
            send = len(stext)
            while pos < send:
                m = rx.search(shay, pos)
                if m is None:
                    break
                le = stext.find("\n", m.end())
                if le < 0:
                    le = send
                hits += 1
                pos = le + 1
        threshold = 0.2 if has_fuzzy else 0.6
        if hits > seen * threshold:
            return None

    joined = "\n".join(lines)
    if lines and joined.count("\n") != len(lines) - 1:
        # Embedded newlines (e.g. --read0 records); line-boundary
        # bookkeeping would be wrong
        return None
    hay = joined.lower() if lower_hay else joined

    # Lines with non-ASCII content can match through normalization or
    # rune-wise lowering that the regex does not model; always keep them
    # as candidates (mirrors fzf's bytes-vs-runes split).
    non_ascii_idx = []
    if not joined.isascii():
        cur = 0
        scan = 0
        n = len(joined)
        na = re.compile("[^\x00-\x7f]")
        m = na.search(joined, 0)
        while m is not None:
            ls = joined.rfind("\n", 0, m.start()) + 1
            cur += joined.count("\n", scan, ls)
            le = joined.find("\n", m.end())
            if le < 0:
                le = n
            non_ascii_idx.append(cur)
            scan = le + 1
            cur += 1
            if scan > n:
                break
            m = na.search(joined, scan)

    out = []
    cur = 0
    scan = 0
    n = len(joined)

    # Two-stage scan for a single fuzzy term: a literal memchr-speed pass
    # over the rarest pattern character narrows the lines, and the linear
    # chain regex then verifies each candidate line in place. When the
    # rare character is too common the plain full scan below is used.
    rare = None
    if has_fuzzy and len(branches) == 1:
        term_text = best[0].text if pattern.extended else pattern.text
        counts = {c: hay.count(c) for c in set(term_text)}
        rare = min(counts, key=counts.get)
        if counts[rare] > len(lines) // 2:
            rare = None
    if rare is not None:
        chain_rx = re.compile(rx_src[1:] if rx_src.startswith("^")
                              else rx_src)
        match_at = chain_rx.match
        find = hay.find
        jfind = joined.find
        jrfind = joined.rfind
        jcount = joined.count
        pos = 0
        while True:
            p = find(rare, pos)
            if p < 0:
                break
            ls = jrfind("\n", 0, p) + 1
            le = jfind("\n", p)
            if le < 0:
                le = n
            if match_at(hay, ls, le):
                cur += jcount("\n", scan, ls)
                scan = ls
                out.append((cur, lines[cur]))
            pos = le + 1
    else:
        pos = 0
        while pos <= n:
            m = rx.search(hay, pos)
            if m is None:
                break
            ls = joined.rfind("\n", 0, m.start()) + 1
            cur += joined.count("\n", scan, ls)
            le = joined.find("\n", m.end())
            if le < 0:
                le = n
            out.append((cur, lines[cur]))
            scan = le + 1
            cur += 1
            pos = le + 1

    if non_ascii_idx:
        merged = dict(out)
        for i in non_ascii_idx:
            if i not in merged:
                merged[i] = lines[i]
        out = sorted(merged.items())
    return out


def _run(lines, query, fuzzy=True, extended=True, case="smart",
         normalize=True, algo="v2", scheme=None, nth=None,
         delimiter=None, tiebreak=None, sort=True, tac=False,
         with_positions=False):
    """Shared engine: returns (rank, index, text, score, positions) tuples
    in fzf's output order."""
    pattern, criteria = _prepare(query, fuzzy, extended, case, normalize,
                                 algo, scheme, nth, delimiter, tiebreak,
                                 with_positions)
    slab = _algo.Slab()
    if not isinstance(lines, (list, tuple)):
        lines = list(lines)

    # fzf streams (and therefore never sorts) only when sorting is disabled
    # and --tac is not given; otherwise the sortedness of the output is
    # decided by the pattern alone (a quirk faithfully replicated here:
    # `--no-sort --tac` still sorts unless the query is unsortable).
    streaming = not sort and not tac
    effective_sort = pattern.sortable and not streaming

    results = []
    if pattern.is_empty():
        for index, text in enumerate(lines):
            results.append((None, index, text, 0, None))
    else:
        scheme_obj = pattern.scheme
        forward = pattern.forward
        with_pos = pattern.with_pos

        pairs = _bulk_candidates(lines, pattern)
        if pairs is None:
            pairs = enumerate(lines)

        # The default criteria pair (score, length) needs neither offsets
        # nor the generic criteria loop; rank tuples are built inline.
        simple_rank = effective_sort and criteria == [BY_SCORE, BY_LENGTH]
        trim = _algo.trim_length

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
            for index, text in pairs:
                res, pos = pfun(term_cs, term_norm, forward, text, term_text,
                                with_pos, slab, scheme_obj)
                start = res[0]
                if start >= 0:
                    score = res[2]
                    if simple_rank:
                        sc = 0 if score < 0 else (65535 if score > 65535
                                                  else score)
                        tl = trim(text)
                        rank = (65535 - sc, tl if tl < 65535 else 65535)
                    elif effective_sort:
                        rank = build_rank(text, [(start, res[1])], score,
                                          criteria)
                    else:
                        rank = None
                    results.append((rank, index, text, score, pos))
        else:
            for index, text in pairs:
                item = Item(text, index)
                matched = pattern.match_item(item, slab)
                if matched is not None:
                    offsets, score, pos = matched
                    if simple_rank:
                        sc = 0 if score < 0 else (65535 if score > 65535
                                                  else score)
                        tl = trim(text)
                        rank = (65535 - sc, tl if tl < 65535 else 65535)
                    elif effective_sort:
                        rank = build_rank(text, offsets, score, criteria)
                    else:
                        rank = None
                    results.append((rank, index, text, score, pos))

    if effective_sort:
        if tac:
            results.sort(key=lambda r: (r[0], -r[1]))
        else:
            results.sort(key=lambda r: (r[0], r[1]))
    elif tac and not streaming:
        results.reverse()
    return results


def run_filter(lines, query, **options):
    """Match lines against the query exactly like `fzf --filter`.

    Returns the list of Match objects in fzf's output order.
    """
    return [Match(text, index, score, pos)
            for _rank, index, text, score, pos in _run(lines, query,
                                                       **options)]


def filter_lines(query, lines, **options):
    """Return the matching lines (str) in fzf's output order."""
    return [t[2] for t in _run(lines, query, **options)]
