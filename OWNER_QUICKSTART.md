# Owner Quickstart — v1.8

1. `ai_os.py init ...` với user/problem/action/result/MVP. Trong Git repo, init tự cài `.github/workflows/ai-build-os.yml` (opt out bằng `--no-ci`).
2. Chọn vertical slice nhỏ nhất tạo behavior/capability.
3. `begin` với `--modify/--create` hẹp; risk + claim mặc định tự động.
4. Nếu phát hiện thêm adjacent file, dùng `amend` thay vì khai scope rộng từ đầu.
5. Worker patch → focused verify → inspect output + task delta → `done`; v1.8 reconcile actual risk/delivery delta trước acceptance.
6. Nếu Shipping Circuit Breaker ACTIVE, task kế tiếp phải ship behavior/capability hoặc có explicit override có lý do.
7. Nếu cùng task fail first-pass 2 revision liên tiếp, revision kế tiếp cần `--stop-loss-ack` mô tả root-cause hypothesis mới.
8. Chỉ dùng `runnable` khi first-runnable là metric hữu ích.

Owner approval vẫn bắt buộc cho R3, production mutation, migration/deploy, delete/overwrite/in-place mutation và các thao tác nhạy cảm tương đương. Risk auto-floor có thể nâng task lên R3 nhưng không tự cấp approval.

### Health

```bash
python scripts/ai_os.py check --strict
python scripts/ai_os.py doctor
python scripts/validate_ai_os.py --ci   # trong clean CI checkout / commit boundary
python scripts/self_test.py
python scripts/project_ci.py --list
```

### R3 review

Dùng `templates/REVIEW_REPORT.md`; reviewer identity phải khác writer và report phải bind đúng `Task ID / revision / Reviewed snapshot SHA256`.
