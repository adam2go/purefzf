"""End-to-end CLI tests (purefzf --filter)."""

import os
import subprocess
import sys

import pytest

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")


def run_cli(args, input_bytes):
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [_SRC] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
    proc = subprocess.run(
        [sys.executable, "-m", "purefzf.cli"] + args,
        input=input_bytes, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=env)
    return proc


def test_basic_filter():
    proc = run_cli(["--filter", "b"], b"ab\nbbb\n")
    assert proc.returncode == 0
    assert proc.stdout == b"bbb\nab\n"


def test_exit_code_no_match():
    proc = run_cli(["--filter", "zzz"], b"ab\n")
    assert proc.returncode == 1
    assert proc.stdout == b""


def test_exit_code_usage_error():
    proc = run_cli(["--bogus"], b"")
    assert proc.returncode == 2
    assert b"unknown option" in proc.stderr


def test_requires_filter_flag():
    proc = run_cli([], b"a\n")
    assert proc.returncode == 2


def test_attached_short_option():
    proc = run_cli(["-fb"], b"ab\nbbb\n")
    assert proc.stdout == b"bbb\nab\n"


def test_long_option_with_equals():
    proc = run_cli(["--filter=b", "--tiebreak=begin"], b"xb\nbx\n")
    assert proc.stdout == b"bx\nxb\n"


def test_print0_read0():
    proc = run_cli(["--filter", "b", "--read0", "--print0"],
                   b"ab\nx\0bbb\0")
    assert proc.returncode == 0
    assert proc.stdout == b"bbb\0ab\nx\0"


def test_print_query():
    proc = run_cli(["--filter", "q", "--print-query"], b"\n")
    assert proc.returncode == 1
    assert proc.stdout == b"q\n"


def test_no_trailing_newline_input():
    proc = run_cli(["--filter", "b"], b"bbb")
    assert proc.stdout == b"bbb\n"


def test_empty_input():
    proc = run_cli(["--filter", ""], b"")
    assert proc.returncode == 1


def test_invalid_utf8_replaced_like_fzf():
    # fzf converts every invalid byte to U+FFFD and prints the replaced
    # text; purefzf does the same
    data = b"keep\xffme b\nother\n"
    proc = run_cli(["--filter", "keep"], data)
    assert proc.stdout == "keep�me b\n".encode("utf-8")


def test_version():
    proc = run_cli(["--version"], b"")
    assert proc.returncode == 0
    assert proc.stdout.startswith(b"purefzf ")


def test_help():
    proc = run_cli(["--help"], b"")
    assert proc.returncode == 0
    assert b"--filter" in proc.stdout


@pytest.mark.parametrize("flag,expected", [
    (["+s"], b"ab\nbbb\n"),          # input order
    (["--no-sort"], b"ab\nbbb\n"),
    (["--tac"], b"bbb\nab\n"),       # sorted; tac flips ties only
])
def test_order_flags(flag, expected):
    proc = run_cli(["--filter", "b"] + flag, b"ab\nbbb\n")
    assert proc.stdout == expected
