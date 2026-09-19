import json

import pytest

from spyder_code_agent.agent_loop import AgentLoop, AgentProtocolError
from spyder_code_agent.project_context import CURRENT_EDITOR_NAME, ProjectContext


class SequenceProvider:
    def __init__(self, responses):
        self.responses = list(responses)
        self.prompts = []

    def complete(self, prompt):
        self.prompts.append(prompt)
        return self.responses.pop(0)


def _final(**answer):
    return json.dumps({"type": "final", "answer": answer})


def test_agent_loop_records_plan_and_completed_tool_activity(tmp_path):
    (tmp_path / "model.py").write_text("def preprocess_batch(x):\n    return x\n", encoding="utf-8")
    provider = SequenceProvider(
        [
            json.dumps(
                {
                    "type": "plan",
                    "plan": "Inspect the traceback and locate preprocess_batch.",
                    "tool_call": {"tool": "search_project", "arguments": {"query": "preprocess_batch"}},
                }
            ),
            _final(
                error_type="TypeError",
                description="The symbol is defined in model.py.",
                solution="Check the call signature.",
            ),
        ]
    )

    result = AgentLoop(provider, ProjectContext(project_root=tmp_path)).run("TypeError: bad call")

    assert result.plan.startswith("Inspect")
    assert result.tool_calls == 1
    assert result.activities[0].message.startswith("Plan:")
    assert result.activities[1].message == "Searching project for 'preprocess_batch' — complete"
    assert "Tool result for search_project" in provider.prompts[1]
    assert "Completed tool calls" in provider.prompts[1]
    assert "Return JSON only" in provider.prompts[1]
    assert "Do not include a 'plan' field" in provider.prompts[1]


def test_agent_loop_maps_common_read_file_alias_to_the_safe_project_tool(tmp_path):
    (tmp_path / "model.py").write_text("def convert(value):\n    return value\n", encoding="utf-8")
    provider = SequenceProvider(
        [
            json.dumps(
                {
                    "type": "plan",
                    "plan": "Search files, then inspect a safe match.",
                    "tool_call": {"tool": "search_code", "arguments": {"pattern": "convert"}},
                }
            ),
            json.dumps({"type": "tool_call", "tool": "read_file", "arguments": {"filename": "model.py"}}),
            _final(description="convert returns its input."),
        ]
    )

    result = AgentLoop(provider, ProjectContext(project_root=tmp_path)).run("Find convert")

    assert result.tool_calls == 2
    assert result.activities[1].tool == "search_project"
    assert result.activities[2].tool == "read_project_file"
    assert "def convert(value)" in provider.prompts[-1]


def test_agent_loop_ignores_non_actionable_gateway_metadata(tmp_path):
    (tmp_path / "model.py").write_text("VALUE = 1\n", encoding="utf-8")
    provider = SequenceProvider(
        [
            json.dumps(
                {
                    "type": "plan",
                    "plan": "Inspect project files.",
                    "tool_call": {"tool": "list_project_files", "arguments": {}},
                    "provider_metadata": {"trace": "ignored"},
                }
            ),
            json.dumps(
                {
                    "type": "tool_call",
                    "tool": "get_runtime_info",
                    "arguments": {},
                    "reasoning": "ignored",
                }
            ),
            json.dumps(
                {
                    "type": "final",
                    "answer": {"description": "Project files and runtime were inspected.", "confidence": 0.8},
                    "usage": {"output_tokens": 20},
                }
            ),
        ]
    )

    result = AgentLoop(provider, ProjectContext(project_root=tmp_path)).run("debug this")

    assert result.tool_calls == 2
    assert result.suggestion.description == "Project files and runtime were inspected."


def test_agent_loop_normalizes_common_function_call_gateway_envelopes(tmp_path):
    (tmp_path / "model.py").write_text("VALUE = 1\n", encoding="utf-8")
    provider = SequenceProvider(
        [
            json.dumps(
                {
                    "type": "plan",
                    "plan": "Inspect the project.",
                    "tool_call": {"name": "list_project_files", "parameters": "{}"},
                }
            ),
            json.dumps(
                {
                    "type": "function_call",
                    "function": {"name": "get_runtime_info", "arguments": "{}"},
                }
            ),
            json.dumps({"answer": {"description": "Gateway envelopes were normalized."}}),
        ]
    )

    result = AgentLoop(provider, ProjectContext(project_root=tmp_path)).run("debug this")

    assert result.tool_calls == 2
    assert result.suggestion.description == "Gateway envelopes were normalized."


@pytest.mark.parametrize(
    "responses, message",
    [
        (["not json"], "malformed agent JSON"),
        ([json.dumps({"type": "plan", "plan": "x", "tool_call": {"tool": "shell", "arguments": {}}})], "disallowed tool"),
        ([json.dumps({"type": "plan", "plan": "x", "tool_call": {"tool": "read_project_file", "arguments": {"path": "../x.py"}}})], "invalid tool arguments"),
    ],
)
def test_agent_loop_rejects_malformed_or_unsafe_provider_tool_requests(responses, message):
    with pytest.raises(AgentProtocolError, match=message):
        AgentLoop(SequenceProvider(responses), ProjectContext()).run("debug this")


