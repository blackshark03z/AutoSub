# CI, Goal and Multi-Agent Operation — v1.15

## Commit-aware CI

`ai_os.py init` tự cài `.github/workflows/ai-build-os.yml` khi repo có Git, trừ khi dùng `--no-ci`. Workflow mặc định chạy:

1. `python scripts/validate_ai_os.py --ci` — commit provenance + OS invariants trên mọi change set.
2. `python scripts/project_ci.py --ci` — product checks auto-detect trên mọi change set.
3. `python scripts/self_test.py` — chỉ khi Build OS core (`scripts/*.py`/templates) đổi, tránh cộng regression-suite latency vào PR app bình thường.

`--ci` tách khỏi local continuity. Local mode bind task với working-tree snapshot trước commit; CI mode bind **commit/PR application delta** với evidence mới của cùng change set. Mỗi evidence v1.8 lưu `task_delta_file_hashes`; CI chỉ chấp nhận current file fingerprint nếu nó khớp một verified hash trong evidence bundle mới/updated của change set và immutable history hash cũng khớp.

Điều này giải quyết commit boundary: `done → git commit → CI` không fail chỉ vì `STATE.HEAD` vẫn là pre-commit HEAD. Ngược lại, sửa source sau evidence rồi commit sẽ bị báo `CI unbound application change`.

### Trust boundary

Repo-local evidence/CLI is **A1**, not a security boundary against an actor with unrestricted repository write authority. v1.15 adds an external Guardian signature for triggered R2/R3 reviewer trust: the private key stays outside the repository; CI/validator verifies the bundled attestation with `AI_BUILD_OS_GUARDIAN_PUBLIC_KEY`.

**Required CI alone is not the same as protected merge.** `ai_os.py assurance` reports A3 only when a trusted runtime explicitly attests that required checks + branch/merge protection are configured. The managed GitHub workflow intentionally does not infer branch protection from the presence of a YAML file. Configure repository variable `AI_BUILD_OS_GUARDIAN_PUBLIC_KEY_PEM` for signature verification. Set `AI_BUILD_OS_GUARDIAN_EXTERNAL_ATTESTED=true` only when the signer/private-key authority is genuinely outside Worker control; a public key by itself remains A1. Set `AI_BUILD_OS_PROTECTED_MERGE_ATTESTED=true` only after branch protection/required checks are actually enforced. A4 additionally requires isolated Worker/reviewer authority.

## Product checks

`project_ci.py` detect các script/check chuẩn của Node, Python, Go và Rust. Đây là floor, không thay thế project-specific browser/E2E/release CI.

## Multi-agent

- một writer/worktree cho một active task;
- `begin --ready` cho lead tạo task rồi worker `claim`;
- `pause/resume` release/reclaim lease mà không đổi task baseline;
- reviewer/subagent read-only;
- scope được kiểm trên **task delta**, không phải toàn dirty state;
- `amend` là cách hợp lệ để mở adjacent scope;
- R3 review vẫn phải independent và snapshot-bound.

v1.13 retains machine-readable automatic delegation requests/plans but still does not embed a provider-specific model scheduler. The coding environment/orchestrator consumes `goal next --json.delegation` and `.ai/runtime/delegation_request.json`; task kernel + CI remain the integrity boundary.

## Goal dispatch

`goal next --json` là scheduling hint canonical. Scout/Reviewer read-only không chiếm writer slot. Nhiều Worker READY chỉ được chạy song song khi mỗi worker có isolated Git worktree/branch; một root vẫn giữ `ACTIVE_TASK` single-writer. Worker `done` tự sync node result về Goal DAG.
