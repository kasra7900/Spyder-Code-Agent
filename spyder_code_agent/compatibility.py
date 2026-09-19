"""Runtime compatibility checks kept independent of Qt and Spyder imports."""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from typing import Optional, Tuple

MIN_PYTHON = (3, 9)
MAX_PYTHON_EXCLUSIVE = (3, 14)
MIN_SPYDER = (6, 0)
MAX_SPYDER_EXCLUSIVE = (6, 2)


def version_tuple(value: str) -> Tuple[int, ...]:
    """Return numeric version components without depending on ``packaging``."""
    matched = re.match(r"\s*(\d+(?:\.\d+)*)", value or "")
    if not matched:
        return ()
    return tuple(int(part) for part in matched.group(1).split("."))


def _at_least(version: Tuple[int, ...], minimum: Tuple[int, ...]) -> bool:
    return version + (0,) * (len(minimum) - len(version)) >= minimum


def _before(version: Tuple[int, ...], maximum: Tuple[int, ...]) -> bool:
    return version + (0,) * (len(maximum) - len(version)) < maximum


@dataclass(frozen=True)
class CompatibilityStatus:
    supported: bool
    message: str = ""


def check_runtime(
    python_version: Optional[Tuple[int, ...]] = None,
    spyder_version: Optional[str] = None,
) -> CompatibilityStatus:
    """Check the explicitly tested runtime range and return an actionable result."""
    python_version = python_version or sys.version_info[:2]
    if not _at_least(python_version, MIN_PYTHON) or not _before(
        python_version, MAX_PYTHON_EXCLUSIVE
    ):
        return CompatibilityStatus(
            False,
            "Spyder Code Agent supports Python 3.9 through 3.13; "
            f"found Python {'.'.join(map(str, python_version))}.",
        )
    if spyder_version is None:
        return CompatibilityStatus(False, "Spyder 6.0 through 6.1 is required but is not installed.")

    parsed = version_tuple(spyder_version)
    if not parsed or not _at_least(parsed, MIN_SPYDER) or not _before(
        parsed, MAX_SPYDER_EXCLUSIVE
    ):
        return CompatibilityStatus(
            False,
            "Spyder Code Agent supports Spyder 6.0 through 6.1; "
            f"found Spyder {spyder_version or 'unknown'}.",
        )
    return CompatibilityStatus(True)


def require_supported_runtime(spyder_version: str) -> None:
    """Raise a clear error for the plugin loader instead of failing mysteriously."""
    status = check_runtime(spyder_version=spyder_version)
    if not status.supported:
        raise RuntimeError(status.message)
