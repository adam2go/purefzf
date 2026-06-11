#!/usr/bin/env python3
"""Session benchmark: repeated queries against one corpus.

Compares, on identical query sequences:
  - one-shot purefzf.filter per query (any installed version)
  - purefzf.Index (0.3.0+), where corpus preparation is amortized and
    incremental queries narrow through the verified-match cache
  - spawning the fzf binary per query (what a wrapper library does)

Outputs are md5-verified across all three before timing. Timing is the
median of --runs session passes over --rounds independent rounds.

Usage:
  python tools/bench_session.py [--a PYTHON --a-name NAME] [--b PYTHON ...]
                                [--fzf BIN] [--runs 7] [--rounds 3]
"""

import argparse
import hashlib
import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CHILD_SRC = r"""
import hashlib, json, sys, time
import purefzf

spec = json.load(open(sys.argv[1]))
lines = open(spec["corpus"], encoding="utf-8", errors="replace").read().split("\n")
if lines and lines[-1] == "":
    lines.pop()
queries = spec["queries"]
mode = spec["mode"]

def run_session():
    out = []
    if mode == "index":
        idx = purefzf.Index(lines)
        for q in queries:
            out.append("\n".join(idx.filter(q)))
    else:
        for q in queries:
            out.append("\n".join(purefzf.filter(q, lines)))
    return "\x00".join(out)

digest = hashlib.md5(run_session().encode("utf-8", "replace")).hexdigest()
times = []
for _ in range(spec["runs"]):
    t0 = time.perf_counter()
    run_session()
    times.append(time.perf_counter() - t0)
json.dump({"version": purefzf.__version__, "md5": digest, "times": times},
          sys.stdout)
"""

SESSIONS = [
    ("typing 'zsh cache' (9 steps)",
     ["z", "zs", "zsh", "zsh ", "zsh c", "zsh ca", "zsh cac", "zsh cach",
      "zsh cache"]),
    ("8 distinct queries",
     ["zsh", "foo bar", "ing$", "'tion", "^ab", "aBc", "qx", "hello"]),
    ("refine then backtrack (7 steps)",
     ["ab", "abc", "abcd", "abc", "ab", "ab e", "ab er"]),
]


def fzf_session(fzf_bin, corpus_path, queries, runs):
    import time as _t
    with open(corpus_path, "rb") as f:
        data = f.read()
    env = dict(os.environ, FZF_DEFAULT_OPTS="")

    def run_once():
        outs = []
        for q in queries:
            proc = subprocess.run([fzf_bin, "--filter", q], input=data,
                                  stdout=subprocess.PIPE, env=env)
            outs.append(proc.stdout.decode("utf-8", "replace").rstrip("\n"))
        return "\x00".join(outs)

    digest = hashlib.md5(run_once().encode("utf-8", "replace")).hexdigest()
    times = []
    for _ in range(runs):
        t0 = _t.perf_counter()
        run_once()
        times.append(_t.perf_counter() - t0)
    return digest, statistics.median(times)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--a", default="/tmp/venv020/bin/python")
    ap.add_argument("--a-name", default="0.2.0 one-shot")
    ap.add_argument("--b", default=sys.executable)
    ap.add_argument("--b-env", default="PYTHONPATH=%s" %
                    os.path.join(ROOT, "src"))
    ap.add_argument("--fzf", default=os.environ.get("FZF_BIN") or
                    shutil.which("fzf"))
    ap.add_argument("--runs", type=int, default=7)
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--json")
    args = ap.parse_args()

    tmpdir = tempfile.mkdtemp(prefix="purefzf-session-")
    corpus = os.path.join(tmpdir, "words.txt")
    shutil.copy("/usr/share/dict/words", corpus)

    b_env = {}
    for part in args.b_env.split(","):
        if part:
            k, _, v = part.partition("=")
            b_env[k] = v

    impls = [
        (args.a_name, [args.a], {}, "oneshot"),
        ("B one-shot", [args.b], b_env, "oneshot"),
        ("B Index", [args.b], b_env, "index"),
    ]
    have_fzf = args.fzf and os.path.exists(args.fzf)
    records = []

    name_w = max(len(n) for n, _c, _e, _m in impls)
    for title, queries in SESSIONS:
        print("%s  (%d queries x %d runs x %d rounds)" %
              (title, len(queries), args.runs, args.rounds))
        digests = {}
        rows = []
        for name, cmd, env_extra, mode in impls:
            spec = {"corpus": corpus, "queries": queries, "mode": mode,
                    "runs": args.runs}
            spec_path = os.path.join(tmpdir, "spec.json")
            with open(spec_path, "w") as f:
                json.dump(spec, f)
            env = dict(os.environ)
            env.update(env_extra)
            medians = []
            for _ in range(args.rounds):
                proc = subprocess.run(cmd + ["-c", CHILD_SRC, spec_path],
                                      stdout=subprocess.PIPE, env=env)
                res = json.loads(proc.stdout)
                digests.setdefault(name, res["md5"])
                medians.append(statistics.median(res["times"]) * 1000)
            rows.append((name, medians))
        ok = len(set(digests.values())) == 1
        fzf_note = ""
        fzf_ms = None
        if have_fzf:
            fzf_md5, fzf_med = fzf_session(args.fzf, corpus, queries,
                                           args.runs)
            fzf_ms = fzf_med * 1000
            fzf_note = "fzf spawn-per-query: %.1f ms%s" % (
                fzf_ms, "" if fzf_md5 == next(iter(digests.values()))
                else "  OUTPUT-DIFF")
        print("  outputs identical across impls: %s" % ok)
        for name, medians in rows:
            med = statistics.median(medians)
            print("  %-*s  %8.1f ms/session  %6.2f ms/query  (rounds %s)" %
                  (name_w + 9, name, med, med / len(queries),
                   "/".join("%.0f" % m for m in medians)))
        if fzf_note:
            print("  " + fzf_note)
        print()
        records.append({"session": title, "queries": queries,
                        "verified": ok,
                        "impls": {n: m for n, m in rows},
                        "fzf_ms": fzf_ms})
    if args.json:
        with open(args.json, "w") as f:
            json.dump({"runs": args.runs, "rounds": args.rounds,
                       "records": records}, f, indent=1)
        print("wrote %s" % args.json)


if __name__ == "__main__":
    main()
