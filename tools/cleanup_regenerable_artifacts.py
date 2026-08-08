from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "maintenance" / "cleanup_manifest.json"
CP12B_ZIP = ROOT / "release" / "CP12B" / "tool_auto_sub_windows_full_portable_cp12b.zip"
EXPECTED_CP12B = "9a1c3b03a18049aca4f63fd43df2092eec35d5c36e9ec176dbaae7bc4d4a51d0"
PROTECTED = {
    ROOT,
    ROOT / ".git",
    ROOT / "release" / "CP12B",
    ROOT / "data",
    CP12B_ZIP,
    ROOT / "project_state.json",
}
OBSOLETE_RELEASE_BINARIES = [
    ROOT / "release" / "CP11A" / "tool_auto_sub_windows_portable_cp11a.zip",
    ROOT / "release" / "CP11C" / "tool_auto_sub_ocr_runtime_addon_cp11c.zip",
    ROOT / "release" / "CP11D" / "tool_auto_sub_windows_full_portable_cp11d.zip",
]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def process_uses_path(path: Path) -> bool:
    command = "Get-CimInstance Win32_Process | Select-Object ProcessId,Name,ExecutablePath,CommandLine | ConvertTo-Json -Depth 4"
    completed = subprocess.run(["powershell", "-NoProfile", "-Command", command], capture_output=True, text=True, encoding="utf-8", errors="replace")
    text = completed.stdout.strip()
    if not text:
        return False
    data = json.loads(text)
    if isinstance(data, dict):
        data = [data]
    needle = str(path.resolve()).lower()
    return any(
        needle in ((item.get("CommandLine") or "") + " " + (item.get("ExecutablePath") or "")).lower()
        and "cleanup_regenerable_artifacts" not in (item.get("CommandLine") or "")
        and "Get-CimInstance Win32_Process" not in (item.get("CommandLine") or "")
        for item in data
    )


def validate_candidate(path: Path, action: str) -> dict[str, Any]:
    resolved = path.resolve()
    errors = []
    if not path.exists():
        return {"path": str(path), "exists": False, "valid": True, "errors": [], "action": action, "size_bytes": 0}
    if not is_inside(resolved, ROOT):
        errors.append("outside_project_root")
    if path.is_symlink():
        errors.append("symlink_or_junction_candidate")
    for protected in PROTECTED:
        protected_resolved = protected.resolve()
        protected_directory_scope = protected_resolved != ROOT.resolve() and protected.exists() and protected.is_dir()
        if resolved == protected_resolved or (protected_directory_scope and is_inside(resolved, protected_resolved)):
            errors.append(f"protected_path:{protected}")
    if process_uses_path(path):
        errors.append("active_process_dependency")
    return {
        "path": str(path),
        "exists": True,
        "valid": not errors,
        "errors": errors,
        "action": action,
        "size_bytes": path.stat().st_size if path.is_file() else directory_size(path),
        "sha256": sha256_file(path) if path.is_file() else None,
    }


def directory_size(path: Path) -> int:
    total = 0
    for root, dirnames, filenames in os.walk(path, topdown=True, followlinks=False):
        dirnames[:] = [name for name in dirnames if not (Path(root) / name).is_symlink()]
        for filename in filenames:
            file_path = Path(root) / filename
            if file_path.is_symlink():
                continue
            try:
                total += file_path.stat().st_size
            except OSError:
                pass
    return total


def build_candidates() -> list[dict[str, Any]]:
    candidates = [validate_candidate(path, "DELETE_OBSOLETE_RELEASE_BINARY") for path in OBSOLETE_RELEASE_BINARIES]
    for path in ROOT.rglob("__pycache__"):
        if path.is_dir() and not path.is_symlink():
            candidates.append(validate_candidate(path, "DELETE_REGENERABLE_BUILD_ARTIFACT"))
    if (ROOT / ".pytest_cache").exists():
        candidates.append(validate_candidate(ROOT / ".pytest_cache", "DELETE_REGENERABLE_BUILD_ARTIFACT"))
    return candidates


def delete_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    path = Path(candidate["path"])
    if not candidate["exists"]:
        return {**candidate, "deleted": False, "result": "already_absent"}
    if not candidate["valid"]:
        return {**candidate, "deleted": False, "result": "blocked"}
    if path.is_file():
        path.unlink()
    else:
        import shutil

        shutil.rmtree(path)
    return {**candidate, "deleted": True, "result": "deleted", "exists_after": path.exists()}


def main() -> None:
    parser = argparse.ArgumentParser(description="Conservative cleanup for regenerable Tool Auto Sub artifacts.")
    parser.add_argument("--apply", action="store_true", help="Actually delete validated candidates. Default is dry-run.")
    parser.add_argument("--manifest", default=str(MANIFEST))
    args = parser.parse_args()

    cp12b_hash = sha256_file(CP12B_ZIP)
    if cp12b_hash != EXPECTED_CP12B:
        raise SystemExit("Refusing cleanup: CP12B protected hash mismatch.")

    candidates = build_candidates()
    results = [delete_candidate(candidate) for candidate in candidates] if args.apply else candidates
    payload = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "mode": "apply" if args.apply else "dry_run",
        "project_root": str(ROOT),
        "protected_hashes": {"cp12b_zip": cp12b_hash},
        "candidates": candidates,
        "results": results,
        "total_candidate_bytes": sum(item["size_bytes"] for item in candidates if item.get("valid")),
        "deleted_bytes": sum(item["size_bytes"] for item in results if item.get("deleted")),
    }
    manifest = Path(args.manifest)
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({
        "status": "PASS",
        "mode": payload["mode"],
        "manifest": str(manifest),
        "candidate_count": len(candidates),
        "deleted_bytes": payload["deleted_bytes"],
    }, indent=2))


if __name__ == "__main__":
    main()
