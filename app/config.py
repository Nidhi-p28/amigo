from __future__ import annotations

import ctypes
import os
from pathlib import Path
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")


def _is_reparse_point(path: Path) -> bool:
    """Return True for symlinks/junctions (including Windows reparse points)."""
    try:
        if path.is_symlink():
            return True
        is_junction = getattr(path, "is_junction", None)
        if callable(is_junction) and is_junction():
            return True
        if os.name == "nt":
            attributes = ctypes.windll.kernel32.GetFileAttributesW(str(path))
            invalid = 0xFFFFFFFF
            file_attribute_reparse_point = 0x400
            return attributes not in (-1, invalid) and bool(attributes & file_attribute_reparse_point)
    except (OSError, AttributeError):
        # Fail closed: if link/reparse metadata cannot be inspected, deny the path.
        return True
    return False


def _read_stage() -> int:
    try:
        return max(1, min(3, int(os.getenv("AMIGO_STAGE", "1"))))
    except (TypeError, ValueError):
        return 1


STAGE = _read_stage()
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3.5:4b")
OLLAMA_KEEP_ALIVE = os.getenv("OLLAMA_KEEP_ALIVE", "30m").strip() or "30m"

def _read_positive_int(name: str, default: int, minimum: int = 256, maximum: int = 32768) -> int:
    try:
        value = int(os.getenv(name, str(default)))
        return max(minimum, min(maximum, value))
    except (TypeError, ValueError):
        return default

OLLAMA_NUM_CTX = _read_positive_int("OLLAMA_NUM_CTX", 4096, 1024, 32768)
OLLAMA_NUM_PREDICT = _read_positive_int("OLLAMA_NUM_PREDICT", 256, 64, 4096)
OLLAMA_THINKING_ENABLED = os.getenv("OLLAMA_THINKING", "false").strip().lower() in {"1", "true", "yes", "on"}
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash").strip()
WAKE_WORD_ENABLED = os.getenv("AMIGO_WAKE_WORD_ENABLED", "true").strip().lower() not in {"0", "false", "no", "off"}

# Product data is kept outside the source checkout. Resolve paths without creating
# them so importing app modules (including tests) has no filesystem side effects.
_local_app_data = Path(os.getenv("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
_documents = Path(os.getenv("USERPROFILE") or str(Path.home())) / "Documents"
APP_DATA_DIR = _local_app_data / "Amigo"
DATABASE_DIR = APP_DATA_DIR
DATABASE_PATH = DATABASE_DIR / "amigo.sqlite3"
LEGACY_DATABASE_PATH = PROJECT_ROOT / "data" / "amigo.sqlite3"

AMIGO_DOCUMENTS_DIR = _documents / "Amigo"
WORKSPACE_DIR = AMIGO_DOCUMENTS_DIR / "My Materials"
KNOWLEDGE_DIR = AMIGO_DOCUMENTS_DIR / "Knowledge"
LEGACY_WORKSPACE_DIR = PROJECT_ROOT / "workspace"


def prepare_app_directories() -> None:
    """Create Amigo-owned roots on application startup and reject redirected roots."""
    for directory, label in (
        (APP_DATA_DIR, "Amigo app data directory"),
        (AMIGO_DOCUMENTS_DIR, "Amigo documents directory"),
        (WORKSPACE_DIR, "Amigo materials directory"),
        (KNOWLEDGE_DIR, "Amigo knowledge directory"),
    ):
        current = directory
        # Check each Amigo-owned path component before creation; existing parent
        # folders (such as a redirected Documents folder) remain user-controlled.
        if current.exists() and _is_reparse_point(current):
            raise RuntimeError(f"{label} cannot be a symbolic link, junction, or reparse point.")
        current.mkdir(parents=True, exist_ok=True)
        if _is_reparse_point(current):
            raise RuntimeError(f"{label} cannot be a symbolic link, junction, or reparse point.")

    if DATABASE_PATH.exists():
        if _is_reparse_point(DATABASE_PATH):
            raise RuntimeError("Amigo's database cannot be a link or junction.")
        if DATABASE_PATH.stat().st_nlink > 1:
            raise RuntimeError("Amigo's database cannot be a hard-linked file.")
