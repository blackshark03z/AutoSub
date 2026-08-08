from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "maintenance" / "storage_inventory.json"
CP12B_ZIP = ROOT / "release" / "CP12B" / "tool_auto_sub_windows_full_portable_cp12b.zip"
ACCEPTED_MEDIA_CANDIDATES = [
    ROOT / "data" / "projects" / "vertical_slice_cp07" / "renders" / "cp08e2_decoupled_suppression_english_plate_720p.mp4",
    ROOT / "data" / "projects" / "production_golden_path_cp09" / "exports" / "release_20260718_050055_88c16e_37394ab6_dir" / "final_video.mp4",
]
EXPECTED_CP12B = "9a1c3b03a18049aca4f63fd43df2092eec35d5c36e9ec176dbaae7bc4d4a51d0"
EXPECTED_ACCEPTED = "37394ab6ce036abdbebb6e7d9cebc8d3dc2661adae1324f0b635184042589646"
EXTERNAL_SCOPE = [
    Path(r"D:\Tool Auto Sub CP11A"),
    Path(r"D:\tool_auto_sub_cp11c_clean_validation"),
    Path(r"D:\Tool Auto Sub CP11C Clean"),
]
DEV_OCR_RUNTIME = Path(r"D:\tool_auto_sub_ocr_runtime")
BINARY_SUFFIXES = {".zip", ".mp4", ".mov", ".mkv", ".webm", ".avi", ".exe", ".dll", ".pyd", ".pdmodel", ".pdiparams", ".whl"}
MEDIA_SUFFIXES = {".mp4", ".mov", ".mkv", ".webm", ".avi"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ps_json(command: str) -> Any:
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    text = completed.stdout.strip()
    if not text:
        return []
    return json.loads(text)


TRACKED_FILES: set[str] | None = None


def tracked_files() -> set[str]:
    global TRACKED_FILES
    if TRACKED_FILES is None:
        completed = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=False)
        TRACKED_FILES = {line.strip().replace("\\", "/") for line in completed.stdout.splitlines() if line.strip()}
    return TRACKED_FILES


def file_info(path: Path, root: Path = ROOT) -> dict[str, Any]:
    stat = path.stat()
    try:
        rel = path.resolve().relative_to(ROOT).as_posix()
    except Exception:
        rel = str(path)
    tracked = rel in tracked_files()
    return {
        "absolute_path": str(path.resolve()),
        "relative_path": rel,
        "size_bytes": stat.st_size,
        "size_mib": round(stat.st_size / 1024**2, 3),
        "size_gib": round(stat.st_size / 1024**3, 6),
        "last_modified": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
        "tracked_by_git": tracked,
        "category": categorize_file(path),
        "referenced_by_current_docs": referenced_by_current_docs(path),
        "reproducible": is_reproducible_file(path),
        "unique": None,
        "proposed_retention_action": proposed_action(path),
    }


def categorize_file(path: Path) -> str:
    parts = {part.lower() for part in path.parts}
    suffix = path.suffix.lower()
    if ".git" in parts:
        return "git"
    if "release" in parts and suffix == ".zip":
        return "release_zip"
    if "evidence" in parts and suffix in BINARY_SUFFIXES:
        return "evidence_binary"
    if "data" in parts and "projects" in parts and suffix in MEDIA_SUFFIXES:
        return "project_run_media"
    if "__pycache__" in parts or suffix in {".pyc", ".pyo"}:
        return "cache"
    if path.match("*.tmp") or suffix in {".tmp", ".part"}:
        return "temporary"
    if suffix in BINARY_SUFFIXES:
        return "binary"
    return "metadata_or_source"


def referenced_by_current_docs(path: Path) -> bool:
    try:
        rel = str(path.resolve().relative_to(ROOT)).replace("/", "\\")
    except Exception:
        return False
    docs = [
        ROOT / "README.md",
        ROOT / "docs" / "CURRENT_STATE.md",
        ROOT / "docs" / "ARCHITECTURE.md",
        ROOT / "docs" / "OPERATIONS.md",
        ROOT / "CHANGELOG.md",
        ROOT / "project_state.json",
    ]
    return any(rel in doc.read_text(encoding="utf-8", errors="ignore") for doc in docs if doc.exists())


def is_reproducible_file(path: Path) -> bool:
    rel = path.as_posix().lower()
    if "__pycache__" in rel or path.suffix.lower() in {".pyc", ".pyo"}:
        return True
    if rel.endswith("release/cp11a/tool_auto_sub_windows_portable_cp11a.zip"):
        return True
    if rel.endswith("release/cp11c/tool_auto_sub_ocr_runtime_addon_cp11c.zip"):
        return True
    if rel.endswith("release/cp11d/tool_auto_sub_windows_full_portable_cp11d.zip"):
        return True
    return False


