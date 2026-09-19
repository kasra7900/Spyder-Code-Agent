"""Safe, framework-independent context for the project-scoped agent tools."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path, PurePath, PurePosixPath, PureWindowsPath
from typing import Dict, Optional

CURRENT_EDITOR_NAME = "current_editor.py"
MAX_EDITOR_CHARS = 60_000

# Project-wide access is intentionally limited to ordinary source and
# documentation files. These match the files exposed by the read-only tool
# registry; credentials, hidden metadata, environments, and generated output
# never become patch targets.
_SAFE_PROJECT_SUFFIXES = {".py", ".pyi", ".pyx", ".md", ".rst", ".txt", ".toml", ".yaml", ".yml", ".json"}
_IGNORED_PROJECT_PARTS = {
    ".git", ".hg", ".svn", ".venv", "venv", "env", "__pycache__", "build", "dist",
    "node_modules", ".tox", ".nox", ".mypy_cache", ".pytest_cache", ".ruff_cache",
}

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


def safe_project_relative_path(value: object) -> Optional[Path]:
    """Validate one portable, non-sensitive path relative to a project root."""
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        return None
    raw = value.strip()
    posix = PurePosixPath(raw)
    windows = PureWindowsPath(raw)
    if (
        raw != value
        or "\\" in raw
        or posix.is_absolute()
        or windows.is_absolute()
        or windows.drive
        or windows.root
        or ".." in posix.parts
        or ".." in windows.parts
    ):
        return None
    parts = tuple(part for part in posix.parts if part not in {"", "."})
    if not parts or any(
        part.startswith(".") or part.lower() in _IGNORED_PROJECT_PARTS or is_sensitive_name(part)
        for part in parts
    ):
        return None
    relative = Path(*parts)
    return relative if relative.suffix.lower() in _SAFE_PROJECT_SUFFIXES else None


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

    @property
    def has_active_editor(self) -> bool:
        """Whether Spyder supplied an editor, including an intentionally blank one."""
        if self.active_editor_available is not None:
            return self.active_editor_available
        return bool(self.active_editor_text)

    def approved_project_file(self, filename: object) -> Optional[Path]:
        """Resolve an existing safe project file, without following it outside.

        The caller receives the resolved path only after a user-approved
        project root and the source-file policy have both been verified.
        """
        relative = safe_project_relative_path(filename)
        root = self.normalized_project_root()
        if relative is None or root is None:
            return None
        candidate = root / relative
        try:
            resolved = candidate.resolve(strict=True)
        except (OSError, RuntimeError):
            return None
        try:
            resolved.relative_to(root)
        except ValueError:
            return None
        return resolved if resolved.is_file() and not resolved.is_symlink() else None

    def patch_target_is_approved(self, filename: object) -> bool:
        """Whether a provider may propose a patch for the active project."""
        if filename == CURRENT_EDITOR_NAME:
            return self.has_active_editor
        return self.approved_project_file(filename) is not None
