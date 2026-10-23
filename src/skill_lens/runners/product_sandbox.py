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

import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from skill_lens.models import ProductSandboxStatus, SandboxBackend, SandboxMode
from skill_lens.scripts import probe_sandbox

TRUST_NOTE = (
    "runs with permission prompts disabled and the full environment; no skill-lens "
    "sandbox applies; bundled scripts are reachable through the product's own tools"
)
_SCRIPTS_CLAUSE = "bundled scripts are reachable through the product's own tools"
_ISOLATED = (
    "runs with permission prompts disabled, a temporary home directory and only an "
    "allowlisted environment"
)


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