def proposed_action(path: Path) -> str:
    rel = path.as_posix().lower()
    if rel.endswith("release/cp12b/tool_auto_sub_windows_full_portable_cp12b.zip"):
        return "KEEP_CANONICAL"
    if path in ACCEPTED_MEDIA_CANDIDATES:
        return "KEEP_CANONICAL"
    if rel.endswith("release/cp11a/tool_auto_sub_windows_portable_cp11a.zip"):
        return "DELETE_OBSOLETE_RELEASE_BINARY"
    if rel.endswith("release/cp11c/tool_auto_sub_ocr_runtime_addon_cp11c.zip"):
        return "DELETE_OBSOLETE_RELEASE_BINARY"
    if rel.endswith("release/cp11d/tool_auto_sub_windows_full_portable_cp11d.zip"):
        return "DELETE_OBSOLETE_RELEASE_BINARY"
    if "__pycache__" in rel or path.suffix.lower() in {".pyc", ".pyo"}:
        return "DELETE_REGENERABLE_BUILD_ARTIFACT"
    if "data/projects" in rel and path.suffix.lower() in MEDIA_SUFFIXES:
        return "KEEP_ACTIVE_USER_DATA"
    if path.suffix.lower() in {".md", ".json", ".txt"}:
        return "KEEP_SMALL_METADATA"
    return "REVIEW_UNKNOWN"


def safe_walk(root: Path):
    if not root.exists():
        return
    for current, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        kept_dirs = []
        for dirname in dirnames:
            child = current_path / dirname
            if child.is_symlink():
                continue
            kept_dirs.append(dirname)
        dirnames[:] = kept_dirs
        for filename in filenames:
            path = current_path / filename
            if path.is_symlink():
                continue
            yield path


def directory_size(path: Path) -> dict[str, Any]:
    total = 0
    file_count = 0
    latest = 0.0
    for file_path in safe_walk(path) or []:
        try:
            stat = file_path.stat()
        except OSError:
            continue
        total += stat.st_size
        file_count += 1
        latest = max(latest, stat.st_mtime)
    return {
        "absolute_path": str(path.resolve()),
        "relative_path": str(path.resolve().relative_to(ROOT)) if path.resolve().is_relative_to(ROOT) else str(path.resolve()),
        "size_bytes": total,
        "size_mib": round(total / 1024**2, 3),
        "size_gib": round(total / 1024**3, 6),
        "file_count": file_count,
        "last_modified": datetime.fromtimestamp(latest, timezone.utc).isoformat() if latest else None,
    }


def collect_directories(files: list[Path]) -> list[dict[str, Any]]:
    sizes: dict[Path, int] = defaultdict(int)
    counts: dict[Path, int] = defaultdict(int)
    latest: dict[Path, float] = defaultdict(float)
    for file_path in files:
        try:
            stat = file_path.stat()
        except OSError:
            continue
        for parent in [file_path.parent, *file_path.parents]:
            if parent == ROOT.parent:
                break
            if ROOT not in [parent, *parent.parents]:
                continue
            sizes[parent] += stat.st_size
            counts[parent] += 1
            latest[parent] = max(latest[parent], stat.st_mtime)
    result = []
    for path, size in sizes.items():
        try:
            rel = str(path.relative_to(ROOT))
        except ValueError:
            rel = str(path)
        result.append({
            "absolute_path": str(path.resolve()),
            "relative_path": rel,
            "size_bytes": size,
            "size_mib": round(size / 1024**2, 3),
            "size_gib": round(size / 1024**3, 6),
            "file_count": counts[path],
            "last_modified": datetime.fromtimestamp(latest[path], timezone.utc).isoformat() if latest[path] else None,
        })
    return sorted(result, key=lambda item: item["size_bytes"], reverse=True)