def test_agent_loop_enforces_maximum_tool_calls(tmp_path):
    provider = SequenceProvider(
        [
            json.dumps(
                {
                    "type": "plan",
                    "plan": "Use the editor twice.",
                    "tool_call": {"tool": "get_active_editor", "arguments": {}},
                }
            ),
            json.dumps({"type": "tool_call", "tool": "get_runtime_info", "arguments": {}}),
            json.dumps({"type": "tool_call", "tool": "get_active_editor", "arguments": {}}),
        ]
    )

    with pytest.raises(AgentProtocolError, match=r"tool-call limit \(1\) reached"):
        AgentLoop(provider, ProjectContext(), max_tool_calls=1).run("debug this")


def test_agent_loop_stops_a_duplicate_tool_and_requests_a_final_answer(tmp_path):
    (tmp_path / "test.py").write_text("def convert(value):\n    return value\n", encoding="utf-8")
    provider = SequenceProvider(
        [
            json.dumps(
                {
                    "type": "plan",
                    "plan": "Search for convert and explain the result.",
                    "tool_call": {"tool": "search_project", "arguments": {"query": "convert"}},
                }
            ),
            json.dumps({"type": "tool_call", "tool": "search_project", "arguments": {"query": "convert"}}),
            _final(description="convert is defined in test.py and returns its input."),
        ]
    )

    result = AgentLoop(provider, ProjectContext(project_root=tmp_path)).run("Find convert")

    assert result.tool_calls == 1
    assert "Skipped duplicate request" in result.activities[-1].message
    assert "Do not call any tool" in provider.prompts[-1]


def test_agent_loop_can_request_a_final_answer_when_the_tool_budget_is_reached():
    provider = SequenceProvider(
        [
            json.dumps(
                {
                    "type": "plan",
                    "plan": "Inspect runtime and summarize.",
                    "tool_call": {"tool": "get_runtime_info", "arguments": {}},
                }
            ),
            json.dumps({"type": "tool_call", "tool": "get_active_editor", "arguments": {}}),
            _final(description="The runtime information was collected."),
        ]
    )

    result = AgentLoop(provider, ProjectContext(), max_tool_calls=1).run("Inspect runtime")

    assert result.tool_calls == 1
    assert "tool-call limit (1) was reached" in provider.prompts[-1]
    assert "python_version" in provider.prompts[-1]


def test_agent_loop_reports_no_project_as_limited_context_not_a_filesystem_fallback():
    provider = SequenceProvider(
        [
            json.dumps(
                {
                    "type": "plan",
                    "plan": "Try the project listing.",
                    "tool_call": {"tool": "list_project_files", "arguments": {}},
                }
            ),
            _final(description="Only active-editor and runtime context is available."),
        ]
    )

    result = AgentLoop(provider, ProjectContext()).run("debug this")

    assert result.tool_calls == 1
    assert result.activities[-1].message == "Blocked: Listing safe project files"
    assert "No active Spyder project is open" in provider.prompts[0]
    assert "project_available" in provider.prompts[1]


def test_agent_loop_requires_project_search_before_a_function_import_conclusion(tmp_path):
    (tmp_path / "test.py").write_text("def convert(value):\n    return value\n", encoding="utf-8")
    provider = SequenceProvider(
        [
            json.dumps(
                {
                    "type": "plan",
                    "plan": "Inspect the open editor first.",
                    "tool_call": {"tool": "get_active_editor", "arguments": {}},
                }
            ),
            _final(description="No convert function was found in the editor."),
            json.dumps({"type": "tool_call", "tool": "search_project", "arguments": {"query": "convert"}}),
            _final(description="convert is defined in test.py."),
        ]
    )

    result = AgentLoop(
        provider, ProjectContext(project_root=tmp_path, active_editor_available=True, active_editor_text="data = {}")
    ).run("Inspect convert and every direct caller/importer before proposing a patch.")

    assert result.tool_calls == 2
    assert "must first call search_project" in provider.prompts[2]
    assert result.suggestion.description == "convert is defined in test.py."


def test_agent_loop_requires_reads_of_files_named_by_the_user(tmp_path):
    (tmp_path / "test.py").write_text("def convert(value):\n    return value\n", encoding="utf-8")
    (tmp_path / "1.py").write_text("print('caller')\n", encoding="utf-8")
    provider = SequenceProvider(
        [
            json.dumps(
                {
                    "type": "plan",
                    "plan": "List the project before reviewing imports.",
                    "tool_call": {"tool": "list_project_files", "arguments": {}},
                }
            ),
            _final(description="No import change is needed."),
            json.dumps({"type": "tool_call", "tool": "read_project_file", "arguments": {"path": "test.py"}}),
            _final(description="Only test.py was inspected."),
            json.dumps({"type": "tool_call", "tool": "read_project_file", "arguments": {"path": "1.py"}}),
            _final(description="Both named files were inspected."),
        ]
    )

    result = AgentLoop(provider, ProjectContext(project_root=tmp_path)).run(
        "Import convert from test.py into 1.py."
    )

    assert result.tool_calls == 3
    assert "explicitly named these project files" in provider.prompts[2]
    assert "1.py" in provider.prompts[4]
    assert result.suggestion.description == "Both named files were inspected."


