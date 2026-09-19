"""Spyder 6 plugin registration adapter."""

from __future__ import annotations

try:
    import spyder
    from spyder.api.plugin_registration.decorators import on_plugin_available
    from spyder.api.plugins import Plugins, SpyderDockablePlugin
except ImportError as error:  # Helpful when somebody imports the plugin outside Spyder.
    raise ImportError(
        "Spyder Code Agent's plugin adapter requires Spyder 6.0 through 6.1. "
        "Install the package into Spyder's Python environment."
    ) from error

from .compatibility import require_supported_runtime

require_supported_runtime(spyder.__version__)

# This intentionally follows the runtime check above, so an unsupported
# Spyder version fails before importing Qt-dependent plugin code.
from .container import AgentContainer  # noqa: E402


class CodeAgent(SpyderDockablePlugin):
    """Dockable Code Agent pane registered through the ``spyder.plugins`` entry point."""

    NAME = "code_agent"
    REQUIRES = []
    OPTIONAL = [Plugins.Editor, Plugins.IPythonConsole, Plugins.Projects]
    TABIFY = [Plugins.VariableExplorer]
    WIDGET_CLASS = AgentContainer
    CONF_SECTION = NAME

    def on_initialize(self):
        """Initialize plugin-local state before Spyder registers dependencies.

        Spyder 6.1 calls this abstract lifecycle hook during plugin creation.
        Dependency wiring belongs in the ``on_plugin_available`` handlers below,
        because Spyder explicitly forbids accessing other plugins here.
        """

    @staticmethod
    def get_name():
        return "Code Agent"

    @staticmethod
    def get_description():
        return "Local Python diagnostics and optional AI-assisted debugging"

    @classmethod
    def get_icon(cls):
        return cls.create_icon("python")

    @on_plugin_available(plugin=Plugins.Editor)
    def on_editor_available(self):
        self.get_widget().set_editor(self.get_plugin(Plugins.Editor))

    @on_plugin_available(plugin=Plugins.IPythonConsole)
    def on_ipython_available(self):
        self.get_widget().set_ipython(self.get_plugin(Plugins.IPythonConsole))

    @on_plugin_available(plugin=Plugins.Projects)
    def on_project_available(self):
        self.get_widget().set_projects(self.get_plugin(Plugins.Projects))

    def update_font(self):
        """Spyder calls this hook; the widget follows the application font by default."""