def duplicate_analysis(large_files: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for item in large_files:
        if item["size_bytes"] >= 1_000_000 and Path(item["absolute_path"]).suffix.lower() in BINARY_SUFFIXES:
            groups[item["size_bytes"]].append(item)
    duplicates = []
    for size, items in groups.items():
        if len(items) < 2:
            continue
        hashed = []
        for item in items:
            digest = sha256_file(Path(item["absolute_path"]))
            hashed.append({**item, "sha256": digest})
        by_hash: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in hashed:
            by_hash[item["sha256"]].append(item)
        for digest, same in by_hash.items():
            if len(same) > 1:
                duplicates.append({"size_bytes": size, "sha256": digest, "copies": same})
    return {"duplicate_groups": duplicates}


def git_footprint() -> dict[str, Any]:
    git_dir = ROOT / ".git"
    packs = []
    for path in sorted((git_dir / "objects" / "pack").glob("*")):
        if path.is_file():
            packs.append(file_info(path))
    object_paths = {}
    rev_list = subprocess.run(["git", "rev-list", "--objects", "--all"], cwd=ROOT, capture_output=True, text=True, check=False)
    if rev_list.returncode == 0:
        for line in rev_list.stdout.splitlines():
            object_id, _, object_path = line.partition(" ")
            if object_path:
                object_paths[object_id] = object_path
    verify = subprocess.run(["git", "verify-pack", "-v", ".git/objects/pack/*.idx"], cwd=ROOT, capture_output=True, text=True, shell=True)
    large_objects = []
    if verify.returncode == 0:
        for line in verify.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 5 and parts[1] in {"blob", "tree", "commit", "tag"}:
                try:
                    size = int(parts[2])
                except ValueError:
                    continue
                if size >= 10 * 1024 * 1024:
                    large_objects.append({
                        "object": parts[0],
                        "type": parts[1],
                        "size_bytes": size,
                        "size_mib": round(size / 1024**2, 3),
                        "path": object_paths.get(parts[0]),
                    })
    for loose in sorted((git_dir / "objects").glob("[0-9a-f][0-9a-f]/*")):
        if not loose.is_file():
            continue
        object_id = loose.parent.name + loose.name
        object_type = subprocess.run(["git", "cat-file", "-t", object_id], cwd=ROOT, capture_output=True, text=True, check=False).stdout.strip()
        object_size_text = subprocess.run(["git", "cat-file", "-s", object_id], cwd=ROOT, capture_output=True, text=True, check=False).stdout.strip()
        try:
            object_size = int(object_size_text)
        except ValueError:
            continue
        if object_size >= 10 * 1024 * 1024:
            large_objects.append({
                "object": object_id,
                "type": object_type,
                "size_bytes": object_size,
                "size_mib": round(object_size / 1024**2, 3),
                "path": object_paths.get(object_id),
                "loose_object_file_size_bytes": loose.stat().st_size,
            })
    return {
        "git_size": directory_size(git_dir),
        "object_database_size": directory_size(git_dir / "objects"),
        "pack_files": packs,
        "large_objects_over_10mib": sorted(large_objects, key=lambda item: item["size_bytes"], reverse=True)[:50],
        "history_rewrite_performed": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only storage footprint audit for Tool Auto Sub.")
    parser.add_argument("--output", default=str(OUTPUT))
    args = parser.parse_args()

    files = list(safe_walk(ROOT))
    file_infos = [file_info(path) for path in files]
    directories = collect_directories(files)
    release_zips = [item for item in file_infos if item["category"] == "release_zip"]
    evidence_binaries = [item for item in file_infos if item["category"] == "evidence_binary"]
    project_media = [item for item in file_infos if item["category"] == "project_run_media"]
    cache_files = [item for item in file_infos if item["category"] == "cache"]
    top_files = sorted(file_infos, key=lambda item: item["size_bytes"], reverse=True)
    top_level = [directory_size(path) for path in sorted(ROOT.iterdir()) if path.is_dir() and not path.is_symlink()]
    external = [directory_size(path) for path in EXTERNAL_SCOPE if path.exists()]
    dev_ocr = directory_size(DEV_OCR_RUNTIME) if DEV_OCR_RUNTIME.exists() else {"absolute_path": str(DEV_OCR_RUNTIME), "exists": False, "size_bytes": 0}
    protected_hashes = {
        "cp12b_zip": sha256_file(CP12B_ZIP),
        "accepted_media": sha256_file(next(path for path in ACCEPTED_MEDIA_CANDIDATES if path.exists())),
        "cp12b_zip_matches_expected": sha256_file(CP12B_ZIP) == EXPECTED_CP12B,
        "accepted_media_matches_expected": sha256_file(next(path for path in ACCEPTED_MEDIA_CANDIDATES if path.exists())) == EXPECTED_ACCEPTED,
    }
    free_space = ps_json("Get-PSDrive -PSProvider FileSystem | Select-Object Name,Root,Free,Used | ConvertTo-Json -Depth 3")
    if isinstance(free_space, dict):
        free_space = [free_space]
    payload = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "project_root": str(ROOT),
        "free_space": free_space,
        "project_total": directory_size(ROOT),
        "top_level_directories": sorted(top_level, key=lambda item: item["size_bytes"], reverse=True),
        "top_50_directories": directories[:50],
        "top_100_files": top_files[:100],
        "release_zips": sorted(release_zips, key=lambda item: item["size_bytes"], reverse=True),
        "evidence_binary_sizes": sorted(evidence_binaries, key=lambda item: item["size_bytes"], reverse=True),
        "project_run_media_sizes": sorted(project_media, key=lambda item: item["size_bytes"], reverse=True),
        "cache_and_temporary_files": sorted(cache_files, key=lambda item: item["size_bytes"], reverse=True)[:200],
        "external_project_created_validation_dirs": external,
        "development_ocr_runtime": {**dev_ocr, "classification": "KEEP_DEVELOPMENT_DEPENDENCY"},
        "duplicate_analysis": duplicate_analysis(top_files[:1000]),
        "git_footprint": git_footprint(),
        "protected_hashes": protected_hashes,
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({
        "status": "PASS",
        "output": str(output),
        "project_size_bytes": payload["project_total"]["size_bytes"],
        "top_file_count": len(payload["top_100_files"]),
    }, indent=2))


if __name__ == "__main__":
    main()
