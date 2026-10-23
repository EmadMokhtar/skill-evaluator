"""Isolate a product runner or judge: decided once per run, said on every report.

A product runs with permission prompts disabled, as the user who started
skill-lens. Its working directory is a fresh temporary directory, but that is
no boundary: the agent, the skill's hooks and its scripts can read and write
whatever that user can (issue #99). This module narrows that, in two layers,
and the trust note says exactly which applied -- never more:

* **Home isolation**, under `auto` and `required`: a temporary home per work
  item and an environment built from an allowlist, so personal installs,
  logins and caches stay out of a run. It is not a sandbox: a process can
  still write `/Users/you/anything` by its absolute path, and the report
  says so.
* **An OS sandbox** where one works -- `sandbox-exec` on macOS, `bwrap` on
  Linux, probed the way `scripts.probe_sandbox` probes them for scripts,
  but with the network left open (the product must reach its provider).
  Writes are confined to the working directory and the product's own
  temporary directories; reads of other skill-lens temporary directories
  are refused; everything else stays readable, because the products and
  their runtimes are installed in the user's home.

`off` is the behaviour before this module existed, byte for byte.

Imports nothing from `runners/product.py`, which imports this.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from skill_lens.models import ProductSandboxStatus, SandboxBackend, SandboxMode
from skill_lens.scripts import probe_sandbox, product_bwrap_argv, product_macos_profile

if TYPE_CHECKING:
    from skill_lens.runners.product import Product

TRUST_NOTE = (
    "runs with permission prompts disabled and the full environment; no skill-lens "
    "sandbox applies; bundled scripts are reachable through the product's own tools"
)
_SCRIPTS_CLAUSE = "bundled scripts are reachable through the product's own tools"
_ISOLATED = (
    "runs with permission prompts disabled, a temporary home directory and only an "
    "allowlisted environment"
)


# What the isolation sets, or drops on purpose: passing one through would
# point the product back at the real home. `Config` refuses these names in
# `pass_env`, and `product_environment` never lets one through regardless.
CONTROLLED_ENV: frozenset[str] = frozenset(
    {
        "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "TMPDIR", "TMP", "TEMP",
        "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME",
        "CLAUDE_CONFIG_DIR", "COPILOT_HOME", "CLAUDE_CODE_TMPDIR", "CLAUDE_TMPDIR",
    }
)  # fmt: skip
# Kept from the harness when set. Not secrets, except that a proxy URL may
# carry one -- and the product needs its proxy and CA to reach its provider.
KEPT_ENV: tuple[str, ...] = (
    "PATH", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "SHELL", "USER", "LOGNAME",
    "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "ALL_PROXY",
    "https_proxy", "http_proxy", "no_proxy", "all_proxy",
    "SSL_CERT_FILE", "SSL_CERT_DIR", "NODE_EXTRA_CA_CERTS",
)  # fmt: skip
# Nothing starts on Windows without these.
KEPT_ENV_WINDOWS: tuple[str, ...] = ("SystemRoot", "COMSPEC", "PATHEXT")
HOME_PREFIX = "skill-lens-home-"


@dataclass(frozen=True)
class ProductEnv:
    """A preset's own variables under home isolation.

    `auth` is passed when set and counted by the auth check; `hint` names
    what to export, for its refusal; `passed` is passed but not counted (an
    endpoint is not a credential); `fixed` is set for the product whatever
    the harness has; `temporary` names variables the product reads instead
    of `TMPDIR` for its own temporary files, each set to the work item's
    temporary directory.
    """

    auth: tuple[str, ...]
    hint: str
    passed: tuple[str, ...] = ()
    fixed: tuple[tuple[str, str], ...] = ()
    temporary: tuple[str, ...] = ()


# Keyed by product name, so a preset under a wrapper `command` keeps them.
# `cli` has no entry: skill-lens does not know its auth.
PRODUCT_ENV: dict[str, ProductEnv] = {
    # DISABLE_AUTOUPDATER: a fresh home must not start an update on every case.
    # CLAUDE_CODE_TMPDIR: Claude Code ignores TMPDIR for its own files and
    # defaults to /tmp, which no backend lets it write; every Bash command
    # ends with `pwd -P >| $CLAUDE_CODE_TMPDIR/claude-<id>-cwd` (verified in
    # 2.1.289), so without it every command would report exit 1.
    "claude-code": ProductEnv(
        auth=("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"),
        hint="CLAUDE_CODE_OAUTH_TOKEN (from `claude setup-token`) or ANTHROPIC_API_KEY",
        passed=("ANTHROPIC_BASE_URL",),
        fixed=(("DISABLE_AUTOUPDATER", "1"),),
        temporary=("CLAUDE_CODE_TMPDIR",),
    ),
    # Copilot's documented auth variables, in its order of precedence.
    "copilot": ProductEnv(
        auth=("COPILOT_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"), hint="COPILOT_GITHUB_TOKEN"
    ),
}


class ProductSetupError(Exception):
    """A product runner cannot run here: raised from `preflight` (exit 2 via
    `cli.py`) and from `deliver_skill`, where `run` turns it into
    `RunResult.error` for the arm it concerns. Also raised when the run
    requires a product sandbox and none works.

    Defined here, beside the sandbox decision, and imported by
    `runners/product.py`, so both modules raise the one type `cli.py` catches.
    """


@dataclass(frozen=True)
class ProductSandbox:
    """The run's product sandbox: what was asked, what applies, and why.

    Made once per run by `decide_product_sandbox` and shared by every
    product runner and the product judge, so they can never disagree.
    """

    mode: SandboxMode
    backend: SandboxBackend
    detail: str

    @property
    def home_isolated(self) -> bool:
        return self.mode != "off"

    def trust(self) -> str:
        """The trust note: what applied, never more. Fixed harness text."""
        if not self.home_isolated:
            return TRUST_NOTE
        if self.backend == "none":
            return (
                f"{_ISOLATED}; that is not a sandbox: no skill-lens sandbox applies "
                f"({self.detail}), and the product can read and write anything you can; "
                f"{_SCRIPTS_CLAUSE}"
            )
        return (
            f"{_ISOLATED}, under {self.backend}: it cannot change files outside its working "
            "directory and its own temporary directories, or read other skill-lens temporary "
            "directories; it can read anything else you can, and reach the network; "
            f"{_SCRIPTS_CLAUSE}"
        )

    def wrap(
        self, argv: Sequence[str], writable: Sequence[Path], tempdir: Path | None = None
    ) -> list[str]:
        """`argv` inside the backend, writes confined to `writable`; unchanged
        for "none". `tempdir` is injectable for tests; by default the system
        temporary directory, resolved, as the script sandbox uses it."""
        if self.backend == "none":
            return list(argv)
        tempdir = Path(tempfile.gettempdir()).resolve() if tempdir is None else tempdir
        if self.backend == "sandbox-exec":
            return ["sandbox-exec", "-p", product_macos_profile(writable, tempdir), *argv]
        return product_bwrap_argv(writable, tempdir, argv)

    def status(self) -> ProductSandboxStatus:
        return ProductSandboxStatus(
            mode=self.mode,
            backend=self.backend,
            home_isolated=self.home_isolated,
            detail=self.detail,
        )


PRODUCT_SANDBOX_OFF = ProductSandbox(mode="off", backend="none", detail='product_sandbox = "off"')


def decide_product_sandbox(
    mode: SandboxMode,
    *,
    system: str | None = None,
    which: Callable[[str], str | None] = shutil.which,
    run: Callable[..., Any] = subprocess.run,
) -> ProductSandbox:
    """Decide the run's product sandbox, once, before any case.

    `off` probes nothing. `auto` and `required` execute the backend once --
    present is not the same as working -- with the product probe. `required`
    with no working backend raises `ProductSetupError`: home isolation alone
    never satisfies it, because home isolation is not a sandbox. `system`,
    `which` and `run` are injectable so the table is testable everywhere.
    """
    if mode == "off":
        return PRODUCT_SANDBOX_OFF
    probed = probe_sandbox(mode, system=system, which=which, run=run, product=True)
    if mode == "required" and probed.backend == "none":
        raise ProductSetupError(
            f'product_sandbox = "required" but no sandbox is available: {probed.detail}'
        )
    return ProductSandbox(mode=mode, backend=probed.backend, detail=probed.detail)


def product_environment(
    product: Product,
    root: Path,
    *,
    parent: Mapping[str, str] | None = None,
    windows: bool | None = None,
) -> dict[str, str]:
    """The environment a product runs in under home isolation: built, never inherited.

    `root` is the work item's private directory; the product's home is
    `root/home` and its temporary directory `root/tmp`. `CLAUDE_CONFIG_DIR`
    and `COPILOT_HOME` are dropped with everything else not kept, so each
    product falls back to its directory under the temporary home. `parent`
    and `windows` are injectable for tests.
    """
    parent = os.environ if parent is None else parent
    windows = os.name == "nt" if windows is None else windows
    own = PRODUCT_ENV.get(product.name)
    names = [
        *KEPT_ENV,
        *(KEPT_ENV_WINDOWS if windows else ()),
        *(own.auth + own.passed if own else ()),
        *(product.pass_env or ()),
    ]
    env = {name: parent[name] for name in names if name in parent and name not in CONTROLLED_ENV}
    home = root / "home"
    temporary = str(root / "tmp")
    if own is not None:
        env.update(own.fixed)
        env.update(dict.fromkeys(own.temporary, temporary))
    env.update(
        HOME=str(home),
        XDG_CONFIG_HOME=str(home / ".config"),
        XDG_CACHE_HOME=str(home / ".cache"),
        XDG_DATA_HOME=str(home / ".local" / "share"),
        XDG_STATE_HOME=str(home / ".local" / "state"),
        TMPDIR=temporary,
        TMP=temporary,
        TEMP=temporary,
    )
    if windows:
        env.update(
            USERPROFILE=str(home),
            APPDATA=str(home / "AppData" / "Roaming"),
            LOCALAPPDATA=str(home / "AppData" / "Local"),
        )
    return env


def environment_refusal(
    product: Product,
    sandbox: ProductSandbox,
    role: str,
    *,
    parent: Mapping[str, str] | None = None,
) -> str | None:
    """Why this product cannot run under home isolation, or None. Spends nothing.

    A preset must find one of its own auth variables, or one `pass_env`
    names, set and non-empty in the harness: a temporary home has no login
    in it, so without one every case would error on the product's auth --
    better said once, up front, as exit 2. Presence, not validity: an
    expired token is still an errored case at run time. A wrapper `command`
    may bring its own auth, so it is not checked. A `cli` product has no
    known auth, and a command that lost its key may exit 0 and score as a
    *failed* case, so it must name what it needs -- `pass_env = []` when
    nothing. `role` ("runner" or "judge") names the seat.
    """
    if not sandbox.home_isolated:
        return None
    parent = os.environ if parent is None else parent
    where = f'{role} {product.name}: product_sandbox = "{sandbox.mode}"'
    own = PRODUCT_ENV.get(product.name)
    if own is None:
        if product.pass_env is not None:
            return None
        return (
            f"{where} builds the product's environment from an allowlist, so the command "
            "gets none of your variables unless you name them; set "
            f"[runners.{product.name}] pass_env to the variables it needs (pass_env = [] "
            'when it needs none), or set product_sandbox = "off"'
        )
    if product.wrapper:
        return None
    if any(parent.get(name) for name in (*own.auth, *(product.pass_env or ()))):
        return None
    return (
        f"{where} gives the product a temporary home directory, so it cannot use a login "
        f"stored in your home directory or keychain; export {own.hint}, name the variable "
        f"your setup uses in [runners.{product.name}] pass_env, or set "
        'product_sandbox = "off"'
    )


@dataclass(frozen=True)
class Isolation:
    """What one work item's product processes start under.

    `root` is the private directory holding the temporary home, or None
    under `off`, where `env` is the whole harness environment and `launch`
    wraps nothing -- the behaviour before issue #99, byte for byte.
    """

    sandbox: ProductSandbox
    root: Path | None
    env: dict[str, str]

    def launch(self, argv: Sequence[str], cwd: Path, writable: Sequence[Path] = ()) -> list[str]:
        """`argv` as it must start: wrapped in the backend, which may write
        only `cwd`, the temporary home and `writable` (the MCP bridge's
        directory, for a `tools:` case)."""
        if self.root is None:
            return list(argv)
        return self.sandbox.wrap(argv, [cwd, self.root, *writable])


@contextmanager
def isolate(sandbox: ProductSandbox, product: Product) -> Iterator[Isolation]:
    """One work item's isolation: a fresh private directory, removed however
    the block ends -- an error or a killed product included -- and never
    kept, `--keep-workspace` or not: it may hold what the product wrote
    about its own login.

    `mkdtemp` is atomic, so two arms or two concurrent cases never share a
    home. Every product process the work item starts uses the same one.
    Raises `OSError` when the directory cannot be made, which a caller that
    must not raise turns into its own error.
    """
    if not sandbox.home_isolated:
        yield Isolation(sandbox=sandbox, root=None, env=os.environ.copy())
        return
    root = Path(tempfile.mkdtemp(prefix=HOME_PREFIX)).resolve()
    try:
        (root / "home").mkdir()
        (root / "tmp").mkdir()
        yield Isolation(sandbox=sandbox, root=root, env=product_environment(product, root))
    finally:
        shutil.rmtree(root, ignore_errors=True)
