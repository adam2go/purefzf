#!/usr/bin/env python3
"""A/B benchmark: compare two purefzf installations (e.g. the released
baseline vs the working tree) and the fzf binary on identical workloads.

Methodology:
- every implementation runs in its own subprocess (no shared state);
- for each workload the outputs of A, B, and fzf are first checked to be
  identical (md5 over the joined output lines); only then is it timed;
- each (impl, workload) is timed `--runs` times per round (median taken),
  for `--rounds` independent rounds; the table reports the median of the
  per-round medians and the min/max across rounds.

Usage:
  python tools/bench_ab.py \
      --a /tmp/venv010/bin/python --a-name 0.1.0 \
      --b python3 --b-env PYTHONPATH=src --b-name dev \
      [--fzf /path/to/fzf] [--runs 7] [--rounds 3] [--json out.json]
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
opts = spec.get("opts") or {}
query = spec["query"]

out = purefzf.filter(query, lines, **opts)
digest = hashlib.md5("\n".join(out).encode("utf-8", "replace")).hexdigest()

times = []
for _ in range(spec["runs"]):
    t0 = time.perf_counter()
    purefzf.filter(query, lines, **opts)
    times.append(time.perf_counter() - t0)

json.dump({"version": purefzf.__version__, "n": len(out),
           "md5": digest, "times": times}, sys.stdout)
"""


