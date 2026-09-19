"""Provider-neutral agent orchestration and safe structured response parsing."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import PurePath, PureWindowsPath
import re
from typing import Mapping, Optional, Protocol

from .diagnostics import DiagnosticReport, diagnose_traceback, project_guidance


class AgentConfigurationError(RuntimeError):
    """Raised when an optional model provider is unavailable or misconfigured."""


class AgentResponseError(ValueError):
    """Raised when a provider response cannot be safely interpreted."""


@dataclass(frozen=True)
class AgentSuggestion:
    error_type: str = ""
    description: str = ""
    evidence: str = ""
    solution: str = ""
    example: str = ""
    fixed_file: str = ""
    fixed_code: str = ""


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
        or len(path.parts) > 1
        or len(windows_path.parts) > 1
        or cleaned != path.name
        or cleaned != windows_path.name
    ):
        return ""
    return path.name


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

    return AgentSuggestion(
        error_type=string("error_type"),
        description=string("description"),
        evidence=string("evidence"),
        solution=string("solution"),
        example=string("example"),
        fixed_file=_safe_relative_filename(data.get("fixed_file")),
        fixed_code=string("fixed_code"),
    )


def build_prompt(user_request: str, context_code: str, report: DiagnosticReport) -> str:
    """Build a provider-agnostic request with deterministic diagnostics as context."""
    guidance = []
    for framework in report.frameworks:
        guidance.extend(project_guidance(framework))
    return "\n".join(
        [
            "You are a careful Python debugging assistant embedded in Spyder.",
            "Return ONLY a JSON object with error_type, description, evidence, solution, example, fixed_file, fixed_code.",
            "Do not invent files. fixed_file must be the basename of one supplied context file, or an empty string. "
            "When the supplied context file is current_editor.py, that exact name means the open editor only, never a disk path.",
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
        content = response.choices[0].message.content
        if not content:
            raise AgentResponseError("The provider returned an empty response.")
        return content
