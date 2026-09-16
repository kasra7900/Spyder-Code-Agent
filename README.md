# Spyder Code Agent

Spyder Code Agent is a Spyder dock plugin for Python debugging. It captures local IPython tracebacks, gives deterministic local guidance immediately, and can optionally ask an OpenAI-compatible endpoint for a structured patch. It is especially useful for Python, scikit-learn, PyTorch, and TensorFlow/Keras workflows—but it does not install or import any ML framework.

## Supported versions

- Python **3.9–3.13**
- Spyder **6.0–6.1**
- Windows, macOS, and Linux, with a local Spyder IPython kernel

Spyder 5 and earlier are not supported because they use a different plugin API. Spyder 6.2+ and Python 3.14+ are intentionally rejected at startup until they are tested. A clear plugin-loader error states the detected unsupported version.

## How installation works

Spyder Code Agent consists of one Spyder dock plugin and an optional installer helper. They have different jobs:

| Component | What it does | When it is used |
| --- | --- | --- |
| `spyder-code-agent` | Adds the **Code Agent** pane to Spyder and provides diagnostics. | Every time Spyder runs. |
| `tools/install_into_spyder.py` | Verifies the selected Python really hosts Spyder, installs the plugin there, and checks that Spyder can discover it. | Once during setup or after upgrading. |

Changing **Tools > Preferences > Python interpreter** in Spyder changes the Python environment used by an IPython kernel. It does **not** change the Python environment that runs Spyder's UI or loads dock plugins. This distinction matters because the Code Agent pane must be installed in the latter environment; ML/DL libraries can remain in the former.

### Standalone Spyder

**Spyder Code Agent does not support the Spyder 6.0–6.1 standalone installer.** These standalone releases use an isolated runtime and do not provide a supported mechanism for installing third-party dock plugins. Installing this package in a project's Conda environment, venv, or the IPython console cannot make a pane appear in a separate standalone Spyder application.

For full integration, install Spyder in a Conda environment or virtual environment and install this plugin in that same Spyder-host environment. A future standalone-compatible release is only feasible when Spyder provides a stable, supported third-party plugin manager; this project will evaluate it once it is available and tested.

For users who must keep a standalone Spyder installation, the planned alternative is a separate, environment-level diagnostic agent that can analyze tracebacks and files but does not add a native Spyder pane.

## Install

Install into the **same Python environment that launches the Spyder desktop application**. This is *not* necessarily the interpreter displayed by a connected IPython kernel. A kernel can be any project environment, but the pane and its `spyder.plugins` entry point are loaded only by Spyder's host process.

> **Standalone Spyder limitation:** Spyder 6.0–6.1's standalone installer does not officially support third-party plugins. No Python package can make an entry point installed in a separate project environment appear in that standalone app. For third-party plugins, use a Conda or virtual-environment installation of Spyder. Spyder documents this limitation and is developing a plugin manager for 6.2.

```bash
# Recommended: create a dedicated environment, then install Spyder and the plugin.
python -m venv .venv
# Linux/macOS
. .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install "spyder-code-agent[spyder]"
spyder
```

To enable an OpenAI-compatible provider, install the optional extra:

```bash
python -m pip install "spyder-code-agent[openai]"
```

For development from this checkout:

```bash
python -m pip install --upgrade pip
python -m pip install -e ".[spyder,openai,test]"
spyder
```

Restart Spyder after installation. The Code Agent pane is available from **View > Panes > Code Agent**. The plugin is registered through the `spyder.plugins` package entry point; no manual plugin copy is needed.

### Existing Spyder installation

First find the Python executable that launches Spyder. If you start Spyder from a terminal, use the interpreter in that same environment. With Conda, this is normally the environment you activated before running `spyder`.

Install the plugin with that exact executable:

```bash
/path/to/spyder-host-python -m pip install spyder-code-agent
/path/to/spyder-host-python -m spyder_code_agent.doctor
```

For this source checkout, the helper verifies the chosen interpreter really contains Spyder, installs into it, and checks that Spyder can load the entry point:

```bash
python tools/install_into_spyder.py --spyder-python /path/to/spyder-host-python --editable
```

If the doctor says `Spyder is not installed in this environment`, you selected a project/kernel environment. Installing there can be useful for ML libraries used by the kernel, but it cannot add a UI pane to Spyder.

## Use

1. Code Agent includes the open editor as `current_editor.py`. Use **+ Add file** to add only the other Python files you want to share as context.
2. Run code in Spyder's local IPython console. A traceback triggers a local diagnosis automatically.
3. Local diagnostics explain common imports, paths, keys, types, shape/broadcast errors, memory exhaustion, and device issues without any API key.
4. For an optional LLM suggestion, use **Settings** to supply a base URL, API key, and model name for an OpenAI-compatible service, then send the traceback or question.
5. Review the proposed patch. **Apply fix** only changes the open editor or exactly one selected context file whose basename matches the provider response. Selected filenames must be unique; it never follows absolute paths or `..` paths supplied by a model.

