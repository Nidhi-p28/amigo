from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import sys
import webbrowser
import uuid
from typing import Callable
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any
from urllib.parse import urlparse

from .config import WORKSPACE_DIR

# Deliberately restricted allowlist. Add entries only when you know what executable they launch.
APP_ALIASES: dict[str, str] = {
    "vscode": "vscode",
    "notepad": "notepad.exe",
    "calculator": "calc.exe",
    "paint": "mspaint.exe",
    "explorer": "explorer.exe",
    "chrome": "chrome.exe",
    "edge": "msedge.exe",
}

TEXT_EXTENSIONS = {
    ".txt", ".md", ".py", ".json", ".csv", ".html", ".htm", ".css", ".js",
    ".ts", ".tsx", ".jsx", ".xml", ".yaml", ".yml", ".toml", ".ini", ".log",
    ".sql", ".sh", ".bat", ".ps1", ".example"
}
MAX_READ_BYTES = 1_000_000


def _is_link_or_junction(path: Path) -> bool:
    """Detect links/junctions so path validation never follows them outside the workspace."""
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


def _workspace_root() -> Path:
    """Re-check the fixed workspace root each time a tool is invoked."""
    configured = Path(WORKSPACE_DIR)
    if _is_link_or_junction(configured):
        raise ValueError("Amigo's workspace cannot be a symbolic link or junction.")
    if not configured.exists() or not configured.is_dir():
        raise ValueError("Amigo's workspace is missing or is not a directory.")
    root = configured.resolve(strict=True)
    if _is_link_or_junction(configured):
        raise ValueError("Amigo's workspace cannot be a symbolic link or junction.")
    return root


def _safe_workspace_path(relative_path: str | None = ".") -> Path:
    """Validate a relative path, block traversal/links/Windows ADS, and resolve within workspace."""
    raw = (relative_path if isinstance(relative_path, str) else ".").strip()
    if not raw:
        raw = "."
    if "\x00" in raw:
        raise ValueError("Null characters are not allowed in workspace paths.")

    # Normalize both slash styles, then check POSIX and Windows interpretations.
    portable = raw.replace("\\", "/")
    posix_path = PurePosixPath(portable)
    windows_path = PureWindowsPath(raw)
    if posix_path.is_absolute() or windows_path.is_absolute() or windows_path.drive or windows_path.root:
        raise ValueError("Use a relative path inside Amigo's workspace; absolute paths are blocked.")

    parts = [part for part in posix_path.parts if part not in {"", "."}]
    if any(part == ".." for part in parts):
        raise ValueError("Path traversal is blocked: Amigo can only access its workspace.")
    # A colon can introduce an NTFS alternate data stream even in an otherwise relative path.
    if any(":" in part for part in parts):
        raise ValueError("Colon characters are blocked in workspace path components.")

    root = _workspace_root()
    candidate = root.joinpath(*parts)

    # Reject reparse points at each existing component, including links that point back inside.
    current = root
    for part in parts:
        current = current / part
        if _is_link_or_junction(current):
            raise ValueError("Symbolic links and junctions are blocked inside Amigo's workspace.")

    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError("That path is outside Amigo's workspace and is blocked.") from exc
    return resolved


def _relative_workspace_path(target: Path) -> str:
    root = _workspace_root()
    try:
        relative = target.resolve(strict=False).relative_to(root)
    except ValueError as exc:
        raise ValueError("That path is outside Amigo's workspace and is blocked.") from exc
    return relative.as_posix() if relative.parts else "."


def _ensure_safe_directories(directory: Path) -> None:
    """Create missing workspace directories one level at a time, rejecting reparse points."""
    root = _workspace_root()
    validated = _safe_workspace_path(_relative_workspace_path(directory))
    try:
        relative = validated.relative_to(root)
    except ValueError as exc:
        raise ValueError("Directory is outside Amigo's workspace.") from exc

    current = root
    for part in relative.parts:
        current = current / part
        if _is_link_or_junction(current):
            raise ValueError("Symbolic links and junctions are blocked inside Amigo's workspace.")
        if current.exists():
            if not current.is_dir():
                raise ValueError(f"A file blocks the required folder path: {_relative_workspace_path(current)}")
        else:
            current.mkdir()
        # Re-validate the path after each filesystem operation.
        _safe_workspace_path(_relative_workspace_path(current))


