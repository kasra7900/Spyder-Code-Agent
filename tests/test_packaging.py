from pathlib import Path

import spyder_code_agent


ROOT = Path(__file__).resolve().parents[1]


def test_package_core_imports_without_spyder_or_openai():
    assert spyder_code_agent.__version__ == "0.2.0"
    assert callable(spyder_code_agent.diagnose_traceback)


def test_project_metadata_has_single_build_configuration_and_plugin_entry_point():
    metadata = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    setup_shim = (ROOT / "setup.py").read_text(encoding="utf-8")

    assert "[build-system]" in metadata
    assert 'spyder = ["spyder>=6.0,<6.2"]' in metadata
    assert "dependencies = []" in metadata
    assert 'code_agent = "spyder_code_agent.plugin:CodeAgent"' in metadata
    assert 'spyder-code-agent-doctor = "spyder_code_agent.doctor:main"' in metadata
    assert "version=" not in setup_shim
