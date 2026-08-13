# Evidence Store — v1.16

Mỗi accepted task tạo revision bất biến. Manifest bind Task ID, revision, success criterion, verified snapshot, task delta, command results và verdict. R0/R1 dùng COMPACT evidence; R2/R3 dùng FULL. R1 chỉ cần negative khi task đánh dấu failure behavior; R2 cần negative + integration; R3 thêm full-suite/critical gate và independent review.

State Hazard là risk-triggered: S0/S1 không thêm proof gate; S2 cần representative transition proof; S3+ thêm temporal/competing-writer proof. Passing state proofs có thể được reuse qua `.ai/evidence/_state_cache/` khi contract, exact command và dependency fingerprint đều không đổi. Cache entry chỉ là index; source manifest/hash vẫn là bằng chứng gốc.

Evidence output được redact cho common bearer/basic credentials, cookies, JWT, private keys, API tokens và credential-bearing URLs. Không cố ý lưu secrets hoặc raw output vượt mức cần thiết.
