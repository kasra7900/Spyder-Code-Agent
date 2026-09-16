"""Compatibility shim for legacy ``setup.py`` installers.

Package metadata lives exclusively in ``pyproject.toml``. Keeping this tiny
shim lets older tooling invoke ``python setup.py --name`` without reintroducing
a second, divergent metadata definition.
"""

from setuptools import setup


if __name__ == "__main__":
    setup()
