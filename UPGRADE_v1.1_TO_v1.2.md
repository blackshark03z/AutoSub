# Upgrade v1.1 → v1.2

## Backup

Commit hoặc copy `.ai/` trước khi nâng cấp. Không overwrite state đang có writer sống.

## Files nên thay thế

- `scripts/validate_ai_os.py`
- `scripts/refresh_context_capsule.py`
- `scripts/append_cost_ledger.py`
- thêm `scripts/ai_os.py` và `scripts/self_test.py`
- task/capsule/evidence/report templates
- quality/cost/continuity docs

## Cost ledger migration

v1.2 dùng schema mới. Giữ ledger v1.1 làm archive, sau đó tạo header v1.2 hoặc map dữ liệu cũ:

- `task_revision`: mặc định `1`;
- `risk_tier`: lấy từ task cũ;
- lifecycle timestamps/cycle/first-pass/human-wait/escaped-defect: để trống hoặc `unknown`;
- token cũ có thể map vào implementation tokens nếu không tách được coordination;
- `currency`: ghi rõ `USD`, `VND` hoặc currency thực tế.

Không tự suy diễn timestamp/cost không có evidence.

## Active task migration

Nếu đang có task sống:

1. Pause writer và ghi checkpoint.
2. Thêm `Relevant Decisions`, `Lifecycle Timing` và completion fields mới.
3. Đối chiếu Project ID, branch, HEAD, State Revision và task ID giữa State/Task/Capsule.
4. Refresh capsule bằng script mới.
5. Chạy `python scripts/ai_os.py check`.
6. Chỉ resume khi validator PASS.

## Expected breaking changes

- R3 + LEAN/STANDARD bị từ chối.
- COMPLETED nhưng lease chưa RELEASED hoặc evidence path không tồn tại bị từ chối.
- Fingerprint chéo không nhất quán bị từ chối.
- Duplicate `(task_id, task_revision)` trong ledger bị từ chối.
- Capsule không còn carry-forward decision từ capsule cũ.