def _find_app_executable(alias: str) -> str | None:
    target = APP_ALIASES.get(alias.lower().strip())
    if not target:
        return None

    if target == "vscode":
        found = shutil.which("code")
        if found:
            return found
        candidates = [
            Path(os.getenv("LOCALAPPDATA", "")) / "Programs/Microsoft VS Code/Code.exe",
            Path(os.getenv("PROGRAMFILES", "C:/Program Files")) / "Microsoft VS Code/Code.exe",
            Path(os.getenv("PROGRAMFILES(X86)", "C:/Program Files (x86)")) / "Microsoft VS Code/Code.exe",
        ]
        for candidate in candidates:
            if str(candidate) and candidate.is_file():
                return str(candidate)
        return None

    found = shutil.which(target)
    if found:
        return found

    if sys.platform == "win32":
        program_files = Path(os.getenv("PROGRAMFILES", "C:/Program Files"))
        program_files_x86 = Path(os.getenv("PROGRAMFILES(X86)", "C:/Program Files (x86)"))
        local = Path(os.getenv("LOCALAPPDATA", "C:/Users/Default/AppData/Local"))
        candidates: dict[str, list[Path]] = {
            "chrome.exe": [
                program_files / "Google/Chrome/Application/chrome.exe",
                program_files_x86 / "Google/Chrome/Application/chrome.exe",
                local / "Google/Chrome/Application/chrome.exe",
            ],
            "msedge.exe": [
                program_files / "Microsoft/Edge/Application/msedge.exe",
                program_files_x86 / "Microsoft/Edge/Application/msedge.exe",
            ],
        }
        for candidate in candidates.get(target, []):
            if candidate.is_file():
                return str(candidate)
        # Some installations use a custom location. Check Windows App Paths
        # registry entries for the allowlisted Chromium browsers before giving up.
        if target in {"chrome.exe", "msedge.exe"}:
            try:
                import winreg
                registry_subkey = rf"Software\Microsoft\Windows\CurrentVersion\App Paths\{target}"
                registry_locations = (
                    (winreg.HKEY_CURRENT_USER, 0),
                    (winreg.HKEY_LOCAL_MACHINE, 0),
                    (winreg.HKEY_LOCAL_MACHINE, getattr(winreg, "KEY_WOW64_32KEY", 0)),
                )
                for hive, view_flag in registry_locations:
                    try:
                        with winreg.OpenKey(hive, registry_subkey, 0, winreg.KEY_READ | view_flag) as key:
                            registered, _ = winreg.QueryValueEx(key, "")
                        registered_path = Path(os.path.expandvars(str(registered).strip('" ')))
                        if registered_path.is_file():
                            return str(registered_path)
                    except OSError:
                        continue
            except ImportError:
                pass
        if target in {"notepad.exe", "calc.exe", "mspaint.exe", "explorer.exe"}:
            return target
    return None


def _normalize_label(value: str) -> str:
    """Normalize a spoken/file/app label by ignoring spacing and punctuation."""
    import re
    return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())


