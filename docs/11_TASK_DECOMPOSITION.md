# Task Decomposition

## Fast Lane R0/R1

Dùng một task cô lập khi outcome, scope và rollback rõ. Không tách diagnosis/review thành ceremony riêng nếu focused reproduction và fix có thể hoàn tất an toàn trong cùng bounded task.

## R2/R3 hoặc root cause mơ hồ

```text
DIAGNOSE
→ REPAIR
→ ACCEPT / RELEASE
```

DIAGNOSE: reproduce, root cause, không package/full suite sớm. REPAIR: xử lý một nguyên nhân, focused test và runtime smoke. ACCEPT/RELEASE: fresh verification, broader suite theo gate, provider-backed canary một lần, package/activation một lần hoặc `BLOCKED`.

Mỗi task vẫn có một outcome và quyền riêng. Không gộp diagnosis mơ hồ với production activation.

## Circuit breaker

Foundation/risk-retirement task phải nêu capability nó mở khóa. Sau ba task liên tiếp không có user-visible behavior hoặc executable capability, dừng để thu nhỏ milestone hoặc chọn task shipping tiếp theo.
