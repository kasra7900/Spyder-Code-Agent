import importlib
import sys

import pytest

from spyder_code_agent.patch_review import (
    MAX_PATCH_FILE_CHARS,
    MAX_PATCHES,
    PatchReviewService,
    PatchValidationError,
)
from spyder_code_agent.project_context import CURRENT_EDITOR_NAME, ProjectContext


def _context_and_service(tmp_path):
    originals = {
        CURRENT_EDITOR_NAME: "def run():\n    return helper()\n",
        "helpers.py": "def helper():\n    return 1\n",
        "other.py": "VALUE = 1\n",
    }
    for name, content in originals.items():
        if name != CURRENT_EDITOR_NAME:
            (tmp_path / name).write_text(content, encoding="utf-8")
    context = ProjectContext(project_root=tmp_path, active_editor_available=True)
    return context, originals, PatchReviewService(context, originals)


def test_clean_patch_review_core_import_without_spyder_qt_or_openai(monkeypatch):
    for module_name in ("spyder", "qtpy", "openai"):
        monkeypatch.delitem(sys.modules, module_name, raising=False)

    assert importlib.import_module("spyder_code_agent.patch_review").PatchReviewService


def test_valid_multi_file_proposal_includes_editor_and_project_file_diffs(tmp_path):
    _, originals, service = _context_and_service(tmp_path)
    proposal = service.create_proposal(
        [
            {"file": CURRENT_EDITOR_NAME, "content": "def run():\n    return helper() + 1\n"},
            {"file": "helpers.py", "content": "def helper():\n    return 2\n"},
        ]
    )

    assert proposal.targets == (CURRENT_EDITOR_NAME, "helpers.py")
    assert "--- current_editor.py (current)" in proposal.files[0].diff
    assert "+    return helper() + 1" in proposal.files[0].diff
    assert proposal.files[1].original_content == originals["helpers.py"]


def test_legacy_single_file_patch_becomes_a_reviewable_proposal(tmp_path):
    _, _, service = _context_and_service(tmp_path)
    proposal = service.create_proposal(legacy_file="helpers.py", legacy_code="def helper():\n    return 7\n")

    assert proposal.targets == ("helpers.py",)
    assert "return 7" in proposal.files[0].diff


def test_project_relative_patch_target_is_supported(tmp_path):
    source = tmp_path / "src"
    source.mkdir()
    target = source / "helpers.py"
    target.write_text("def helper():\n    return 1\n", encoding="utf-8")
    context = ProjectContext(project_root=tmp_path)
    service = PatchReviewService(context, {"src/helpers.py": target.read_text(encoding="utf-8")})

    proposal = service.create_proposal(
        [{"file": "src/helpers.py", "content": "def helper():\n    return 2\n"}]
    )

    assert proposal.targets == ("src/helpers.py",)


@pytest.mark.parametrize(
    "patches",
    [
        [{"file": "unselected.py", "content": "x"}],
        [{"file": "../helpers.py", "content": "x"}],
        [{"file": "/helpers.py", "content": "x"}],
        [{"file": "C:\\helpers.py", "content": "x"}],
        [{"file": "settings.py", "content": "x"}],
        [{"file": "helpers.py", "content": "x"}, {"file": "helpers.py", "content": "y"}],
        [{"file": "helpers.py", "text": "x"}],
        ["not an object"],
    ],
)
def test_proposal_rejects_unsafe_duplicate_or_malformed_targets(tmp_path, patches):
    _, _, service = _context_and_service(tmp_path)

    with pytest.raises(PatchValidationError):
        service.create_proposal(patches)


def test_patch_count_and_content_size_limits_are_enforced(tmp_path, monkeypatch):
    for index in range(MAX_PATCHES + 1):
        (tmp_path / f"file_{index}.py").write_text("old", encoding="utf-8")
    context = ProjectContext(project_root=tmp_path)
    service = PatchReviewService(context, {f"file_{index}.py": "old" for index in range(MAX_PATCHES + 1)})
    many = [{"file": f"file_{index}.py", "content": "new"} for index in range(MAX_PATCHES + 1)]

    with pytest.raises(PatchValidationError, match="at most"):
        service.create_proposal(many)
    with pytest.raises(PatchValidationError, match="per-file"):
        service.create_proposal([{"file": "file_0.py", "content": "x" * (MAX_PATCH_FILE_CHARS + 1)}])

    monkeypatch.setattr("spyder_code_agent.patch_review.MAX_PATCH_TOTAL_CHARS", 3)
    with pytest.raises(PatchValidationError, match="total"):
        service.create_proposal([{"file": "file_0.py", "content": "four"}])


