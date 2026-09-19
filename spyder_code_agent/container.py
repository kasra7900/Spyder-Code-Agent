"""Qt adapter for the provider-neutral diagnostic agent.

Only this module depends on Spyder/Qt.  The core remains usable in a normal
Python process and does not require an API client at import time.
"""

from __future__ import annotations

from html import escape
import json
import os
from pathlib import Path
import tempfile
import uuid

from qtpy.QtCore import QThread, QTimer, Signal
from qtpy.QtWidgets import (
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextBrowser,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from spyder.api.widgets.main_widget import PluginMainWidget

from .agent import (
    AgentConfigurationError,
    AgentResponseError,
    AgentService,
    OpenAICompatibleProvider,
    parse_suggestion,
)
from .agent_loop import AgentActivity, AgentLoop, AgentRun
from .diagnostics import is_traceback_text
from .project_context import CURRENT_EDITOR_NAME, ProjectContext, is_sensitive_name


MAX_CONTEXT_CHARS = 120_000
CURRENT_EDITOR_CONTEXT_FILE = CURRENT_EDITOR_NAME


def _settings_file() -> Path:
    """Use an OS-appropriate private config location, not the project directory."""
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "spyder-code-agent" / "settings.json"


SETTINGS_FILE = _settings_file()
LEGACY_SETTINGS_FILE = Path.home() / ".agent_config"


class APIDialog(QDialog):
    """Provider settings dialog. Values are never written into project files."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Code Agent API settings")
        self.setFixedWidth(440)
        self.model_name_input = QLineEdit()
        self.base_url_input = QLineEdit()
        self.api_key_input = QLineEdit()
        self.api_key_input.setEchoMode(QLineEdit.Password)

        form_layout = QFormLayout()
        form_layout.addRow(QLabel("Model name"), self.model_name_input)
        form_layout.addRow(QLabel("Base URL"), self.base_url_input)
        form_layout.addRow(QLabel("API key"), self.api_key_input)

        save_btn = QPushButton("Save")
        cancel_btn = QPushButton("Cancel")
        save_btn.clicked.connect(self.accept)
        cancel_btn.clicked.connect(self.reject)
        buttons = QHBoxLayout()
        buttons.addStretch()
        buttons.addWidget(cancel_btn)
        buttons.addWidget(save_btn)

        layout = QVBoxLayout()
        layout.addLayout(form_layout)
        layout.addLayout(buttons)
        self.setLayout(layout)

    def get_values(self):
        return (
            self.base_url_input.text().strip(),
            self.api_key_input.text().strip(),
            self.model_name_input.text().strip(),
        )


class LLMWorker(QThread):
    """Run the optional provider and read-only agent loop off the Qt UI thread."""

    result_received = Signal(object)
    activity_received = Signal(object)
    error_occurred = Signal(str)
    finished_response = Signal()

    def __init__(self, prompt, base_url, api_key, model_name, project_context):
        super().__init__()
        self.prompt = prompt
        self.project_context = project_context
        self.base_url = base_url
        self.api_key = api_key
        self.model_name = model_name

    def run(self):
        try:
            provider = OpenAICompatibleProvider(self.base_url, self.api_key, self.model_name)
            result = AgentLoop(provider, self.project_context).run(
                self.prompt, on_activity=self.activity_received.emit
            )
            self.result_received.emit(result)
        except (AgentConfigurationError, AgentResponseError) as error:
            self.error_occurred.emit(str(error))
        except Exception as error:  # Provider/network exceptions need a visible UI message.
            self.error_occurred.emit(f"Provider request failed: {error}")
        finally:
            self.finished_response.emit()


class AgentContainer(PluginMainWidget):
    """Spyder dock widget for context selection, local diagnostics and optional LLM help."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.current_base_url = ""
        self.current_api_key = ""
        self.current_model_name = ""
        self.editor = None
        self.ipython = None
        self.worker = None
        self.error_timer = None
        self.error_file = Path(tempfile.gettempdir()) / (
            f"spyder-code-agent-{os.getpid()}-{uuid.uuid4().hex}.json"
        )
        self._hooked_shells = set()
        self.pending_fix = None
        self.pending_fix_file = ""
        self.pending_fix_editor = None
        self._request_editor = None
        self.projects = None
        self.load_setting_from_file()

    def get_title(self):
        return "Code Agent"

    def set_ipython(self, ipython):
        self.ipython = ipython
        signal = getattr(ipython, "sig_shellwidget_created", None)
        if signal is not None:
            signal.connect(self.inject_error_handler)
        shell = getattr(ipython, "get_current_shellwidget", lambda: None)()
        if shell is not None:
            self.inject_error_handler(shell)

    def set_editor(self, editor):
        self.editor = editor

    def set_projects(self, projects):
        # The adapter obtains a root only from this optional Spyder plugin.
        self.projects = projects
        self.update_context_scope()

    def setup(self):
        self.conversation_history = []
        self.selected_files = []
        self.chat_display = QTextBrowser()
        self.chat_display.setOpenExternalLinks(False)
        self.context_scope = QLabel()
        self.plan_display = QTextBrowser()
        self.plan_display.setMaximumHeight(70)
        self.activity_display = QTextBrowser()
        self.activity_display.setMaximumHeight(120)
        self.user_input = QTextEdit()
        self.user_input.setMaximumHeight(90)

        self.add_file_btn = QPushButton("+ Add file")
        self.add_file_btn.clicked.connect(self.add_file)
        self.settings_btn = QPushButton("Settings")
        self.settings_btn.clicked.connect(self.set_api)
        self.send_btn = QPushButton("Send")
        self.send_btn.clicked.connect(self.send_message)
        self.apply_btn = QPushButton("Apply fix")
        self.apply_btn.clicked.connect(self.apply_fix)
        self.apply_btn.setEnabled(False)

        buttons = QHBoxLayout()
        buttons.addWidget(self.settings_btn)
        buttons.addWidget(self.send_btn)
        buttons.addStretch()
        buttons.addWidget(self.apply_btn)
        layout = QVBoxLayout()
        layout.addWidget(QLabel("Context scope"))
        layout.addWidget(self.context_scope)
        layout.addWidget(self.add_file_btn)
        layout.addWidget(QLabel("Agent plan"))
        layout.addWidget(self.plan_display)
        layout.addWidget(QLabel("Tool activity"))
        layout.addWidget(self.activity_display)
        layout.addWidget(self.chat_display)
        layout.addWidget(self.user_input)
        layout.addLayout(buttons)
        central = QWidget()
        central.setLayout(layout)
        self.setLayout(QVBoxLayout())
        self.layout().addWidget(central)
        self.update_context_scope()

    def load_setting_from_file(self):
        # Read the legacy location once so upgrades do not discard existing settings.
        for settings_path in (SETTINGS_FILE, LEGACY_SETTINGS_FILE):
            try:
                data = json.loads(settings_path.read_text(encoding="utf-8"))
                self.current_base_url = str(data.get("base_url", ""))
                self.current_api_key = str(data.get("api_key", ""))
                self.current_model_name = str(data.get("model_name", ""))
                return
            except (OSError, ValueError, TypeError):
                continue

    def save_settings_to_file(self):
        try:
            SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
            SETTINGS_FILE.write_text(
                json.dumps(
                    {
                        "base_url": self.current_base_url,
                        "api_key": self.current_api_key,
                        "model_name": self.current_model_name,
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            if os.name != "nt":
                os.chmod(SETTINGS_FILE, 0o600)
            return True
        except OSError:
            return False

    def set_api(self):
        dialog = APIDialog(self)
        dialog.base_url_input.setText(self.current_base_url)
        dialog.api_key_input.setText(self.current_api_key)
        dialog.model_name_input.setText(self.current_model_name)
        if dialog.exec_() != QDialog.Accepted:
            return
        url, key, name = dialog.get_values()
        if not all((url, key, name)):
            self.show_error("Base URL, API key, and model name are required.")
            return
        self.current_base_url, self.current_api_key, self.current_model_name = url, key, name
        message = "API settings saved." if self.save_settings_to_file() else "API settings could not be saved."
        self.chat_display.append(f"<b>System:</b> {escape(message)}")

    def add_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select file", "", "Python files (*.py);;All files (*)")
        if not path or path in self.selected_files:
            return
        filename = Path(path).name
        if filename == CURRENT_EDITOR_CONTEXT_FILE:
            self.show_error(f"{filename} is reserved for the open editor context.")
            return
        if is_sensitive_name(filename):
            self.show_error(f"{filename} was not added because sensitive files cannot be shared with the agent.")
            return
        if any(Path(selected).name == filename for selected in self.selected_files):
            self.show_error(
                f"{filename} was not added because selected context filenames must be unique."
            )
            return
        self.selected_files.append(path)
        self.chat_display.append(f"<b>Context added:</b> <code>{escape(filename)}</code>")
        self.update_context_scope()

    def get_project_files(self):
        result = {}
        for raw_path in self.selected_files:
            path = Path(raw_path)
            try:
                result[path.name] = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError) as error:
                self.show_error(f"Could not read {path.name}: {error}")
        return result

    def get_current_file_content(self):
        current_editor = self._get_current_editor()
        if current_editor is None:
            return ""
        try:
            return current_editor.toPlainText()
        except (AttributeError, RuntimeError):
            return ""

    def _get_current_editor(self):
        """Return Spyder's active editor, without guessing a filesystem path."""
        if self.editor is None:
            return None
        try:
            return self.editor.get_current_editor()
        except (AttributeError, RuntimeError):
            return None

    def _context_string(self):
        context = self.get_project_files()
        # This sentinel is intentionally not a real path. It lets the provider
        # request a patch for the open editor while preserving the rule that
        # model output can never choose an arbitrary disk path. Keep it in the
        # context even when extra files are selected: those files are often
        # dependencies of the code presently being debugged.
        self._request_editor = self._get_current_editor()
        if self._request_editor is not None:
            context = {
                CURRENT_EDITOR_CONTEXT_FILE: self.get_current_file_content(),
                **context,
            }
        rendered = "\n\n".join(f"# FILE: {name}\n{code}" for name, code in context.items())
        if len(rendered) > MAX_CONTEXT_CHARS:
            self.chat_display.append("<b>System:</b> Context was truncated to protect the provider request size.")
        return rendered[:MAX_CONTEXT_CHARS]

    def _active_project_root(self):
        """Read a root only from Spyder Projects; never fall back to cwd/home."""
        if self.projects is None:
            return None
        candidates = []
        for method_name in ("get_active_project_path", "get_project_path", "get_active_project"):
            method = getattr(self.projects, method_name, None)
            if callable(method):
                try:
                    candidates.append(method())
                except (AttributeError, RuntimeError, TypeError):
                    continue
        for candidate in candidates:
            root = getattr(candidate, "root_path", candidate)
            if isinstance(root, (str, Path)):
                path = Path(root)
                if path.is_absolute() and path.is_dir():
                    return path
        return None

    def update_context_scope(self):
        """Render names only; never leak absolute adapter/editor paths in the pane."""
        if not hasattr(self, "context_scope"):
            return
        root = self._active_project_root()
        project = f"Project: {escape(root.name)}" if root is not None else "No active project (selected context only)"
        editor = "active editor available" if self._get_current_editor() is not None else "no active editor"
        selected = ", ".join(escape(Path(path).name) for path in self.selected_files) or "none"
        self.context_scope.setText(f"{project} · {editor} · selected: {selected}")

    def _project_context(self):
        self._request_editor = self._get_current_editor()
        return ProjectContext(
            project_root=self._active_project_root(),
            active_editor_text=self.get_current_file_content(),
            active_editor_name=CURRENT_EDITOR_CONTEXT_FILE,
            active_editor_available=self._request_editor is not None,
            selected_context=self.get_project_files(),
        )

    def inject_error_handler(self, shell=None):
        """Install one guarded traceback hook per kernel and start one polling timer."""
        if shell is None or id(shell) in self._hooked_shells:
            return
        code = """
import json as _agent_json
import sys as _agent_sys
import traceback as _agent_traceback
_agent_ipython = get_ipython()
if not getattr(_agent_ipython, "_spyder_code_agent_traceback_hook", False):
    _agent_original_showtraceback = _agent_ipython.showtraceback
    def _agent_showtraceback(*args, **kwargs):
        try:
            _agent_error = "".join(_agent_traceback.format_exception(*_agent_sys.exc_info()))
            with open(__ERROR_FILE__, "w", encoding="utf-8") as _agent_handle:
                _agent_json.dump({"error": _agent_error}, _agent_handle)
        except Exception:
            pass
        return _agent_original_showtraceback(*args, **kwargs)
    _agent_ipython.showtraceback = _agent_showtraceback
    _agent_ipython._spyder_code_agent_traceback_hook = True
""".replace("__ERROR_FILE__", repr(str(self.error_file)))
        try:
            shell.execute(code, hidden=True)
        except TypeError:
            shell.execute(code)
        self._hooked_shells.add(id(shell))
        if self.error_timer is None:
            self.error_timer = QTimer(self)
            self.error_timer.setInterval(500)
            self.error_timer.timeout.connect(self.check_error_file)
            self.error_timer.start()

    def check_error_file(self):
        try:
            data = json.loads(self.error_file.read_text(encoding="utf-8"))
            self.error_file.unlink()
        except FileNotFoundError:
            return
        except (OSError, ValueError):
            return  # Kernel may still be writing; retry at the next timer tick.
        error_text = str(data.get("error", ""))
        if error_text and "NoneType: None" not in error_text:
            self.on_auto_error(error_text)

    def on_auto_error(self, error_text):
        self.chat_display.append("<b>Error detected.</b> Running local diagnosis and provider analysis if configured.")
        self.user_input.setPlainText(f"Diagnose and fix this traceback:\n{error_text}")
        self.send_message()

    def _show_local_diagnosis(self, text):
        report = AgentService().diagnose(text)
        items = "".join(f"<li>{escape(step)}</li>" for step in report.debugging_steps)
        self.chat_display.append(
            f"<b>Local diagnosis: {escape(report.error_type)}</b><br>{escape(report.summary)}<ul>{items}</ul>"
        )

    def send_message(self):
        user_text = self.user_input.toPlainText().strip()
        if not user_text:
            return
        self.chat_display.append(f"<b>You:</b> {escape(user_text)}")
        self.user_input.clear()
        if is_traceback_text(user_text):
            self._show_local_diagnosis(user_text)
        self.update_context_scope()
        if not all((self.current_base_url, self.current_api_key, self.current_model_name)):
            self.chat_display.append(
                "<b>System:</b> Local diagnosis is ready. Configure an optional OpenAI-compatible provider "
                "to enable read-only project-agent exploration."
            )
            return
        self.send_btn.setEnabled(False)
        self.plan_display.setText("Waiting for the provider to produce a debugging plan…")
        self.activity_display.clear()
        self.worker = LLMWorker(
            user_text,
            self.current_base_url,
            self.current_api_key,
            self.current_model_name,
            self._project_context(),
        )
        self.worker.activity_received.connect(self.on_agent_activity)
        self.worker.result_received.connect(self.on_agent_result)
        self.worker.error_occurred.connect(self.show_error)
        self.worker.finished_response.connect(self.on_finished)
        self.worker.start()

    def on_agent_activity(self, activity):
        if not isinstance(activity, AgentActivity):
            return
        if activity.kind == "plan":
            self.plan_display.setPlainText(activity.message)
        else:
            colour = "#444" if activity.ok else "#b00020"
            self.activity_display.append(f"<span style='color:{colour}'>{escape(activity.message)}</span>")

    def on_agent_result(self, result):
        if not isinstance(result, AgentRun):
            self.show_error("Provider returned an invalid agent result.")
            return
        self.activity_display.append(
            f"<span style='color:#444'>Agent completed {result.tool_calls} read-only tool call(s).</span>"
        )
        self._render_suggestion(result.suggestion)

    def on_response(self, text):
        """Compatibility path for callers that still provide one JSON suggestion."""
        try:
            suggestion = parse_suggestion(text)
        except AgentResponseError as error:
            self.show_error(str(error))
            return
        self._render_suggestion(suggestion)

    def _render_suggestion(self, suggestion):
        self.pending_fix = suggestion.fixed_code or None
        self.pending_fix_file = suggestion.fixed_file
        self.pending_fix_editor = (
            self._request_editor if suggestion.fixed_file == CURRENT_EDITOR_CONTEXT_FILE else None
        )
        self.apply_btn.setEnabled(bool(self.pending_fix))
        parts = []
        if suggestion.error_type:
            parts.append(f"<b>Agent: {escape(suggestion.error_type)}</b><br>{escape(suggestion.description)}")
        elif suggestion.description:
            parts.append(f"<b>Agent diagnosis:</b><br>{escape(suggestion.description)}")
        if suggestion.evidence:
            parts.append(f"<b>Evidence collected:</b><br>{escape(suggestion.evidence).replace(chr(10), '<br>')}")
        if suggestion.solution:
            parts.append(f"<b>Suggested approach:</b><br>{escape(suggestion.solution).replace(chr(10), '<br>')}")
        if suggestion.example:
            parts.append(f"<pre>{escape(suggestion.example)}</pre>")
        if self.pending_fix:
            target = (
                "the current editor"
                if suggestion.fixed_file == CURRENT_EDITOR_CONTEXT_FILE
                else suggestion.fixed_file or "the current editor"
            )
            parts.append(f"<b>Patch ready for:</b> {escape(target)}. Review it, then click Apply fix.")
        self.chat_display.append("<hr>".join(parts) or "<b>Agent:</b> No structured advice returned.")

    def apply_fix(self):
        if not self.pending_fix:
            return
        try:
            if self.pending_fix_file == CURRENT_EDITOR_CONTEXT_FILE:
                if self.pending_fix_editor is None:
                    raise ValueError(
                        "The editor used for this suggestion is no longer available; no file was changed."
                    )
                self.pending_fix_editor.set_text(self.pending_fix)
            elif self.pending_fix_file:
                candidates = [Path(path) for path in self.selected_files if Path(path).name == self.pending_fix_file]
                if len(candidates) != 1:
                    raise ValueError("The suggested file is not a uniquely selected context file; no file was changed.")
                target = candidates[0]
                temporary = target.with_name(f".{target.name}.spyder-code-agent.tmp")
                temporary.write_text(self.pending_fix, encoding="utf-8")
                os.replace(temporary, target)
                if self.editor is not None:
                    self.editor.load(str(target))
            else:
                if self.editor is None:
                    raise ValueError("No active Spyder editor is available for this patch.")
                self.editor.get_current_editor().set_text(self.pending_fix)
        except (OSError, ValueError, AttributeError) as error:
            self.show_error(f"Patch was not applied: {error}")
            return
        self.pending_fix = None
        self.pending_fix_file = ""
        self.pending_fix_editor = None
        self.apply_btn.setEnabled(False)
        self.chat_display.append("<b>Fix applied.</b> Review and run the changed code before keeping it.")

    def show_error(self, message):
        self.chat_display.append(f"<b style='color:#b00020'>Code Agent error:</b> {escape(str(message))}")

    def on_finished(self):
        self.send_btn.setEnabled(True)

    def update_actions(self):
        pass
