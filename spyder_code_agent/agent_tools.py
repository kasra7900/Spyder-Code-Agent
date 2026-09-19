"""Allowlisted read-only tools for the project-scoped debugging agent."""

from __future__ import annotations

import importlib.util
import os
import platform
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Dict, Iterable, Mapping, Optional, Set

from .diagnostics import diagnose_traceback
from .project_context import (
    CURRENT_EDITOR_NAME,
    ProjectContext,
    is_sensitive_name,
    redact_sensitive_text,
)

MAX_LIST_RESULTS = 100
MAX_LIST_FILE_BYTES = 512_000
MAX_READ_BYTES = 120_000
MAX_SEARCH_FILES = 100
MAX_SEARCH_TOTAL_BYTES = 1_000_000
MAX_SEARCH_RESULTS = 50
MAX_SEARCH_QUERY_CHARS = 160
MAX_SEARCH_LINE_CHARS = 300
MAX_TRACEBACK_CHARS = 20_000
MAX_FILES_SCANNED = 2_000
MAX_DIRECTORIES_SCANNED = 500

_IGNORED_DIRECTORIES = {
    ".git", ".hg", ".svn", ".venv", "venv", "env", "__pycache__", "build", "dist",
    "node_modules", ".tox", ".nox", ".mypy_cache", ".pytest_cache", ".ruff_cache",
}
_SOURCE_SUFFIXES = {".py", ".pyi", ".pyx", ".md", ".rst", ".txt", ".toml", ".yaml", ".yml", ".json"}

TOOL_SPECS = {
    "get_active_editor": "Read the open editor. Use this for current_editor.py; it is not a project filesystem path. Arguments: {}.",
    "list_project_files": "List up to 100 safe, relevant project-relative files. Arguments: {}.",
    "read_project_file": "Read a previously listed project-relative file. Arguments: {\"path\": \"src/x.py\"}.",
    "search_project": "Literal text search in safe project source files. Arguments: {\"query\": \"symbol\"}.",
    "get_runtime_info": "Get safe Python/platform/framework availability only. Arguments: {}.",
    "diagnose_traceback": "Run local deterministic traceback diagnostics. Arguments: {\"traceback\": \"...\"}.",
}

# This small schema is shown verbatim to providers. Keep it separate from the
# longer descriptions above so models can copy exact names and argument keys.
CANONICAL_TOOL_DICTIONARY = {
    "get_active_editor": {},
    "list_project_files": {},
    "read_project_file": {"path": "project-relative path returned by search/list"},
    "search_project": {"query": "literal text to find"},
    "get_runtime_info": {},
    "diagnose_traceback": {"traceback": "traceback text"},
}

# Models often use these generic names despite being told the canonical names.
# Aliases are deliberately few and map only to tools with identical, already
# validated read-only semantics; they never add a capability.
TOOL_ALIASES = {
    "get_editor": "get_active_editor",
    "read_active_editor": "get_active_editor",
    "read_current_editor": "get_active_editor",
    "list_files": "list_project_files",
    "list_project": "list_project_files",
    "read_file": "read_project_file",
    "search_code": "search_project",
    "search_files": "search_project",
    "search_in_files": "search_project",
    "search_project_files": "search_project",
    "find_in_files": "search_project",
    "grep": "search_project",
    "get_runtime": "get_runtime_info",
}

_ARGUMENT_ALIASES = {
    "read_project_file": {"file": "path", "file_path": "path", "filename": "path"},
    "search_project": {
        "pattern": "query",
        "text": "query",
        "term": "query",
        "search_term": "query",
        "search_query": "query",
        "keyword": "query",
        "q": "query",
    },
    "diagnose_traceback": {"error": "traceback", "trace": "traceback"},
}


def canonical_tool_name(value: object) -> str:
    """Return an allowlisted canonical tool name, or an empty string."""
    if not isinstance(value, str):
        return ""
    return TOOL_ALIASES.get(value, value) if value in TOOL_ALIASES or value in TOOL_SPECS else ""


