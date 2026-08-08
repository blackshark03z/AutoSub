#!/usr/bin/env python3
"""Risk-proportional immutable evidence bundles and verification execution."""
from __future__ import annotations

import csv
import json
import re
import shlex
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from runtime_support import (
    application_file_fingerprint,
    application_snapshot,
    atomic_write_json,
    atomic_write_text,
    sha256_file,
    sha256_text,
    task_delta_files,
)

INSPECTION_STATES = {"AUTOMATED_EXIT_ONLY", "AGENT_INSPECTED", "HUMAN_INSPECTED", "NOT_INSPECTED"}
HEX64 = re.compile(r"^[0-9a-f]{64}$")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def next_revision(root: Path, task_id: str) -> int:
    revisions: set[int] = set()
    ledger = root / ".ai" / "COST_LEDGER.csv"
    if ledger.is_file():
        with ledger.open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                if row.get("task_id") == task_id:
                    try:
                        revisions.add(int(row.get("task_revision") or 0))
                    except ValueError:
                        pass
    task_dir = root / ".ai" / "evidence" / task_id
    if task_dir.is_dir():
        for child in task_dir.iterdir():
            match = re.fullmatch(r"r(\d{3,})", child.name)
            if match:
                revisions.add(int(match.group(1)))
    history_dir = root / ".ai" / "history" / task_id
    if history_dir.is_dir():
        for child in history_dir.glob("r*.json"):
            match = re.fullmatch(r"r(\d{3,})\.json", child.name)
            if match:
                revisions.add(int(match.group(1)))
    return max(revisions, default=0) + 1


def revision_name(revision: int) -> str:
    return f"r{revision:03d}"


