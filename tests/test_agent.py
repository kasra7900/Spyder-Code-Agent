import pytest

from spyder_code_agent.agent import (
    AgentConfigurationError,
    AgentResponseError,
    AgentService,
    parse_suggestion,
)


class FakeProvider:
    def __init__(self, response):
        self.response = response
        self.prompt = ""

    def complete(self, prompt):
        self.prompt = prompt
        return self.response


def test_agent_builds_ml_aware_prompt_and_returns_structured_suggestion():
    provider = FakeProvider(
        '{"error_type":"RuntimeError","description":"shape mismatch",'
        '"evidence":"batch dimension differs","solution":"align batches","example":"x = x.reshape(1, -1)",'
        '"fixed_file":"train.py","fixed_code":"print(1)"}'
    )
    service = AgentService(provider)

    suggestion = service.ask("RuntimeError: tensor shape mismatch on CUDA", "# FILE: train.py\n...")

    assert suggestion.fixed_file == "train.py"
    assert suggestion.fixed_code == "print(1)"
    assert suggestion.evidence == "batch dimension differs"
    assert "PyTorch" in provider.prompt
    assert "API keys" in provider.prompt


def test_parser_rejects_invalid_json_and_unsafe_paths():
    with pytest.raises(AgentResponseError):
        parse_suggestion("this is not json")

    suggestion = parse_suggestion('{"fixed_file":"../../secrets.py","fixed_code":"bad"}')
    assert suggestion.fixed_file == ""
    assert suggestion.fixed_code == "bad"

    windows_suggestion = parse_suggestion('{"fixed_file":"C:\\\\secrets.py","fixed_code":"bad"}')
    assert windows_suggestion.fixed_file == ""
    assert parse_suggestion('{"fixed_file":"C:secrets.py","fixed_code":"bad"}').fixed_file == ""


def test_parser_keeps_the_current_editor_sentinel_as_a_safe_filename():
    suggestion = parse_suggestion('{"fixed_file":"current_editor.py","fixed_code":"print(5)"}')

    assert suggestion.fixed_file == "current_editor.py"


def test_no_provider_has_actionable_error():
    with pytest.raises(AgentConfigurationError, match="No model provider"):
        AgentService().ask("KeyError: x", "")