Settings are stored outside the repository: `%APPDATA%\spyder-code-agent\settings.json` on Windows, or `$XDG_CONFIG_HOME/spyder-code-agent/settings.json` (normally `~/.config/...`) on macOS/Linux. On POSIX it is written with owner-only permissions. Do not commit this file, API keys, or provider URLs containing credentials. Existing `~/.agent_config` settings from earlier releases are read during upgrade and copied to the safer location the next time you save Settings.

## ML and deep-learning assistance

The agent detects scikit-learn, PyTorch, and TensorFlow/Keras clues in a traceback and adds framework-specific review guidance. It can help plan or review:

- preprocessing and leakage-safe train/validation/test splits;
- model boundaries, tensor/array shapes, dtypes, and labels;
- training/evaluation loops, suitable metrics, and checkpointing;
- deterministic seeds and experiment reproducibility;
- GPU/device availability and CPU/GPU placement checks.

These frameworks remain optional: install them in the Spyder kernel environment only when your project needs them. The plugin does not claim a model provider exists until its optional client is installed and Settings are complete.

## Architecture

- `spyder_code_agent.plugin`: the small Spyder 6 registration adapter.
- `spyder_code_agent.container`: Qt UI, explicit context selection, guarded IPython traceback hook, and patch application.
- `spyder_code_agent.diagnostics`: dependency-free traceback categorization and ML/DL guidance.
- `spyder_code_agent.agent`: provider-neutral prompt/response logic, strict response parsing, and a lazy OpenAI-compatible provider.
- `spyder_code_agent.compatibility`: Python/Spyder range checks with actionable errors.

The traceback hook is installed once per local kernel and writes a unique temporary JSON file that the dock widget polls. Remote kernels or kernels on another machine cannot use this local-file transport; paste the traceback into the pane instead.

## Project status and roadmap

### Available now

- [x] Spyder 6 dock plugin with host-environment installation checks.
- [x] Automatic local-IPython traceback capture and dependency-free Python diagnostics.
- [x] Optional OpenAI-compatible provider with structured, validated responses.
- [x] Safe patches for the active editor and explicitly selected context files.
- [x] ML/DL guidance for scikit-learn, PyTorch, and TensorFlow/Keras errors.

### Building toward a Spyder-native code agent

The current release is a debugging assistant, not a replacement for Codex or OpenCode. The following work will turn it into a tool-using agent while keeping user control over code and files:

- [ ] Add a project-scoped tool loop for listing files, reading files, and searching code.
- [ ] Show a short agent plan and live tool activity in the Spyder pane.
- [ ] Generate reviewable, multi-file diffs instead of applying opaque file replacements.
- [ ] Add explicit approvals before file writes, code execution, or test runs.
- [ ] Run selected tests in the configured Spyder kernel and summarize failures.
- [ ] Add ML/DL environment tools for GPU, CUDA, framework versions, tensor shapes, and training-loop checks.
- [ ] Add evaluation fixtures for debugging quality, tool safety, and regression testing.

The agent will remain project-scoped by default. It will not read files outside the approved project context, execute system commands, or modify files without a visible request and user confirmation.

## Troubleshooting

| Symptom | Resolution |
| --- | --- |
| Code Agent pane is missing | Run `/path/to/spyder-host-python -m spyder_code_agent.doctor`. If it reports a missing entry point, reinstall with that exact interpreter, then restart Spyder. |
| Spyder was installed with the standalone installer | Spyder 6.0–6.1 does not support installing third-party plugins there. Install Spyder in Conda/venv instead; a separate project environment cannot add the pane to the standalone app. |
| Plugin says the Spyder/Python version is unsupported | Use Python 3.9–3.13 and `spyder>=6.0,<6.2`; do not force-install across that boundary. |
| `OpenAI support is optional and is not installed` | Run `python -m pip install "spyder-code-agent[openai]"` in Spyder's environment. |
| `ModuleNotFoundError` in your code | Compare `sys.executable` in Spyder with the interpreter used for `pip install`. |
| No automatic traceback capture | Confirm the code ran in a local Spyder IPython console; remote kernels require pasting the traceback. |
| Patch will not apply | Add the target file explicitly, ensure its basename is unique among selected files, then review/retry. |

## Development and verification

```bash
python -m pip install -e ".[spyder,test]"
python -m pytest
python -m ruff check spyder_code_agent tests
python -m build
```

The test suite covers clean core import, metadata/entry-point declarations, version checks, traceback/ML diagnostics, provider response safety, and mocked Spyder plugin loading. Test real Spyder versions in isolated Python 3.9–3.13 environments before publishing.

## Publishing

1. Update the version in `pyproject.toml` and `spyder_code_agent/__init__.py` together.
2. Run the commands above in a clean virtual environment.
3. Inspect `dist/`, then upload with `python -m twine upload dist/*` using trusted publishing or a token stored outside this repository.

## License

MIT License. See [LICENSE](LICENSE).
