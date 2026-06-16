# purefzf

[![CI](https://github.com/adam2go/purefzf/actions/workflows/ci.yml/badge.svg)](https://github.com/adam2go/purefzf/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/purefzf)](https://pypi.org/project/purefzf/)

A pure Python implementation of [fzf](https://github.com/junegunn/fzf)'s
matching engine: the FuzzyMatchV2 scoring algorithm, the extended-search
syntax, and the non-interactive `--filter` mode.

- **Zero dependencies, zero binaries.** `pip install purefzf` is the whole
  setup. Works where the fzf binary cannot: Pyodide/WASM, AWS Lambda,
  locked-down CI sandboxes, agent runtimes. Existing Python packages
  (pyfzf, iterfzf) are wrappers that spawn the fzf binary at runtime.
- **Verified against fzf, not "inspired by" it.** The scoring model is
  ported line-by-line from fzf v0.73.1 (commit `ce4bef75`) and checked
  three ways (see [Verification](#verification)):
  - fzf's own unit tests for the algorithm, pattern parser, and tokenizer,
    ported to pytest — **220 tests, all passing**;
  - differential testing against the real fzf binary — **5,237 of 5,240
    cases (99.94%) byte-identical output**, the remaining 3 traced to an
    fzf bug, not a porting gap ([details](#known-differences));
  - 6,000 randomized cross-checks between the optimized ASCII fast path
    and the direct port of the Go code.
- CPython 3.9–3.14 and PyPy, tested in CI.

## Usage

### CLI

`purefzf` is `fzf --filter`: it reads lines from stdin and prints the
matches in fzf's ranking order.

```console
$ git ls-files | purefzf -f 'tests py$'
tests/test_party.py
tests/test_display.py
$ history | purefzf -f '!sudo git' --tac | head -5
```

Supported flags (all with fzf semantics, including exit codes 0/1/2):
`-f/--filter`, `-e/--exact`, `+e`, `-x/--extended`, `+x`,
`-i/--ignore-case`, `+i`, `--smart-case`, `--literal`, `--algo=v1|v2`,
`--scheme=default|path|history`, `-n/--nth`, `-d/--delimiter`,
`--tiebreak`, `+s/--no-sort`, `--tac`, `--read0`, `--print0`,
`--print-query`.

### Library

```python
import purefzf

# the equivalent of `fzf --filter='qry'`: matched lines in fzf order
purefzf.filter("qry", ["query.py", "quarry.rs", "manifest.json"])
# -> ['query.py', 'quarry.rs']

# same options as the CLI
purefzf.filter("conf", lines, nth="2..", delimiter=":", tiebreak="begin")

# Match objects with score, original index, and matched positions
for m in purefzf.matches("qry", lines, with_positions=True):
    print(m.score, m.text, m.positions)

# Repeated queries against one corpus: build an Index once.
# Corpus preparation is amortized, and incremental queries are narrowed
# to the verified match set of the previous, broader query
# ('z' -> 'zs' -> 'zsh' runs in fractions of a millisecond per step).
idx = purefzf.Index(lines)
idx.filter("zs")
idx.filter("zsh")            # same options as purefzf.filter
idx.matches("zsh c", with_positions=True)

# low-level: score a single candidate
# (case_sensitive, normalize, forward, text, pattern, with_pos)
result, positions = purefzf.fuzzy_match_v2(
    False, True, True, "src/QueryBuilder.py", "qry", True)
result     # (start, end, score) -> (4, 9, 66)
positions  # [8, 7, 4]
```

The extended-search syntax works exactly as in fzf: `term1 term2` (AND),
`a | b` (OR), `'exact`, `'boundary'`, `^prefix`, `suffix$`, `^equal$`,
`!negation`, smart-case, and latin-script normalization (`danco` matches
`Danço`; disable with `--literal`).

## Scope

purefzf implements the **matching engine and filter mode**, not the
interactive terminal UI. Not included (today): the TUI, previews,
key bindings, `--with-nth`/`--accept-nth` output transforms, ANSI color
processing, and multi-threaded matching. The algorithm layer is complete,
so a TUI built on top of it only needs a terminal frontend.

## Verification

All three layers run in CI on every commit; the differential layer runs on
any machine that has an fzf binary (development-time only — purefzf itself
never shells out).

1. **fzf's official unit tests, ported.** Every assertion from
   `algo_test.go`, `pattern_test.go`, and `tokenizer_test.go` at v0.73.1
   is reproduced in `tests/` with the same inputs and expected scores.
   The chunk-cache tests are intentionally out of scope (the cache is an
   interactive-typing optimization, not part of matching semantics).

   ```console
   $ python -m pytest        # 220 passed
   ```

2. **Differential testing against the fzf binary.** `tools/diff_fzf.py`
   compares `purefzf --filter` with `fzf --filter` byte by byte across
   8 corpora (dictionary words, file paths, source code, structured
   fields, unicode, empty/whitespace/200KB lines, invalid UTF-8) ×
   30 option sets × up to 37 queries:

   ```console
   $ FZF_BIN=$(which fzf) python tools/diff_fzf.py
   5237/5240 byte-identical (99.94%), 3 known divergences (fzf slab reuse, see README), 0 unexplained
   ```

3. **Fast-path cross-checking.** The optimized ASCII path must produce
   bit-identical results (score, offsets, positions) to the direct port
   of the Go code on 6,000 randomized inputs per run.

## Known differences

The complete list — anything not listed here that differs from
`fzf --filter` is a bug, please report it:

1. **fzf's position backtrace reads recycled memory; purefzf doesn't.**
   fzf reuses per-worker score buffers ("slabs") without zeroing, and the
   `preferMatch` heuristic in FuzzyMatchV2's backtrace can read cells the
   current match never wrote. With `--tiebreak=chunk` or
   `--scheme=path`/`--tiebreak=pathname` (the modes that depend on exact
   match positions), the same line can therefore rank differently in fzf
   depending on what the worker processed before it — feed fzf one stream
   containing a duplicated line and the two copies can come out ranked
   apart. purefzf behaves like fzf with a freshly zeroed slab: on isolated
   input the two agree exactly (this is how the 3 differential failures
   above were verified). 
2. **Regex delimiters use Python `re`, not Go RE2.** `--delimiter` accepts
   a regex when it isn't a literal string; the two dialects agree on
   everything typical (`[0-9]+`, `\s+`, `\t`) but are not identical at
   the edges (e.g. RE2 has no backreferences, `re` has no `\p{...}`).
3. **`--tiebreak=pathname` offsets count runes, not bytes.** fzf compares
   a byte offset with a rune offset when locating the last path separator;
   for non-ASCII paths purefzf uses rune offsets consistently.

## Performance

On selective queries — the typical case for an agent or a script —
purefzf 0.2.0 is **in the same league as the fzf binary itself**: a query
like `zsh` over the 235k-word dictionary takes 12 ms in-process vs 11 ms
for spawning fzf, and a no-match query is faster than the spawn. The
trick is a bulk prefilter: the corpus is joined once and scanned with a
single backtrack-free regex (plus a memchr-speed pass over the rarest
pattern character), so per-line Python work only happens on candidate
lines. Match-heavy queries are bounded by the scoring DP and stay
10–20× slower than Go.

### Experiment: 0.1.0 → 0.2.0

Both versions run in separate subprocesses on the same corpora
(0.1.0 installed from PyPI, 0.2.0 from the working tree); for every
workload the two outputs are first verified identical (md5), then each
version is timed as the median of 7 runs, repeated in 3 independent
rounds — the table shows the median round with min–max spread across
rounds. The full matrix was run three times end-to-end (the third with the
0.3.0 tree); per-workload speedups agree within 3% across runs. Environment: Apple M4 Pro, macOS 26.5,
CPython 3.12.7, fzf 0.73.1 as the end-to-end reference (its column
includes process spawn; the purefzf columns are in-process library
calls — that is the embedding scenario purefzf exists for).

| workload                      | matches | 0.1.0 ms       | 0.2.0 ms      | speedup | fzf e2e |
|-------------------------------|--------:|---------------:|--------------:|--------:|--------:|
| 235k words, `zsh`             |      62 |   73 (73–73)   |  12 (12–13)   |  5.9×   |   11 ms |
| 235k words, `ion`             |  16,443 |  144 (143–144) |  90 (89–93)   |  1.6×   |   21 ms |
| 235k words, `q`               |   3,641 |   77 (76–77)   |  13 (13–14)   |  5.7×   |   13 ms |
| 235k words, `'tion`           |   7,422 |  176 (175–177) |  31 (31–32)   |  5.7×   |   16 ms |
| 235k words, `^ab`             |     666 |  160 (160–161) |  17 (17–17)   |  9.2×   |   12 ms |
| 235k words, `ing$`            |   5,540 |  181 (181–183) |  24 (24–25)   |  7.5×   |   15 ms |
| 235k words, `foo bar`         |      16 |  255 (255–256) |  20 (20–20)   | 12.6×   |   13 ms |
| 235k words, `^ab cd$ \| ing$` |       5 |  399 (398–400) |  24 (24–24)   | 16.7×   |   13 ms |
| 235k words, `aBc` (no match)  |       0 |   80 (79–80)   |   8 (8–8)     |  9.6×   |   12 ms |
| 108k paths, `test`            | 104,348 | 1111 (1105–1138) | 1028 (1016–1034) | 1.1× | 73 ms |
| 108k paths, `pyini`           |  60,575 |  610 (606–612) | 560 (557–563) |  1.1×   |   49 ms |
| 108k paths, `lib/site`        | 107,156 | 1822 (1814–1836) | 1726 (1722–1740) | 1.1× | 82 ms |
| 6k unicode, `danco`           |   3,000 |   17 (17–17)   |  18 (18–18)   |  0.96×  |    6 ms |
| 100k fields, `user1 group3` + `--nth=1,2` | 5,029 | 509 (507–510) | 503 (503–505) | 1.0× | 18 ms |

Raw data for both end-to-end runs:
[`bench/0.2.0-vs-0.1.0.json`](bench/0.2.0-vs-0.1.0.json),
[`bench/0.2.0-vs-0.1.0-run2.json`](bench/0.2.0-vs-0.1.0-run2.json).
Reproduce with:

```console
$ python -m venv /tmp/v && /tmp/v/bin/pip install purefzf==0.1.0
$ FZF_BIN=$(which fzf) python tools/bench_ab.py --a /tmp/v/bin/python --a-name 0.1.0
$ FZF_BIN=$(which fzf) python tools/bench.py   # lib vs CLI vs fzf end-to-end
```

What the numbers say:

- **Selective and multi-term queries got 5.7–16.7× faster.** The bulk
  prefilter rejects non-candidate lines at C speed, so cost now scales
  with the result size more than the corpus size. Fuzzy terms compile to
  an anchored greedy chain (`^[^\na]*a[^\nb]*b…`) whose every step has
  exactly one outcome — linear worst case by construction (a 200KB
  degenerate line is a regression test).
- **Match-heavy queries improved only 6–9%.** When 96% of 108k lines
  match, the time goes to the Smith-Waterman scoring of every line; that
  is the algorithm, not overhead. Every match needs exactly one
  optimal-alignment DP to get its score, so on CPython this is the floor
  (profiling puts 76% of the time inside the scoring loop's arithmetic).
  This is the workload where the Go binary keeps its 10–20× lead — and
  where **PyPy is the real lever** (see below).
- **Small corpora are unchanged by design** (the prefilter only engages
  at ≥4,096 lines; below that the per-line path is already sub-ms), and
  `--nth` queries bypass the prefilter because field reordering
  invalidates per-line necessary conditions.
- The CLI adds ~25 ms of interpreter startup over the library numbers;
  `fzf e2e` includes its own ~5 ms process spawn.

### Experiment: repeated queries with `Index` (0.3.0)

`purefzf.Index` pre-joins the corpus once and caches verified match sets
for query shapes where narrowing is provable (single-term AND groups of
fuzzy / `'exact` / `^prefix` terms; suffix/equal/boundary anchors,
inverse terms, OR groups, and `--nth` always bypass the cache). The
cache only ever shrinks the candidate set — every result is recomputed
by the real match functions, which is why the outputs below could be
md5-verified identical across all implementations and fzf itself.

Same methodology as above (subprocess isolation, verify-then-time,
median of 7 session passes × 3 rounds), 235k-word corpus:

| session                          | 0.2.0 one-shot | 0.3.0 one-shot | 0.3.0 Index | fzf spawn/query |
|----------------------------------|---------------:|---------------:|------------:|----------------:|
| typing `zsh cache` (9 steps)     |  209 ms        |  151 ms        |  **26 ms**  |  111 ms         |
| 8 unrelated queries              |  149 ms        |  148 ms        |  117 ms     |   98 ms         |
| refine + backtrack (7 steps)     | 1038 ms        |  300 ms        | **170 ms**  |  105 ms         |

Per query, the typing session drops from 23.2 ms to **2.9 ms** — once
narrowed, each keystroke costs 0.01–2.8 ms, which is faster than
spawning the fzf binary for every keystroke (12.3 ms/query) by 4×.
The one-shot improvement between 0.2.0 and 0.3.0 comes from a sampling
fix the session work surfaced: the prefilter's selectivity estimate now
uses seven evenly spread windows instead of three (a sorted dictionary's
tail is all z-words, which biased the old estimate and disabled the
prefilter for queries like `z`: 78 ms → 20 ms).
Raw data for both runs:
[`bench/0.3.0-sessions.json`](bench/0.3.0-sessions.json),
[`bench/0.3.0-sessions-run2.json`](bench/0.3.0-sessions-run2.json).

```console
$ FZF_BIN=$(which fzf) python tools/bench_session.py
```

### Need more throughput? Run on PyPy

The match-heavy floor is a *CPython* floor: the scoring DP is exactly the
kind of tight arithmetic loop PyPy's JIT compiles to near-native speed.
Same 235k-word corpus, identical match counts, warm steady state (median
of 9 runs after warmup; CPython 3.12.7 vs PyPy 7.3.20 / 3.11):

| query (matches)        | CPython warm | PyPy warm | speedup |
|------------------------|-------------:|----------:|--------:|
| `zsh` (62)             |    13.6 ms   |   7.4 ms  |  1.8×   |
| `q` (3,641)            |    14.3 ms   |   6.8 ms  |  2.1×   |
| `'tion` (7,422)        |    31.9 ms   |  11.3 ms  |  2.8×   |
| `^ab cd$ \| ing$` (5)  |    24.3 ms   |   7.0 ms  |  3.4×   |
| `ion` (16,443)         |    91.7 ms   |  44.8 ms  |  2.0×   |
| `tion` (9,048)         |    73.8 ms   |  32.7 ms  |  2.3×   |
| `e` (158,127, heaviest)|   262 ms     | 130 ms    |  2.0×   |

So the workload that benefits *least* from the prefilter benefits *most*
from the JIT — PyPy roughly halves the heaviest match-bound query and
gives up to 3.4× on multi-term ones. **Caveat, measured honestly:** PyPy
pays JIT compilation on the first call (cold `ion`: 102 ms on PyPy vs
92 ms on CPython), so it helps a long-running embedding (a server or
agent reusing the interpreter — the `Index` scenario) but *not* a single
one-shot CLI invocation. purefzf is CI-tested on PyPy on every commit.
Raw data: [`bench/pypy-cpython.json`](bench/pypy-cpython.json),
[`bench/pypy-pypy.json`](bench/pypy-pypy.json).

```console
$ python3   tools/bench_pypy.py --label CPython
$ pypy3     tools/bench_pypy.py --label PyPy
```

### What is *not* worth optimizing (measured, then dropped)

For the record, two CPython micro-optimizations were prototyped, measured
rigorously, and reverted because the gain did not justify the complexity
on byte-verified code: skipping the terminal DP row's write-only buffers
(<1% — the gap-fill slice stores are already rare and C-speed) and
specializing short patterns. The DP arithmetic itself is the cost, and
the honest answer there is PyPy, not uglier CPython code. multiprocessing
would parallelize match-heavy scans across cores but is deliberately not
bundled: process-spawn and corpus-serialization overhead only pay off on
very large corpora, and the platform-specific complexity is not worth it
for the common case.

## How the port stays faithful

The matcher is ported function-by-function from `src/algo/algo.go`,
`pattern.go`, `tokenizer.go`, and `result.go` at fzf v0.73.1, including
the parts that are easy to get subtly wrong: the bonus matrix and scheme
tables, camelCase/number boundary bonuses, the consecutive-chunk bonus
rules, first-char multiplier, the `--no-sort --tac` sorting quirk, the
streaming vs. collected output paths, smart-case and per-term
normalization decisions, the exact V2→V1 fallback thresholds
(`N×M > 102400` or `M > 1000`), tiebreak rank encoding, and Go's
per-byte U+FFFD substitution for invalid UTF-8.

## Changelog

Version history is in [CHANGELOG.md](CHANGELOG.md).

## License

MIT. The algorithm and its test suite are ported from
[junegunn/fzf](https://github.com/junegunn/fzf) (MIT licensed) — all
credit for the matching algorithm's design belongs there.
