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
            _final(description="Only selected context is available."),
        ]
    )

    result = AgentLoop(provider, ProjectContext()).run("debug this")

    assert result.tool_calls == 1
    assert result.activities[-1].message == "Blocked: Listing safe project files"
    assert "No active Spyder project is open" in provider.prompts[0]
    assert "project_available" in provider.prompts[1]


def test_agent_loop_rejects_patch_outside_current_or_selected_context():
    provider = SequenceProvider(
        [
            json.dumps({"type": "plan", "plan": "Explain the error."}),
            _final(description="Advice", fixed_file="unapproved.py", fixed_code="print('no')"),
        ]
    )

    with pytest.raises(AgentProtocolError, match="outside the current editor"):
        AgentLoop(provider, ProjectContext()).run("debug this")


def test_agent_loop_accepts_current_editor_and_selected_file_patches():
    editor_provider = SequenceProvider(
        [
            json.dumps({"type": "plan", "plan": "Fix the open file."}),
            _final(fixed_file=CURRENT_EDITOR_NAME, fixed_code="print('fixed')"),
        ]
    )
    selected_provider = SequenceProvider(
        [
            json.dumps({"type": "plan", "plan": "Fix the selected file."}),
            _final(fixed_file="helpers.py", fixed_code="print('fixed')"),
        ]
    )

    assert AgentLoop(
        editor_provider, ProjectContext(active_editor_available=True)
    ).run("debug").suggestion.fixed_code
    assert AgentLoop(
        selected_provider, ProjectContext(selected_context={"helpers.py": "print('old')"})
    ).run("debug").suggestion.fixed_file == "helpers.py"
