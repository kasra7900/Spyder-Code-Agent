import json
import re
import os
from spyder.api.widgets.main_widget import PluginMainWidget
from qtpy.QtWidgets import (QVBoxLayout, QTextBrowser, QTextEdit, QPushButton, 
                            QWidget, QHBoxLayout, QDialog, QLabel, QLineEdit, 
                            QFormLayout, QMenu, QAction, QFileDialog)
from openai import OpenAI
from qtpy.QtCore import QThread, Signal, QTimer 

SETTINGS_FILE = os.path.join(os.path.expanduser("~"), ".agent_config")

# API setting form
class APIDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("API Settings")
        self.setFixedWidth(400)
        
        # create field
        self.model_name_input = QLineEdit()
        self.base_url_input = QLineEdit()
        self.api_key_input = QLineEdit()
        self.api_key_input.setEchoMode(QLineEdit.Password) # hide pass

        # form design
        form_layout = QFormLayout()
        form_layout.addRow(QLabel("Model Name"), self.model_name_input)
        form_layout.addRow(QLabel("Base URL"), self.base_url_input)
        form_layout.addRow(QLabel("API Key"), self.api_key_input)
        
        # submit
        self.save_btn = QPushButton("Save")
        self.cancel_btn = QPushButton("Cancel")
        self.save_btn.clicked.connect(self.accept) 
        self.cancel_btn.clicked.connect(self.reject) 
        
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        btn_layout.addWidget(self.cancel_btn)
        btn_layout.addWidget(self.save_btn)
        
        main_layout = QVBoxLayout()
        main_layout.addLayout(form_layout)
        main_layout.addLayout(btn_layout)
        self.setLayout(main_layout)

    def get_values(self):
        return self.base_url_input.text().strip(), self.api_key_input.text().strip(), self.model_name_input.text().strip()

# Define Agent
class LLMWorker(QThread):
    text_received = Signal(str)
    error_occurred = Signal(str)
    finished_response = Signal()
    
    def __init__(self, 
                 prompt, 
                 URL,
                 API,
                 model_name,
                 context_code=""):
        super().__init__()
        self.prompt = prompt
        self.context_code = context_code
        self.URL= URL
        self.API= API
        self.model_name= model_name
    
    def run(self):
        full_prompt = (
            "You are an expert AI Python Programming Assistant inside Spyder IDE.\n"
            "Always respond ONLY with a JSON object with these exact keys:\n"
            "- error_type: short error class name\n"
            "- description: one sentence explanation\n"
            "- solution: plain text fix\n"
            "- example: only the fixed code snippet\n"
            "- fixed_file: the filename that needs to be changed (e.g. 'model.py')\n"
            "- fixed_code: the complete corrected content of that file\n\n"
            "If no file context exists, only fix the problematic line.\n\n"
            f"--- PROJECT FILES ---\n{self.context_code}\n--------------------\n\n"
            f"User: {self.prompt}"
        )
        
        try:
            client = OpenAI(
                base_url= self.URL, 
                api_key= self.API
            )
            
            response= client.chat.completions.create(
                model= self.model_name,
                messages=[
                    {"role" : "system", "content" : full_prompt}
                    ],
                temperature=0.8,
                response_format={"type" : "json_object"}
                )
            answer= response.choices[0].message.content
            self.text_received.emit(answer)
   
        except Exception as e:
            self.error_occurred.emit(str(e))

        finally:
            self.finished_response.emit()
        
