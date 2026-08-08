# Upgrade v1.3 → v1.5

1. Backup `.ai/`, commit hoặc lưu application diff.
2. Thay `scripts/`, `templates/`, `docs/` và root guides bằng v1.5.
3. Giữ `.ai/PROJECT.md`, `.ai/DECISIONS.md` và ledger hiện tại.
4. Thêm các field mới vào `ACTIVE_TASK.md` nếu đang có task live:
   - Task Revision
   - Starting/Verified Snapshot SHA256
   - First Runnable Evidence
   - Evidence Bundle
5. Không chuyển evidence v1.3 thành accepted v1.5 bằng cách đổi tên. Đóng task cũ hoặc tạo revision mới và chạy lại verification.
6. Chạy:

```bash
python scripts/ai_os.py doctor
python scripts/ai_os.py check
python scripts/self_test.py
```

Evidence mới dùng `.ai/evidence/TASK-ID/rNNN/`. Ledger revision được cấp tự động bởi `begin`.
