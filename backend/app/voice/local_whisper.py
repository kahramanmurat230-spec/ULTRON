"""Local-only Faster-Whisper loader for ULTRON.

Keeps STT sovereign: models are resolved from the local workspace and CUDA
runtime DLLs are discovered from the locally installed NVIDIA Python wheels.
No network/model download is performed here.
"""
from __future__ import annotations

import os
from pathlib import Path


_DLL_DIRS: list[object] = []


def _repo_root() -> Path:
    # backend/app/voice/local_whisper.py -> repo root is parents[3]
    return Path(__file__).resolve().parents[3]


def _configure_cuda_dlls() -> None:
    """Make locally installed CUDA DLLs visible on Windows."""
    if os.name != "nt":
        return
    site = Path(os.environ.get("PYTHONPATH", "")) if False else None
    candidates = [
        Path(os.sys.prefix) / "Lib" / "site-packages" / "nvidia" / "cublas" / "bin",
        Path(os.sys.prefix) / "Lib" / "site-packages" / "nvidia" / "cuda_nvrtc" / "bin",
        Path(os.sys.prefix) / "Lib" / "site-packages" / "nvidia" / "cudnn" / "bin",
    ]
    for directory in candidates:
        if not directory.is_dir():
            continue
        value = str(directory)
        if value not in os.environ.get("PATH", "").split(os.pathsep):
            os.environ["PATH"] = value + os.pathsep + os.environ.get("PATH", "")
        try:
            _DLL_DIRS.append(os.add_dll_directory(value))
        except (AttributeError, OSError):
            pass


def resolve_model(configured: str | None) -> Path:
    """Resolve a local Whisper model without contacting Hugging Face."""
    root = _repo_root()
    value = (configured or "").strip()
    candidates: list[Path] = []
    if value:
        raw = Path(value).expanduser()
        if raw.is_absolute():
            candidates.append(raw)
        else:
            candidates.extend((root / raw, root / "backend" / raw))
    # Safe local default for the ULTRON development tree.
    candidates.append(root / "models" / "whisper-tiny")
    for candidate in candidates:
        if candidate.exists() and (candidate / "model.bin").exists():
            return candidate.resolve()
    searched = ", ".join(str(p) for p in candidates)
    raise FileNotFoundError(f"Yerel Whisper modeli bulunamadı. Aranan: {searched}")


def load_whisper(configured: str | None = None):
    """Load local Whisper on CUDA when possible, otherwise CPU.

    Returns (model, info). CPU fallback is still fully local and offline.
    """
    from faster_whisper import WhisperModel

    model_path = resolve_model(configured)
    _configure_cuda_dlls()

    try:
        model = WhisperModel(str(model_path), device="cuda", compute_type="float16")
        return model, {
            "model": str(model_path),
            "device": "cuda",
            "compute_type": "float16",
            "local_only": True,
        }
    except Exception as cuda_exc:
        model = WhisperModel(str(model_path), device="cpu", compute_type="int8")
        return model, {
            "model": str(model_path),
            "device": "cpu",
            "compute_type": "int8",
            "local_only": True,
            "cuda_fallback": str(cuda_exc)[:300],
        }
