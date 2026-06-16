#!/usr/bin/env python3
"""Measure purefzf throughput on CPython vs PyPy on an identical corpus.

PyPy's JIT needs warmup, so this reports steady-state (warm) timings --
the right measure for a long-running embedding (a server or agent that
reuses the interpreter across many queries). It also reports the cold
first-call time, because for a single one-shot CLI invocation PyPy can be
slower than CPython (the JIT has not compiled the hot loops yet).

Run the SAME command under each interpreter and diff the output, e.g.:

    python3            tools/bench_pypy.py --label CPython --json cp.json
    /path/to/pypy3     tools/bench_pypy.py --label PyPy    --json pp.json

The corpus is /usr/share/dict/words (identical bytes across interpreters,
so match counts line up). Both must produce the same matches; this script
prints the count so you can confirm.
"""

import argparse
import json
import sys
import time

# Selectivity ladder; the DP-bound queries (many real matches, each
# needing one optimal-alignment score) are where the JIT helps most.
QUERIES = ["zsh", "q", "'tion", "^ab cd$ | ing$", "ion", "tion", "e"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default=sys.implementation.name)
    ap.add_argument("--corpus", default="/usr/share/dict/words")
    ap.add_argument("--warmup", type=int,
                    default=12 if sys.implementation.name == "pypy" else 2)
    ap.add_argument("--runs", type=int, default=9)
    ap.add_argument("--json")
    args = ap.parse_args()

    import purefzf

    with open(args.corpus, encoding="utf-8", errors="replace") as f:
        lines = f.read().split("\n")
    if lines and lines[-1] == "":
        lines.pop()

    print("%s (%s %s) on %d lines" % (
        args.label, sys.implementation.name,
        ".".join(map(str, sys.version_info[:3])), len(lines)))
    print("%-18s %9s %10s %10s" % ("query", "matches", "cold ms", "warm ms"))
    records = []
    for q in QUERIES:
        t0 = time.perf_counter()
        out = purefzf.filter(q, lines)
        cold = (time.perf_counter() - t0) * 1000
        for _ in range(args.warmup):
            purefzf.filter(q, lines)
        times = []
        for _ in range(args.runs):
            t0 = time.perf_counter()
            purefzf.filter(q, lines)
            times.append(time.perf_counter() - t0)
        times.sort()
        warm = times[len(times) // 2] * 1000
        print("%-18r %9d %10.1f %10.1f" % (q, len(out), cold, warm))
        records.append({"query": q, "matches": len(out),
                        "cold_ms": cold, "warm_ms": warm})

    if args.json:
        with open(args.json, "w") as f:
            json.dump({"label": args.label,
                       "implementation": sys.implementation.name,
                       "version": ".".join(map(str, sys.version_info[:3])),
                       "corpus_lines": len(lines), "records": records}, f,
                      indent=1)
        print("wrote %s" % args.json)


if __name__ == "__main__":
    main()
