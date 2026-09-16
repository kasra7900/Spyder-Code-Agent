"""Deterministic traceback analysis and ML/DL debugging guidance."""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import List


@dataclass(frozen=True)
class DiagnosticReport:
    error_type: str
    summary: str
    likely_causes: List[str] = field(default_factory=list)
    debugging_steps: List[str] = field(default_factory=list)
    frameworks: List[str] = field(default_factory=list)


_ERROR_LINE = re.compile(r"^([A-Za-z_][\w.]*?(?:Error|Exception|Warning))(?::\s*(.*))?$", re.M)


def _frameworks(text: str) -> List[str]:
    lower = text.lower()
    found = []
    for name, markers in (
        ("scikit-learn", ("sklearn", "scikit-learn")),
        ("PyTorch", ("torch", "cuda", "tensor")),
        ("TensorFlow/Keras", ("tensorflow", "tf.", "keras")),
    ):
        if any(marker in lower for marker in markers):
            found.append(name)
    return found


def diagnose_traceback(traceback_text: str) -> DiagnosticReport:
    """Provide useful local guidance even when no LLM provider is configured."""
    matches = list(_ERROR_LINE.finditer(traceback_text or ""))
    if matches:
        error_type, detail = matches[-1].group(1), matches[-1].group(2) or ""
    else:
        error_type, detail = "RuntimeError", "No standard exception line was found in the traceback."
    lower = (traceback_text + "\n" + detail).lower()
    causes: List[str] = []
    steps: List[str] = ["Read the last traceback frame first and verify the values used on that line."]

    if error_type in {"ModuleNotFoundError", "ImportError"}:
        causes.append("The package may be absent from the same Python environment used by Spyder.")
        steps.append("In Spyder's console, run `import sys; print(sys.executable)` and install the package there.")
    elif error_type == "KeyError":
        causes.append("A mapping key or DataFrame column name does not exist exactly as requested.")
        steps.append("Print available keys/columns and check whitespace, casing, and train/test schema drift.")
    elif error_type in {"TypeError", "AttributeError"}:
        causes.append("An object has an unexpected type or a value is None.")
        steps.append("Inspect `type(value)` and the inputs immediately before the failing call.")
    elif error_type == "FileNotFoundError":
        causes.append("The working directory or data path differs from what the script assumes.")
        steps.append("Print `Path.cwd()` and use paths derived from `Path(__file__).resolve().parent` where appropriate.")
    elif "shape" in lower or "size mismatch" in lower or "broadcast" in lower:
        causes.append("Tensor/array shapes or dimensions are incompatible with the operation or model input contract.")
        steps.append("Log shape, dtype, and batch dimension at every preprocessing/model boundary.")
    elif "out of memory" in lower or "cuda oom" in lower:
        causes.append("GPU/CPU memory use exceeds the available device memory.")
        steps.append("Reduce batch size, release stale tensors, and inspect device memory before retrying.")
    elif "device" in lower and ("cuda" in lower or "mps" in lower):
        causes.append("Inputs and model parameters may be on different devices.")
        steps.append("Check every tensor and model parameter device, then move them consistently.")

    frameworks = _frameworks(traceback_text)
    if "PyTorch" in frameworks:
        steps.append("For PyTorch, verify `torch.cuda.is_available()` and use `model.to(device)` and `batch.to(device)`.")
    if "TensorFlow/Keras" in frameworks:
        steps.append("For TensorFlow, inspect `tf.config.list_physical_devices('GPU')` and dataset element specs.")
    if "scikit-learn" in frameworks:
        steps.append("For scikit-learn, fit preprocessing only on training data and evaluate with a held-out split.")

    return DiagnosticReport(
        error_type=error_type,
        summary=detail or f"{error_type} raised during execution.",
        likely_causes=causes or ["Use the traceback frames and current values to isolate the failing contract."],
        debugging_steps=steps,
        frameworks=frameworks,
    )


def project_guidance(framework: str) -> List[str]:
    """Return framework-specific design/review prompts without importing that framework."""
    normalized = framework.lower()
    common = [
        "Set and record Python, NumPy, and framework random seeds.",
        "Keep preprocessing, model construction, training, and evaluation in separate testable functions.",
        "Report task-appropriate metrics and retain a validation split untouched by fitting decisions.",
    ]
    if normalized in {"sklearn", "scikit-learn"}:
        return common + [
            "Use a Pipeline/ColumnTransformer to prevent preprocessing leakage.",
            "Use cross-validation for model selection and report the final held-out score separately.",
        ]
    if normalized in {"pytorch", "torch"}:
        return common + [
            "Make device selection explicit and make DataLoader workers configurable.",
            "Separate `train()` and `eval()` paths; checkpoint model, optimizer, and epoch state.",
        ]
    if normalized in {"tensorflow", "keras", "tensorflow/keras"}:
        return common + [
            "Inspect tf.data element specs and batch shapes before fitting.",
            "Use callbacks for checkpoints, early stopping, and learning-rate scheduling.",
        ]
    return common