def test_agent_loop_rejects_patch_outside_active_project():
    provider = SequenceProvider(
        [
            json.dumps({"type": "plan", "plan": "Explain the error."}),
            _final(description="Advice", fixed_file="unapproved.py", fixed_code="print('no')"),
        ]
    )

    with pytest.raises(AgentProtocolError, match="outside the current editor"):
        AgentLoop(provider, ProjectContext()).run("debug this")


def test_agent_loop_accepts_current_editor_and_previously_read_project_file_patch(tmp_path):
    (tmp_path / "helpers.py").write_text("print('old')\n", encoding="utf-8")
    editor_provider = SequenceProvider(
        [
            json.dumps({"type": "plan", "plan": "Fix the open file."}),
            _final(fixed_file=CURRENT_EDITOR_NAME, fixed_code="print('fixed')"),
        ]
    )
    project_provider = SequenceProvider(
        [
            json.dumps(
                {
                    "type": "plan",
                    "plan": "Find and inspect the helper before proposing a fix.",
                    "tool_call": {"tool": "search_project", "arguments": {"query": "old"}},
                }
            ),
            json.dumps({"type": "tool_call", "tool": "read_project_file", "arguments": {"path": "helpers.py"}}),
            _final(fixed_file="helpers.py", fixed_code="print('fixed')"),
        ]
    )

    assert AgentLoop(
        editor_provider, ProjectContext(active_editor_available=True)
    ).run("debug").suggestion.fixed_code
    assert AgentLoop(
        project_provider, ProjectContext(project_root=tmp_path)
    ).run("debug").suggestion.fixed_file == "helpers.py"


def test_agent_loop_accepts_previously_read_project_patch_targets(tmp_path):
    (tmp_path / "helpers.py").write_text("print('helper')\n", encoding="utf-8")
    (tmp_path / "other.py").write_text("print('other')\n", encoding="utf-8")
    provider = SequenceProvider(
        [
            json.dumps(
                {
                    "type": "plan",
                    "plan": "Find and inspect both files before proposing coordinated edits.",
                    "tool_call": {"tool": "search_project", "arguments": {"query": "print"}},
                }
            ),
            json.dumps({"type": "tool_call", "tool": "read_project_file", "arguments": {"path": "helpers.py"}}),
            json.dumps({"type": "tool_call", "tool": "read_project_file", "arguments": {"path": "other.py"}}),
            _final(
                patches=[
                    {"file": CURRENT_EDITOR_NAME, "content": "print('editor')"},
                    {"file": "helpers.py", "content": "print('helper')"},
                ]
            ),
        ]
    )
    context = ProjectContext(project_root=tmp_path, active_editor_available=True)

    result = AgentLoop(provider, context).run("debug")

    assert len(result.suggestion.patches) == 2


def test_agent_loop_audits_unread_search_call_sites_before_accepting_a_patch(tmp_path):
    (tmp_path / "test.py").write_text(
        "def convert(value):\n    return value['name']\n", encoding="utf-8"
    )
    (tmp_path / "1.py").write_text(
        "from test import convert\nprint(convert({'name': 'Ada'}))\n", encoding="utf-8"
    )
    provider = SequenceProvider(
        [
            json.dumps(
                {
                    "type": "plan",
                    "plan": "Find convert and inspect its callers before proposing a safe change.",
                    "tool_call": {"tool": "search_project", "arguments": {"query": "convert"}},
                }
            ),
            json.dumps({"type": "tool_call", "tool": "read_project_file", "arguments": {"path": "test.py"}}),
            _final(
                patches=[
                    {"file": "test.py", "content": "def convert(value):\n    return '' if value is None else value['name']\n"}
                ]
            ),
            json.dumps({"type": "tool_call", "tool": "read_project_file", "arguments": {"path": "1.py"}}),
            _final(
                patches=[
                    {"file": "test.py", "content": "def convert(value):\n    return '' if value is None else value['name']\n"},
                    {"file": "1.py", "content": "from test import convert\nprint(convert({'name': 'Ada'}))\n"},
                ]
            ),
        ]
    )

    result = AgentLoop(provider, ProjectContext(project_root=tmp_path)).run(
        "Update all files needed for convert to handle None safely."
    )

    assert result.tool_calls == 3
    assert result.suggestion.patches[-1]["file"] == "1.py"
    assert "patch-completeness review" in provider.prompts[3]
    assert "Earlier patch draft" in provider.prompts[4]


def test_agent_loop_rejects_a_project_patch_that_was_not_read(tmp_path):
    (tmp_path / "unselected.py").write_text("print('old')\n", encoding="utf-8")
    provider = SequenceProvider(
        [
            json.dumps({"type": "plan", "plan": "Propose an edit."}),
            _final(patches=[{"file": "unselected.py", "content": "print('no')"}]),
        ]
    )

    with pytest.raises(AgentProtocolError, match="did not read"):
        AgentLoop(provider, ProjectContext(project_root=tmp_path)).run("debug")
