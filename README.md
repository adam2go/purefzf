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
    ported to pytest — **179 tests, all passing**;
  - differential testing against the real fzf binary — **4,978 of 4,981
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
   $ python -m pytest        # 179 passed
   ```

2. **Differential testing against the fzf binary.** `tools/diff_fzf.py`
   compares `purefzf --filter` with `fzf --filter` byte by byte across
   8 corpora (dictionary words, file paths, source code, structured
   fields, unicode, empty/whitespace/200KB lines, invalid UTF-8) ×
   29 option sets × up to 37 queries:

   ```console
   $ FZF_BIN=$(which fzf) python tools/diff_fzf.py
   4978/4981 byte-identical (99.94%), 3 known divergences (fzf slab reuse, see README), 0 unexplained
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

Honest numbers: the fzf binary is a multi-threaded Go program and remains
5–25× faster end-to-end. purefzf is for environments where that binary
cannot run, and it is fast enough to be practical — selective queries
scan **1.3–3.2M lines/s** in-process on CPython 3.12 (Apple M-series):

| workload                        | matches | purefzf lib | purefzf CLI | fzf binary |
|---------------------------------|--------:|------------:|------------:|-----------:|
| 235k words, `ion`               |  16,443 |      145 ms |      170 ms |      21 ms |
| 235k words, `zsh`               |      62 |       75 ms |      101 ms |      12 ms |
| 235k words, `'tion`             |   7,422 |      180 ms |      205 ms |      16 ms |
| 235k words, `^ab cd$ \| ing$`   |       5 |      405 ms |      434 ms |      13 ms |
| 108k paths, `test`              | 104,331 |     1.11 s  |     1.14 s  |      75 ms |
| 108k paths, `pyini`             |  60,566 |      610 ms |      650 ms |      48 ms |
| 108k paths, `lib/site`          | 107,135 |     1.85 s  |     1.88 s  |      82 ms |

Median of 7 runs; every workload's output is verified byte-identical to
the fzf binary before it is timed. Reproduce with:

```console
$ FZF_BIN=$(which fzf) python tools/bench.py
```

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

## License

MIT. The algorithm and its test suite are ported from
[junegunn/fzf](https://github.com/junegunn/fzf) (MIT licensed) — all
credit for the matching algorithm's design belongs there.
