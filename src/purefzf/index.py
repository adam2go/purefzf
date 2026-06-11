"""Session API: a pre-indexed corpus for repeated queries.

Index amortizes the per-call corpus preparation (joining, lowering,
sample windows, non-ASCII bookkeeping) and -- for query shapes where it
is provably safe -- narrows each query to the verified match set of a
previous, broader query. Results are always produced by the real match
functions; the cache only shrinks the candidate set, never substitutes
for matching, so Index output is identical to the one-shot API.

Narrowing safety. A cached entry for query Q may seed query Q' only when
matches(Q') is guaranteed to be a subset of matches(Q). That holds
per-term for:

- fuzzy terms: extending the term extends the required subsequence, so
  any line matching the longer term matches the shorter one;
- 'exact terms: an occurrence of the longer substring contains an
  occurrence of its prefix;
- ^prefix terms: a longer line prefix implies the shorter one.

Appending whole AND-terms also narrows (every AND-set must match). All
other shapes -- suffix$/^equal$/'boundary' terms (extension moves the
anchor), inverse terms, OR groups, --nth field scoping -- bypass the
cache entirely.

Case folding: a case-insensitive cached term (stored lowercase) may seed
a case-sensitive extension because a sensitive match implies the same
characters match insensitively after lowering. The reverse direction is
rejected by the raw prefix comparison.
"""

from array import array

from . import core as _core
from .core import Match, _Corpus, _prepare, _run_prepared
from .pattern import TERM_EXACT, TERM_FUZZY, TERM_PREFIX

_RUN_DEFAULTS = {
    "fuzzy": True,
    "extended": True,
    "case": "smart",
    "normalize": True,
    "algo": "v2",
    "scheme": None,
    "nth": None,
    "delimiter": None,
    "tiebreak": None,
    "sort": True,
    "tac": False,
    "with_positions": False,
}

# Options that can change which lines match (not just their order); they
# partition the narrowing cache into independent buckets.
_SEMANTIC_OPTS = ("fuzzy", "extended", "case", "normalize", "algo", "scheme")

_NARROWING_TYPES = (TERM_FUZZY, TERM_EXACT, TERM_PREFIX)

_MAX_BUCKET_ENTRIES = 32
_MAX_CACHED_INDICES = 2_000_000


class Index:
    """A reusable snapshot of the input lines for repeated queries.

    >>> idx = purefzf.Index(lines)
    >>> idx.filter("zs")
    >>> idx.filter("zsh")     # narrowed to the verified 'zs' match set

    The constructor copies the line list; later mutation of the source
    iterable does not affect the Index. Instances are not thread-safe.
    """

    def __init__(self, lines, cache=True):
        self._corpus = _Corpus(list(lines))
        self._cache_enabled = cache
        self._buckets = {}
        self._cached_indices = 0

    def __len__(self):
        return len(self._corpus.lines)

    @property
    def lines(self):
        return self._corpus.lines

    def filter(self, query, **options):
        """Like purefzf.filter(query, lines, **options)."""
        return [t[2] for t in self._execute(query, options)]

    def matches(self, query, **options):
        """Like purefzf.matches(query, lines, **options)."""
        return [Match(text, index, score, pos)
                for _rank, index, text, score, pos
                in self._execute(query, options)]

    def _execute(self, query, options):
        unknown = set(options) - set(_RUN_DEFAULTS)
        if unknown:
            raise TypeError("unknown options: %s" % ", ".join(sorted(unknown)))
        opts = dict(_RUN_DEFAULTS)
        opts.update(options)

        pattern, criteria = _prepare(
            query, opts["fuzzy"], opts["extended"], opts["case"],
            opts["normalize"], opts["algo"], opts["scheme"], opts["nth"],
            opts["delimiter"], opts["tiebreak"], opts["with_positions"])

        terms_key = None
        bucket = None
        candidate_pairs = None
        if self._cache_enabled:
            terms_key = self._narrowing_key(pattern)
            if terms_key is not None:
                bucket_key = tuple(opts[k] for k in _SEMANTIC_OPTS)
                bucket = self._buckets.setdefault(bucket_key, {})
                ancestor = self._best_ancestor(bucket, terms_key)
                if ancestor is not None:
                    candidate_pairs = self._narrowed_pairs(
                        bucket[ancestor], pattern)

        results = _run_prepared(self._corpus, pattern, criteria,
                                opts["sort"], opts["tac"],
                                candidate_pairs=candidate_pairs)

        if bucket is not None:
            indices = array("I", sorted(r[1] for r in results))
            if len(indices) <= len(self._corpus.lines) // 3 or \
                    len(self._corpus.lines) < 3:
                self._store(bucket, terms_key, indices)
        return results

    def _narrowed_pairs(self, indices, pattern):
        """Candidate (index, line) pairs for a cached ancestor match set.
        Large subsets go through the same C-speed bulk prefilter as full
        corpora (on a sub-corpus, with indices remapped); small ones are
        cheaper to verify directly."""
        lines = self._corpus.lines
        if len(indices) > 512:
            sub = _Corpus([lines[i] for i in indices])
            local = _core._bulk_candidates(sub, pattern, min_lines=1024)
            if local is not None:
                return [(indices[li], text) for li, text in local]
        return ((i, lines[i]) for i in indices)

    @staticmethod
    def _narrowing_key(pattern):
        """The cache key for the query, or None when the query shape does
        not support narrowing."""
        if not pattern.extended or pattern.nth or not pattern.term_sets:
            return None
        key = []
        for term_set in pattern.term_sets:
            if len(term_set) != 1:
                return None  # OR group
            term = term_set[0]
            if term.inv or term.typ not in _NARROWING_TYPES:
                return None
            key.append((term.typ, term.text, term.case_sensitive))
        return tuple(key)

    @staticmethod
    def _narrows(ancestor, new):
        """True when matches(new) is provably a subset of matches(ancestor)."""
        if len(ancestor) > len(new):
            return False
        for (anc_typ, anc_text, anc_cs), (new_typ, new_text, new_cs) in \
                zip(ancestor, new):
            if anc_typ != new_typ:
                return False
            if anc_cs:
                if not (new_cs and new_text.startswith(anc_text)):
                    return False
            else:
                if not new_text.lower().startswith(anc_text):
                    return False
        return True

    def _best_ancestor(self, bucket, terms_key):
        best = None
        best_len = -1
        for key in bucket:
            if self._narrows(key, terms_key):
                total = sum(len(text) for _typ, text, _cs in key)
                if total > best_len:
                    best, best_len = key, total
        return best

    def _store(self, bucket, terms_key, indices):
        if terms_key in bucket:
            self._cached_indices -= len(bucket.pop(terms_key))
        bucket[terms_key] = indices
        self._cached_indices += len(indices)
        while len(bucket) > _MAX_BUCKET_ENTRIES or \
                self._cached_indices > _MAX_CACHED_INDICES:
            oldest = next(iter(bucket))
            self._cached_indices -= len(bucket.pop(oldest))
            if not bucket:
                break
