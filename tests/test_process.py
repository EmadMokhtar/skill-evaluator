"""The process helpers shared by `scripts.py` and `runners/product.py`."""

from __future__ import annotations

import subprocess
import sys

from skill_lens.process import group_kwargs, read_capped_handle, reap_and_kill_group


def _start(code: str, stdout) -> subprocess.Popen[bytes]:
    return subprocess.Popen(  # noqa: S603 - fixed argv, no shell
        [sys.executable, "-c", code],
        stdin=subprocess.DEVNULL,
        stdout=stdout,
        stderr=subprocess.DEVNULL,
        **group_kwargs(),
    )


def test_a_quick_exit_is_reaped_with_its_code(tmp_path):
    with (tmp_path / "out").open("w+b") as out:
        process = _start("import sys; sys.exit(7)", out)
        assert reap_and_kill_group(process, timeout=30.0) == (7, False)


def test_a_timeout_kills_the_process_and_says_so(tmp_path):
    with (tmp_path / "out").open("w+b") as out:
        process = _start("import time; time.sleep(60)", out)
        exit_code, timed_out = reap_and_kill_group(process, timeout=0.5)
        assert timed_out is True
        assert exit_code is None
        assert process.poll() is not None  # reaped, not left behind


def test_read_capped_handle_reads_through_the_handle_and_marks_the_cut(tmp_path):
    with (tmp_path / "out").open("w+b") as out:
        process = _start("print('x' * 1000)", out)
        reap_and_kill_group(process, timeout=30.0)
        text = read_capped_handle(out, budget=100)
    assert text.startswith("x" * 100)
    assert "[truncated, 901 bytes omitted]" in text  # 1000 x's + newline - 100


def test_read_capped_handle_returns_everything_under_the_cap(tmp_path):
    with (tmp_path / "out").open("w+b") as out:
        process = _start("print('hello')", out)
        reap_and_kill_group(process, timeout=30.0)
        assert read_capped_handle(out, budget=100) == "hello\n"
