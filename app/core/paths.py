from pathlib import Path
import sys


def ensure_within_root(root: Path, candidate: Path) -> Path:
    root_resolved = root.resolve()
    candidate_resolved = candidate.resolve()
    if candidate_resolved != root_resolved and root_resolved not in candidate_resolved.parents:
        raise ValueError(f"Path escapes project root: {candidate}")
    return candidate_resolved


def ensure_dir(path: Path) -> Path:
    target = windows_safe_path(path)
    target.mkdir(parents=True, exist_ok=True)
    return path


def windows_safe_path(path: Path | str) -> Path:
    """Return a path that avoids Windows MAX_PATH (260 char) limitations.
    
    On Windows, when an absolute path length >= 240, wraps it with '\\\\?\\' prefix
    so Win32 APIs can address paths up to 32,767 characters.
    """
    p = Path(path)
    if sys.platform == "win32":
        try:
            resolved_str = str(p.resolve())
        except Exception:
            resolved_str = str(p)
        if not resolved_str.startswith("\\\\?\\") and len(resolved_str) >= 240:
            return Path(f"\\\\?\\{resolved_str}")
    return p


def safe_write_text(path: Path | str, content: str, encoding: str = "utf-8") -> Path:
    """Safely write text to a file, handling parent directories and Windows MAX_PATH."""
    target = windows_safe_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding=encoding)
    return Path(path)


def safe_read_text(path: Path | str, encoding: str = "utf-8") -> str:
    """Safely read text from a file, handling Windows MAX_PATH."""
    target = windows_safe_path(path)
    return target.read_text(encoding=encoding)

