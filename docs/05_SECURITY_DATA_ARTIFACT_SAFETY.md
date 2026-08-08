# Security, Data and Artifact Safety

Operation classes: `READ_ONLY`, `CREATE_NEW_VERSION`, `MUTATE_IN_PLACE`, `DELETE`. Preflight manifest phải liệt kê input/output, file tạo/ghi đè, data mutation, provider call, disk/RAM/process, lineage và rollback.

Không silent fallback. Redact secrets khỏi prompt, logs và evidence. Với mutation dùng clone/backup và chứng minh rollback. Migration theo `expand → migrate → switch → contract`, không contract trước verification. Active artifact reference phải chỉ version được chọn; không tự chọn `latest`.
