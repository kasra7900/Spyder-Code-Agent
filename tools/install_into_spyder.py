#!/usr/bin/env python3
"""Install this checkout into the Python environment that launches Spyder.

Example:
    python tools/install_into_spyder.py --spyder-python /path/to/python

The selected IPython kernel is not relevant. ``--spyder-python`` must point to
the interpreter that imports and starts the Spyder application itself.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def run(command: list[str]) -> int:
    print("+", " ".join(command))
    return subprocess.run(command, check=False).returncode


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--spyder-python",
        required=True,
        type=Path,
        help="Python executable used to launch Spyder (not an IPython kernel).",
    )
    parser.add_argument(
        "--editable",
        action="store_true",
        help="Install this checkout in editable mode for development.",
    )
    args = parser.parse_args()
    host_python = args.spyder_python.expanduser().resolve()

    if not host_python.is_file():
        parser.error(f"No Python executable exists at: {host_python}")

    if run([str(host_python), "-c", "import spyder; print(spyder.__version__)"]):
        print(
            "\nThat interpreter does not contain Spyder. Select the Python "
            "environment that launches the Spyder desktop application.",
            file=sys.stderr,
        )
        return 2

    install_command = [str(host_python), "-m", "pip", "install"]
    if args.editable:
        install_command.append("--editable")
    install_command.append(str(ROOT))
    if run(install_command):
        print(
            "\nInstallation failed. If this is Spyder's standalone installer, "
            "third-party plugins are not supported in Spyder 6.0–6.1. Use a "
            "Conda/venv installation of Spyder, or wait for official plugin "
            "manager support in Spyder 6.2+.",
            file=sys.stderr,
        )
        return 1

    return run([str(host_python), "-m", "spyder_code_agent.doctor"])


if __name__ == "__main__":
    raise SystemExit(main())
