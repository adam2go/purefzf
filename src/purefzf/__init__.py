"""purefzf: a pure Python implementation of fzf's matching engine.

The scoring model, extended-search syntax, and result ordering are ported
from fzf v0.73.1 and verified against both fzf's unit tests and the fzf
binary itself (byte-identical `--filter` output on the differential suite).

Quick start::

    import purefzf

    # The non-interactive equivalent of `fzf --filter='qry'`
    purefzf.filter("qry", ["query.py", "quarry.rs", "manifest.json"])

    # Match objects with score / index / (optional) matched positions
    purefzf.matches("qry", lines, with_positions=True)

    # Low-level: score a single candidate
    purefzf.fuzzy_match_v2(False, False, True, "Quarry", "qry", True)
"""

__version__ = "0.1.0"

from .algo import (  # noqa: F401
    Slab,
    Scheme,
    fuzzy_match_v1,
    fuzzy_match_v2,
    exact_match_naive,
    exact_match_boundary,
    prefix_match,
    suffix_match,
    equal_match,
)
from .core import Match, run_filter  # noqa: F401
from .core import filter_lines as filter  # noqa: F401
from .pattern import build_pattern  # noqa: F401
from .tokenizer import Delimiter  # noqa: F401


def matches(query, lines, **options):
    """Match lines against the query; returns a list of Match objects in
    fzf's output order. Accepts the same options as `fzf --filter`:

    fuzzy=True, extended=True, case="smart" ("ignore"/"respect"),
    normalize=True, algo="v2" ("v1"), scheme=None ("default"/"path"/"history"),
    nth=None, delimiter=None, tiebreak=None, sort=True, tac=False,
    with_positions=False
    """
    return run_filter(lines, query, **options)
