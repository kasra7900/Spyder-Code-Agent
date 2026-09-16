from spyder_code_agent.compatibility import check_runtime, version_tuple


def test_version_tuple_accepts_release_and_prerelease_versions():
    assert version_tuple("6.1.0rc1") == (6, 1, 0)
    assert version_tuple("not a version") == ()


def test_runtime_accepts_documented_range():
    assert check_runtime((3, 9), "6.0.0").supported
    assert check_runtime((3, 13), "6.1.9").supported


def test_runtime_rejects_unsupported_versions_with_actionable_messages():
    python_status = check_runtime((3, 14), "6.1.0")
    spyder_status = check_runtime((3, 12), "6.2.0")

    assert not python_status.supported
    assert "Python 3.9 through 3.13" in python_status.message
    assert not spyder_status.supported
    assert "Spyder 6.0 through 6.1" in spyder_status.message
