#!/usr/bin/env python3
"""Shared filesystem, Git snapshot, task-delta and lifecycle helpers."""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import subprocess
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

OS_EXCLUDED_PREFIXES = (".ai/", ".git/", ".github/workflows/ai-build-os.yml")
TASK_BASELINE_RELATIVE = Path(".ai/runtime/task_baseline.json")


def atomic_write_text(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)


def atomic_write_json(path: Path, value: Any) -> None:
    atomic_write_text(path, json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8", errors="replace"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git(root: Path, *args: str, check: bool = True, text: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=root, check=check, capture_output=True, text=text)


def in_git_repo(root: Path) -> bool:
    result = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], cwd=root, capture_output=True, text=True)
    return result.returncode == 0 and result.stdout.strip() == "true"


def _application_status_lines(root: Path) -> list[str]:
    result = _git(root, "status", "--porcelain=v1", "--untracked-files=all")
    lines: list[str] = []
    for raw in result.stdout.splitlines():
        if not raw:
            continue
        path = raw[3:] if len(raw) >= 4 else raw
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        normalized = path.replace("\\", "/")
        if normalized.startswith(OS_EXCLUDED_PREFIXES):
            continue
        lines.append(raw)
    return sorted(lines)


def changed_application_files(root: Path) -> list[str]:
    """Return all application paths currently different from HEAD."""
    if not in_git_repo(root):
        return []
    files: set[str] = set()
    for raw in _application_status_lines(root):
        path = raw[3:] if len(raw) >= 4 else raw
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        files.add(path.replace("\\", "/"))
    return sorted(files)


def application_file_fingerprint(root: Path, relative: str) -> str:
    """Fingerprint current working-tree content for task-delta comparisons.

    This intentionally describes application content, not Git index state. A pre-existing
    dirty file is therefore tolerated until this fingerprint changes during the task.
    """
    path = root / relative
    if path.is_symlink():
        return "SYMLINK:" + sha256_text(os.readlink(path))
    if path.is_file():
        return "FILE:" + sha256_file(path)
    if path.exists():
        return "NON_FILE:" + path.__class__.__name__
    return "MISSING"


