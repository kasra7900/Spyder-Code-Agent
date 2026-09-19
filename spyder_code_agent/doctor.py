"""Verify that this package is installed in the Python process running Spyder.

This module deliberately has no Qt dependency.  Run it with the same Python
interpreter that launches Spyder, not with the selected IPython kernel.
"""

from __future__ import annotations

import importlib
import sys
from importlib import metadata

ENTRY_POINT_GROUP = "spyder.plugins"
ENTRY_POINT_NAME = "code_agent"


def _matching_entry_points():
    """Return this package's registered Spyder entry points across Python APIs."""
    entry_points = metadata.entry_points()
    if hasattr(entry_points, "select"):
        return list(entry_points.select(group=ENTRY_POINT_GROUP, name=ENTRY_POINT_NAME))
    return [
        item
        for item in entry_points.get(ENTRY_POINT_GROUP, [])
        if item.name == ENTRY_POINT_NAME
    ]


def check_host_environment() -> tuple[bool, list[str]]:
    """Check plugin discovery and imports in the current Python process."""
    messages = [f"Python executable: {sys.executable}"]

    try:
        spyder = importlib.import_module("spyder")
    except ImportError:
        messages.append(
            "Spyder is not installed in this environment. This is probably a "
            "kernel/project environment, not the Spyder host environment."
        )
        return False, messages

    messages.append(f"Spyder version: {spyder.__version__}")
    entry_points = _matching_entry_points()
    if not entry_points:
        messages.append(
            "The 'code_agent' entry point is missing. Reinstall the package with "
            "this exact Python interpreter."
        )
        return False, messages

    try:
        plugin_class = entry_points[0].load()
    except Exception as error:  # Spyder's loader reports the same failure at startup.
        messages.append(f"The plugin entry point could not load: {error}")
        return False, messages

    if "on_initialize" not in plugin_class.__dict__:
        messages.append(
            "The plugin does not implement Spyder's required on_initialize "
            "lifecycle hook. Reinstall the current version of this package."
        )
        return False, messages

    if plugin_class.NAME != ENTRY_POINT_NAME:
        messages.append(
            f"Entry point name is '{ENTRY_POINT_NAME}', but plugin.NAME is "
            f"'{plugin_class.NAME}'."
        )
        return False, messages

    messages.append("Plugin entry point is registered and imports successfully.")
    messages.append("Restart Spyder, then open View > Panes > Code Agent.")
    return True, messages


def main() -> int:
    """Print a concise host-environment diagnostic and return a shell status."""
    healthy, messages = check_host_environment()
    print("Spyder Code Agent installation check")
    print("\n".join(messages))
    return 0 if healthy else 1


if __name__ == "__main__":
    raise SystemExit(main())
