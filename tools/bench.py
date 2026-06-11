#!/usr/bin/env python3
"""Benchmark purefzf against the fzf binary.

Every workload is first verified: the output of `purefzf --filter` must be
byte-identical to `fzf --filter` before any timing happens. Timings are the
median of N runs (default 7).

Usage:
    python tools/bench.py [--fzf /path/to/fzf] [--runs 7]
"""

import argparse
import os
import shutil
import statistics
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import purefzf  # noqa: E402


def build_workloads():
    workloads = []
    words_path = "/usr/share/dict/words"
    if os.path.exists(words_path):
        with open(words_path, "rb") as f:
            words = f.read()
        for query in ("ion", "zsh", "'tion", "^ab cd$ | ing$"):
            workloads.append(("words-%dk %r" % (words.count(b"\n") // 1000,
                                                query), words, query))

    paths = []
    for base in (os.path.dirname(os.__file__),):
        for dirpath, _d, filenames in os.walk(base):
            for fn in filenames:
                paths.append(os.path.join(dirpath, fn))
    data = ("\n".join(paths) + "\n").encode()
    for query in ("test", "pyini", "lib/site"):
        workloads.append(("paths-%dk %r" % (len(paths) // 1000, data, ),
                          data, query))
        workloads[-1] = ("paths-%dk %r" % (len(paths) // 1000, query),
                         data, query)
    return workloads


def time_runs(fn, runs):
    times = []
    for _ in range(runs):
        t0 = time.perf_counter()
        fn()
        times.append(time.perf_counter() - t0)
    return statistics.median(times)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fzf", default=os.environ.get("FZF_BIN") or
                    shutil.which("fzf"))
    ap.add_argument("--runs", type=int, default=7)
    args = ap.parse_args()
    have_fzf = args.fzf and os.path.exists(args.fzf)
    env = dict(os.environ, FZF_DEFAULT_OPTS="")

    print("purefzf %s  (Python %s)" % (purefzf.__version__,
                                       sys.version.split()[0]))
    if have_fzf:
        ver = subprocess.run([args.fzf, "--version"],
                             stdout=subprocess.PIPE).stdout.decode().strip()
        print("fzf %s at %s" % (ver, args.fzf))
    print()

    header = "%-28s %9s %10s %12s %12s %14s" % (
        "workload", "matches", "lines/s", "lib (ms)", "cli e2e (ms)",
        "fzf e2e (ms)")
    print(header)
    print("-" * len(header))

    for name, data, query in build_workloads():
        lines = data.decode("utf-8", "replace").splitlines()

        # --- verify before timing ---
        expected = None
        if have_fzf:
            fzf_out = subprocess.run([args.fzf, "--filter", query],
                                     input=data, stdout=subprocess.PIPE,
                                     env=env).stdout
            pure_out = subprocess.run(
                [sys.executable, "-m", "purefzf.cli", "--filter", query],
                input=data, stdout=subprocess.PIPE,
                env=dict(env, PYTHONPATH=os.path.join(ROOT, "src"))).stdout
            if fzf_out != pure_out:
                print("%-28s VERIFY FAILED -- skipping" % name)
                continue
            expected = fzf_out

        matches = purefzf.filter(query, lines)
        if expected is not None:
            assert ("\n".join(matches) + "\n" if matches else "") == \
                expected.decode("utf-8", "replace"), "library/CLI mismatch"

        lib_ms = time_runs(lambda: purefzf.filter(query, lines),
                           args.runs) * 1000

        cli_ms = time_runs(lambda: subprocess.run(
            [sys.executable, "-m", "purefzf.cli", "--filter", query],
            input=data, stdout=subprocess.DEVNULL,
            env=dict(env, PYTHONPATH=os.path.join(ROOT, "src"))),
            args.runs) * 1000

        if have_fzf:
            fzf_ms = time_runs(lambda: subprocess.run(
                [args.fzf, "--filter", query], input=data,
                stdout=subprocess.DEVNULL, env=env), args.runs) * 1000
            fzf_col = "%14.1f" % fzf_ms
        else:
            fzf_col = "%14s" % "n/a"

        print("%-28s %9d %10.0f %12.1f %12.1f %s" % (
            name, len(matches), len(lines) / (lib_ms / 1000), lib_ms, cli_ms,
            fzf_col))


if __name__ == "__main__":
    main()
