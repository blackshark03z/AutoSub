# Cost–Quality Learning Loop

`COST_LEDGER.csv` ghi cycle time, first runnable, first-pass acceptance, token/cost khi có, số gate và thời gian review.

Các field `later_rework` và `escaped_defect` thường chưa biết lúc close. Dùng `reconcile` sau vận hành để cập nhật mà không sửa evidence.

`report` tổng hợp:

- accepted outcomes
- median cycle time
- first-pass acceptance
- later rework
- escaped defects
- recorded AI/provider cost

Khuyến nghị chỉ xuất hiện khi dữ liệu đủ hoặc có anomaly rõ; không tự động hạ safety/acceptance gates.