def canonical_tool_arguments(name: object, value: object) -> object:
    """Normalize harmless argument-envelope variations before validation.

    Only the one supported value for a tool is retained. Extra gateway
    metadata never reaches a tool and therefore cannot increase capability.
    """
    canonical_name = canonical_tool_name(name)
    if not canonical_name or not isinstance(value, Mapping):
        return value
    if canonical_name in {"get_active_editor", "list_project_files", "get_runtime_info"}:
        return {}
    aliases = _ARGUMENT_ALIASES.get(canonical_name, {})
    expected_key = {"read_project_file": "path", "search_project": "query", "diagnose_traceback": "traceback"}.get(
        canonical_name
    )
    if expected_key is None:
        return value
    accepted_keys = (expected_key, *aliases)
    for supplied_key in accepted_keys:
        supplied_value = value.get(supplied_key)
        if isinstance(supplied_value, Mapping):
            supplied_value = supplied_value.get("text", supplied_value.get("value"))
        if isinstance(supplied_value, str):
            return {expected_key: supplied_value}
    return value


@dataclass(frozen=True)
class ToolResult:
    name: str
    ok: bool
    data: Dict[str, Any]
    error: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _contains(root: Path, candidate: Path) -> bool:
    try:
        candidate.relative_to(root)
        return True
    except ValueError:
        return False


def _relative_path(value: object) -> Optional[Path]:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        return None
    raw = value.strip()
    posix = PurePosixPath(raw)
    windows = PureWindowsPath(raw)
    if (
        posix.is_absolute()
        or windows.is_absolute()
        or windows.drive
        or windows.root
        or ".." in posix.parts
        or ".." in windows.parts
    ):
        return None
    # Models must use portable project-relative paths; accepting backslashes on
    # POSIX would make policy review ambiguous.
    if "\\" in raw:
        return None
    parts = [part for part in posix.parts if part not in {"", "."}]
    return Path(*parts) if parts else None


def _is_ignored(relative: Path) -> bool:
    if any(
        part.lower() in _IGNORED_DIRECTORIES or part.startswith(".") or is_sensitive_name(part)
        for part in relative.parts[:-1]
    ):
        return True
    return is_sensitive_name(relative.name) or relative.suffix.lower() not in _SOURCE_SUFFIXES


def _read_text(path: Path, byte_limit: int) -> Optional[str]:
    try:
        with path.open("rb") as handle:
            data = handle.read(byte_limit + 1)
    except OSError:
        return None
    if len(data) > byte_limit or b"\x00" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


