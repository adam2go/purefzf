# Changelog

All notable changes to purefzf are documented here. Versions follow
[semantic versioning](https://semver.org/). Every release is verified
against fzf v0.73.1 (commit `ce4bef75`) — see the README for the full
test methodology.

## 0.3.1

Documentation and tooling only — no library code changed, so output and
behavior are identical to 0.3.0.

- **Measured and documented PyPy throughput.** The match-heavy "floor" is
  CPython-specific: the scoring DP is a tight arithmetic loop the PyPy JIT
  compiles to near-native speed. New `tools/bench_pypy.py` measures
  CPython vs PyPy on an identical corpus; warm steady-state speedups are
  **1.8–3.4×** (2.0× on the heaviest 158k-match query), with an honest
  cold-start caveat: PyPy's first call pays JIT compilation, so it helps a
  long-running embedding (a server or agent reusing the interpreter — the
  `Index` scenario), not a single one-shot CLI invocation. Raw data under
  `bench/`.
- Recorded two CPython micro-optimizations that were prototyped, measured,
  and **reverted** because the gain did not justify added complexity on
  byte-verified code (terminal-DP-row buffer skip: <1%; short-pattern
  specialization), plus why multiprocessing is deliberately not bundled.

## 0.3.0

- **`purefzf.Index(lines)`: a reusable corpus snapshot for repeated
  queries.** Per-call preparation (joining, case folding, sample windows,
  non-ASCII bookkeeping) is computed once. Queries whose shape provably
  narrows — single-term AND groups of fuzzy / `'exact` / `^prefix` terms —
  are seeded with the verified match set of the broadest cached ancestor
  and then re-verified by the real match functions. Suffix/equal/boundary
  anchors, inverse terms, OR groups, and `--nth` bypass the cache. The
  cache only ever shrinks the candidate set, so `Index` output is
  identical to the one-shot API (md5-verified in the experiments).
- **Up to 8× faster incremental query sessions.** Typing `zsh cache` one
  key at a time drops from 23.2 ms/query to 2.9 ms/query — 4× faster than
  spawning the fzf binary per keystroke.
- Fixed a prefilter sampling bias the session work surfaced: selectivity
  is now estimated from seven evenly spread windows instead of
  first/middle/last, which misread sorted corpora (a dictionary's tail is
  all z-words) and disabled the prefilter for queries like `z`
  (one-shot 78 ms → 20 ms).
- 27 new `Index` session-equivalence tests (220 total).

## 0.2.0

- **5.7–16.7× faster selective and multi-term queries.** A bulk prefilter
  joins the corpus once and scans it with a single backtrack-free regex
  built from the most selective AND-set; fuzzy terms compile to an
  anchored greedy chain (`^[^\na]*a[^\nb]*b…`) with guaranteed linear
  worst-case time, plus a memchr-speed pass over the rarest pattern
  character. Per-line Python work now happens only on candidate lines.
- ASCII fast paths for whitespace trimming and prefix/suffix/equal
  matching; inline rank tuples for the default tiebreak; no `Match`-object
  construction on the CLI hot path.
- Differential matrix grew to 5,240 cases (`--read0` set added); a 200KB
  degenerate line is a quadratic-blowup regression test.

## 0.1.0

- Initial release: a faithful pure-Python port of fzf's matching engine
  at v0.73.1 — FuzzyMatchV2/V1, exact/boundary/prefix/suffix/equal match,
  the extended-search syntax, smart-case, and latin-script normalization.
- Non-interactive `--filter` CLI with fzf semantics (`--nth`,
  `--delimiter`, `--tiebreak`, `--scheme`, `--algo`, `--tac`,
  `--read0`/`--print0`, exit codes 0/1/2) and a library API
  (`purefzf.filter` / `purefzf.matches` / `purefzf.fuzzy_match_v2`).
- Verified: 179 ported upstream unit tests; 4,978/4,981 (99.94%)
  byte-identical differential cases against the fzf binary, the 3
  divergences root-caused to an fzf slab-reuse bug. Zero runtime
  dependencies, CPython 3.9–3.14 + PyPy.