def _start_menu_shortcuts() -> list[Path]:
    """Return application shortcuts from the user's and machine's Start Menus."""
    roots = [
        Path(os.getenv("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs",
        Path(os.getenv("PROGRAMDATA", "C:/ProgramData")) / "Microsoft/Windows/Start Menu/Programs",
    ]
    found: list[Path] = []
    for root in roots:
        if not str(root) or not root.is_dir():
            continue
        try:
            for shortcut in root.rglob("*.lnk"):
                # Do not resolve shortcuts: they are intentionally launched by Windows shell.
                if shortcut.is_file() and not _is_link_or_junction(shortcut):
                    found.append(shortcut)
                    if len(found) >= 4000:
                        return found
        except OSError:
            continue
    # Stable de-duplication across per-user and all-users menus.
    return list(dict.fromkeys(found))


def _launch_start_menu_app(query: str) -> dict[str, Any]:
    """Launch an installed app by a unique Start Menu shortcut name, never by shell command."""
    if os.name != "nt":
        return {"ok": False, "message": f"'{query}' is not one of Amigo's built-in app aliases. Finding installed apps by Start Menu shortcut is supported on Windows."}
    requested = _normalize_label(Path(query.strip().strip('"')).stem)
    if not requested:
        return {"ok": False, "message": "Please give the application's name."}
    shortcuts = _start_menu_shortcuts()
    exact = [p for p in shortcuts if _normalize_label(p.stem) == requested]
    matches = exact or [p for p in shortcuts if requested in _normalize_label(p.stem) or _normalize_label(p.stem) in requested]
    # Keep one result per visible shortcut name/path. If names are ambiguous, make the user choose.
    if len(matches) == 1:
        target = matches[0]
        try:
            os.startfile(str(target))  # type: ignore[attr-defined]
            return {"ok": True, "message": f"Launched {target.stem}.", "app": target.stem, "shortcut": str(target)}
        except OSError as exc:
            return {"ok": False, "message": f"Windows couldn't launch '{target.stem}': {exc}"}
    if len(matches) > 1:
        labels = [f"{p.stem} — {p.parent.name}" for p in matches[:8]]
        return {"ok": False, "ambiguous": True, "candidates": labels, "message": "Several installed apps match that name. Please choose one: " + "; ".join(f"{i+1}. {label}" for i, label in enumerate(labels))}
    return {"ok": False, "message": f"I couldn't find an installed app shortcut named '{query}'. Try the app's exact name as it appears in the Windows Start menu."}


def open_application(app: str) -> dict[str, Any]:
    alias = app.lower().strip().replace(" ", "")
    if alias == "fileexplorer":
        alias = "explorer"
    if alias == "googlechrome":
        alias = "chrome"
    if alias == "microsoftedge":
        alias = "edge"
    if alias not in APP_ALIASES:
        # Beyond built-ins, launch only apps represented by a Windows Start Menu
        # shortcut. This supports user-installed apps without arbitrary shell commands.
        return _launch_start_menu_app(app.strip())

    executable = _find_app_executable(alias)
    if not executable:
        return {
            "ok": False,
            "message": f"Couldn't find {app}. For VS Code, add 'code' to PATH or install it in its default folder.",
        }
    try:
        subprocess.Popen([executable], shell=False)
        return {"ok": True, "message": f"Launched {app}.", "app": alias}
    except OSError as exc:
        return {"ok": False, "message": f"Could not launch {app}: {exc}"}


def open_url(url: str) -> dict[str, Any]:
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return {"ok": False, "message": "Only complete http:// or https:// URLs can be opened."}
    try:
        opened = webbrowser.open(url.strip(), new=2)
        return {"ok": bool(opened), "message": "Opened URL in the default browser." if opened else "The browser did not confirm opening the URL."}
    except Exception as exc:
        return {"ok": False, "message": f"Couldn't open URL: {exc}"}


def open_workspace_folder(path: str = ".") -> dict[str, Any]:
    """Open a validated workspace folder in Windows File Explorer."""
    try:
        target = _safe_workspace_path(path)
        if not target.exists() or not target.is_dir():
            return {"ok": False, "message": f"Workspace folder not found: {path}"}
        # Resolve and validate again immediately before crossing to the OS launcher.
        target = _safe_workspace_path(_relative_workspace_path(target))
        if not target.is_dir():
            return {"ok": False, "message": "That workspace path is not a folder."}
        if os.name == "nt":
            os.startfile(str(target))  # type: ignore[attr-defined]
            return {"ok": True, "message": f"Opened workspace folder: {_relative_workspace_path(target)}"}
        return {"ok": False, "message": "Opening a folder in File Explorer is supported on Windows only."}
    except (OSError, ValueError) as exc:
        return {"ok": False, "message": str(exc)}


def find_workspace_items(query: str, kind: str = "any") -> dict[str, Any]:
    """Find workspace files/folders by natural name, returning candidates for clarification."""
    query = str(query or "").strip().strip('"\'')
    if not query:
        return {"ok": False, "message": "Tell me the file or folder name to find."}
    if kind not in {"any", "file", "folder"}:
        kind = "any"
    try:
        root = _workspace_root()
        _safe_workspace_path(".")
        q_full = _normalize_label(query.replace("\\", "/").rstrip("/"))
        q_base = _normalize_label(Path(query.replace("\\", "/")).name)
        candidates: list[tuple[int, str, str]] = []
        seen = 0
        # Walk manually and do not traverse links or junctions.
        stack = [(root, "")]
        while stack and seen < 5000:
            folder, rel_folder = stack.pop()
            try:
                entries = sorted(folder.iterdir(), key=lambda item: item.name.casefold())
            except OSError:
                continue
            for item in entries:
                seen += 1
                if _is_link_or_junction(item):
                    continue
                rel = f"{rel_folder}/{item.name}".strip("/")
                is_folder = item.is_dir()
                if kind == "file" and is_folder or kind == "folder" and not is_folder:
                    if is_folder and seen < 5000:
                        stack.append((item, rel))
                    continue
                stem = item.stem if not is_folder else item.name
                full_n = _normalize_label(rel)
                name_n = _normalize_label(item.name)
                stem_n = _normalize_label(stem)
                best = 0
                if q_full in {full_n, name_n, stem_n}:
                    best = 100
                elif q_base and q_base in {name_n, stem_n}:
                    best = 95
                elif len(q_base) >= 3 and (q_base in name_n or name_n in q_base or q_base in stem_n or stem_n in q_base):
                    best = 60
                if best:
                    candidates.append((best, rel, "folder" if is_folder else "file"))
                if is_folder and seen < 5000:
                    stack.append((item, rel))
                if seen >= 5000:
                    break
        candidates.sort(key=lambda row: (-row[0], row[1].casefold()))
        if not candidates:
            return {"ok": True, "query": query, "matches": [], "message": f"No file or folder matching '{query}' was found in Amigo's workspace."}
        top_score = candidates[0][0]
        # Prefer exact/high-confidence hits, but retain ties so the agent can clarify.
        selected = [row for row in candidates if row[0] == top_score][:8]
        # Exact/normalized matches across a file and folder are deliberately ambiguous.
        return {"ok": True, "query": query, "matches": [{"path": rel, "type": item_type, "score": score} for score, rel, item_type in selected], "ambiguous": len(selected) > 1}
    except (OSError, ValueError) as exc:
        return {"ok": False, "message": str(exc)}


def open_workspace_item(path: str) -> dict[str, Any]:
    """Open an existing file or folder from the fixed workspace using its Windows default app."""
    try:
        target = _safe_workspace_path(path)
        if not target.exists():
            return {"ok": False, "message": f"That file or folder does not exist in Amigo's workspace: {path}"}
        if target.is_dir():
            return open_workspace_folder(path)
        # Validate again immediately before opening via Windows' registered default application.
        target = _safe_workspace_path(path)
        if not target.is_file():
            return {"ok": False, "message": "Only regular workspace files and folders can be opened."}
        # Opening a hard-linked file could expose a location outside the workspace
        # through the default application; keep the same guard used by read/update.
        if getattr(target.stat(), "st_nlink", 1) > 1:
            return {"ok": False, "message": "Opening a file with multiple hard links is blocked by Amigo's workspace safety policy."}
        if os.name == "nt":
            os.startfile(str(target))  # type: ignore[attr-defined]
            return {"ok": True, "message": f"Opened workspace file: {_relative_workspace_path(target)}", "path": _relative_workspace_path(target)}
        return {"ok": False, "message": "Opening workspace files in their default app is supported on Windows only."}
    except (OSError, ValueError) as exc:
        return {"ok": False, "message": str(exc)}


def list_files(path: str = ".") -> dict[str, Any]:
    try:
        target = _safe_workspace_path(path)
        if not target.exists():
            return {"ok": False, "message": f"Folder not found in Amigo workspace: {path}"}
        if not target.is_dir():
            return {"ok": False, "message": f"Not a folder: {path}"}
        entries = sorted(list(target.iterdir()), key=lambda p: p.name.lower())
        items = []
        for child in entries[:100]:
            if _is_link_or_junction(child):
                kind = "blocked_link"
            else:
                kind = "folder" if child.is_dir() else "file"
            items.append({"name": child.name, "type": kind})
        return {
            "ok": True,
            "path": _relative_workspace_path(target),
            "items": items,
            "truncated": len(entries) > 100,
        }
    except (OSError, ValueError) as exc:
        return {"ok": False, "message": str(exc)}


def create_folder(path: str) -> dict[str, Any]:
    try:
        target = _safe_workspace_path(path)
        if target.exists():
            return {"ok": False, "message": f"Path already exists: {path}"}
        _ensure_safe_directories(target.parent)
        # Re-check all path components after parent creation.
        target = _safe_workspace_path(path)
        if target.exists():
            return {"ok": False, "message": f"Path already exists: {path}"}
        target.mkdir()
        _safe_workspace_path(path)
        return {"ok": True, "message": f"Created folder: {_relative_workspace_path(target)}"}
    except (OSError, ValueError) as exc:
        return {"ok": False, "message": str(exc)}


def create_file(path: str, content: str) -> dict[str, Any]:
    """Create a new UTF-8 text file inside workspace; exclusive mode prevents overwrites."""
    try:
        target = _safe_workspace_path(path)
        if target.exists():
            return {"ok": False, "message": f"File already exists: {path}. I won't overwrite it automatically."}
        if target.suffix.lower() not in TEXT_EXTENSIONS:
            return {"ok": False, "message": f"This starter version creates text files only. Supported extensions include: {', '.join(sorted(TEXT_EXTENSIONS))}."}
        _ensure_safe_directories(target.parent)
        target = _safe_workspace_path(path)
        if target.exists():
            return {"ok": False, "message": f"File already exists: {path}. I won't overwrite it automatically."}
        # 'x' mode is exclusive: it refuses to replace a file/symlink that appeared meanwhile.
        with target.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
        return {"ok": True, "message": f"Created file: {_relative_workspace_path(target)}", "path": _relative_workspace_path(target), "bytes": target.stat().st_size}
    except (OSError, ValueError) as exc:
        return {"ok": False, "message": str(exc)}


def read_file(path: str) -> dict[str, Any]:
    try:
        target = _safe_workspace_path(path)
        if not target.is_file():
            return {"ok": False, "message": f"File not found in Amigo workspace: {path}"}
        if target.suffix.lower() not in TEXT_EXTENSIONS:
            return {"ok": False, "message": "Reading is limited to supported text-based file types in this version."}
        file_info = target.stat()
        # Hard links can expose a file that also exists outside the workspace.
        # Conservative policy: do not read files with multiple filesystem links.
        if getattr(file_info, "st_nlink", 1) > 1:
            return {"ok": False, "message": "Reading files with multiple hard links is blocked by Amigo's workspace safety policy."}
        if file_info.st_size > MAX_READ_BYTES:
            return {"ok": False, "message": "File is too large to read in one request (limit: 1 MB)."}
        # Revalidate before opening so newly introduced links/junctions are rejected.
        target = _safe_workspace_path(path)
        return {"ok": True, "path": _relative_workspace_path(target), "content": target.read_text(encoding="utf-8", errors="replace")}
    except (OSError, ValueError) as exc:
        return {"ok": False, "message": str(exc)}


def update_file(path: str, content: str) -> dict[str, Any]:
    """Replace the contents of an existing supported text file inside workspace."""
    temporary: Path | None = None
    try:
        target = _safe_workspace_path(path)
        if not target.exists() or not target.is_file():
            return {"ok": False, "message": f"File not found in Amigo workspace: {path}"}
        if target.suffix.lower() not in TEXT_EXTENSIONS:
            return {"ok": False, "message": "Updating is limited to supported text-based files."}
        info = target.stat()
        if getattr(info, "st_nlink", 1) > 1:
            return {"ok": False, "message": "Updating a hard-linked file is blocked by Amigo's workspace safety policy."}
        if info.st_size > MAX_READ_BYTES or len(content.encode("utf-8")) > MAX_READ_BYTES:
            return {"ok": False, "message": "File content exceeds the 1 MB safety limit."}

        # Write a new sibling and atomically replace only after revalidating the original.
        relative = _relative_workspace_path(target)
        parent_rel = str(PurePosixPath(relative).parent)
        if parent_rel == ".":
            parent_rel = "."
        temp_name = f".amigo-update-{uuid.uuid4().hex}.tmp"
        temporary = _safe_workspace_path(f"{parent_rel}/{temp_name}" if parent_rel != "." else temp_name)
        with temporary.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
        _safe_workspace_path(relative)
        latest = target.stat()
        if getattr(latest, "st_nlink", 1) > 1 or not target.is_file():
            raise ValueError("The original file changed in an unsafe way; update was refused.")
        os.replace(temporary, target)
        temporary = None
        return {"ok": True, "message": f"Updated file: {relative}", "path": relative, "bytes": target.stat().st_size}
    except (OSError, ValueError) as exc:
        return {"ok": False, "message": str(exc)}
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def delete_file(path: str) -> dict[str, Any]:
    """Delete one regular workspace file after caller has obtained user confirmation."""
    try:
        target = _safe_workspace_path(path)
        if not target.exists() or not target.is_file():
            return {"ok": False, "message": f"File not found in Amigo workspace: {path}"}
        if getattr(target.stat(), "st_nlink", 1) > 1:
            return {"ok": False, "message": "Deleting a hard-linked file is blocked by Amigo's workspace safety policy."}
        relative = _relative_workspace_path(target)
        _safe_workspace_path(relative)
        target.unlink()
        return {"ok": True, "message": f"Deleted file: {relative}", "path": relative}
    except (OSError, ValueError) as exc:
        return {"ok": False, "message": str(exc)}


def _workspace_item(path: str) -> tuple[Path, str]:
    """Resolve an existing regular file or directory without following links."""
    target = _safe_workspace_path(path)
    if not target.exists():
        raise ValueError(f"Workspace item not found: {path}")
    if target == _workspace_root():
        raise ValueError("The workspace root cannot be renamed or moved.")
    if not (target.is_file() or target.is_dir()):
        raise ValueError("Only regular files and folders can be renamed or moved.")
    if target.is_file() and getattr(target.stat(), "st_nlink", 1) > 1:
        raise ValueError("Moving a hard-linked file is blocked by Amigo's workspace safety policy.")
    kind = "folder" if target.is_dir() else "file"
    return target, kind


def rename_workspace_item(path: str, new_name: str) -> dict[str, Any]:
    """Rename one file or folder in its current workspace folder, refusing collisions."""
    try:
        source, kind = _workspace_item(path)
        raw_name = str(new_name or "").strip()
        portable_name = raw_name.replace("\\", "/")
        name_path = PurePosixPath(portable_name)
        if (not raw_name or "/" in portable_name or len(name_path.parts) != 1
                or name_path.parts[0] in {".", ".."}):
            raise ValueError("Give a new name only; use move to change the item's folder.")
        if ":" in raw_name or "\x00" in raw_name:
            raise ValueError("Colon and null characters are blocked in workspace names.")
        root = _workspace_root()
        source_relative = source.relative_to(root).as_posix()
        new_relative = (source.parent / raw_name).relative_to(root).as_posix()
        destination = _safe_workspace_path(new_relative)
        if destination == source:
            raise ValueError("That is already the item's current name.")
        if destination.exists():
            raise ValueError(f"A workspace item already exists with that name: {new_relative}")
        if _is_link_or_junction(source):
            raise ValueError("Symbolic links and junctions cannot be renamed by Amigo.")
        # Revalidate immediately before the filesystem operation. os.rename preserves the
        # item and, on Windows, refuses to replace an existing destination.
        source = _safe_workspace_path(source_relative)
        destination = _safe_workspace_path(new_relative)
        if destination.exists():
            raise ValueError(f"A workspace item already exists with that name: {new_relative}")
        os.rename(source, destination)
        if source.exists() or not destination.exists() or _is_link_or_junction(destination):
            return {"ok": False, "message": "The rename could not be verified. Check the workspace before retrying."}
        return {
            "ok": True,
            "message": f"Renamed workspace {kind}: {source_relative} → {new_relative}",
            "previous_path": source_relative,
            "path": new_relative,
            "type": kind,
        }
    except (OSError, ValueError) as exc:
        return {"ok": False, "message": str(exc)}


def move_workspace_item(path: str, destination_path: str) -> dict[str, Any]:
    """Move one file or folder within the workspace; destination may be a folder or full path."""
    try:
        source, kind = _workspace_item(path)
        raw_destination = str(destination_path or "").strip()
        if not raw_destination:
            raise ValueError("Give a destination workspace-relative path.")
        destination = _safe_workspace_path(raw_destination)
        if destination.exists() and destination.is_dir():
            destination = destination / source.name
        elif raw_destination.endswith(("/", "\\")):
            missing_folder = raw_destination.rstrip("/\\")
            raise ValueError(f"Destination folder does not exist: {missing_folder}")

        root = _workspace_root()
        source_relative = source.relative_to(root).as_posix()
        destination_relative = destination.relative_to(root).as_posix()
        destination = _safe_workspace_path(destination_relative)
        if destination == source:
            raise ValueError("That item is already at the requested destination.")
        if source.is_dir():
            try:
                destination.relative_to(source)
            except ValueError:
                pass
            else:
                raise ValueError("A folder cannot be moved into itself or one of its subfolders.")
        if destination.exists():
            raise ValueError(f"A workspace item already exists at the destination: {destination_relative}")
        if not destination.parent.exists() or not destination.parent.is_dir():
            raise ValueError(f"Destination folder does not exist: {_relative_workspace_path(destination.parent)}")
        if _is_link_or_junction(source) or _is_link_or_junction(destination.parent):
            raise ValueError("Symbolic links and junctions are blocked inside Amigo's workspace.")
        source = _safe_workspace_path(source_relative)
        destination = _safe_workspace_path(destination_relative)
        if destination.exists():
            raise ValueError(f"A workspace item already exists at the destination: {destination_relative}")
        os.rename(source, destination)
        if source.exists() or not destination.exists() or _is_link_or_junction(destination):
            return {"ok": False, "message": "The move could not be verified. Check the workspace before retrying."}
        return {
            "ok": True,
            "message": f"Moved workspace {kind}: {source_relative} → {destination_relative}",
            "previous_path": source_relative,
            "path": destination_relative,
            "type": kind,
        }
    except (OSError, ValueError) as exc:
        return {"ok": False, "message": str(exc)}


def tool_schemas() -> list[dict[str, Any]]:
    """Ollama/OpenAI-style function tool declarations."""
    return [
        {
            "type": "function",
            "function": {
                "name": "open_application",
                "description": "Launch an installed Windows application. Use a built-in alias (vscode, notepad, calculator, paint, explorer, chrome, edge), or the app's exact name as shown in the Start menu. For non-built-in apps, Amigo searches Start Menu shortcuts; never use shell commands.",
                "parameters": {"type": "object", "required": ["app"], "properties": {"app": {"type": "string"}}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "open_url",
                "description": "Open a complete http or https URL in the default web browser. Do not use this for web searching.",
                "parameters": {"type": "object", "required": ["url"], "properties": {"url": {"type": "string"}}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "list_files",
                "description": "List files and folders inside Amigo's workspace only. Paths must be relative to the workspace; use '.' for its root. Links/junctions are blocked.",
                "parameters": {"type": "object", "properties": {"path": {"type": "string", "default": "."}}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "find_workspace_items",
                "description": "Search for files and folders by name inside the workspace. Use this before open/read/update/delete when a spoken or incomplete name may match multiple entries. If multiple candidates are returned, ask the user to choose; do not guess.",
                "parameters": {"type": "object", "required": ["query"], "properties": {"query": {"type": "string"}, "kind": {"type": "string", "enum": ["any", "file", "folder"]}}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "open_workspace_item",
                "description": "Open an existing file or folder from the fixed Amigo workspace in its default Windows app or File Explorer. Path must be workspace-relative. Use this instead of pretending an item was opened.",
                "parameters": {"type": "object", "required": ["path"], "properties": {"path": {"type": "string"}}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "create_folder",
                "description": "Create a new folder inside Amigo's workspace only. Path must be workspace-relative. Does not overwrite existing folders; links/junctions are blocked.",
                "parameters": {"type": "object", "required": ["path"], "properties": {"path": {"type": "string"}}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "create_file",
                "description": "Create a new text file inside Amigo's workspace only. Never overwrites existing files. Parent folders are created when needed; links/junctions are blocked.",
                "parameters": {"type": "object", "required": ["path", "content"], "properties": {"path": {"type": "string"}, "content": {"type": "string"}}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "Read a supported text file inside Amigo's workspace only. Absolute paths, traversal, symbolic links and junctions are blocked.",
                "parameters": {"type": "object", "required": ["path"], "properties": {"path": {"type": "string"}}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "update_file",
                "description": "Replace the contents of an existing supported text file inside Amigo's workspace only. Use only when the user explicitly asked to edit/update/replace that file. Does not allow paths outside workspace or hard-linked files.",
                "parameters": {"type": "object", "required": ["path", "content"], "properties": {"path": {"type": "string"}, "content": {"type": "string"}}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "rename_workspace_item",
                "description": "Rename one existing file or folder inside Amigo's workspace, keeping it in the same folder. Give a new name only. Never overwrites an existing item; links, junctions, hard-linked files, absolute paths and traversal are blocked.",
                "parameters": {"type": "object", "required": ["path", "new_name"], "properties": {"path": {"type": "string"}, "new_name": {"type": "string"}}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "move_workspace_item",
                "description": "Move one existing file or folder within Amigo's workspace. destination_path is a workspace-relative target path or an existing destination folder. Never overwrites an existing item or creates missing destination folders; links, junctions, hard-linked files, absolute paths and traversal are blocked.",
                "parameters": {"type": "object", "required": ["path", "destination_path"], "properties": {"path": {"type": "string"}, "destination_path": {"type": "string"}}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "delete_file",
                "description": "Delete one regular file inside workspace. This action must only be performed after explicit user confirmation in the UI. Never delete folders or files outside workspace.",
                "parameters": {"type": "object", "required": ["path"], "properties": {"path": {"type": "string"}}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "open_workspace_folder",
                "description": "Open a folder inside Amigo's fixed workspace in Windows File Explorer. The path must be relative to workspace; use '.' for workspace root.",
                "parameters": {"type": "object", "properties": {"path": {"type": "string", "default": "."}}},
            },
        },
    ]


def execute_tool(
    name: str,
    arguments: dict[str, Any],
    *,
    confirm_delete: Callable[[str], bool] | None = None,
) -> dict[str, Any]:
    """Execute only known tools, with strict argument validation and confirmed deletion."""
    if not isinstance(arguments, dict):
        return {"ok": False, "message": "Tool arguments must be a JSON object."}
    try:
        if name == "open_application":
            app = arguments.get("app")
            if not isinstance(app, str):
                raise ValueError("app must be text")
            return open_application(app)
        if name == "open_url":
            url = arguments.get("url")
            if not isinstance(url, str):
                raise ValueError("url must be text")
            return open_url(url)
        if name == "open_workspace_folder":
            path = arguments.get("path", ".")
            if not isinstance(path, str):
                raise ValueError("path must be text")
            return open_workspace_folder(path)
        if name == "find_workspace_items":
            query, kind = arguments.get("query"), arguments.get("kind", "any")
            if not isinstance(query, str) or not isinstance(kind, str):
                raise ValueError("query and kind must be text")
            return find_workspace_items(query, kind)
        if name == "open_workspace_item":
            path = arguments.get("path")
            if not isinstance(path, str):
                raise ValueError("path must be text")
            return open_workspace_item(path)
        if name == "list_files":
            path = arguments.get("path", ".")
            if not isinstance(path, str):
                raise ValueError("path must be text")
            return list_files(path)
        if name == "create_folder":
            path = arguments.get("path")
            if not isinstance(path, str):
                raise ValueError("path must be text")
            return create_folder(path)
        if name == "create_file":
            path, content = arguments.get("path"), arguments.get("content")
            if not isinstance(path, str) or not isinstance(content, str):
                raise ValueError("path and content must be text")
            return create_file(path, content)
        if name == "read_file":
            path = arguments.get("path")
            if not isinstance(path, str):
                raise ValueError("path must be text")
            return read_file(path)
        if name == "update_file":
            path, content = arguments.get("path"), arguments.get("content")
            if not isinstance(path, str) or not isinstance(content, str):
                raise ValueError("path and content must be text")
            return update_file(path, content)
        if name == "rename_workspace_item":
            path, new_name = arguments.get("path"), arguments.get("new_name")
            if not isinstance(path, str) or not isinstance(new_name, str):
                raise ValueError("path and new_name must be text")
            return rename_workspace_item(path, new_name)
        if name == "move_workspace_item":
            path, destination_path = arguments.get("path"), arguments.get("destination_path")
            if not isinstance(path, str) or not isinstance(destination_path, str):
                raise ValueError("path and destination_path must be text")
            return move_workspace_item(path, destination_path)
        if name == "delete_file":
            path = arguments.get("path")
            if not isinstance(path, str):
                raise ValueError("path must be text")
            target = _safe_workspace_path(path)
            if not target.exists() or not target.is_file():
                return {"ok": False, "message": f"File not found in Amigo workspace: {path}"}
            relative = _relative_workspace_path(target)
            if confirm_delete is None:
                return {"ok": False, "message": "Deletion requires confirmation in the Amigo window; nothing was deleted."}
            if not confirm_delete(relative):
                return {"ok": False, "message": f"Deletion cancelled by the user: {relative}"}
            return delete_file(relative)
        return {"ok": False, "message": f"Blocked unknown tool: {name}"}
    except ValueError as exc:
        return {"ok": False, "message": str(exc)}