class ToolRegistry:
    """Executes only a fixed set of read-only tools against one context."""

    def __init__(self, context: ProjectContext) -> None:
        self.context = context
        self._listed_files: Set[str] = set()
        self._search_match_paths: Set[str] = set()
        self._search_queries: Set[str] = set()
        # Full source snapshots stay local. They are used only for the
        # reviewable patch's stale-content check; the provider receives the
        # redacted result below.
        self._read_snapshots: Dict[str, str] = {}
        self._scan_truncated = False

    @property
    def read_snapshots(self) -> Dict[str, str]:
        """Return copies of files explicitly read through the safe tool."""
        return dict(self._read_snapshots)

    @property
    def search_match_paths(self) -> Set[str]:
        """Return paths surfaced by project searches during this request."""
        return set(self._search_match_paths)

    @property
    def search_queries(self) -> Set[str]:
        """Return normalized literal searches completed during this request."""
        return set(self._search_queries)

    def execute(self, name: object, arguments: object) -> ToolResult:
        name = canonical_tool_name(name)
        if not name:
            return ToolResult(str(name), False, {}, "Unknown or disallowed tool requested.")
        arguments = canonical_tool_arguments(name, arguments)
        error = self.validate(name, arguments)
        if error:
            return ToolResult(name, False, {}, error)
        return getattr(self, f"_{name}")(arguments)

    def validate(self, name: object, arguments: object) -> str:
        """Validate untrusted tool input without performing any I/O."""
        name = canonical_tool_name(name)
        if not name:
            return "Unknown or disallowed tool requested."
        arguments = canonical_tool_arguments(name, arguments)
        if not isinstance(arguments, Mapping):
            return "Tool arguments must be a JSON object."
        validators = {
            "get_active_editor": self._empty_arguments,
            "list_project_files": self._empty_arguments,
            "get_runtime_info": self._empty_arguments,
            "read_project_file": self._path_arguments,
            "search_project": self._query_arguments,
            "diagnose_traceback": self._traceback_arguments,
        }
        return validators[name](arguments)

    @staticmethod
    def _empty_arguments(arguments: Mapping[str, object]) -> str:
        return "" if not arguments else "This tool does not accept arguments."

    @staticmethod
    def _path_arguments(arguments: Mapping[str, object]) -> str:
        if set(arguments) != {"path"} or _relative_path(arguments.get("path")) is None:
            return "read_project_file requires one safe project-relative 'path'."
        return ""

    @staticmethod
    def _query_arguments(arguments: Mapping[str, object]) -> str:
        query = arguments.get("query")
        if set(arguments) != {"query"} or not isinstance(query, str):
            return "search_project requires one string 'query'."
        if (
            not query.strip()
            or len(query) > MAX_SEARCH_QUERY_CHARS
            or "\x00" in query
            or any(ord(character) < 32 for character in query)
        ):
            return f"Search query must be 1-{MAX_SEARCH_QUERY_CHARS} visible characters."
        return ""

    @staticmethod
    def _traceback_arguments(arguments: Mapping[str, object]) -> str:
        traceback = arguments.get("traceback")
        if set(arguments) != {"traceback"} or not isinstance(traceback, str):
            return "diagnose_traceback requires one string 'traceback'."
        if not traceback.strip() or len(traceback) > MAX_TRACEBACK_CHARS:
            return f"Traceback must be 1-{MAX_TRACEBACK_CHARS} characters."
        return ""

    def _root_or_unavailable(self, name: str) -> Optional[ToolResult]:
        root = self.context.normalized_project_root()
        if root is None:
            return ToolResult(
                name,
                False,
                {"project_available": False},
                "No active Spyder project is open; project filesystem tools are unavailable.",
            )
        return None

    def _safe_project_files(self, root: Path) -> Iterable[tuple[Path, Path]]:
        scanned = 0
        directories_scanned = 0
        self._scan_truncated = False
        for directory, directories, filenames in os.walk(root, followlinks=False):
            if directories_scanned >= MAX_DIRECTORIES_SCANNED:
                self._scan_truncated = True
                return
            directories_scanned += 1
            directory_path = Path(directory)
            try:
                relative_directory = directory_path.relative_to(root)
            except ValueError:
                continue
            directories[:] = sorted(
                child
                for child in directories
                if not _is_ignored(relative_directory / child / "placeholder.py")
            )
            for filename in sorted(filenames):
                if scanned >= MAX_FILES_SCANNED:
                    self._scan_truncated = True
                    return
                scanned += 1
                relative = relative_directory / filename
                candidate = directory_path / filename
                if _is_ignored(relative) or not candidate.is_file():
                    continue
                try:
                    resolved = candidate.resolve(strict=True)
                    size = resolved.stat().st_size
                except (OSError, RuntimeError):
                    continue
                if not _contains(root, resolved) or size > MAX_LIST_FILE_BYTES:
                    continue
                yield relative, resolved

    def _get_active_editor(self, arguments: Mapping[str, object]) -> ToolResult:
        return ToolResult("get_active_editor", True, self.context.active_editor())

    def _list_project_files(self, arguments: Mapping[str, object]) -> ToolResult:
        unavailable = self._root_or_unavailable("list_project_files")
        if unavailable:
            return unavailable
        root = self.context.normalized_project_root()
        files = []
        for relative, _ in self._safe_project_files(root):
            if len(files) >= MAX_LIST_RESULTS:
                break
            name = relative.as_posix()
            self._listed_files.add(name)
            files.append(name)
        return ToolResult(
            "list_project_files",
            True,
            {
                "project_available": True,
                "files": files,
                "truncated": len(files) >= MAX_LIST_RESULTS or self._scan_truncated,
            },
        )

    def _read_project_file(self, arguments: Mapping[str, object]) -> ToolResult:
        # Some providers naturally treat the logical editor name as a project
        # path. Keep the sentinel out of the filesystem while serving the same
        # redacted, read-only editor content instead of wasting a tool call.
        if arguments["path"] == CURRENT_EDITOR_NAME:
            editor = self.context.active_editor()
            if not editor["available"]:
                return ToolResult(
                    "read_project_file", False, {}, "No active editor is available for current_editor.py."
                )
            return ToolResult(
                "read_project_file",
                True,
                {
                    "path": CURRENT_EDITOR_NAME,
                    "content": editor["content"],
                    "truncated": editor["truncated"],
                    "source": "active_editor",
                },
            )
        unavailable = self._root_or_unavailable("read_project_file")
        if unavailable:
            return unavailable
        relative = _relative_path(arguments["path"])
        name = relative.as_posix()
        if name not in self._listed_files:
            return ToolResult(
                "read_project_file", False, {}, "File must be selected from list_project_files before it can be read."
            )
        root = self.context.normalized_project_root()
        candidate = root / relative
        try:
            resolved = candidate.resolve(strict=True)
        except (OSError, RuntimeError):
            return ToolResult("read_project_file", False, {}, "Requested project file does not exist.")
        if not _contains(root, resolved) or _is_ignored(relative) or not resolved.is_file():
            return ToolResult("read_project_file", False, {}, "Requested file is outside the approved safe project scope.")
        content = _read_text(resolved, MAX_READ_BYTES)
        if content is None:
            return ToolResult("read_project_file", False, {}, "File is binary, unreadable, or exceeds the read limit.")
        self._read_snapshots[name] = content
        return ToolResult("read_project_file", True, {"path": name, "content": redact_sensitive_text(content)})

    def _search_project(self, arguments: Mapping[str, object]) -> ToolResult:
        unavailable = self._root_or_unavailable("search_project")
        if unavailable:
            return unavailable
        query = arguments["query"].strip()
        self._search_queries.add(query)
        root = self.context.normalized_project_root()
        matches, examined, total_bytes = [], 0, 0
        for relative, resolved in self._safe_project_files(root):
            if examined >= MAX_SEARCH_FILES or total_bytes >= MAX_SEARCH_TOTAL_BYTES or len(matches) >= MAX_SEARCH_RESULTS:
                break
            try:
                size = resolved.stat().st_size
            except OSError:
                continue
            if total_bytes + size > MAX_SEARCH_TOTAL_BYTES:
                continue
            content = _read_text(resolved, min(MAX_READ_BYTES, MAX_SEARCH_TOTAL_BYTES - total_bytes))
            examined += 1
            total_bytes += size
            if content is None:
                continue
            for line_number, line in enumerate(content.splitlines(), start=1):
                if query in line:
                    # A returned search match is as explicit and bounded as a
                    # list entry, so the agent may inspect that exact safe file
                    # in the next read-only step without a redundant listing.
                    self._listed_files.add(relative.as_posix())
                    self._search_match_paths.add(relative.as_posix())
                    matches.append(
                        {
                            "path": relative.as_posix(),
                            "line": line_number,
                            "text": redact_sensitive_text(line[:MAX_SEARCH_LINE_CHARS]),
                        }
                    )
                    if len(matches) >= MAX_SEARCH_RESULTS:
                        break
        return ToolResult(
            "search_project",
            True,
            {
                "query": query,
                "matches": matches,
                "files_examined": examined,
                "truncated": (
                    len(matches) >= MAX_SEARCH_RESULTS
                    or examined >= MAX_SEARCH_FILES
                    or total_bytes >= MAX_SEARCH_TOTAL_BYTES
                    or self._scan_truncated
                ),
            },
        )

    def _get_runtime_info(self, arguments: Mapping[str, object]) -> ToolResult:
        frameworks = {
            "scikit_learn": importlib.util.find_spec("sklearn") is not None,
            "pytorch": importlib.util.find_spec("torch") is not None,
            "tensorflow": importlib.util.find_spec("tensorflow") is not None,
        }
        return ToolResult(
            "get_runtime_info",
            True,
            {
                "python_version": platform.python_version(),
                "implementation": platform.python_implementation(),
                "platform": platform.system(),
                "frameworks_available": frameworks,
                "gpu": "Not probed: read-only tools do not import frameworks or execute device checks.",
            },
        )

    def _diagnose_traceback(self, arguments: Mapping[str, object]) -> ToolResult:
        report = diagnose_traceback(arguments["traceback"])
        return ToolResult("diagnose_traceback", True, asdict(report))
