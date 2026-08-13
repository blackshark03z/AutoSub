# Codebase Health Ratchet

Mục tiêu là giữ codebase sống khỏe sau nhiều Goal mà không biến mọi task thành refactor review.

## Nguyên tắc

- **Ratchet, not perfection:** health baseline grandfather technical debt có sẵn; protected CI chỉ fail violation mới.
- **Hard:** forbidden tracked cache/log/build artifacts, oversized new binary, configured architecture-boundary violation.
- **Conditional hard:** runtime dependency tăng phải có structured decision trong task acceptance: capability mua được, alternatives đã cân nhắc, removal/exit cost.
- **Ratchet warning:** largest-file growth, total LOC growth, file-count/dependency growth và generated archive growth.
- **Hotspot first:** ưu tiên refactor nơi touch nhiều + first-pass/rework/defect cao; file lớn nhưng ổn định có thể để yên.

## Commands

```bash
python scripts/ai_os.py health baseline
python scripts/ai_os.py health check
python scripts/ai_os.py health report
```

`init` tự tạo baseline. FAST/STANDARD chỉ chịu cheap delta guard. Goal completion lưu health delta vào Goal result và cập nhật baseline sau acceptance.

Cấu hình machine-readable nằm ở `config/codebase_health.json`. `architecture_boundaries` mặc định rỗng vì dependency direction phải phản ánh kiến trúc project thật, không được tool đoán bừa. Từ v1.14, executable project phải đưa ra **explicit architecture decision** trước protected CI: cấu hình boundaries hoặc ghi một lý do no-boundaries đủ cụ thể bằng `health architecture-decision`.

## Bloat and maintainability signals

Health snapshots also track product-tree MB, Git object-store MB, source LOC/files and largest-file trend. Large/growing files and net LOC growth are ratchet warnings, not automatic refactor mandates. Runtime dependency growth is evaluated against the **current task-start baseline**, so a dependency justified by one Goal node is not charged again to later nodes.

The scanner prunes ignored heavy directories before descent, keeping health checks practical on repositories with large dependency caches.


## v1.14 hard ratchets

New source files above the configurable hard LOC ceiling are rejected. Existing large files are grandfathered for small fixes, but a single task cannot grow them beyond the configured growth budget; high-pain hotspots use a tighter budget. The check is scoped to the active task baseline, so unrelated legacy monsters do not block delivery.
