"""Provider-neutral agent orchestration and safe structured response parsing."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import PurePath, PureWindowsPath
from typing import Mapping, Optional, Protocol, Tuple

from .diagnostics import DiagnosticReport, diagnose_traceback, project_guidance


class AgentConfigurationError(RuntimeError):
    """Raised when an optional model provider is unavailable or misconfigured."""


class AgentResponseError(ValueError):
    """Raised when a provider response cannot be safely interpreted."""


def provider_error_message(error: Exception) -> str:
    """Turn common provider failures into actionable, secret-free UI text."""
    status = getattr(error, "status_code", None)
    text = str(error).lower()
    if status == 429:
        return "The model provider is rate-limiting requests (429). Wait briefly, then try Send again. No file was changed."
    if status in {401, 403}:
        return "The model provider rejected the API credentials (401/403). Check Settings; no file was changed."
    if status == 404:
        return "The configured model or API endpoint was not found (404). Check the model name and base URL in Settings."
    if (isinstance(status, int) and 500 <= status < 600) or "5xx" in text:
        return (
            "The model provider returned a temporary server error (5xx). Try Send again; if it repeats, "
            "choose another available model or check the provider status. No file was changed."
        )
    return "The model provider request failed. Check the endpoint, model, and network connection; no file was changed."


@dataclass(frozen=True)
class AgentSuggestion:
    error_type: str = ""
    description: str = ""
    evidence: str = ""
    solution: str = ""
    example: str = ""
    fixed_file: str = ""
    fixed_code: str = ""
    patches: Tuple[Mapping[str, object], ...] = ()


class Provider(Protocol):
    def complete(self, prompt: str) -> str:
        """Return a JSON-object response for a prompt."""


def _safe_relative_filename(value: object) -> str:
    if not isinstance(value, str):
        return ""
    cleaned = value.strip()
    path = PurePath(cleaned)
    windows_path = PureWindowsPath(cleaned)
    if (
        not cleaned
        or path.is_absolute()
        or windows_path.is_absolute()
        or windows_path.drive
        or windows_path.root
        or ".." in path.parts
        or ".." in windows_path.parts
        or "\\" in cleaned
    ):
        return ""
    return cleaned


def parse_suggestion(payload: str) -> AgentSuggestion:
    """Parse an LLM reply without ever trusting its requested filesystem path."""
    cleaned = (payload or "").strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", cleaned, re.S | re.I)
    if fenced:
        cleaned = fenced.group(1)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as error:
        raise AgentResponseError("The provider returned invalid JSON; no fix was applied.") from error
    if not isinstance(data, Mapping):
        raise AgentResponseError("The provider response must be a JSON object; no fix was applied.")

    def string(key: str) -> str:
        value = data.get(key, "")
        return value if isinstance(value, str) else str(value)

    # ``patches`` was added after the original single-file response schema.
    # Treat an omitted (or explicit null) field as an empty list so legacy
    # ``fixed_file`` / ``fixed_code`` responses continue to parse.
    patches = data.get("patches", [])
    if patches is None:
        patches = []
    if not isinstance(patches, list) or not all(isinstance(item, Mapping) for item in patches):
        raise AgentResponseError("The provider returned malformed patch proposal data; no patch was accepted.")

    return AgentSuggestion(
        error_type=string("error_type"),
        description=string("description"),
        evidence=string("evidence"),
        solution=string("solution"),
        example=string("example"),
        fixed_file=_safe_relative_filename(data.get("fixed_file")),
        fixed_code=string("fixed_code"),
        patches=tuple(dict(item) for item in patches),
    )


def build_prompt(user_request: str, context_code: str, report: DiagnosticReport) -> str:
    """Build a provider-agnostic request with deterministic diagnostics as context."""
    guidance = []
    for framework in report.frameworks:
        guidance.extend(project_guidance(framework))
    return "\n".join(
        [
            "You are a careful Python debugging assistant embedded in Spyder.",
            "Return ONLY a JSON object with error_type, description, evidence, solution, example, fixed_file, fixed_code, patches.",
            "Do not invent files. fixed_file must identify one supplied context file using a safe project-relative path, "
            "or be empty. When the supplied context file is current_editor.py, that exact name means the open editor only, never a disk path.",
            "For coordinated edits, use patches as an array of {file, content} full replacements. Each file must be "
            "current_editor.py or a safe project-relative supplied context file. Never include both patches and "
            "legacy fixed_file/fixed_code in one response.",
            "Do not include credentials, API keys, or secrets in code or explanations.",
            f"Local diagnosis: {report.error_type}: {report.summary}",
            "Local debugging steps: " + " | ".join(report.debugging_steps),
            "ML/DL review guidance: " + " | ".join(guidance or project_guidance("python")),
            "--- CONTEXT FILES ---",
            context_code or "(No file context supplied.)",
            "--- END CONTEXT ---",
            "User request:",
            user_request,
        ]
    )


class AgentService:
    """Coordinates deterministic diagnosis and an optional interchangeable provider."""

    def __init__(self, provider: Optional[Provider] = None) -> None:
        self.provider = provider

    def diagnose(self, traceback_text: str) -> DiagnosticReport:
        return diagnose_traceback(traceback_text)

    def prepare(self, user_request: str, context_code: str) -> str:
        return build_prompt(user_request, context_code, self.diagnose(user_request))

    def ask(self, user_request: str, context_code: str) -> AgentSuggestion:
        if self.provider is None:
            raise AgentConfigurationError(
                "No model provider is configured. Install spyder-code-agent[openai] and configure an API endpoint, "
                "or use the built-in local diagnosis."
            )
        return parse_suggestion(self.provider.complete(self.prepare(user_request, context_code)))


class OpenAICompatibleProvider:
    """Lazy OpenAI-compatible provider; importing the plugin never requires this extra."""

    def __init__(self, base_url: str, api_key: str, model_name: str) -> None:
        if not all((base_url.strip(), api_key.strip(), model_name.strip())):
            raise AgentConfigurationError("Base URL, API key, and model name are all required.")
        self.base_url = base_url.strip()
        self.api_key = api_key.strip()
        self.model_name = model_name.strip()

    def complete(self, prompt: str) -> str:
        try:
            from openai import OpenAI
        except ImportError as error:
            raise AgentConfigurationError(
                "OpenAI support is optional and is not installed. Run `pip install spyder-code-agent[openai]`."
            ) from error
        client = OpenAI(base_url=self.base_url, api_key=self.api_key)
        try:
            response = client.chat.completions.create(
                model=self.model_name,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a safe debugging agent. Follow the exact JSON protocol in the user message. "
                            "Return one JSON object only, with no Markdown or prose outside that object."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.2,
                response_format={"type": "json_object"},
            )
        except Exception as error:
            raise AgentResponseError(provider_error_message(error)) from error
        content = response.choices[0].message.content
        if not content:
            raise AgentResponseError("The provider returned an empty response.")
        return content
