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
from collections.abc import Callable, Mapping, Sequence
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
        "CLAUDE_CONFIG_DIR", "COPILOT_HOME",
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

    `auth` is passed when set and counted by the auth check; `passed` is
    passed but not counted (an endpoint is not a credential); `fixed` is set
    for the product whatever the harness has.
    """

    auth: tuple[str, ...]
    passed: tuple[str, ...] = ()
    fixed: tuple[tuple[str, str], ...] = ()


# Keyed by product name, so a preset under a wrapper `command` keeps them.
# `cli` has no entry: skill-lens does not know its auth.
PRODUCT_ENV: dict[str, ProductEnv] = {
    # DISABLE_AUTOUPDATER: a fresh home must not start an update on every case.
    "claude-code": ProductEnv(
        auth=("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"),
        passed=("ANTHROPIC_BASE_URL",),
        fixed=(("DISABLE_AUTOUPDATER", "1"),),
    ),
    # Copilot's documented auth variables, in its order of precedence.
    "copilot": ProductEnv(auth=("COPILOT_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN")),
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
    if own is not None:
        env.update(own.fixed)
    home = root / "home"
    temporary = str(root / "tmp")
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
