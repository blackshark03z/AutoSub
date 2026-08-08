#!/usr/bin/env python3
"""Regression suite for v1.8 speed, provenance, scope, risk and evidence invariants."""
from __future__ import annotations

import csv
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(root: Path, *args: str, expect: int = 0) -> subprocess.CompletedProcess[str]:
    if os.environ.get("AI_OS_SELF_TEST_TRACE"):
        print(f"TRACE {root.name}: python {' '.join(args)}", flush=True)
    result = subprocess.run([sys.executable, *args], cwd=root, capture_output=True, text=True, timeout=20)
    if result.returncode != expect:
        raise AssertionError(
            f"Expected rc={expect}, got {result.returncode}\n$ python {' '.join(args)}\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )
    return result


def cmd(root: Path, *args: str) -> None:
    if os.environ.get("AI_OS_SELF_TEST_TRACE"):
        print(f"TRACE {root.name}: {' '.join(args)}", flush=True)
    result = subprocess.run(list(args), cwd=root, capture_output=True, text=True, timeout=20)
    if result.returncode:
        raise AssertionError(f"command failed {' '.join(args)}\n{result.stdout}\n{result.stderr}")


def fresh(base: Path, name: str) -> Path:
    work = base / name
    shutil.copytree(ROOT, work)
    for path in work.rglob("__pycache__"):
        shutil.rmtree(path)
    (work / ".gitignore").write_text("__pycache__/\n*.pyc\n", encoding="utf-8")
    cmd(work, "git", "init", "-q", "-b", "main")
    cmd(work, "git", "config", "user.email", "self-test@example.invalid")
    cmd(work, "git", "config", "user.name", "AI OS Self Test")
    cmd(work, "git", "add", ".")
    cmd(work, "git", "commit", "-q", "-m", "baseline")
    run(
        work, "scripts/ai_os.py", "init", "--project-id", "TEST", "--owner", "Owner",
        "--problem", "Test problem", "--target-user", "User", "--primary-action", "Run",
        "--observable-result", "Result", "--mvp-goal", "MVP",
    )
    assert (work / ".github/workflows/ai-build-os.yml").is_file()
    return work


def begin_r1(work: Path, task_id: str = "TASK-001", *, ready: bool = False, modify: str = "src/**,tests/**", create: str = "src/**,tests/**") -> None:
    args = [
        "scripts/ai_os.py", "begin", "--task-id", task_id, "--outcome", "Normalize input",
        "--risk", "R1", "--success-criterion", "SC-001", "--delivery-delta", "EXECUTABLE_CAPABILITY",
        "--modify", modify, "--create", create,
    ]
    if ready:
        args.append("--ready")
    run(work, *args)


def main() -> None:
    run(ROOT, "scripts/validate_ai_os.py", "--template")
    with tempfile.TemporaryDirectory(prefix="ai-os-v18-") as tmp:
        base = Path(tmp)

        # Lifecycle is complete: READY -> claim -> pause -> resume; start auto-claims by default.
        lifecycle = fresh(base, "lifecycle")
        begin_r1(lifecycle, ready=True)
        result = run(lifecycle, "scripts/ai_os.py", "done", "--outcome", "No", "--focused-command", "true", expect=1)
        assert "CLAIMED lease" in result.stdout + result.stderr
        run(lifecycle, "scripts/ai_os.py", "claim", "--session-label", "WORKER-A")
        run(lifecycle, "scripts/ai_os.py", "pause")
        run(lifecycle, "scripts/ai_os.py", "resume", "--session-label", "WORKER-B")
        status = json.loads(run(lifecycle, "scripts/ai_os.py", "status", "--json").stdout)
        assert status["task_status"] == "ACTIVE" and status["writer"] == "WORKER-B"

        # Fast Lane: R1 needs focused evidence + explicit inspection, negative check is trigger-based.
        work = fresh(base, "r1")
        begin_r1(work)
        capsule = (work / ".ai" / "CONTEXT_CAPSULE.md").read_text(encoding="utf-8")
        assert len(capsule) // 4 < 450, f"LEAN worker packet too large: ~{len(capsule)//4} tokens"
        (work / "src").mkdir(); (work / "tests").mkdir()
        (work / "src" / "normalize.py").write_text("def normalize(value):\n    return value.strip().lower()\n", encoding="utf-8")
        (work / "tests" / "test_normalize.py").write_text("from src.normalize import normalize\nassert normalize(' OK ') == 'ok'\n", encoding="utf-8")
        result = run(work, "scripts/ai_os.py", "done", "--outcome", "Normalize input", "--focused-command", "true", expect=1)
        assert "output-inspected-by" in result.stdout + result.stderr
        run(
            work, "scripts/ai_os.py", "done", "--outcome", "Normalize input",
            "--focused-command", f"{sys.executable} -c \"from src.normalize import normalize; assert normalize(' OK ') == 'ok'\"",
            "--artifact", "src/normalize.py", "--first-pass-accepted", "yes", "--output-inspected-by", "agent:worker",
        )
        run(work, "scripts/ai_os.py", "check")
        bundle = work / ".ai" / "evidence" / "TASK-001" / "r001"
        manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["evidence_mode"] == "COMPACT"
        assert [c["kind"] for c in manifest["checks"]] == ["focused"]
        assert manifest["checks"][0]["inspection_status"] == "AGENT_INSPECTED"
        assert set(manifest["task_delta_files"]) == {"src/normalize.py", "tests/test_normalize.py"}
        assert set(manifest["task_delta_file_hashes"]) == {"src/normalize.py", "tests/test_normalize.py"}
        assert all(value.startswith("FILE:") for value in manifest["task_delta_file_hashes"].values())

        # v1.8 commit-aware CI: commit after done is valid, post-evidence source drift is not.
        ciwork = fresh(base, "ci-binding")
        base_sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ciwork, capture_output=True, text=True, check=True).stdout.strip()
        begin_r1(ciwork, task_id="TASK-CI")
        (ciwork / "src").mkdir(); (ciwork / "tests").mkdir()
        (ciwork / "src" / "feature.py").write_text("def value(): return 1\n", encoding="utf-8")
        (ciwork / "tests" / "test_feature.py").write_text("from src.feature import value\nassert value() == 1\n", encoding="utf-8")
        run(
            ciwork, "scripts/ai_os.py", "done", "--outcome", "Commit-bound feature",
            "--focused-command", f"{sys.executable} -c \"from src.feature import value; assert value() == 1; print('CI-EVIDENCE-OK')\"",
            "--expected-output", "CI-EVIDENCE-OK", "--output-inspected-by", "agent:worker", "--first-pass-accepted", "yes",
        )
        cmd(ciwork, "git", "add", "."); cmd(ciwork, "git", "commit", "-q", "-m", "verified feature")
        run(ciwork, "scripts/validate_ai_os.py", "--ci", "--ci-base-ref", base_sha)
        (ciwork / "src" / "feature.py").write_text("def value(): return 2\n", encoding="utf-8")
        cmd(ciwork, "git", "add", "src/feature.py"); cmd(ciwork, "git", "commit", "-q", "-m", "unverified drift")
        result = run(ciwork, "scripts/validate_ai_os.py", "--ci", "--ci-base-ref", base_sha, expect=1)
        assert "CI unbound application change: src/feature.py" in result.stdout

        # Tampering with accepted evidence is detected.
        log = bundle / "logs" / manifest["checks"][0]["stdout"]["path"]
        original = log.read_bytes(); log.write_bytes(original + b"tamper")
        result = run(work, "scripts/ai_os.py", "check", expect=1)
        assert "hash mismatch" in result.stdout
        log.write_bytes(original)
        run(work, "scripts/ai_os.py", "check")

        # Accepted source cannot drift without a new task.
        source = work / "src" / "normalize.py"; original_source = source.read_text(encoding="utf-8")
        source.write_text(original_source + "# drift\n", encoding="utf-8")
        result = run(work, "scripts/ai_os.py", "check", expect=1)
        assert "changed after accepted snapshot" in result.stdout
        source.write_text(original_source, encoding="utf-8")

        # Scope uses task delta, not the whole dirty worktree.
        scope = fresh(base, "scope-delta")
        (scope / "outside.py").write_text("value = 1\n", encoding="utf-8")
        cmd(scope, "git", "add", "outside.py"); cmd(scope, "git", "commit", "-q", "-m", "outside baseline")
        (scope / "outside.py").write_text("value = 2\n", encoding="utf-8")  # pre-existing dirty
        begin_r1(scope, modify="src/**", create="src/**")
        run(scope, "scripts/ai_os.py", "check")  # pre-existing dirt no longer blocks task start/check
        (scope / "src").mkdir(); (scope / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
        run(scope, "scripts/ai_os.py", "check")
        (scope / "outside.py").write_text("value = 3\n", encoding="utf-8")
        result = run(scope, "scripts/ai_os.py", "check", expect=1)
        assert "Unauthorized task-delta paths" in result.stdout
        (scope / "outside.py").write_text("value = 2\n", encoding="utf-8")
        run(scope, "scripts/ai_os.py", "check")

        # Scope can be amended without replacing/restarting the task.
        amend = fresh(base, "amend")
        begin_r1(amend, modify="app/a.py", create="app/a.py")
        (amend / "app").mkdir(); (amend / "app" / "a.py").write_text("a=1\n", encoding="utf-8")
        (amend / "app" / "b.py").write_text("b=1\n", encoding="utf-8")
        result = run(amend, "scripts/ai_os.py", "check", expect=1)
        assert "app/b.py" in result.stdout
        run(amend, "scripts/ai_os.py", "amend", "--add-create", "app/b.py", "--reason", "root cause crosses adjacent module")
        run(amend, "scripts/ai_os.py", "check")

        # Risk cannot be under-declared: overwrite auto-floors to R3 and requires approval.
        risk = fresh(base, "risk-floor")
        result = run(
            risk, "scripts/ai_os.py", "begin", "--task-id", "TASK-RISK", "--outcome", "Overwrite artifact",
            "--risk", "R0", "--success-criterion", "SC-001", "--delivery-delta", "RISK_RETIREMENT",
            "--modify", "out.txt", "--artifact-operation", "OVERWRITE", expect=1,
        )
        assert "Effective R3" in result.stdout + result.stderr
        run(
            risk, "scripts/ai_os.py", "begin", "--task-id", "TASK-RISK", "--outcome", "Overwrite artifact",
            "--risk", "R0", "--success-criterion", "SC-001", "--delivery-delta", "RISK_RETIREMENT",
            "--modify", "out.txt", "--artifact-operation", "OVERWRITE", "--owner-authorization", "APPROVED",
            "--authorization-reference", "TICKET-1",
        )
        risk_status = json.loads(run(risk, "scripts/ai_os.py", "status", "--json").stdout)
        assert risk_status["risk"] == "R3"
        result = run(risk, "scripts/ai_os.py", "done", "--outcome", "No", "--focused-command", "true", expect=1)
        assert "negative-command" in result.stdout + result.stderr

        # Shared/API surface is automatically at least R2; R2 cannot skip integration.
        r2 = fresh(base, "r2")
        run(
            r2, "scripts/ai_os.py", "begin", "--task-id", "TASK-R2", "--outcome", "API behavior",
            "--risk", "R0", "--success-criterion", "SC-001", "--delivery-delta", "EXECUTABLE_CAPABILITY",
            "--modify", "src/api/**", "--create", "src/api/**",
        )
        r2_status = json.loads(run(r2, "scripts/ai_os.py", "status", "--json").stdout)
        assert r2_status["risk"] == "R2"
        result = run(
            r2, "scripts/ai_os.py", "done", "--outcome", "API behavior",
            "--focused-command", "true", "--negative-command", "true",
            "--output-inspected-by", "agent:worker", expect=1,
        )
        assert "integration-command" in result.stdout + result.stderr

        # R3 requires structured, independent, snapshot-bound review.
        r3 = fresh(base, "r3")
        run(
            r3, "scripts/ai_os.py", "begin", "--task-id", "TASK-R3", "--outcome", "Secure result",
            "--risk", "R3", "--success-criterion", "SC-001", "--delivery-delta", "RISK_RETIREMENT",
            "--modify", "src/security.py", "--create", "src/security.py", "--owner-authorization", "APPROVED",
            "--authorization-reference", "TICKET-2",
        )
        (r3 / "src").mkdir(); (r3 / "src" / "security.py").write_text("def safe(v): return v != 'bad'\n", encoding="utf-8")
        (r3 / ".ai" / "reviews").mkdir()
        status = json.loads(run(r3, "scripts/ai_os.py", "status", "--json").stdout)
        review = r3 / ".ai" / "reviews" / "valid.md"
        review.write_text(
            "# Independent Review Report\n\n"
            "- Task ID: TASK-R3\n- Task revision: 1\n"
            f"- Reviewed snapshot SHA256: {status['snapshot_sha256']}\n"
            "- Reviewer identity: HUMAN-SECURITY\n- Reviewer role: SECURITY_REVIEWER\n"
            "- Independent from writer: yes\n- Writer identity: AI-WORKER\n"
            "- Verdict: PASS\n- Reviewed at: 2026-08-08T01:30:00+00:00\n",
            encoding="utf-8",
        )
        run(
            r3, "scripts/ai_os.py", "done", "--outcome", "Secure result",
            "--focused-command", "true", "--negative-command", "true",
            "--integration-command", "true", "--rollback-command", "true",
            "--full-suite-command", "true", "--review-report", ".ai/reviews/valid.md",
            "--output-inspected-by", "human:reviewer",
        )
        r3_manifest = json.loads((r3 / ".ai/evidence/TASK-R3/r001/manifest.json").read_text(encoding="utf-8"))
        assert r3_manifest["evidence_mode"] == "FULL"
        run(r3, "scripts/ai_os.py", "check")

        # Abort cannot launder task changes into the next baseline.
        aborted = fresh(base, "abort")
        begin_r1(aborted)
        (aborted / "src").mkdir(); (aborted / "src" / "temp.py").write_text("x=1\n", encoding="utf-8")
        result = run(aborted, "scripts/ai_os.py", "abort", expect=1)
        assert "Refusing to abort with task delta" in result.stdout + result.stderr
        (aborted / "src" / "temp.py").unlink(); (aborted / "src").rmdir()
        run(aborted, "scripts/ai_os.py", "abort")

        # v1.8 reconciliation: actual diff can escalate risk even when declarations/path names do not.
        recon = fresh(base, "reconciliation")
        run(
            recon, "scripts/ai_os.py", "begin", "--task-id", "TASK-ACTUAL-RISK", "--outcome", "Persist record",
            "--risk", "R0", "--success-criterion", "SC-001", "--delivery-delta", "RISK_RETIREMENT",
            "--modify", "src/backend/**", "--create", "src/backend/**",
        )
        (recon / "src" / "backend").mkdir(parents=True)
        (recon / "src" / "backend" / "store.py").write_text(
            "import sqlite3\n"
            "def save(path):\n"
            "    conn = sqlite3.connect(path)\n"
            "    conn.execute(\"INSERT INTO items(value) VALUES ('x')\")\n"
            "    conn.commit()\n", encoding="utf-8",
        )
        result = run(
            recon, "scripts/ai_os.py", "done", "--outcome", "Persist record",
            "--focused-command", "true", expect=1,
        )
        assert "Actual task delta requires R3" in result.stdout + result.stderr
        (recon / "src" / "backend" / "store.py").unlink(); (recon / "src" / "backend").rmdir(); (recon / "src").rmdir()
        run(recon, "scripts/ai_os.py", "abort")

        # v1.8 project-sensitive business terms can raise actual risk without noisy global keywords.
        project_path = recon / ".ai" / "PROJECT.md"
        project_body = project_path.read_text(encoding="utf-8").replace("Sensitive business terms: NONE", "Sensitive business terms: account_balance")
        project_path.write_text(project_body, encoding="utf-8")
        run(recon, "scripts/ai_os.py", "begin", "--task-id", "TASK-BUSINESS-RISK", "--outcome", "Adjust balance logic",
            "--risk", "R0", "--success-criterion", "SC-001", "--delivery-delta", "RISK_RETIREMENT",
            "--modify", "src/core.py", "--create", "src/core.py")
        (recon / "src").mkdir(exist_ok=True); (recon / "src" / "core.py").write_text("account_balance = 10\n", encoding="utf-8")
        result = run(recon, "scripts/ai_os.py", "done", "--outcome", "Adjust balance logic", "--focused-command", "true", expect=1)
        assert "Actual task delta requires R2" in result.stdout + result.stderr
        (recon / "src" / "core.py").unlink(); (recon / "src").rmdir(); run(recon, "scripts/ai_os.py", "abort")

        # v1.8 reconciliation: docs/tests-only work cannot claim a shipping delivery delta.
        run(
            recon, "scripts/ai_os.py", "begin", "--task-id", "TASK-FAKE-SHIP", "--outcome", "Document internals",
            "--risk", "R0", "--success-criterion", "SC-001", "--delivery-delta", "EXECUTABLE_CAPABILITY",
            "--modify", "internal_notes.md", "--create", "internal_notes.md",
        )
        (recon / "internal_notes.md").write_text("internal notes\n", encoding="utf-8")
        result = run(
            recon, "scripts/ai_os.py", "done", "--outcome", "Document internals",
            "--focused-command", "true", expect=1,
        )
        assert "docs/tests-only task delta" in result.stdout + result.stderr
        (recon / "internal_notes.md").unlink()
        run(recon, "scripts/ai_os.py", "abort")

        # v1.8 shipping circuit breaker is visible and blocks further non-shipping starts.
        for index in range(1, 4):
            run(
                recon, "scripts/ai_os.py", "begin", "--task-id", f"TASK-NONSHIP-{index}",
                "--outcome", f"Internal checkpoint {index}", "--risk", "R0", "--success-criterion", "SC-001",
                "--delivery-delta", "NO_DELTA", "--modify", "internal.txt",
            )
            run(
                recon, "scripts/ai_os.py", "done", "--outcome", f"Internal checkpoint {index}",
                "--focused-command", "true",
            )
        breaker_status = json.loads(run(recon, "scripts/ai_os.py", "status", "--json").stdout)
        assert breaker_status["shipping_circuit_breaker"] == "ACTIVE"
        assert breaker_status["consecutive_non_shipping_tasks"] == 3
        capsule = (recon / ".ai" / "CONTEXT_CAPSULE.md").read_text(encoding="utf-8")
        assert "Shipping breaker: ACTIVE" in capsule
        result = run(
            recon, "scripts/ai_os.py", "begin", "--task-id", "TASK-BLOCKED", "--outcome", "More internals",
            "--risk", "R0", "--success-criterion", "SC-001", "--delivery-delta", "NO_DELTA",
            "--modify", "internal.txt", expect=1,
        )
        assert "Shipping Circuit Breaker is ACTIVE" in result.stdout + result.stderr
        run(
            recon, "scripts/ai_os.py", "begin", "--task-id", "TASK-OVERRIDE", "--outcome", "Required internal cleanup",
            "--risk", "R0", "--success-criterion", "SC-001", "--delivery-delta", "NO_DELTA", "--modify", "internal.txt",
            "--breaker-override", "--breaker-override-reason", "dependency migration cleanup",
        )
        run(recon, "scripts/ai_os.py", "done", "--outcome", "Required internal cleanup", "--focused-command", "true")
        override_report = run(recon, "scripts/ai_os.py", "report").stdout
        assert "Shipping breaker overrides: 1/" in override_report and "dependency migration cleanup" in override_report
        run(
            recon, "scripts/ai_os.py", "begin", "--task-id", "TASK-SHIP-NEXT", "--outcome", "Ship next capability",
            "--risk", "R0", "--success-criterion", "SC-001", "--delivery-delta", "EXECUTABLE_CAPABILITY",
            "--modify", "src/**", "--create", "src/**",
        )

        # v1.8 cross-revision stop-loss activates only after two explicit failed first passes.
        stoploss = fresh(base, "stop-loss")
        for rev in range(1, 3):
            run(stoploss, "scripts/ai_os.py", "begin", "--task-id", "TASK-LOOP", "--outcome", f"Attempt {rev}",
                "--risk", "R0", "--success-criterion", "SC-001", "--delivery-delta", "NO_DELTA", "--modify", "internal.txt")
            run(stoploss, "scripts/ai_os.py", "done", "--outcome", f"Attempt {rev}", "--focused-command", "true", "--first-pass-accepted", "no")
        result = run(stoploss, "scripts/ai_os.py", "begin", "--task-id", "TASK-LOOP", "--outcome", "Attempt 3",
            "--risk", "R0", "--success-criterion", "SC-001", "--delivery-delta", "NO_DELTA", "--modify", "internal.txt", expect=1)
        assert "Stop-loss active" in result.stdout + result.stderr
        run(stoploss, "scripts/ai_os.py", "begin", "--task-id", "TASK-LOOP", "--outcome", "Attempt 3",
            "--risk", "R0", "--success-criterion", "SC-001", "--delivery-delta", "NO_DELTA", "--modify", "internal.txt",
            "--stop-loss-ack", "root cause moved from parser to state transition")
        run(stoploss, "scripts/ai_os.py", "abort")

        # Reusing a task ID increments immutable revision.
        begin_r1(work, task_id="TASK-001")
        source.write_text(original_source + "# revision-2\n", encoding="utf-8")
        run(
            work, "scripts/ai_os.py", "done", "--outcome", "Normalize input again",
            "--focused-command", f"{sys.executable} -c \"from src.normalize import normalize; assert normalize('X') == 'x'\"",
            "--output-inspected-by", "agent:worker",
        )
        assert (work / ".ai/evidence/TASK-001/r001").is_dir() and (work / ".ai/evidence/TASK-001/r002").is_dir()
        with (work / ".ai/COST_LEDGER.csv").open(encoding="utf-8", newline="") as handle:
            rows = [r for r in csv.DictReader(handle) if r["task_id"] == "TASK-001"]
        assert [r["task_revision"] for r in rows] == ["1", "2"]

        run(work, "scripts/ai_os.py", "reconcile", "--task-id", "TASK-001", "--task-revision", "1", "--later-rework", "no", "--escaped-defect", "no", "--notes", "7-day check")
        report = run(work, "scripts/ai_os.py", "report").stdout
        assert "Later rework: 0/" in report and "Escaped defects: 0/" in report

    print("SELF_TEST: PASS")


if __name__ == "__main__":
    main()
