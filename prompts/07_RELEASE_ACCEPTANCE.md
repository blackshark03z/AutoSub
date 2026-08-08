# Release Acceptance

Dùng fresh session và mode `RELEASE_ACCEPTANCE`.

1. Chạy invariant check và xác minh Git/runtime/data fingerprint.
2. Disk/RAM/process preflight.
3. Artifact/data protection và rollback proof.
4. Identity/auth.
5. Focused critical checks.
6. Broader/full suite theo R3 gate.
7. Provider-backed canary một lần nếu được phép.
8. Package một lần.
9. Install/start/stop/cleanup.
10. Activate hoặc `BLOCKED`.
11. Close lifecycle chỉ sau final runtime evidence.

Không lặp operation đắt nếu chưa có relevant change.
