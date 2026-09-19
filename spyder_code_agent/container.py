"""Qt adapter for the provider-neutral diagnostic agent.

Only this module depends on Spyder/Qt.  The core remains usable in a normal
Python process and does not require an API client at import time.
"""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from html import escape
from pathlib import Path

from qtpy.QtCore import QThread, QTimer, Signal
from qtpy.QtWidgets import (
    QCheckBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
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
from .patch_review import PatchReviewService, PatchValidationError
from .project_context import CURRENT_EDITOR_NAME, ProjectContext

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
        self._request_context = None
        self._request_originals = {}
        self._request_project_paths = {}
        self.patch_review_service = None
        self.patch_proposal = None
        self.patch_checkboxes = {}
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
        self.chat_display = QTextBrowser()
        self.chat_display.setOpenExternalLinks(False)
        self.context_scope = QLabel()
        self.plan_display = QTextBrowser()
        self.plan_display.setMaximumHeight(70)
        self.activity_display = QTextBrowser()
        self.activity_display.setMaximumHeight(120)
        self.patch_proposal_label = QLabel("Patch proposal")
        self.patch_proposal_panel = QWidget()
        self.patch_proposal_layout = QVBoxLayout()
        self.patch_proposal_panel.setLayout(self.patch_proposal_layout)
        self.user_input = QTextEdit()
        self.user_input.setMaximumHeight(90)

        self.settings_btn = QPushButton("Settings")
        self.settings_btn.clicked.connect(self.set_api)
        self.send_btn = QPushButton("Send")
        self.send_btn.clicked.connect(self.send_message)
        self.apply_btn = QPushButton("Apply selected patches")
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
        layout.addWidget(QLabel("Agent plan"))
        layout.addWidget(self.plan_display)
        layout.addWidget(QLabel("Tool activity"))
        layout.addWidget(self.activity_display)
        layout.addWidget(self.patch_proposal_label)
        layout.addWidget(self.patch_proposal_panel)
        layout.addWidget(self.chat_display)
        layout.addWidget(self.user_input)
        layout.addLayout(buttons)
        central = QWidget()
        central.setLayout(layout)
        self.setLayout(QVBoxLayout())
        self.layout().addWidget(central)
        self._set_patch_review_visible(False)
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
        project = f"Project: {escape(root.name)}" if root is not None else "No active Spyder project"
        editor = "active editor available" if self._get_current_editor() is not None else "no active editor"
        access = "safe project files available on request" if root is not None else "project files unavailable"
        self.context_scope.setText(f"{project} · {editor} · {access}")

    def _project_context(self):
        self._request_editor = self._get_current_editor()
        active_content = self.get_current_file_content()
        self._request_project_paths = {}
        self._request_originals = {}
        if self._request_editor is not None:
            self._request_originals[CURRENT_EDITOR_CONTEXT_FILE] = active_content
        self._request_context = ProjectContext(
            project_root=self._active_project_root(),
            active_editor_text=active_content,
            active_editor_name=CURRENT_EDITOR_CONTEXT_FILE,
            active_editor_available=self._request_editor is not None,
        )
        return self._request_context

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
        if self._request_context is not None:
            # Project files become eligible for a patch only after the agent
            # used the read-only tool to inspect them in this exact request.
            # Store the local source snapshots and resolved, project-contained
            # paths for the later stale check and user-approved write.
            for name, content in result.file_snapshots.items():
                path = self._request_context.approved_project_file(name)
                if path is not None:
                    self._request_originals[name] = content
                    self._request_project_paths[name] = path
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
        self._clear_patch_review()
        if self._request_context is None:
            self._project_context()
        try:
            self.patch_review_service = PatchReviewService(self._request_context, self._request_originals)
            self.patch_proposal = self.patch_review_service.create_proposal(
                suggestion.patches, suggestion.fixed_file, suggestion.fixed_code
            )
        except PatchValidationError as error:
            self.patch_review_service = None
            self.patch_proposal = None
            self.show_error(f"Patch proposal was rejected: {error}")
        self.pending_fix = None
        self.pending_fix_file = ""
        self.pending_fix_editor = None
        self.apply_btn.setEnabled(bool(self.patch_proposal and self.patch_proposal.files))
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
        if self.patch_proposal and self.patch_proposal.files:
            self._render_patch_review()
            parts.append(
                "<b>Patch proposal ready.</b> Review each local diff, select the files you approve, then click "
                "Apply selected patches. Nothing is written until confirmation."
            )
        self.chat_display.append("<hr>".join(parts) or "<b>Agent:</b> No structured advice returned.")

    def apply_fix(self):
        if not self.patch_proposal or not self.patch_review_service:
            return
        approved = [target for target, checkbox in self.patch_checkboxes.items() if checkbox.isChecked()]
        if not approved:
            self.show_error("Select at least one reviewed patch before applying it.")
            return
        count = len(approved)
        confirmation = QMessageBox.question(
            self,
            "Approve project file changes",
            "Allow Code Agent to replace exactly these reviewed file"
            f"{'s' if count != 1 else ''}?\n\n"
            + "\n".join(f"• {target}" for target in approved)
            + "\n\nOnly these checked diffs will be written. This cannot be undone here.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirmation != QMessageBox.Yes:
            return
        current = self._current_patch_contents()
        results = self.patch_review_service.apply_selected(
            self.patch_proposal, approved, current, self._apply_patch_file
        )
        self._report_patch_results(results)
        self._clear_patch_review()

    def _current_patch_contents(self):
        current = {}
        for patch in self.patch_proposal.files:
            if patch.target == CURRENT_EDITOR_CONTEXT_FILE:
                try:
                    current[patch.target] = self._request_editor.toPlainText()
                except (AttributeError, RuntimeError):
                    current[patch.target] = None
            else:
                path = self._request_project_paths.get(patch.target)
                try:
                    current[patch.target] = path.read_text(encoding="utf-8") if path is not None else None
                except (OSError, UnicodeError):
                    current[patch.target] = None
        return current

    def _apply_patch_file(self, patch):
        try:
            if patch.target == CURRENT_EDITOR_CONTEXT_FILE:
                if self._request_editor is None:
                    raise ValueError(
                        "The editor used for this suggestion is no longer available; no file was changed."
                    )
                self._request_editor.set_text(patch.proposed_content)
            else:
                target = self._request_project_paths.get(patch.target)
                if target is None:
                    raise ValueError("The suggested project file was not read in this request; no file was changed.")
                PatchReviewService.atomic_write(target, patch.proposed_content)
                if self.editor is not None:
                    try:
                        self.editor.load(str(target))
                    except (AttributeError, RuntimeError):
                        pass
        except (OSError, ValueError, AttributeError):
            raise

    def _render_patch_review(self):
        for patch in self.patch_proposal.files:
            checkbox = QCheckBox(f"Approve {patch.target}")
            checkbox.setChecked(False)
            self.patch_checkboxes[patch.target] = checkbox
            diff = QTextBrowser()
            diff.setPlainText(patch.diff)
            diff.setMaximumHeight(180)
            self.patch_proposal_layout.addWidget(checkbox)
            self.patch_proposal_layout.addWidget(diff)
        self._set_patch_review_visible(True)

    def _clear_patch_review(self):
        if hasattr(self, "patch_proposal_layout"):
            while self.patch_proposal_layout.count():
                item = self.patch_proposal_layout.takeAt(0)
                widget = item.widget()
                if widget is not None:
                    widget.deleteLater()
        self.patch_checkboxes = {}
        self.patch_proposal = None
        self.patch_review_service = None
        if hasattr(self, "apply_btn"):
            self.apply_btn.setEnabled(False)
        self._set_patch_review_visible(False)

    def _set_patch_review_visible(self, visible):
        if hasattr(self, "patch_proposal_label"):
            self.patch_proposal_label.setVisible(visible)
        if hasattr(self, "patch_proposal_panel"):
            self.patch_proposal_panel.setVisible(visible)

    def _report_patch_results(self, results):
        lines = []
        for result in results:
            target = escape(result.target or "Patch proposal")
            lines.append(f"<b>{escape(result.status.title())}:</b> {target} — {escape(result.message)}")
        self.chat_display.append("<br>".join(lines))

    def show_error(self, message):
        self.chat_display.append(f"<b style='color:#b00020'>Code Agent error:</b> {escape(str(message))}")

    def on_finished(self):
        self.send_btn.setEnabled(True)

    def update_actions(self):
        pass
