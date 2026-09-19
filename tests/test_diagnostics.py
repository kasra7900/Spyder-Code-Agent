from spyder_code_agent.diagnostics import diagnose_traceback, is_traceback_text, project_guidance


def test_traceback_detection_does_not_mislabel_a_normal_agent_question():
    assert not is_traceback_text("Search convert function in this project and explain how it is used.")
    assert is_traceback_text("Traceback (most recent call last):\nKeyError: 'label'")
    assert is_traceback_text("KeyError: 'label'")


def test_diagnoses_common_python_error():
    report = diagnose_traceback('Traceback (most recent call last):\nKeyError: "label"')

    assert report.error_type == "KeyError"
    assert "column" in report.likely_causes[0].lower()
    assert any("keys/columns" in step for step in report.debugging_steps)


def test_diagnoses_pytorch_shape_and_device_context_without_importing_torch():
    report = diagnose_traceback("RuntimeError: size mismatch for tensor on CUDA")

    assert "PyTorch" in report.frameworks
    assert any("shape" in item.lower() for item in report.likely_causes)
    assert any("cuda.is_available" in step for step in report.debugging_steps)


def test_framework_project_guidance_is_structured_and_optional():
    guidance = project_guidance("scikit-learn")

    assert any("Pipeline" in item for item in guidance)
    assert any("validation split" in item for item in guidance)
