# Cost Efficiency and Model Use

Tối ưu `TOTAL COST PER ACCEPTED OUTCOME`, không tối ưu giá từng lượt và không đặt hard token cap.

## Profile theo risk

- R0/R1: LEAN mặc định; STANDARD chỉ khi uncertainty/coupling tăng.
- R2: STANDARD mặc định; DEEP khi root cause mơ hồ hoặc shared critical core.
- R3: DEEP bắt buộc.

Không dùng DEEP cho format, Git summary, rerun test, Markdown, log extraction hoặc report generation.

## Coordination tax

Đo riêng token/context dùng cho brief, policy, state, review và handoff so với token dùng cho implementation/debug. Fast Lane tồn tại để giảm coordination ratio của task nhỏ mà không bỏ quality floor.

KPI:

- `coordination_input_tokens / total_input_tokens`;
- time to first runnable;
- cycle minutes theo risk tier;
- first-pass acceptance;
- retry và rework;
- human wait/review;
- cost per accepted capability;
- escaped defect và rollback.

## Continue và stop-loss

Tiếp tục khi phần chi tiếp theo có đường rõ ràng tới evidence mới, giảm uncertainty, accepted functionality hoặc safety verification bắt buộc.

Dừng/split/đổi strategy khi cùng approach thất bại hai lần, test/provider/context/review lặp không có relevant change, scope creep hoặc hai work cycle không tạo evidence mới.

Expected cost range là anomaly detector. Không hạ acceptance, security, data/artifact safety, rollback, cleanup hoặc risk gate để “đạt ngân sách”.
