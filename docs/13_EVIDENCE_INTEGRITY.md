# Evidence Integrity — v1.8

Evidence là immutable acceptance record, với mức lưu trữ tỷ lệ theo risk.

## Snapshot + task delta

Manifest lưu verified application snapshot, `task_delta_files` và `task_delta_file_hashes`. Fingerprint là `FILE:<sha256>`, `SYMLINK:<sha256>` hoặc `MISSING` cho deletion. Local close từ chối nếu application snapshot đổi sau verification hoặc task delta vượt authorized scope. CI dùng các per-file fingerprint này để bind committed content với verified evidence.

## COMPACT vs FULL

- **R0/R1 — COMPACT:** cùng manifest/hash semantics, stdout/stderr cap 32 KiB/stream.
- **R2/R3 — FULL:** log cap 256 KiB/stream cho integration/critical evidence.

Artifact chỉ được copy khi caller chỉ định. Accepted bundle/revision không bị overwrite.

## Inspection và output assertion

R1+ yêu cầu `--output-inspected-by agent:<id>|human:<id>`; đây vẫn là declaration, không phải cryptographic proof. `--expected-output "marker"` là optional semantic assertion: marker phải thật sự tồn tại trong stdout/stderr được lưu. Nó tăng chi phí fake PASS nhưng không được diễn giải là bằng chứng reviewer đã đọc output.

## Commit provenance

`validate_ai_os.py --ci` chỉ dùng evidence manifest nằm trong Git change set hiện tại, verify bundle/history hash, rồi match current application file fingerprint với `task_delta_file_hashes`. Vì vậy post-evidence source drift không thể merge qua required CI check nếu evidence không được cập nhật/reverified.

## R3

Review phải độc lập với writer theo declaration, khớp task/revision/verified snapshot, có ISO timestamp và PASS/ACCEPTED verdict. Strong reviewer identity vẫn cần trust boundary ngoài local repo nếu threat model yêu cầu.