def build_corpora(tmpdir):
    corpora = {}

    words = "/usr/share/dict/words"
    if os.path.exists(words):
        dst = os.path.join(tmpdir, "words.txt")
        shutil.copy(words, dst)
        corpora["words-235k"] = dst

    paths = []
    for base in (os.path.dirname(os.__file__),):
        for dirpath, _d, fns in os.walk(base):
            for fn in fns:
                paths.append(os.path.join(dirpath, fn))
    dst = os.path.join(tmpdir, "paths.txt")
    with open(dst, "w") as f:
        f.write("\n".join(paths) + "\n")
    corpora["paths-%dk" % (len(paths) // 1000)] = dst

    uni = ["Só Danço Samba %d" % i for i in range(3000)] + \
          ["plain ascii line %d" % i for i in range(3000)]
    dst = os.path.join(tmpdir, "unicode.txt")
    with open(dst, "w") as f:
        f.write("\n".join(uni) + "\n")
    corpora["unicode-6k"] = dst

    rows = ["user%03d:group%d:home/dir%d/file%d.txt" % (i % 500, i % 7,
                                                        i % 13, i)
            for i in range(100000)]
    dst = os.path.join(tmpdir, "fields.txt")
    with open(dst, "w") as f:
        f.write("\n".join(rows) + "\n")
    corpora["fields-100k"] = dst

    return corpora


WORKLOADS = [
    # (corpus key, query, options, fzf extra args or None if incomparable)
    ("words-235k", "zsh", {}, []),
    ("words-235k", "ion", {}, []),
    ("words-235k", "q", {}, []),
    ("words-235k", "'tion", {}, []),
    ("words-235k", "^ab", {}, []),
    ("words-235k", "ing$", {}, []),
    ("words-235k", "foo bar", {}, []),
    ("words-235k", "^ab cd$ | ing$", {}, []),
    ("words-235k", "aBc", {}, []),
    ("paths-124k", "test", {}, []),
    ("paths-124k", "pyini", {}, []),
    ("paths-124k", "lib/site", {}, []),
    ("unicode-6k", "danco", {}, []),
    ("fields-100k", "user1 group3", {"nth": "1,2", "delimiter": ":"},
     ["--nth=1,2", "--delimiter=:"]),
]


def run_child(python_cmd, env_extra, spec, tmpdir):
    spec_path = os.path.join(tmpdir, "spec.json")
    with open(spec_path, "w") as f:
        json.dump(spec, f)
    env = dict(os.environ)
    env.update(env_extra)
    proc = subprocess.run(python_cmd + ["-c", CHILD_SRC, spec_path],
                          stdout=subprocess.PIPE, env=env)
    if proc.returncode != 0:
        raise RuntimeError("child failed: %r" % (python_cmd,))
    return json.loads(proc.stdout)


def fzf_md5_and_time(fzf_bin, corpus, query, extra, runs):
    import time as _t
    with open(corpus, "rb") as f:
        data = f.read()
    env = dict(os.environ, FZF_DEFAULT_OPTS="")
    out = subprocess.run([fzf_bin, "--filter", query] + extra, input=data,
                         stdout=subprocess.PIPE, env=env).stdout
    digest = hashlib.md5(out.rstrip(b"\n")).hexdigest()
    times = []
    for _ in range(runs):
        t0 = _t.perf_counter()
        subprocess.run([fzf_bin, "--filter", query] + extra, input=data,
                       stdout=subprocess.DEVNULL, env=env)
        times.append(_t.perf_counter() - t0)
    return digest, statistics.median(times)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--a", default="/tmp/venv010/bin/python")
    ap.add_argument("--a-name", default="A")
    ap.add_argument("--b", default=sys.executable)
    ap.add_argument("--b-env", default="PYTHONPATH=%s" %
                    os.path.join(ROOT, "src"))
    ap.add_argument("--b-name", default="B")
    ap.add_argument("--fzf", default=os.environ.get("FZF_BIN") or
                    shutil.which("fzf"))
    ap.add_argument("--runs", type=int, default=7)
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--json")
    args = ap.parse_args()

    def parse_env(s):
        out = {}
        for part in s.split(","):
            if part:
                k, _, v = part.partition("=")
                out[k] = v
        return out

    impls = [
        (args.a_name, [args.a], {}),
        (args.b_name, [args.b], parse_env(args.b_env)),
    ]

    tmpdir = tempfile.mkdtemp(prefix="purefzf-ab-")
    corpora = build_corpora(tmpdir)
    # paths corpus key has a dynamic line count; rewrite workload keys
    paths_key = [k for k in corpora if k.startswith("paths-")][0]
    workloads = [(paths_key if c.startswith("paths-") else c, q, o, x)
                 for c, q, o, x in WORKLOADS]

    have_fzf = args.fzf and os.path.exists(args.fzf)
    records = []
    name_a, name_b = impls[0][0], impls[1][0]

    header = "%-30s %9s | %11s %11s %8s | %10s" % (
        "workload", "matches", name_a + " ms", name_b + " ms", "speedup",
        "fzf ms" if have_fzf else "")
    print(header)
    print("-" * len(header))

    for corpus_key, query, opts, fzf_extra in workloads:
        corpus = corpora.get(corpus_key)
        if corpus is None:
            continue
        spec = {"corpus": corpus, "query": query, "opts": opts,
                "runs": args.runs}

        rounds = {name: [] for name, _, _ in impls}
        md5s = {}
        n_matches = None
        for _ in range(args.rounds):
            for name, cmd, env_extra in impls:
                res = run_child(cmd, env_extra, spec, tmpdir)
                md5s.setdefault(name, res["md5"])
                if md5s[name] != res["md5"]:
                    raise RuntimeError("non-deterministic output: " + name)
                n_matches = res["n"]
                rounds[name].append(statistics.median(res["times"]) * 1000)

        if md5s[name_a] != md5s[name_b]:
            print("%-30s VERIFY FAILED: %s != %s" %
                  ("%s %r" % (corpus_key, query), name_a, name_b))
            continue

        fzf_col = ""
        fzf_ms = None
        if have_fzf and fzf_extra is not None:
            fzf_md5, fzf_ms = fzf_md5_and_time(args.fzf, corpus, query,
                                               fzf_extra, args.runs)
            if fzf_md5 != md5s[name_b]:
                fzf_col = "OUTPUT-DIFF"
            else:
                fzf_col = "%10.1f" % (fzf_ms * 1000)

        a_med = statistics.median(rounds[name_a])
        b_med = statistics.median(rounds[name_b])
        spread_a = "%0.0f-%0.0f" % (min(rounds[name_a]), max(rounds[name_a]))
        spread_b = "%0.0f-%0.0f" % (min(rounds[name_b]), max(rounds[name_b]))
        print("%-30s %9d | %11.1f %11.1f %7.2fx | %s   [A %s, B %s]" % (
            "%s %r" % (corpus_key, query), n_matches, a_med, b_med,
            a_med / b_med, fzf_col, spread_a, spread_b))
        records.append({
            "corpus": corpus_key, "query": query, "opts": opts,
            "matches": n_matches,
            "a": {"name": name_a, "round_medians_ms": rounds[name_a]},
            "b": {"name": name_b, "round_medians_ms": rounds[name_b]},
            "fzf_ms": fzf_ms * 1000 if fzf_ms else None,
            "verified": True,
        })

    if args.json:
        with open(args.json, "w") as f:
            json.dump({"runs": args.runs, "rounds": args.rounds,
                       "python": sys.version, "records": records}, f,
                      indent=1)
        print("\nwrote %s" % args.json)


if __name__ == "__main__":
    main()
