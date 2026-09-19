import importlib
from pathlib import Path
import sys

import pytest

from spyder_code_agent.agent_tools import (
    MAX_LIST_RESULTS,
    MAX_READ_BYTES,
    MAX_SEARCH_RESULTS,
    ToolRegistry,
    canonical_tool_name,
)
from spyder_code_agent.project_context import CURRENT_EDITOR_NAME, ProjectContext, is_sensitive_name


def test_core_agent_modules_import_without_spyder_qt_or_openai(monkeypatch):
    for module_name in ("spyder", "qtpy", "openai"):
        monkeypatch.delitem(sys.modules, module_name, raising=False)

    assert importlib.import_module("spyder_code_agent.project_context").ProjectContext
    assert importlib.import_module("spyder_code_agent.agent_tools").ToolRegistry
    assert importlib.import_module("spyder_code_agent.agent_loop").AgentLoop


def test_active_editor_uses_a_logical_name_and_redacts_obvious_tokens():
    context = ProjectContext(
        active_editor_name="/private/location/example.py",
        active_editor_text="token = keep-this-private\nprint('ok')",
    )

    result = ToolRegistry(context).execute("get_active_editor", {})

    assert result.ok
    assert result.data["name"] == "example.py"
    assert "/private/location" not in result.data["name"]
    assert "keep-this-private" not in result.data["content"]


def test_project_file_tools_reject_traversal_and_require_a_prior_listing(tmp_path):
    (tmp_path / "main.py").write_text("value = 1\n", encoding="utf-8")
    registry = ToolRegistry(ProjectContext(project_root=tmp_path))

    assert not registry.execute("read_project_file", {"path": "../outside.py"}).ok
    assert not registry.execute("read_project_file", {"path": "/outside.py"}).ok
    assert not registry.execute("read_project_file", {"path": "C:outside.py"}).ok
    assert not registry.execute("read_project_file", {"path": "main.py"}).ok

    listed = registry.execute("list_project_files", {})
    assert listed.ok
    assert listed.data["files"] == ["main.py"]
    assert registry.execute("read_project_file", {"path": "main.py"}).data["content"] == "value = 1\n"


def test_common_model_tool_aliases_keep_canonical_tool_restrictions(tmp_path):
    (tmp_path / "main.py").write_text("value = convert(1)\n", encoding="utf-8")
    registry = ToolRegistry(ProjectContext(project_root=tmp_path))

    assert canonical_tool_name("read_file") == "read_project_file"
    assert canonical_tool_name("search_code") == "search_project"
    assert not canonical_tool_name("shell")
    assert registry.execute("list_files", {}).ok
    assert registry.execute("read_file", {"filename": "main.py"}).data["content"] == "value = convert(1)\n"
    assert registry.execute("search_code", {"pattern": "convert"}).ok
    assert not registry.execute("read_file", {"path": "../outside.py"}).ok


def test_search_results_authorize_only_the_matched_safe_file_for_reading(tmp_path):
    (tmp_path / "matched.py").write_text("def convert(value):\n    return value\n", encoding="utf-8")
    (tmp_path / "other.py").write_text("def unrelated():\n    pass\n", encoding="utf-8")
    registry = ToolRegistry(ProjectContext(project_root=tmp_path))

    assert registry.execute("search_project", {"query": "convert"}).ok
    assert registry.execute("read_project_file", {"path": "matched.py"}).ok
    assert not registry.execute("read_project_file", {"path": "other.py"}).ok


def test_project_tools_filter_sensitive_and_ignored_files(tmp_path):
    for name in (
        "main.py",
        ".env",
        "credentials.json",
        "settings.json",
        "id_rsa",
        ".git/objects.py",
        ".venv/lib/hidden.py",
        "build/output.py",
    ):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("needle = 1\n", encoding="utf-8")

    registry = ToolRegistry(ProjectContext(project_root=tmp_path))
    listed = registry.execute("list_project_files", {})
    search = registry.execute("search_project", {"query": "needle"})

    assert listed.data["files"] == ["main.py"]
    assert [match["path"] for match in search.data["matches"]] == ["main.py"]
    assert is_sensitive_name("settings.json")
    assert is_sensitive_name("production_settings.py")


def test_project_root_containment_rejects_a_symlink_escape(tmp_path):
    outside = tmp_path.parent / "outside-agent-tool.py"
    outside.write_text("needle = 'outside'\n", encoding="utf-8")
    link = tmp_path / "escape.py"
    try:
        link.symlink_to(outside)
    except (NotImplementedError, OSError):
        pytest.skip("Symlinks are unavailable on this platform")

    registry = ToolRegistry(ProjectContext(project_root=tmp_path))
    listed = registry.execute("list_project_files", {})
    searched = registry.execute("search_project", {"query": "needle"})

    assert "escape.py" not in listed.data["files"]
    assert not searched.data["matches"]


def test_list_read_and_search_limits_are_bounded(tmp_path):
    for index in range(MAX_LIST_RESULTS + 2):
        (tmp_path / f"file_{index:03}.py").write_text("needle\n", encoding="utf-8")
    (tmp_path / "aaa_large.py").write_text("x" * (MAX_READ_BYTES + 1), encoding="utf-8")
    registry = ToolRegistry(ProjectContext(project_root=tmp_path))

    listed = registry.execute("list_project_files", {})
    searched = registry.execute("search_project", {"query": "needle"})

    assert len(listed.data["files"]) == MAX_LIST_RESULTS
    assert listed.data["truncated"]
    assert len(searched.data["matches"]) == MAX_SEARCH_RESULTS
    assert searched.data["truncated"]
    assert "aaa_large.py" in listed.data["files"]
    assert not registry.execute("read_project_file", {"path": "aaa_large.py"}).ok


def test_invalid_query_and_no_project_are_clear_and_do_not_search_cwd(tmp_path):
    registry = ToolRegistry(ProjectContext())

    unavailable = registry.execute("list_project_files", {})
    blocked_search = registry.execute("search_project", {"query": "needle"})
    invalid_query = ToolRegistry(ProjectContext(project_root=tmp_path)).execute(
        "search_project", {"query": "needle\nother"}
    )

    assert not unavailable.ok
    assert unavailable.data == {"project_available": False}
    assert "No active Spyder project" in unavailable.error
    assert not blocked_search.ok
    assert not invalid_query.ok

    relative_root = ToolRegistry(ProjectContext(project_root=Path("."))).execute("list_project_files", {})
    assert not relative_root.ok


def test_selected_context_and_patch_targets_are_explicit_only():
    context = ProjectContext(
        active_editor_text="print('open')",
        selected_context={"helpers.py": "token: hidden\n", "settings.json": "do not share"},
    )
    selected = ToolRegistry(context).execute("get_selected_context", {})

    assert selected.ok
    assert selected.data["files"][0]["name"] == "helpers.py"
    assert "hidden" not in selected.data["files"][0]["content"]
    assert context.patch_target_is_approved(CURRENT_EDITOR_NAME)
    assert context.patch_target_is_approved("helpers.py")
    assert not context.patch_target_is_approved("other.py")
    assert not context.patch_target_is_approved("settings.json")
