"""purefzf command line interface: fzf's non-interactive --filter mode.

Reads lines from stdin, writes matching lines to stdout in fzf's order.
Exit status: 0 normal, 1 no match, 2 error -- same as fzf.
"""

import sys

from . import __version__
from .core import run_filter

USAGE = """\
usage: purefzf --filter=QUERY [options]

    purefzf is a pure Python port of fzf's matching engine. It implements
    fzf's non-interactive filter mode; the interactive TUI is not included.

  Search
    -f, --filter=QUERY        Print matches for the query and exit (required)
    -e, --exact               Enable exact-match
    +e, --no-exact            Disable exact-match (default)
    -x, --extended            Extended-search mode (default)
    +x, --no-extended         No extended-search mode
    -i, --ignore-case         Case-insensitive match
    +i, --no-ignore-case      Case-sensitive match
        --smart-case          Smart-case match (default)
        --literal             Do not normalize latin script letters
        --algo=TYPE           Fuzzy matching algorithm: [v1|v2] (default: v2)
        --scheme=SCHEME       Scoring scheme: [default|path|history]
    -n, --nth=N[,..]          Comma-separated list of field index expressions
                              for limiting search scope
    -d, --delimiter=STR       Field delimiter regex (default: AWK-style)

  Output order
    +s, --no-sort             Do not sort the result
        --tac                 Reverse the order of the input
        --tiebreak=CRI[,..]   Comma-separated list of sort criteria
                              [length|chunk|begin|end|index|pathname]
                              (default: length)

  I/O
        --read0               Read input delimited by ASCII NUL characters
        --print0              Print output delimited by ASCII NUL characters
        --print-query         Print query as the first line

        --version             Display version information and exit
    -h, --help                Show this message
"""


class _OptError(Exception):
    pass


def _parse_args(args):
    opts = {
        "filter": None,
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
        "read0": False,
        "print0": False,
        "print_query": False,
        "help": False,
        "version": False,
    }

    def value_of(i, arg, name):
        # --name=value | --name value | -nvalue | -n value
        if arg.startswith("--"):
            eq = arg.find("=")
            if eq >= 0:
                return i, arg[eq + 1:]
        elif len(arg) > 2:  # -nvalue
            return i, arg[2:]
        i += 1
        if i >= len(args):
            raise _OptError("option %s requires an argument" % name)
        return i, args[i]

    i = 0
    while i < len(args):
        arg = args[i]
        name = arg.split("=", 1)[0] if arg.startswith("--") else arg[:2]
        if name in ("-f", "--filter"):
            i, opts["filter"] = value_of(i, arg, name)
        elif arg in ("-e", "--exact"):
            opts["fuzzy"] = False
        elif arg in ("+e", "--no-exact"):
            opts["fuzzy"] = True
        elif arg in ("-x", "--extended"):
            opts["extended"] = True
        elif arg in ("+x", "--no-extended"):
            opts["extended"] = False
        elif arg in ("-i", "--ignore-case"):
            opts["case"] = "ignore"
        elif arg in ("+i", "--no-ignore-case"):
            opts["case"] = "respect"
        elif arg == "--smart-case":
            opts["case"] = "smart"
        elif arg == "--literal":
            opts["normalize"] = False
        elif arg == "--no-literal":
            opts["normalize"] = True
        elif name == "--algo":
            i, val = value_of(i, arg, name)
            if val not in ("v1", "v2"):
                raise _OptError("invalid algorithm (expected: v1 or v2)")
            opts["algo"] = val
        elif name == "--scheme":
            i, val = value_of(i, arg, name)
            val = val.lower()
            if val not in ("default", "path", "history"):
                raise _OptError("invalid scoring scheme (expected: default|path|history)")
            opts["scheme"] = val
        elif name in ("-n", "--nth"):
            i, opts["nth"] = value_of(i, arg, name)
        elif name in ("-d", "--delimiter"):
            i, opts["delimiter"] = value_of(i, arg, name)
        elif name == "--tiebreak":
            i, opts["tiebreak"] = value_of(i, arg, name)
        elif arg in ("+s", "--no-sort"):
            opts["sort"] = False
        elif arg == "--sort":
            opts["sort"] = True
        elif arg == "--tac":
            opts["tac"] = True
        elif arg == "--no-tac":
            opts["tac"] = False
        elif arg == "--read0":
            opts["read0"] = True
        elif arg == "--print0":
            opts["print0"] = True
        elif arg == "--print-query":
            opts["print_query"] = True
        elif arg == "--no-print-query":
            opts["print_query"] = False
        elif arg in ("-h", "--help"):
            opts["help"] = True
        elif arg == "--version":
            opts["version"] = True
        else:
            raise _OptError("unknown option: " + arg)
        i += 1
    return opts


def _decode(data):
    """UTF-8 decoding with Go semantics: every invalid byte becomes one
    U+FFFD (fzf converts input to runes the same way and prints the
    replaced text)."""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        pass
    out = []
    i = 0
    n = len(data)
    while i < n:
        try:
            out.append(data[i:].decode("utf-8"))
            break
        except UnicodeDecodeError as exc:
            out.append(data[i:i + exc.start].decode("utf-8"))
            out.append("�")
            i += exc.start + 1
    return "".join(out)


def _read_items(stream, read0):
    data = stream.read()
    if isinstance(data, bytes):
        data = _decode(data)
    items = data.split("\0" if read0 else "\n")
    if items and items[-1] == "":
        items.pop()
    return items


def main(argv=None):
    args = sys.argv[1:] if argv is None else list(argv)
    try:
        opts = _parse_args(args)
    except _OptError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if opts["help"]:
        print(USAGE, end="")
        return 0
    if opts["version"]:
        print("purefzf %s (fzf algorithm version: 0.73.1)" % __version__)
        return 0
    if opts["filter"] is None:
        print("purefzf only implements the non-interactive filter mode; "
              "specify a query with -f/--filter "
              "(see purefzf --help)", file=sys.stderr)
        return 2
    # Normalize a query that arrived with undecodable bytes the same way
    # fzf would see it (surrogates -> U+FFFD)
    opts["filter"] = _decode(opts["filter"].encode("utf-8", "surrogateescape"))

    try:
        items = _read_items(sys.stdin.buffer if hasattr(sys.stdin, "buffer")
                            else sys.stdin, opts["read0"])
        matches = run_filter(
            items, opts["filter"],
            fuzzy=opts["fuzzy"], extended=opts["extended"],
            case=opts["case"], normalize=opts["normalize"],
            algo=opts["algo"], scheme=opts["scheme"], nth=opts["nth"],
            delimiter=opts["delimiter"], tiebreak=opts["tiebreak"],
            sort=opts["sort"], tac=opts["tac"])
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    out = sys.stdout.buffer if hasattr(sys.stdout, "buffer") else sys.stdout
    terminator = b"\0" if opts["print0"] else b"\n"
    chunks = []
    if opts["print_query"]:
        chunks.append(opts["filter"].encode("utf-8", "surrogateescape"))
        chunks.append(terminator)
    for match in matches:
        chunks.append(match.text.encode("utf-8", "surrogateescape"))
        chunks.append(terminator)
    if chunks:
        out.write(b"".join(chunks))
        out.flush()
    return 0 if matches else 1


if __name__ == "__main__":
    sys.exit(main())
