from spyder.api.plugins import SpyderDockablePlugin, Plugins
from spyder.api.plugin_registration.decorators import on_plugin_available
from spyder.api.translations import get_translation
from .container import AgentContainer

_ = get_translation("spyder_code_agent")

class CodeAgent(SpyderDockablePlugin):
    NAME = "code_agent"
    REQUIRES = []
    OPTIONAL = [Plugins.Editor, Plugins.IPythonConsole, Plugins.Projects]
    TABIFY = [Plugins.VariableExplorer]
    WIDGET_CLASS = AgentContainer
    CONF_SECTION = NAME
    
    @staticmethod 
    def get_name():
        return "Code Agent"
    
    @staticmethod 
    def get_description():
        return "AI coding assistant"
    
    @classmethod
    def get_icon(cls):
        return cls.create_icon("python")
    
    def on_initialize(self):
        pass
    
    @on_plugin_available(plugin=Plugins.Editor)
    def on_editor_available(self):
        editor = self.get_plugin(Plugins.Editor)
        print("✅ Editor found:", editor)
        self.get_widget().set_editor(editor)

    @on_plugin_available(plugin=Plugins.IPythonConsole)
    def on_ipython_available(self):
        ipython = self.get_plugin(Plugins.IPythonConsole)
        print("✅ IPython found:", ipython)
        self.get_widget().set_ipython(ipython)
        
    @on_plugin_available(plugin=Plugins.Projects)
    def on_project_available(self):
        projects = self.get_plugin(Plugins.Projects)
        print("✅ Projects found:", projects)
        self.get_widget().set_projects(projects)
    
    def update_font(self):
        pass