def test_stale_editor_or_disk_content_blocks_every_selected_patch(tmp_path):
    _, originals, service = _context_and_service(tmp_path)
    proposal = service.create_proposal(
        [
            {"file": CURRENT_EDITOR_NAME, "content": "changed editor"},
            {"file": "helpers.py", "content": "changed helper"},
        ]
    )
    plan = service.prepare_application(
        proposal,
        proposal.targets,
        {CURRENT_EDITOR_NAME: "user edited editor", "helpers.py": originals["helpers.py"]},
    )

    assert not plan.ready
    assert [result.status for result in plan.results] == ["stale", "skipped"]

    disk_plan = service.prepare_application(
        proposal,
        proposal.targets,
        {CURRENT_EDITOR_NAME: originals[CURRENT_EDITOR_NAME], "helpers.py": "user edited disk file"},
    )
    assert [result.status for result in disk_plan.results] == ["skipped", "stale"]


def test_selected_per_file_approval_applies_only_checked_targets(tmp_path):
    _, originals, service = _context_and_service(tmp_path)
    proposal = service.create_proposal(
        [
            {"file": CURRENT_EDITOR_NAME, "content": "editor replacement"},
            {"file": "helpers.py", "content": "helper replacement"},
        ]
    )
    applied = []
    results = service.apply_selected(
        proposal,
        ["helpers.py"],
        {CURRENT_EDITOR_NAME: originals[CURRENT_EDITOR_NAME], "helpers.py": originals["helpers.py"]},
        lambda patch: applied.append(patch.target),
    )

    assert applied == ["helpers.py"]
    assert [(result.target, result.status) for result in results] == [("helpers.py", "applied")]


def test_atomic_write_replaces_only_the_approved_selected_file(tmp_path):
    target = tmp_path / "helpers.py"
    target.write_text("old\n", encoding="utf-8")

    PatchReviewService.atomic_write(target, "new\n")

    assert target.read_text(encoding="utf-8") == "new\n"
    assert not list(tmp_path.glob(".helpers.py.*.spyder-code-agent.tmp"))


def test_atomic_write_rejects_a_selected_symlink(tmp_path):
    outside = tmp_path / "outside.py"
    outside.write_text("outside\n", encoding="utf-8")
    link = tmp_path / "helpers.py"
    try:
        link.symlink_to(outside)
    except (NotImplementedError, OSError):
        pytest.skip("Symlinks are unavailable on this platform")

    with pytest.raises(ValueError, match="unavailable"):
        PatchReviewService.atomic_write(link, "new\n")
    assert outside.read_text(encoding="utf-8") == "outside\n"


def test_partial_failure_is_reported_and_later_patches_are_skipped(tmp_path):
    _, originals, service = _context_and_service(tmp_path)
    proposal = service.create_proposal(
        [
            {"file": CURRENT_EDITOR_NAME, "content": "editor replacement"},
            {"file": "helpers.py", "content": "helper replacement"},
            {"file": "other.py", "content": "other replacement"},
        ]
    )
    attempts = []

    def apply_one(patch):
        attempts.append(patch.target)
        if patch.target == "helpers.py":
            raise OSError("disk is unavailable")

    results = service.apply_selected(proposal, proposal.targets, originals, apply_one)

    assert attempts == [CURRENT_EDITOR_NAME, "helpers.py"]
    assert [result.status for result in results] == ["applied", "failed", "skipped"]
    assert results[-1].target == "other.py"


def test_legacy_and_multi_file_fields_cannot_be_mixed(tmp_path):
    _, _, service = _context_and_service(tmp_path)

    with pytest.raises(PatchValidationError, match="either 'patches'"):
        service.create_proposal(
            [{"file": "helpers.py", "content": "new"}],
            legacy_file="helpers.py",
            legacy_code="other",
        )
