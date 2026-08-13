# Failure Modes

- PASS nhưng product sai: inspect output thật.
- Endless architecture: quay lại smallest runnable slice.
- Heuristic pile-up: sau hai failure, root-cause/split.
- Worker treo: inspect PID/log/Git trước rerun.
- Multiple writer: dừng writes, phân giải lease.
- State drift: runtime/Git phủ quyết checkpoint.
- Context inflation: refresh capsule và chỉ đọc relevant files.
- Cheap execution thành đắt vì retry: đo total outcome cost.
- Release ceremony quá sớm: giữ development loop nhẹ.
- Provider call lặp: thay input/config hoặc dừng.
- Sunk-cost fallacy: đánh giá marginal value, không bảo vệ effort đã chi.
