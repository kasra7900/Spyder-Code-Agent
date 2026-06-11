# Spyder Code Agent

![Python](https://img.shields.io/badge/Python-3.10%2B-blue)
![Spyder](https://img.shields.io/badge/Spyder-6.0%2B-red)
![License](https://img.shields.io/badge/License-MIT-green)

**AI‑powered coding assistant for Spyder IDE** – automatically detects runtime errors in your Python/ML scripts and fixes them with one click.

![Screenshot placeholder](https://via.placeholder.com/800x400?text=Spyder+Code+Agent)

## Overview

Spyder Code Agent is a plugin that turns Spyder into a **self‑debugging IDE**. It listens to errors thrown in the IPython console, sends the relevant code (your selected files + current editor) to any OpenAI‑compatible LLM, and presents a ready‑to‑apply fix. No manual copy‑paste of tracebacks.

Perfect for **machine learning and data science** workflows where you frequently tweak code and run into `KeyError`, `ValueError`, shape mismatches, or missing imports.

## ✨ Key Features

- **Automatic error capture** – Hooks into Spyder's IPython console; no need to type anything.
- **Selective file context** – Choose exactly which `.py` files the agent can see (not your whole project).
- **One‑click code replacement** – After the agent suggests a fix, click **Apply Fix** and the file is updated instantly.
- **Works with any OpenAI‑compatible API** – Use OpenAI, local LLMs (via vLLM, Ollama, LM Studio), or any custom endpoint.
- **Persistent API settings** – Base URL, API key, and model name are saved in `~/.agent_config`.
- **Built for Spyder 6+** – Seamlessly docks into the IDE.

## 📋 Prerequisites

- **Spyder IDE** 6.0 or higher
- **Python** 3.8+
- An OpenAI‑compatible API endpoint and key (e.g., [OpenAI](https://platform.openai.com/), [Groq](https://groq.com/), [LocalAI](https://localai.io/), etc.)

## ⚙️ Installation

### From PyPI (recommended)

```bash
pip install spyder-code-agent
```

### From source (for development)

```bash
git clone https://github.com/kasra7900/Spyder-Code-Agent.git
cd Spyder-Code-Agent
pip install -e .
```

## 🚀 Usage

### 1. First launch – API settings

When you first open the Code Agent pane, it will automatically show the **API Settings** dialog:

- **Base URL** – e.g., `https://api.openai.com/v1` or `http://localhost:1234/v1`
- **API Key** – your secret key
- **Model Name** – e.g., `Deepseek v3`, `gpt-4`, `llama3`, `codellama`

Save the settings – they are stored in `~/.agent_config` and reused next time.

> **Tip**: You can change these later by deleting the config file (`~/.agent_config`) and restarting Spyder – the dialog will appear again.

### 2. Add files to the context

Use the **+ Add file** button to select Python files (`.py`) that the agent should be allowed to read. These files will be included in every analysis.

- You can add multiple files.
- The plugin does **not** scan your whole project – only the files you explicitly add.

### 3. Run your code as usual

Write your ML script (e.g., data loading, model training) in the Spyder editor. Execute it in the IPython console.

### 4. When an error occurs…

The plugin captures the traceback automatically. It then:

- Combines the error message + stack trace
- Adds the content of all added files + the current editor content
- Sends everything to the LLM

Within seconds, the agent replies with:

- **Error type** (e.g., `KeyError`, `IndexError`)
- **Description** (plain English)
- **Solution** (explanation + fixed code snippet)
- **Fixed code** (the complete corrected file)

### 5. Apply the fix

If you see a fix you trust, click the **✅ Apply Fix** button. The plugin will:

- Replace the target file (if the agent specified a filename) **or** the current editor content
- Reload the file in Spyder automatically

The error is resolved – no manual editing required.

## 🔧 Example workflow

Let’s say you have a script `train.py`:

```python
import pandas as pd
df = pd.read_csv('data.csv')
X = df.drop('target', axis=1)   # but the column is actually 'label'
```

Running this raises `KeyError: 'target'`. The agent sees the error, reads `train.py` (added to context), and suggests:

```
❌ KeyError
Description: Column 'target' does not exist in the DataFrame.
Solution: Change 'target' to the correct column name 'label'.
Fixed code:
   X = df.drop('label', axis=1)
```

You click **Apply Fix** – the file is updated instantly.

## ⚙️ Under the hood

- **Error hook**: The plugin injects a custom `showtraceback` function into the IPython shell. Every error is written to a temporary file (`~/.agent_last_error`) and picked up by a `QTimer`.
- **LLM prompt**: The agent constructs a strict JSON prompt (error type, description, solution, fixed code, fixed file). The LLM must respond in that exact format.
- **File modification**: When you click **Apply Fix**, the plugin writes the new content to the corresponding file path (or to the current editor if no file path is given). It creates no backup by default – be sure to use version control.

## 📄 Configuration

The plugin stores settings in `~/.agent_config` (JSON). Example:

```json
{
  "base_url": "https://api.openai.com/v1",
  "api_key": "sk-...",
  "model_name": "gpt-4"
}
```

You can edit this file manually, but it’s easier to use the dialog that appears on first launch.

## 🤝 Contributing

Issues and pull requests are welcome! To contribute:

1. Fork the repo.
2. Create a feature branch.
3. Install development dependencies: `pip install -e .`
4. Test your changes in Spyder.
5. Submit a PR.

## 📜 License

MIT License. See `LICENSE` for details.

---

**Made with ❤️ for the data science and Machine Learning community**



