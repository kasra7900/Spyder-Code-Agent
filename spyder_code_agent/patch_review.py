"""Safe, reviewable in-memory patch proposals independent of Spyder and Qt."""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from difflib import unified_diff
from hashlib import sha256
from pathlib import Path
from typing import Callable, Iterable, Mapping, Tuple

from .project_context import CURRENT_EDITOR_NAME, ProjectContext, safe_project_relative_path

MAX_PATCHES = 13  # The current editor plus the bounded selected-context list.
MAX_PATCH_FILENAME_CHARS = 240
MAX_PATCH_FILE_CHARS = 120_000
MAX_PATCH_TOTAL_CHARS = 300_000


class PatchValidationError(ValueError):
    """Raised when untrusted proposal data falls outside the review policy."""


def _fingerprint(content: str) -> str:
    return sha256(content.encode("utf-8")).hexdigest()


def _safe_target(value: object, context: ProjectContext) -> str:
    if not isinstance(value, str):
        raise PatchValidationError("Each patch file target must be a string.")
    if value != value.strip() or any(ord(character) < 32 for character in value):
        raise PatchValidationError("Patch target filenames cannot contain surrounding whitespace or control characters.")
    target = value
    if target != CURRENT_EDITOR_NAME and (
        not target
        or len(target) > MAX_PATCH_FILENAME_CHARS
        or safe_project_relative_path(target) is None
    ):
        raise PatchValidationError(
            "Patch targets must be safe project-relative source paths, never absolute, traversal, hidden, or sensitive paths."
        )
    if not context.patch_target_is_approved(target):
        raise PatchValidationError(
            f"Patch target '{target}' is not the current editor or an existing safe file in the active project."
        )
    return target


@dataclass(frozen=True)
class PatchFile:
    """One full-content replacement, held in memory until explicit approval."""

    target: str
    original_content: str
    proposed_content: str
    original_fingerprint: str

    @property
    def diff(self) -> str:
        lines = unified_diff(
            self.original_content.splitlines(keepends=True),
            self.proposed_content.splitlines(keepends=True),
            fromfile=f"{self.target} (current)",
            tofile=f"{self.target} (proposal)",
            lineterm="",
        )
        return "\n".join(lines) or "(No content changes proposed.)"

    def is_stale(self, current_content: object) -> bool:
        return not isinstance(current_content, str) or _fingerprint(current_content) != self.original_fingerprint


@dataclass(frozen=True)
class PatchProposal:
    """A validated, multi-file proposal and the source snapshots it was based on."""

    files: Tuple[PatchFile, ...] = ()

    @property
    def targets(self) -> Tuple[str, ...]:
        return tuple(patch.target for patch in self.files)

    def selected(self, targets: Iterable[object]) -> Tuple[PatchFile, ...]:
        requested = set(targets)
        return tuple(patch for patch in self.files if patch.target in requested)


@dataclass(frozen=True)
class PatchApplyResult:
    target: str
    status: str
    message: str


@dataclass(frozen=True)
class PatchApplyPlan:
    files: Tuple[PatchFile, ...] = ()
    results: Tuple[PatchApplyResult, ...] = ()

    @property
    def ready(self) -> bool:
        return bool(self.files) and not self.results