def ensure_repo_file(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as exc:
        raise SystemExit(f"Path escapes repository: {relative}") from exc
    if not path.is_file():
        raise SystemExit(f"File does not exist: {relative}")
    return path


def sanitize_output(value: str, limit_bytes: int = 256_000) -> tuple[str, bool, int]:
    # Conservative redaction for common secret syntaxes. Projects may add their own redactor upstream.
    redacted = re.sub(r"(?i)(authorization:\s*bearer\s+)[^\s]+", r"\1[REDACTED]", value)
    redacted = re.sub(r"(?i)((?:api[_-]?key|token|secret|password)\s*[=:]\s*)[^\s]+", r"\1[REDACTED]", redacted)
    encoded = redacted.encode("utf-8", errors="replace")
    original_size = len(encoded)
    if original_size <= limit_bytes:
        return redacted, False, original_size
    clipped = encoded[:limit_bytes].decode("utf-8", errors="replace")
    return clipped + "\n[TRUNCATED BY AI BUILD OS]\n", True, original_size


def run_command(
    root: Path,
    kind: str,
    command: str,
    timeout: int,
    logs_dir: Path,
    index: int,
    *,
    allow_shell: bool,
    inspected_by: str | None,
    limit_bytes: int = 256_000,
) -> dict[str, Any]:
    started = now()
    try:
        argv: list[str] | str
        if allow_shell:
            argv = command
        else:
            argv = shlex.split(command, posix=True)
            if not argv:
                raise SystemExit("Verification command cannot be empty")
        result = subprocess.run(
            argv,
            cwd=root,
            shell=allow_shell,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        stdout, stdout_truncated, stdout_original = sanitize_output(result.stdout or "", limit_bytes)
        stderr, stderr_truncated, stderr_original = sanitize_output(result.stderr or "", limit_bytes)
        exit_code = result.returncode
    except subprocess.TimeoutExpired as exc:
        raw_out = exc.stdout.decode(errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        raw_err = exc.stderr.decode(errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        stdout, stdout_truncated, stdout_original = sanitize_output(raw_out, limit_bytes)
        stderr, stderr_truncated, stderr_original = sanitize_output(raw_err + "\n[TIMEOUT]\n", limit_bytes)
        exit_code = 124

    completed = now()
    prefix = f"EV-{index:03d}-{kind}"
    stdout_path = logs_dir / f"{prefix}.stdout.txt"
    stderr_path = logs_dir / f"{prefix}.stderr.txt"
    atomic_write_text(stdout_path, stdout)
    atomic_write_text(stderr_path, stderr)
    inspection = "AUTOMATED_EXIT_ONLY"
    if inspected_by:
        inspection = "HUMAN_INSPECTED" if inspected_by.lower().startswith("human:") else "AGENT_INSPECTED"
    return {
        "id": f"EV-{index:03d}",
        "kind": kind,
        "command": command,
        "execution_mode": "shell" if allow_shell else "argv",
        "started_at": started,
        "completed_at": completed,
        "exit_code": exit_code,
        "result": "PASS" if exit_code == 0 else "FAIL",
        "inspection_status": inspection,
        "inspected_by": inspected_by or "NONE",
        "stdout": {
            "path": stdout_path.name,
            "sha256": sha256_file(stdout_path),
            "stored_bytes": stdout_path.stat().st_size,
            "original_bytes": stdout_original,
            "truncated": stdout_truncated,
        },
        "stderr": {
            "path": stderr_path.name,
            "sha256": sha256_file(stderr_path),
            "stored_bytes": stderr_path.stat().st_size,
            "original_bytes": stderr_original,
            "truncated": stderr_truncated,
        },
    }


def _safe_inline(value: str) -> str:
    return value.replace("`", "'").replace("|", "¦").replace("\n", " ").strip()


def validate_review_report(root: Path, relative: str, expected: dict[str, str]) -> list[str]:
    errors: list[str] = []
    path = ensure_repo_file(root, relative)
    body = path.read_text(encoding="utf-8", errors="replace")

    def field(name: str) -> str:
        match = re.search(rf"^-?\s*{re.escape(name)}:\s*(.*?)\s*$", body, re.MULTILINE | re.IGNORECASE)
        return match.group(1).strip() if match else ""

    required = ["Task ID", "Task revision", "Reviewed snapshot SHA256", "Reviewer identity", "Reviewer role", "Independent from writer", "Verdict", "Reviewed at"]
    for name in required:
        if not field(name):
            errors.append(f"Review report missing {name}")
    if field("Task ID") != expected["task_id"]:
        errors.append("Review report Task ID mismatch")
    if field("Task revision") != str(expected["task_revision"]):
        errors.append("Review report task revision mismatch")
    if field("Reviewed snapshot SHA256") != expected["snapshot_sha256"]:
        errors.append("Review report snapshot mismatch")
    if field("Independent from writer").casefold() not in {"yes", "true"}:
        errors.append("Review report must confirm reviewer independence")
    if field("Reviewer identity") == expected.get("writer_identity"):
        errors.append("Review report reviewer must differ from writer identity")
    if field("Verdict").upper() not in {"PASS", "ACCEPTED"}:
        errors.append("Review report verdict must be PASS or ACCEPTED")
    try:
        parse_iso(field("Reviewed at"))
    except (ValueError, TypeError):
        errors.append("Review report Reviewed at must be ISO-8601")
    return errors


def create_staged_bundle(
    root: Path,
    tx_dir: Path,
    *,
    task: dict[str, str],
    revision: int,
    outcome: str,
    commands: list[tuple[str, str]],
    artifacts: list[str],
    timeout: int,
    allow_shell: bool,
    inspected_by: str | None,
    cleanup_note: str,
    known_limits: str,
    review_report: str | None,
    compact: bool = False,
    expected_outputs: list[str] | None = None,
) -> tuple[Path, dict[str, Any]]:
    stage = tx_dir / "evidence-stage"
    logs_dir = stage / "logs"
    artifacts_dir = stage / "artifacts"
    review_dir = stage / "review"
    logs_dir.mkdir(parents=True)
    artifacts_dir.mkdir(parents=True)
    review_dir.mkdir(parents=True)

    before = application_snapshot(root)
    checks: list[dict[str, Any]] = []
    for index, (kind, command) in enumerate(commands, start=1):
        check = run_command(
            root, kind, command, timeout, logs_dir, index,
            allow_shell=allow_shell, inspected_by=inspected_by,
            limit_bytes=32_768 if compact else 256_000,
        )
        checks.append(check)
        print(f"{kind}: {check['result']} exit={check['exit_code']} command={command}")
        if check["result"] != "PASS":
            raise SystemExit("Verification command failed; task remains open and staged evidence will not be published")

    expected_outputs = [item for item in (expected_outputs or []) if item.strip()]
    output_assertions: list[dict[str, Any]] = []
    if expected_outputs:
        combined_output = "\n".join(
            path.read_text(encoding="utf-8", errors="replace")
            for path in sorted(logs_dir.glob("EV-*.txt"))
        )
        for marker in expected_outputs:
            matched = marker in combined_output
            output_assertions.append({"text": marker, "matched": matched})
            if not matched:
                raise SystemExit(f"Expected output marker not found in verification logs: {marker!r}")

    verified = application_snapshot(root)
    delta_files = task_delta_files(root, task_id=task["task_id"], task_revision=revision)
    delta_hashes = {relative: application_file_fingerprint(root, relative) for relative in delta_files}
    artifact_records: list[dict[str, Any]] = []
    for relative in artifacts:
        source = ensure_repo_file(root, relative)
        target_name = f"{len(artifact_records)+1:03d}-{source.name}"
        target = artifacts_dir / target_name
        shutil.copy2(source, target)
        artifact_records.append({
            "source_path": relative.replace("\\", "/"),
            "bundle_path": f"artifacts/{target_name}",
            "source_sha256": sha256_file(source),
            "bundle_sha256": sha256_file(target),
            "size": target.stat().st_size,
        })

    review_record: dict[str, Any] | None = None
    if review_report:
        source = ensure_repo_file(root, review_report)
        target = review_dir / source.name
        shutil.copy2(source, target)
        review_record = {
            "source_path": review_report.replace("\\", "/"),
            "bundle_path": f"review/{source.name}",
            "sha256": sha256_file(target),
        }

    generated = now()
    manifest: dict[str, Any] = {
        "schema_version": 4,
        "evidence_mode": "COMPACT" if compact else "FULL",
        "task_id": task["task_id"],
        "task_revision": revision,
        "risk_tier": task["risk_tier"],
        "execution_profile": task["execution_profile"],
        "success_criterion": task["success_criterion"],
        "accepted_outcome": outcome,
        "generated_at": generated,
        "starting_snapshot": before,
        "verified_snapshot": verified,
        "task_delta_files": delta_files,
        "task_delta_file_hashes": delta_hashes,
        "checks": checks,
        "output_assertions": output_assertions,
        "artifacts": artifact_records,
        "review": review_record,
        "cleanup_note": cleanup_note,
        "known_limits": known_limits,
        "final_verdict": "PASS",
    }
    atomic_write_json(stage / "manifest.json", manifest)

    rows: list[str] = []
    for check in checks:
        rows.append(
            f"| {check['id']} | {check['kind']} | `{_safe_inline(check['command'])}` | {check['exit_code']} | "
            f"{check['result']} | {check['inspection_status']} | `{check['stdout']['sha256']}` | `{check['stderr']['sha256']}` | "
            f"{check['started_at']} | {check['completed_at']} |"
        )
    artifact_lines = [
        f"- `{_safe_inline(item['source_path'])}` → `{item['bundle_path']}` SHA256 `{item['bundle_sha256']}`"
        for item in artifact_records
    ] or ["- NONE"]
    assertion_lines = [
        f"- {'PASS' if item['matched'] else 'FAIL'}: `{_safe_inline(item['text'])}`"
        for item in output_assertions
    ] or ["- NONE"]
    evidence_md = f"""# Evidence Index

- Task ID: {task['task_id']}
- Task revision: {revision}
- Success Criterion: {task['success_criterion']}
- Accepted outcome: {outcome}
- Generated: {generated}
- Risk tier: {task['risk_tier']}
- Verified snapshot SHA256: {verified['snapshot_sha256']}
- Verified HEAD: {verified['head']}
- Final verdict: PASS
- Evidence schema: 4
- Evidence mode: {"COMPACT" if compact else "FULL"}
- Manifest: manifest.json

## Checks

| ID | Kind | Command | Exit | Result | Inspection | Stdout SHA256 | Stderr SHA256 | Started | Completed |
|---|---|---|---:|---|---|---|---|---|---|
{chr(10).join(rows)}

## Output Assertions

{chr(10).join(assertion_lines)}

## Runtime Artifacts

{chr(10).join(artifact_lines)}

## Side Effects and Cleanup

- Cleanup/rollback verification: {_safe_inline(cleanup_note)}
- Known limits: {_safe_inline(known_limits)}
"""
    worker_md = f"""# Worker Report

- Task ID: {task['task_id']}
- Task revision: {revision}
- Risk / profile: {task['risk_tier']} / {task['execution_profile']}
- Outcome: {outcome}
- Verified snapshot SHA256: {verified['snapshot_sha256']}
- Verification: {len(checks)} checks passed
- Evidence index: EVIDENCE_INDEX.md
- Side effects and cleanup: {cleanup_note}
- Known limits: {known_limits}
- Lease released: yes after successful close transaction
- Next exact action: select the next smallest milestone-linked outcome
"""
    atomic_write_text(stage / "EVIDENCE_INDEX.md", evidence_md)
    atomic_write_text(stage / "WORKER_REPORT.md", worker_md)

    bundle_files: list[dict[str, Any]] = []
    for path in sorted(stage.rglob("*")):
        if path.is_file() and path.name != "bundle.sha256.json":
            bundle_files.append({"path": path.relative_to(stage).as_posix(), "sha256": sha256_file(path), "size": path.stat().st_size})
    atomic_write_json(stage / "bundle.sha256.json", {"schema_version": 1, "files": bundle_files})
    return stage, manifest


def publish_bundle(root: Path, stage: Path, task_id: str, revision: int) -> Path:
    final = root / ".ai" / "evidence" / task_id / revision_name(revision)
    if final.exists():
        raise SystemExit(f"Immutable evidence bundle already exists: {final.relative_to(root)}")
    final.parent.mkdir(parents=True, exist_ok=True)
    stage.rename(final)
    return final


def verify_bundle(root: Path, bundle_dir: Path) -> list[str]:
    errors: list[str] = []
    if not bundle_dir.is_dir():
        return [f"Evidence bundle missing: {bundle_dir}"]
    manifest_path = bundle_dir / "manifest.json"
    hash_path = bundle_dir / "bundle.sha256.json"
    if not manifest_path.is_file():
        return ["Evidence manifest.json missing"]
    if not hash_path.is_file():
        errors.append("Evidence bundle.sha256.json missing")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return [f"Evidence manifest invalid JSON: {exc}"]
    required = ["schema_version", "task_id", "task_revision", "generated_at", "verified_snapshot", "checks", "final_verdict"]
    for name in required:
        if name not in manifest:
            errors.append(f"Evidence manifest missing {name}")
    if manifest.get("final_verdict") != "PASS":
        errors.append("Evidence manifest final_verdict must be PASS")
    try:
        parse_iso(str(manifest.get("generated_at", "")))
    except ValueError:
        errors.append("Evidence manifest generated_at invalid")

    for check in manifest.get("checks", []):
        if check.get("result") != "PASS" or check.get("exit_code") != 0:
            errors.append(f"Evidence check not PASS: {check.get('id')}")
        if check.get("inspection_status") not in INSPECTION_STATES:
            errors.append(f"Evidence inspection status invalid: {check.get('id')}")
        try:
            if parse_iso(check["completed_at"]) < parse_iso(check["started_at"]):
                errors.append(f"Evidence check time reversed: {check.get('id')}")
        except (KeyError, ValueError):
            errors.append(f"Evidence check timestamp invalid: {check.get('id')}")
        for stream in ["stdout", "stderr"]:
            entry = check.get(stream, {})
            relative = entry.get("path", "")
            path = bundle_dir / "logs" / relative
            digest = entry.get("sha256", "")
            if not HEX64.fullmatch(digest):
                errors.append(f"Evidence {stream} hash invalid: {check.get('id')}")
            elif not path.is_file():
                errors.append(f"Evidence {stream} log missing: {relative}")
            elif sha256_file(path) != digest:
                errors.append(f"Evidence {stream} hash mismatch: {check.get('id')}")

    delta_hashes = manifest.get("task_delta_file_hashes")
    if delta_hashes is not None:
        if not isinstance(delta_hashes, dict):
            errors.append("Evidence task_delta_file_hashes must be an object")
        else:
            for relative, fingerprint in delta_hashes.items():
                if not isinstance(relative, str) or not isinstance(fingerprint, str) or not fingerprint:
                    errors.append("Evidence task_delta_file_hashes contains invalid entry")
                    break
    for assertion in manifest.get("output_assertions", []):
        if not assertion.get("matched"):
            errors.append(f"Evidence output assertion failed: {assertion.get('text', '')}")

    for artifact in manifest.get("artifacts", []):
        relative = artifact.get("bundle_path", "")
        path = bundle_dir / relative
        digest = artifact.get("bundle_sha256", "")
        if not HEX64.fullmatch(digest):
            errors.append(f"Artifact hash invalid: {relative}")
        elif not path.is_file():
            errors.append(f"Artifact missing: {relative}")
        elif sha256_file(path) != digest:
            errors.append(f"Artifact hash mismatch: {relative}")

    if hash_path.is_file():
        try:
            hashes = json.loads(hash_path.read_text(encoding="utf-8"))
            for item in hashes.get("files", []):
                path = bundle_dir / item["path"]
                if not path.is_file() or sha256_file(path) != item["sha256"]:
                    errors.append(f"Bundle file integrity mismatch: {item.get('path')}")
        except (json.JSONDecodeError, KeyError) as exc:
            errors.append(f"Bundle hash manifest invalid: {exc}")
    return errors
