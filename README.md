# Spyder Code Agent

[![PyPI version](https://img.shields.io/pypi/v/spyder-code-agent.svg)](https://pypi.org/project/spyder-code-agent/)
[![Python versions](https://img.shields.io/pypi/pyversions/spyder-code-agent.svg)](https://pypi.org/project/spyder-code-agent/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Spyder plugin](https://img.shields.io/badge/Spyder-6.0+-blue.svg)](https://www.spyder-ide.org/)

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