class PatchReviewService:
    """Validate proposals, create diffs, detect stale sources, and apply approved files.

    The service knows no Qt objects and does not discover filesystem paths. A
    caller must supply source snapshots and explicitly invoke ``apply_selected``
    after its own user-confirmation step.
    """

    def __init__(self, context: ProjectContext, originals: Mapping[str, str]) -> None:
        self.context = context
        self.originals = dict(originals)

    @staticmethod
    def validate_payload(patches: object, context: ProjectContext) -> Tuple[tuple[str, str], ...]:
        """Validate untrusted provider patch entries without performing I/O."""
        if patches is None:
            return ()
        if not isinstance(patches, (list, tuple)):
            raise PatchValidationError("'patches' must be a JSON array when supplied.")
        if len(patches) > MAX_PATCHES:
            raise PatchValidationError(f"A proposal may contain at most {MAX_PATCHES} files.")

        validated = []
        seen = set()
        total = 0
        for entry in patches:
            if not isinstance(entry, Mapping) or set(entry) != {"file", "content"}:
                raise PatchValidationError("Each patch must contain only string 'file' and 'content' fields.")
            target = _safe_target(entry["file"], context)
            content = entry["content"]
            if not isinstance(content, str):
                raise PatchValidationError("Patch content must be a string containing a full replacement file.")
            if len(content) > MAX_PATCH_FILE_CHARS:
                raise PatchValidationError(f"Patch content for {target} exceeds the per-file limit.")
            total += len(content)
            if total > MAX_PATCH_TOTAL_CHARS:
                raise PatchValidationError("Patch proposal exceeds the total content-size limit.")
            if target in seen:
                raise PatchValidationError(f"Patch target '{target}' appears more than once.")
            seen.add(target)
            validated.append((target, content))
        return tuple(validated)

    def create_proposal(
        self, patches: object = None, legacy_file: object = "", legacy_code: object = ""
    ) -> PatchProposal:
        """Build a proposal from new or legacy final-response fields."""
        if patches and legacy_code:
            raise PatchValidationError("A final response must use either 'patches' or legacy fixed_file/fixed_code, not both.")
        entries = self.validate_payload(patches, self.context)
        if not entries and legacy_code:
            entries = self.validate_payload(
                [{"file": legacy_file, "content": legacy_code}], self.context
            )
        if not entries:
            return PatchProposal()

        files = []
        for target, content in entries:
            if target not in self.originals or not isinstance(self.originals[target], str):
                raise PatchValidationError(
                    f"No approved source snapshot is available for '{target}'; request a fresh proposal."
                )
            original = self.originals[target]
            files.append(PatchFile(target, original, content, _fingerprint(original)))
        return PatchProposal(tuple(files))

    def prepare_application(
        self, proposal: PatchProposal, approved_targets: Iterable[object], current: Mapping[str, object]
    ) -> PatchApplyPlan:
        """Preflight all selected files so a stale source blocks every write."""
        selected = proposal.selected(approved_targets)
        if not selected:
            return PatchApplyPlan(results=(PatchApplyResult("", "skipped", "No patch files were selected."),))
        stale = {patch.target for patch in selected if patch.is_stale(current.get(patch.target))}
        if stale:
            results = []
            for patch in selected:
                if patch.target in stale:
                    results.append(
                        PatchApplyResult(patch.target, "stale", "Source changed since this proposal; request a fresh patch.")
                    )
                else:
                    results.append(
                        PatchApplyResult(patch.target, "skipped", "Not applied because another selected patch is stale.")
                    )
            return PatchApplyPlan(results=tuple(results))
        return PatchApplyPlan(files=selected)

    def apply_selected(
        self,
        proposal: PatchProposal,
        approved_targets: Iterable[object],
        current: Mapping[str, object],
        apply_one: Callable[[PatchFile], None],
    ) -> Tuple[PatchApplyResult, ...]:
        """Apply preflighted files and report every result without hiding a partial failure."""
        plan = self.prepare_application(proposal, approved_targets, current)
        if not plan.ready:
            return plan.results

        results = []
        for index, patch in enumerate(plan.files):
            try:
                apply_one(patch)
            except Exception as error:
                results.append(
                    PatchApplyResult(
                        patch.target,
                        "failed",
                        f"{type(error).__name__} while applying this approved patch.",
                    )
                )
                results.extend(
                    PatchApplyResult(remaining.target, "skipped", "Not applied after an earlier selected patch failed.")
                    for remaining in plan.files[index + 1 :]
                )
                break
            else:
                results.append(PatchApplyResult(patch.target, "applied", "Applied after explicit approval."))
        return tuple(results)

    @staticmethod
    def atomic_write(path: Path, content: str) -> None:
        """Atomically replace one caller-approved selected file with UTF-8 text."""
        if not isinstance(path, Path) or path.is_symlink() or not path.is_file():
            raise ValueError("Approved selected file is unavailable; no patch was applied.")
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".spyder-code-agent.tmp", dir=str(path.parent), text=True
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            try:
                temporary.unlink()
            except (FileNotFoundError, OSError):
                pass
