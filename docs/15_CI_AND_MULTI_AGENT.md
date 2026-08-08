# CI and Multi-Agent Operation — v1.8

## Commit-aware CI

`ai_os.py init` tự cài `.github/workflows/ai-build-os.yml` khi repo có Git, trừ khi dùng `--no-ci`. Workflow mặc định chạy:

1. `python scripts/validate_ai_os.py --ci` — commit provenance + OS invariants trên mọi change set.
2. `python scripts/project_ci.py --ci` — product checks auto-detect trên mọi change set.
3. `python scripts/self_test.py` — chỉ khi Build OS core (`scripts/*.py`/templates) đổi, tránh cộng regression-suite latency vào PR app bình thường.

`--ci` tách khỏi local continuity. Local mode bind task với working-tree snapshot trước commit; CI mode bind **commit/PR application delta** với evidence mới của cùng change set. Mỗi evidence v1.8 lưu `task_delta_file_hashes`; CI chỉ chấp nhận current file fingerprint nếu nó khớp một verified hash trong evidence bundle mới/updated của change set và immutable history hash cũng khớp.

Điều này giải quyết commit boundary: `done → git commit → CI` không fail chỉ vì `STATE.HEAD` vẫn là pre-commit HEAD. Ngược lại, sửa source sau evidence rồi commit sẽ bị báo `CI unbound application change`.

### Trust boundary

Đây không phải cryptographic attestation. Actor có quyền sửa toàn bộ repository vẫn có thể giả evidence/history nếu không có external signer. Strong provenance cần protected branch/required CI và, nếu threat model yêu cầu, một external trusted review/signing job.

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

v1.8 không kèm scheduler; nó cung cấp lifecycle + integrity boundary cho orchestrator ngoài.
