"""Spyder Code Agent package.

The diagnostic core is importable without Spyder, Qt, an API key, or an LLM
client. The ``plugin`` module is imported only by Spyder's plugin loader.
"""

__version__ = "0.2.0"

from .agent import AgentService, AgentSuggestion
from .diagnostics import DiagnosticReport, diagnose_traceback

__all__ = [
    "AgentService",
    "AgentSuggestion",
    "DiagnosticReport",
    "diagnose_traceback",
    "__version__",
]
