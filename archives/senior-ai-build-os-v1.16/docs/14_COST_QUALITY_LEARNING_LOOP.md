# Cost–Quality Learning Loop — v1.13

North star không phải token thấp nhất hay task PASS nhiều nhất. Tối ưu:

```text
accepted Goal throughput / (AI cost + cycle time + human attention + expected rework/defect loss)
```

## Risk-proportional assurance

- R0/R1: cheapest focused evidence, negative path khi meaningful; không reviewer mặc định.
- R2: frozen Goal acceptance contract + focused/negative/integration. Reviewer chỉ khi trigger hoặc policy predeclared `required`.
- R3: critical gates + independent review.

Đừng bật gate mới cho Fast Lane nếu dữ liệu không cho thấy defect/rework concentration.

## What `report` should teach you

`report` tổng hợp accepted task outcomes, rồi phân đoạn theo risk và actual changed surface từ evidence manifest. Goal result thêm:

- Goal first-pass acceptance
- Goal cycle minutes
- aggregated AI/provider cost
- human review/wait minutes
- coordination/implementation/output tokens khi có telemetry
- cost / accepted Goal

`later_rework` và `escaped_defect` vẫn phải reconcile khi biết kết quả vận hành. Nếu phần lớn còn `unknown`, đừng dùng ledger để tự động nâng/hạ policy.

## Tuning loop

1. Chạy đủ sample thực tế (thường vài chục Goal, không phải 2–3 task).
2. Tìm risk/surface có rework hoặc escaped defect cao.
3. Nâng rigor cục bộ: risk surface map, sensitive term, negative path, frozen acceptance hoặc R2 reviewer trigger.
4. Nơi first-pass cao, defect thấp và cycle tốt: giữ lean.
5. Đo lại.

Spec investment cũng risk-proportional: viết cho tới khi acceptance **falsifiable**, rồi dừng; không có fixed “10 phút spec/task”.