# Agent-Container 
class AgentContainer(PluginMainWidget):
    def __init__(self, 
                 *args, 
                 **kwargs):
        super().__init__(*args, **kwargs)
        
        self.current_base_url = ""
        self.current_api_key = ""
        self.current_model_name= ""
    
        self.load_setting_from_file()
    def get_title(self):
        return "Code Agent"
    
    def set_ipython(self, ipython):
        self.ipython = ipython
        self.shell = None
        
        ipython.sig_shellwidget_created.connect(self.inject_error_handler)
        
        shell = ipython.get_current_shellwidget()
        if shell:
            self.inject_error_handler(shell)    
    
    def set_editor(self, editor):
        self.editor = editor
        
    def set_projects(self, projects):
        self.projects= projects
    
    def get_project_files(self):
        result = {}
        for path in self.selected_files:
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    result[os.path.basename(path)] = f.read()
            except Exception:
                pass
        return result
        
    def on_project_closed(self, project_path):
        self.active_project = None
        self.add_file.setEnabled(False)
        self.add_file.setMenu(None)
        if hasattr(self, 'selected_project_files'):
            self.selected_project_files.clear()
            
    def update_project_files_menu(self, root_path=None):
        if not root_path:
            if not self.active_project:
                return
            if isinstance(self.active_project, str):
                root_path = self.active_project
            else:
                root_path = getattr(self.active_project, 'root_path', None)
        
        if not root_path or not os.path.exists(root_path):
            return
            
        menu = QMenu(self)
        found_files = False
            
        for root, dirs, files in os.walk(root_path):
            dirs[:] = [d for d in dirs if not d.startswith('.') and d not in ('venv', '__pycache__', 'env', '.venv')]
            for file in files:
                if file.endswith('.py'):
                    found_files = True
                    full_path = os.path.join(root, file)
                    rel_path = os.path.relpath(full_path, root_path)
                    
                    action = QAction(rel_path, self)
                    action.triggered.connect(lambda checked, path=full_path: self.toggle_project_file(path))
                    menu.addAction(action)
                    
        if found_files:
            self.add_file.setMenu(menu)
        else:
            self.add_file.setMenu(None)

    def toggle_project_file(self, file_path):
        filename = os.path.basename(file_path)
        
        if file_path in self.selected_project_files:
            del self.selected_project_files[file_path]
            self.chat_display.append(f"<b style='color:orange;'>System:</b> Removed <code>{filename}</code> from context.")
        else:
           
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                self.selected_project_files[file_path] = content
                self.chat_display.append(f"<b style='color:green;'>System:</b> Added <code>{filename}</code> to context.")
            except Exception as e:
                self.chat_display.append(f"<b style='color:red;'>Error reading file: {e}</b>")
    
    def inject_error_handler(self, shell):
        self.shell = shell
        print(f"✅ New shell: {shell}")
        self._connect_signals(shell)
    
    def _connect_signals(self, shell):
        print("✅ Connecting signals...")
        self.error_file = os.path.join(os.path.expanduser("~"), ".agent_last_error")
        
        code = f"""
        import traceback as _tb, json as _json
        
        _original_showtraceback = get_ipython().showtraceback
        
        def _agent_showtraceback(*args, **kwargs):
            try:
                with open('{self.error_file}', 'w') as f:
                    _json.dump({{'error': ''.join(_tb.format_exc())}}, f)
            except:
                pass
            _original_showtraceback(*args, **kwargs)
        
        get_ipython().showtraceback = _agent_showtraceback
        print("__AGENT_READY__")
        """
        shell.execute(code, hidden=False)
        
        # check file every 500ms
        self.error_timer = QTimer()
        self.error_timer.setInterval(500)
        self.error_timer.timeout.connect(self.check_error_file)
        self.error_timer.start()
        print("✅ Done!")
        
    def check_error_file(self):
        if not hasattr(self, 'error_file'):
            return
        try:
            if os.path.exists(self.error_file):
                with open(self.error_file, 'r') as f:
                    data = json.load(f)
                os.remove(self.error_file)
                error_text = data.get('error', '')
                if error_text and 'NoneType: None' not in error_text:
                    print("🔴 Error caught:", error_text[:100])
                    self.on_auto_error(error_text)
        except Exception:
            pass
        
    def on_auto_error(self, error_text):
        self.chat_display.append(
            "<b style='color:orange;'>🔴 Error detected — analyzing...</b>"
        )
        self.user_input.setPlainText(f"Fix this error:\n{error_text}")
        self.send_message()
        
    def load_setting_from_file(self):
        if os.path.exists(SETTINGS_FILE):
            try:
                with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.current_base_url = data.get("base_url", "")
                    self.current_api_key = data.get("api_key", "")
                    self.current_model_name = data.get("model_name", "")
            except Exception:
                pass
            
    def save_settings_to_file(self):
        try:
            data = {
                "base_url": self.current_base_url,
                "api_key": self.current_api_key,
                "model_name": self.current_model_name
            }
            with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=4)
            return True
        except Exception:
            return False
    
    def summarize_history(self):
        if len(self.conversation_history) < 10:
            return
        
        summary_prompt = "Summarize this conversation briefly, keeping key errors and solutions:"
        messages = [{"role": "system", "content": summary_prompt}]
        messages += self.conversation_history
        
        client = OpenAI(base_url=self.current_base_url, api_key=self.current_api_key)
        response = client.chat.completions.create(
            model=self.current_model_name,
            messages=messages,
            max_tokens=100
        )
        summary = response.choices[0].message.content
        
        self.conversation_history = [
            {"role": "assistant", "content": f"Previous conversation summary: {summary}"}
        ]
    
    def setup(self):
        self.conversation_history= []
        self.worker = None
        self.editor = None
        self.active_project = None
        self.selected_project_files = {}
    
        # create widget
        self.chat_display = QTextBrowser()
        self.chat_display.setOpenExternalLinks(False)
    
        self.selected_files = []  # لیست فایل‌های انتخاب شده
        self.add_file_btn = QPushButton("+ Add file")
        #self.add_file_btn.setFixedWidth(100)
        self.add_file_btn.clicked.connect(self.add_file)
    
        self.user_input = QTextEdit()
        self.user_input.setMaximumHeight(80)
    
        self.send_btn = QPushButton("Send")
        self.send_btn.setFixedWidth(100)
        self.send_btn.clicked.connect(self.send_message)
        
        self.apply_btn = QPushButton("✅ Apply Fix")
        self.apply_btn.setFixedWidth(120)
        self.apply_btn.clicked.connect(self.apply_fix)
        
        buttons_layout = QHBoxLayout()
        buttons_layout.addWidget(self.send_btn)     
        buttons_layout.addStretch()                
        buttons_layout.addWidget(self.apply_btn)
        
        layout = QVBoxLayout()
        layout.addWidget(self.add_file_btn, stretch=3)
        layout.addWidget(self.chat_display, stretch=4)
        layout.addWidget(self.user_input, stretch=2)
        layout.addLayout(buttons_layout, stretch=1)
    
        # set center 
        central = QWidget()
        central.setLayout(layout)
        self.setLayout(QVBoxLayout())
        self.layout().addWidget(central)

    def add_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select file", "", "Python files (*.py);;All files (*)"
        )
        if path and path not in self.selected_files:
            self.selected_files.append(path)
            self.chat_display.append(
                f"<b style='color:green;'>📄 Added:</b> {os.path.basename(path)}"
            )

    def set_API(self):
        dialog = APIDialog(self)
        
        dialog.base_url_input.setText(self.current_base_url)
        dialog.api_key_input.setText(self.current_api_key)
        dialog.model_name_input.setText(self.current_model_name)
        
        if dialog.exec_() == QDialog.Accepted:
            url, key, name = dialog.get_values()
            if url and key:
                self.current_base_url = url
                self.current_api_key = key
                self.current_model_name= name
                
                if self.save_settings_to_file():
                    self.chat_display.append("<b style='color:green;'>System:</b> API settings saved to config file.")
                else:
                    self.chat_display.append("<b style='color:red;'>System:</b> API updated but failed to save to file.")
            else:
                self.chat_display.append("<b style='color:red;'>System:</b> URL or Key cannot be empty.")
                
    def get_current_file_content(self):
        try:
            return self.editor.get_current_editor().toPlainText()
        except:
            return ""

    def send_message(self):
        if not self.current_base_url or not self.current_api_key or not self.current_model_name:
            self.chat_display.append(
                "<b style='color:orange;'>System:</b> Please set your API settings first."
            )
            self.set_API()
        if not self.current_base_url or not self.current_api_key or not self.current_model_name:
            return
        
        user_text = self.user_input.toPlainText().strip()
        if not user_text:
            return

        self.chat_display.append(f"<b>You:</b> {user_text}")
        self.user_input.clear()
        self.send_btn.setEnabled(False)
        self.chat_display.append("<b>Agent:</b> ...")

        context = self.get_project_files() or {"current": self.get_current_file_content()}
        context_str = "\n\n".join([
            f"# FILE: {name}\n{code}" 
            for name, code in context.items()
        ])

        self.worker = LLMWorker(
            prompt=user_text,
            context_code=context_str,
            URL= self.current_base_url,
            API= self.current_api_key,
            model_name= self.current_model_name
        )
        self.worker.text_received.connect(self.on_response)
        self.worker.error_occurred.connect(self.on_error)
        self.worker.finished_response.connect(self.on_finished)
        self.worker.start()

    def format_code_blocks(self, text):
        text = re.sub(
            r'```(?:python)?\n?(.*?)```',
            lambda m: (
                f'<div style="background:#1e1e2e; color:#cdd6f4; '
                f'padding:8px 12px; margin:6px 0; border-radius:4px; '
                f'font-family:monospace; white-space:pre;">'
                f'{m.group(1).strip()}</div>'
            ),
            text, flags=re.DOTALL
        )
        text = re.sub(r'`([^`]+)`', r'<code>\1</code>', text)
        return text
    
    def apply_fix(self):
        if not hasattr(self, 'pending_fix') or not self.pending_fix:
            return
        
        target_file = getattr(self, 'pending_fix_file', None)
        
        try:
            if target_file:
                for path in self.selected_files:
                    if os.path.basename(path) == target_file:
                        with open(path, 'w', encoding='utf-8') as f:
                            f.write(self.pending_fix)
    
                        self._reload_file_in_editor(path)
                        break
            else:
                editor = self.editor.get_current_editor()
                editor.set_text(self.pending_fix)
                
            self.pending_fix = None
            self.pending_fix_file = None
            self.apply_btn.setEnabled(False)
            self.chat_display.append("<b style='color:green;'>✅ Fix applied!</b>")
        except Exception as e:
            self.chat_display.append(f"<b style='color:red;'>Error: {e}</b>")
    
    def _reload_file_in_editor(self, path):
        try:
            self.editor.load(path)
        except Exception:
            pass
    
    def on_response(self, text):
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = {"solution": text}

        html_parts = []
        if "error_type" in data:
            html_parts.append(
                f"<div style='border-left:3px solid #c00; "
                f"padding:6px 10px; margin:4px 0; border-radius:4px;'>"
                f"<b style='color:#c00;'>❌ {data['error_type']}</b><br>"
                f"{data.get('description', '')}</div>"
            )
        if "solution" in data:
            solution = self.format_code_blocks(data['solution'])
            html_parts.append(
                f"<div style='border-left:3px solid #0a0; "
                f"padding:6px 10px; margin:4px 0; border-radius:4px;'>"
                f"<b style='color:#070;'>✅ Solution</b><br>{solution}</div>"
            )
        if "example" in data:
            code = data['example'].replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('\n', '<br>')
            html_parts.append(
                f"<div style='color:#cdd6f4; padding:8px 12px; "
                f"margin:4px 0; border-radius:4px; font-family:monospace;'>"
                f"{code}</div>"
            )
        if "fixed_code" in data and data['fixed_code']:
            self.pending_fix= data['fixed_code']
            html_parts.append(
                "<div style='margin:4px 0;'>"
                "<button onclick='void(0)' style='background:#0a0; color:white; "
                "border:none; padding:6px 14px; border-radius:4px; cursor:pointer;'>"
                "✅ Apply Fix</button></div>"
            )
            self.pending_fix = data["fixed_code"]
            self.apply_btn.setEnabled(True)
            
        if "fixed_file" in data:
            self.pending_fix_file = data["fixed_file"]
            html_parts.append(
                "<div style='margin:4px 0;'>"
                "<button onclick='void(0)' style='background:#0a0; color:white; "
                "border:none; padding:6px 14px; border-radius:4px; cursor:pointer;'>"
                "✅ File Fixed</button></div>"
            )

        cursor = self.chat_display.textCursor()
        cursor.movePosition(cursor.End)
        cursor.select(cursor.LineUnderCursor)
        cursor.removeSelectedText()
        cursor.deletePreviousChar()
        self.chat_display.setTextCursor(cursor)
        self.chat_display.append("<b>Agent:</b>")
        self.chat_display.insertHtml("".join(html_parts))

    def on_error(self, error_msg):
        self.chat_display.undo()
        self.chat_display.append(f"<b style='color:red;'>Error:</b> {error_msg}")
        
    def on_finished(self):
        self.send_btn.setEnabled(True)
        if len(self.conversation_history) >= 10:
            self.summarize_history()
        
    def update_actions(self):
        pass