def application_snapshot(root: Path) -> dict[str, Any]:
    """Return a reproducible application snapshot, excluding .ai bookkeeping."""
    if not in_git_repo(root):
        return {
            "git": False,
            "branch": "UNKNOWN",
            "head": "UNKNOWN",
            "worktree": "CLEAN_OR_UNKNOWN",
            "status_lines": [],
            "changed_files": [],
            "tracked_diff_sha256": sha256_text(""),
            "untracked_manifest_sha256": sha256_text(""),
            "snapshot_sha256": sha256_text("NO_GIT"),
        }
    branch = _git(root, "branch", "--show-current").stdout.strip() or "DETACHED"
    head = _git(root, "rev-parse", "HEAD").stdout.strip()
    status_lines = _application_status_lines(root)
    changed_files = changed_application_files(root)

    diff_parts: list[bytes] = []
    for args in [
        ("diff", "--binary", "--", ".", ":(exclude).ai/**", ":(exclude).github/workflows/ai-build-os.yml"),
        ("diff", "--cached", "--binary", "--", ".", ":(exclude).ai/**", ":(exclude).github/workflows/ai-build-os.yml"),
    ]:
        result = _git(root, *args, text=False)
        diff_parts.append(result.stdout)
    tracked_diff_hash = sha256_bytes(b"\n--SPLIT--\n".join(diff_parts))

    untracked_entries: list[dict[str, str | int]] = []
    for status in status_lines:
        if not status.startswith("?? "):
            continue
        relative = status[3:].replace("\\", "/")
        path = root / relative
        if path.is_file():
            untracked_entries.append({"path": relative, "size": path.stat().st_size, "sha256": sha256_file(path)})
        else:
            untracked_entries.append({"path": relative, "size": -1, "sha256": "NON_FILE"})
    untracked_manifest = json.dumps(untracked_entries, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    untracked_hash = sha256_text(untracked_manifest)
    snapshot_material = json.dumps({
        "head": head,
        "tracked_diff_sha256": tracked_diff_hash,
        "untracked_manifest_sha256": untracked_hash,
        "changed_files": changed_files,
    }, sort_keys=True, separators=(",", ":"))
    return {
        "git": True,
        "branch": branch,
        "head": head,
        "worktree": "DIRTY" if status_lines else "CLEAN",
        "status_lines": status_lines,
        "changed_files": changed_files,
        "tracked_diff_sha256": tracked_diff_hash,
        "untracked_manifest_sha256": untracked_hash,
        "snapshot_sha256": sha256_text(snapshot_material),
    }


def capture_task_baseline(root: Path, task_id: str, task_revision: int) -> dict[str, Any]:
    """Capture pre-existing worktree changes so scope enforcement uses only task delta."""
    snapshot = application_snapshot(root)
    preexisting = {
        relative: application_file_fingerprint(root, relative)
        for relative in snapshot.get("changed_files", [])
    }
    baseline = {
        "schema_version": 1,
        "task_id": task_id,
        "task_revision": int(task_revision),
        "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "branch": snapshot.get("branch"),
        "head": snapshot.get("head"),
        "snapshot_sha256": snapshot.get("snapshot_sha256"),
        "preexisting_changed_files": preexisting,
    }
    atomic_write_json(root / TASK_BASELINE_RELATIVE, baseline)
    return baseline


def load_task_baseline(root: Path) -> dict[str, Any] | None:
    path = root / TASK_BASELINE_RELATIVE
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def task_delta_files(root: Path, *, task_id: str | None = None, task_revision: int | None = None) -> list[str]:
    """Return paths changed since the active task baseline.

    Pre-existing dirty paths are ignored only while their working-tree content remains
    exactly as captured. Changing or deleting them during the task makes them part of
    the task delta and therefore subject to the task scope.
    """
    baseline = load_task_baseline(root)
    if baseline is None:
        # Backward-compatible fail-closed behavior for repositories created before v1.6.
        return changed_application_files(root)
    if task_id is not None and baseline.get("task_id") != task_id:
        return changed_application_files(root)
    if task_revision is not None and int(baseline.get("task_revision", -1)) != int(task_revision):
        return changed_application_files(root)

    before: dict[str, str] = baseline.get("preexisting_changed_files", {}) or {}
    current_paths = set(changed_application_files(root))
    candidates = current_paths | set(before)
    delta: list[str] = []
    for relative in sorted(candidates):
        if relative not in before:
            # Clean at task start, dirty now.
            delta.append(relative)
            continue
        current_fingerprint = application_file_fingerprint(root, relative)
        if current_fingerprint != before[relative]:
            delta.append(relative)
    return delta


def task_delta_diffs(
    root: Path, *, task_id: str | None = None, task_revision: int | None = None, max_bytes_per_file: int = 131072
) -> dict[str, str]:
    """Return bounded Git-style diff text for files changed by the active task.

    The diff is anchored to the task baseline HEAD. For pre-existing dirty files this
    may conservatively include their earlier uncommitted edits too; that is preferable
    to silently under-classifying risk when the task touches the same file again.
    """
    paths = task_delta_files(root, task_id=task_id, task_revision=task_revision)
    if not paths:
        return {}
    baseline = load_task_baseline(root) or {}
    baseline_head = str(baseline.get("head") or "HEAD")
    result: dict[str, str] = {}
    for relative in paths:
        text = ""
        if in_git_repo(root):
            proc = subprocess.run(
                ["git", "diff", "--no-ext-diff", "--unified=0", baseline_head, "--", relative],
                cwd=root, capture_output=True, text=False, check=False,
            )
            data = proc.stdout[:max_bytes_per_file]
            text = data.decode("utf-8", errors="replace")
        path = root / relative
        # Git diff is empty for brand-new untracked files; inspect their content directly.
        if not text and path.is_file():
            try:
                data = path.read_bytes()[:max_bytes_per_file]
                decoded = data.decode("utf-8", errors="replace")
                text = "\n".join("+" + line for line in decoded.splitlines())
            except OSError:
                text = ""
        result[relative] = text
    return result


def git_info(root: Path) -> tuple[str, str, str] | None:
    snap = application_snapshot(root)
    if not snap["git"]:
        return None
    return str(snap["branch"]), str(snap["head"]), str(snap["worktree"])


@contextlib.contextmanager
def lifecycle_lock(root: Path) -> Iterator[None]:
    lock_path = root / ".ai" / ".lifecycle.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise SystemExit("Another AI Build OS lifecycle command is already running") from exc
    try:
        os.write(fd, f"pid={os.getpid()} started={time.time()}\n".encode())
        os.close(fd)
        yield
    finally:
        lock_path.unlink(missing_ok=True)


@contextlib.contextmanager
def transaction_journal(root: Path, action: str, payload: dict[str, Any]) -> Iterator[Path]:
    tx_id = f"TX-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{uuid.uuid4().hex[:8]}"
    tx_dir = root / ".ai" / "transactions" / tx_id
    tx_dir.mkdir(parents=True, exist_ok=False)
    atomic_write_json(tx_dir / "intent.json", {"transaction_id": tx_id, "action": action, "payload": payload, "status": "PREPARED"})
    try:
        yield tx_dir
        atomic_write_json(tx_dir / "commit.json", {"transaction_id": tx_id, "action": action, "status": "COMMITTED"})
    except BaseException as exc:
        atomic_write_json(tx_dir / "abort.json", {"transaction_id": tx_id, "action": action, "status": "ABORTED", "error": str(exc)})
        raise
