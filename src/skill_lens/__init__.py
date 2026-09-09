"""skill-lens — run evaluations on Agent Skills."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("skill-lens")
except PackageNotFoundError:  # pragma: no cover - only when running from source tree
    __version__ = "0.0.0"

__all__ = ["__version__"]
