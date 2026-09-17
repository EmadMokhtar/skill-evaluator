"""Start, wait for, and clean up after a child process -- shared by every caller
that spawns one.

`scripts.py` (a bundled script under the sandbox) and `runners/product.py` (an
agent product's own CLI) spawn under different trust models, but the mechanics
they must agree on are the same: the child leads its own process group, the
group is killed after *every* exit so nothing is left behind, and output is
read back through the handle the harness opened -- never by reopening a path
the child could have replaced. One implementation, so two callers cannot
drift. Imports no agent framework and nothing from the rest of skill-lens.
"""

from __future__ import annotations

import os
import signal
import subprocess
import time
from typing import IO, Any


def group_kwargs() -> dict[str, Any]:
    """Make the child lead its own process group, so the group can be killed.

    On POSIX `start_new_session=True` makes the child a session leader, which
    guarantees its process-group id equals its pid -- what lets
    `reap_and_kill_group` address the group as `process.pid` without a
    `getpgid` lookup that would fail exactly when it matters, after the
    leader has been reaped.
    """
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


# How long the reap after SIGKILL may take. SIGKILL cannot be ignored, so this
# only binds on a process stuck in an uninterruptible kernel wait.
_REAP_TIMEOUT_SECONDS = 5.0


def _exit_observed(process: subprocess.Popen[bytes], timeout: float) -> bool:
    """Did the child exit within `timeout`? Either way, the group kill that follows is safe.

    Where `os.waitid` exists -- Linux, and macOS from Python 3.13 (CPython does
    not build it on macOS before then) -- the exit is observed with `WNOWAIT`,
    the same poll-and-back-off loop `Popen.wait` runs but without collecting
    the child, so its pid is still held when `killpg` runs. Everywhere else,
    or if something already reaped the child (`ECHILD`), fall back to
    `Popen.wait`, which reaps first. That is still safe from pid reuse: POSIX
    forbids `fork` from returning a pid that matches an existing process
    *group* id, and a group that still has live members is exactly the case
    where the kill matters.
    """
    deadline = time.monotonic() + timeout
    waitid = getattr(os, "waitid", None)
    if waitid is not None:
        delay = 0.0005
        while True:
            try:
                if waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT):
                    return True
            except ChildProcessError:
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            time.sleep(min(delay, remaining))
            delay = min(delay * 2, 0.05)
    try:
        process.wait(timeout=max(deadline - time.monotonic(), 0.0))
    except subprocess.TimeoutExpired:
        return False
    return True


def _kill_group_posix(process: subprocess.Popen[bytes]) -> None:
    """SIGKILL the whole process group. Never raises.

    `ProcessLookupError` means the group is already gone; `PermissionError`
    is what macOS returns when the group's only remaining member is the
    zombie leader. Neither is a failure to clean up.
    """
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def _kill_tree_windows(process: subprocess.Popen[bytes]) -> None:
    """`taskkill /T /F` the child's tree. Never raises.

    `taskkill /T` walks the tree from the parent, so once the parent has
    exited on its own it finds nothing to walk -- a script that starts a
    background process and exits normally leaves that process running on
    Windows. The timeout path, where the parent is still alive, is covered.

    The trailing `Popen.kill` is for a `taskkill` that is missing or refused;
    it must not leave a timed-out child alive. It is skipped once the child
    has been seen to exit, and an `OSError` from it is swallowed: the child
    can exit between the poll and the kill, and the never-raises contract
    must not depend on CPython's `TerminateProcess` wrapper tolerating that
    race (today it does, by checking `GetExitCodeProcess` first).
    """
    try:
        subprocess.run(  # noqa: S603 - fixed argv, no shell
            # S607: taskkill is found on PATH on purpose; it lives in System32
            # on every Windows install and an absolute path would be wrong.
            ["taskkill", "/T", "/F", "/PID", str(process.pid)],  # noqa: S607
            capture_output=True,
            check=False,
        )
    except OSError:
        pass
    if process.poll() is None:
        try:
            process.kill()
        except OSError:
            pass


def reap_and_kill_group(
    process: subprocess.Popen[bytes], timeout: float
) -> tuple[int | None, bool]:
    """Wait for the script, then kill its group. `(exit_code, timed_out)`.

    The group is killed after *every* exit, not only a timeout: a script that
    starts `sleep 1000` and exits at once has left something behind, and the
    docs promise it does not. On POSIX the order is observe the exit, kill
    the group, then reap -- with the observation leaving the child unreaped
    where `os.waitid` exists, see `_exit_observed`. Never raises.
    """
    if os.name == "nt":
        try:
            process.wait(timeout=timeout)
            timed_out = False
        except subprocess.TimeoutExpired:
            timed_out = True
        _kill_tree_windows(process)
    else:
        timed_out = not _exit_observed(process, timeout)
        _kill_group_posix(process)
    # Reaps where the observation left the status in place, or collects the
    # SIGKILL; otherwise returns the code already collected.
    try:
        exit_code = process.wait(timeout=_REAP_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:  # pragma: no cover - SIGKILL cannot be ignored
        return None, True
    return (None if timed_out else exit_code), timed_out


def read_capped_handle(handle: IO[bytes], budget: int) -> str:
    """The first `budget` bytes of an already-open capture stream, plus an
    honest marker.

    Reads through the harness's own handle -- never by reopening the path.
    Popen shares this file's open-file description with the child (it dups
    the descriptor), so the inode being written is fixed at spawn time: a
    script that later unlinks the path and replaces it with a symlink (to
    smuggle in another file's content) or a FIFO (whose `open("rb")` would
    block forever, past the point the timeout was already enforced) cannot
    change what this reads.
    """
    try:
        size = os.fstat(handle.fileno()).st_size
        handle.seek(0)
        data = handle.read(budget)
    except OSError as exc:
        return f"(unreadable: {exc})"
    text = data.decode("utf-8", errors="replace")
    if size > budget:
        text += f"\n... [truncated, {size - budget:,} bytes omitted]"
    return text
