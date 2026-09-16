import importlib
import sys
import types


class _PluginBase:
    pass


class _WidgetBase:
    pass


class _Signal:
    def __init__(self, *args):
        pass


class _QtObject:
    Password = 1

    def __init__(self, *args, **kwargs):
        pass


def _install_fake_spyder_and_qt(monkeypatch):
    spyder = types.ModuleType("spyder")
    spyder.__version__ = "6.1.0"
    api = types.ModuleType("spyder.api")
    plugins = types.ModuleType("spyder.api.plugins")
    plugins.SpyderDockablePlugin = _PluginBase
    plugins.Plugins = types.SimpleNamespace(
        Editor="editor", IPythonConsole="ipython", Projects="projects", VariableExplorer="variables"
    )
    decorators = types.ModuleType("spyder.api.plugin_registration.decorators")
    decorators.on_plugin_available = lambda **kwargs: lambda function: function
    widgets = types.ModuleType("spyder.api.widgets.main_widget")
    widgets.PluginMainWidget = _WidgetBase
    registration = types.ModuleType("spyder.api.plugin_registration")
    qtpy = types.ModuleType("qtpy")
    qtcore = types.ModuleType("qtpy.QtCore")
    qtcore.QThread = _QtObject
    qtcore.QTimer = _QtObject
    qtcore.Signal = _Signal
    qtwidgets = types.ModuleType("qtpy.QtWidgets")
    for name in (
        "QAction", "QDialog", "QFileDialog", "QFormLayout", "QHBoxLayout", "QLabel", "QLineEdit",
        "QPushButton", "QTextBrowser", "QTextEdit", "QVBoxLayout", "QWidget",
    ):
        setattr(qtwidgets, name, _QtObject)
    for name, module in {
        "spyder": spyder,
        "spyder.api": api,
        "spyder.api.plugins": plugins,
        "spyder.api.plugin_registration": registration,
        "spyder.api.plugin_registration.decorators": decorators,
        "spyder.api.widgets.main_widget": widgets,
        "qtpy": qtpy,
        "qtpy.QtCore": qtcore,
        "qtpy.QtWidgets": qtwidgets,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)


def test_plugin_entry_module_loads_against_supported_spyder(monkeypatch):
    _install_fake_spyder_and_qt(monkeypatch)
    sys.modules.pop("spyder_code_agent.plugin", None)
    sys.modules.pop("spyder_code_agent.container", None)

    module = importlib.import_module("spyder_code_agent.plugin")

    assert module.CodeAgent.NAME == "code_agent"
    assert module.CodeAgent.WIDGET_CLASS.__name__ == "AgentContainer"
    assert "on_initialize" in module.CodeAgent.__dict__
