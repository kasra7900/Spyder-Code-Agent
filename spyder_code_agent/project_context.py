"""Safe, framework-independent context for the project-scoped agent tools."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path, PurePath, PureWindowsPath
import re
from typing import Dict, Mapping, Optional


CURRENT_EDITOR_NAME = "current_editor.py"
MAX_EDITOR_CHARS = 60_000
MAX_SELECTED_FILES = 12
MAX_SELECTED_FILE_CHARS = 40_000
MAX_SELECTED_TOTAL_CHARS = 120_000

_SENSITIVE_NAME = re.compile(
    r"(^|[._-])(env|secret|secrets|credential|credentials|token|tokens|private|apikey|api_key|settings|"
    r"password|passwd|id_rsa|id_ed25519)([._-]|$)",
    re.IGNORECASE,
)
_SENSITIVE_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".kdbx"}
_REDACTION_PATTERNS = (
    re.compile(r"(?i)(\b(?:api[_-]?key|token|secret|password|passwd)\b\s*[:=]\s*)([^\s,;]+)"),
    re.compile(r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----.*?-----END (?:[A-Z ]+ )?PRIVATE KEY-----", re.S),
)


def is_sensitive_name(name: str) -> bool:
    """Return whether a filename is commonly used to store secrets."""
    lowered = (name or "").strip().lower()
    return (
        not lowered
        or lowered == ".env"
        or lowered.startswith(".env.")
        or lowered
        in {
            ".npmrc",
            ".pypirc",
            "credentials",
            "credentials.json",
            "settings.json",
            "settings.py",
            "settings.ini",
            "settings.toml",
            "settings.yaml",
            "settings.yml",
        }
        or bool(_SENSITIVE_NAME.search(lowered))
        or Path(lowered).suffix in _SENSITIVE_SUFFIXES
    )


def redact_sensitive_text(text: str) -> str:
    """Redact obvious inline secrets before a tool result reaches a provider."""
    value = text or ""
    for pattern in _REDACTION_PATTERNS:
        if pattern.groups >= 2:
            value = pattern.sub(r"\1<redacted>", value)
        else:
            value = pattern.sub("<redacted private key>", value)
    return value


def safe_logical_name(value: str, fallback: str = CURRENT_EDITOR_NAME) -> str:
    """Return a display-only basename; never retain an adapter-supplied path."""
    if not isinstance(value, str) or not value.strip():
        return fallback
    windows_path = PureWindowsPath(value.strip())
    candidate = PurePath(value.strip()).name
    if windows_path.is_absolute() or candidate in {"", ".", ".."}:
        return fallback
    # A Windows-style name can arrive from a cross-platform Spyder adapter.
    # Keep only its basename, exactly as we do for POSIX-style input.
    return windows_path.name if "\\" in value else candidate


@dataclass(frozen=True)
class ProjectContext:
    """Data approved by the user/Spyder adapter for a single agent request.

    ``project_root`` is optional. Its absence deliberately gives project tools
    no filesystem boundary and therefore no filesystem access.
    """

    project_root: Optional[Path] = None
    active_editor_text: str = ""
    active_editor_name: str = CURRENT_EDITOR_NAME
    active_editor_available: Optional[bool] = None
    selected_context: Mapping[str, str] = field(default_factory=dict)

    def normalized_project_root(self) -> Optional[Path]:
        if self.project_root is None:
            return None
        try:
            root = Path(self.project_root)
            if not root.is_absolute():
                return None
            root = root.resolve(strict=True)
        except (OSError, RuntimeError):
            return None
        return root if root.is_dir() else None

    def active_editor(self) -> Dict[str, object]:
        return {
            "available": self.has_active_editor,
            "name": safe_logical_name(self.active_editor_name),
            "content": redact_sensitive_text((self.active_editor_text or "")[:MAX_EDITOR_CHARS]),
            "truncated": len(self.active_editor_text or "") > MAX_EDITOR_CHARS,
        }

    def selected_files(self) -> Dict[str, object]:
        """Return bounded explicit context, without paths and with redaction."""
        files = []
        total = 0
        for supplied_name, content in self.selected_context.items():
            if len(files) >= MAX_SELECTED_FILES:
                break
            name = safe_logical_name(supplied_name, fallback="selected_context.py")
            if is_sensitive_name(name) or not isinstance(content, str):
                continue
            remaining = max(MAX_SELECTED_TOTAL_CHARS - total, 0)
            if not remaining:
                break
            limited = content[: min(MAX_SELECTED_FILE_CHARS, remaining)]
            files.append(
                {
                    "name": name,
                    "content": redact_sensitive_text(limited),
                    "truncated": len(content) > len(limited),
                }
            )
            total += len(limited)
        return {"available": bool(files), "files": files, "truncated": len(files) < len(self.selected_context)}

    @property
    def has_active_editor(self) -> bool:
        """Whether Spyder supplied an editor, including an intentionally blank one."""
        if self.active_editor_available is not None:
            return self.active_editor_available
        return bool(self.active_editor_text)

    def patch_target_is_approved(self, filename: object) -> bool:
        """Whether a provider may propose a patch for this logical target.

        This is deliberately name-based: the Qt adapter retains the editor
        object and the explicitly selected paths, and is the only layer that
        can perform the user-triggered write.
        """
        if filename == CURRENT_EDITOR_NAME:
            return self.has_active_editor
        if not isinstance(filename, str) or is_sensitive_name(filename):
            return False
        approved = set()
        for name, content in self.selected_context.items():
            if len(approved) >= MAX_SELECTED_FILES:
                break
            logical_name = safe_logical_name(name, fallback="selected_context.py")
            if isinstance(content, str) and not is_sensitive_name(logical_name):
                approved.add(logical_name)
        return filename in approved
