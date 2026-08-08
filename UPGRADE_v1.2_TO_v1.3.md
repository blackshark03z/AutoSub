# Upgrade v1.2 → v1.3

## Thay đổi bắt buộc

1. Copy `scripts/runtime_support.py` và `.ai/.gitignore`.
2. Thay `scripts/ai_os.py`, `validate_ai_os.py`, `refresh_context_capsule.py`, `append_cost_ledger.py` và `self_test.py` bằng bản v1.3.
3. Đổi field `GPT-5.6 Profile` thành `Execution Profile` trong active task/template/capsule.
4. Thêm `Authorization Reference` ngay sau `Owner Authorization`.
5. Đổi worker prompt sang `prompts/03_WORKER.md`.
6. Cập nhật evidence theo schema `templates/EVIDENCE_INDEX.md`.

## Active task đang chạy

Không migrate giữa task nếu task đang ACTIVE. Close hoặc pause task bằng v1.2, backup `.ai/`, nâng cấp package, sau đó start task mới bằng v1.3.

## Git behavior

v1.3 loại trừ `.ai/**` khỏi worktree fingerprint. Không truyền `--worktree DIRTY` chỉ để bù cho checkpoint do lifecycle tự ghi. Application changes ngoài `.ai/` vẫn được tính bình thường.

## Cost ledger

Header không đổi. Các metric chưa biết có thể để rỗng. Không chuyển ô rỗng cũ thành 0 trừ khi số 0 thực sự đã được đo.

## Validation

```bash
python scripts/validate_ai_os.py --template
python scripts/self_test.py
```
