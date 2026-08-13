# Goal Acceptance — v1.16

Dùng fresh context khi Goal đủ lớn/R2+.

1. Đọc `.ai/GOAL_STATE.json` + Goal acceptance, không reread toàn bộ worker transcripts.
2. Xác minh tất cả node bắt buộc DONE/DEFERRED có lý do hợp lệ.
3. Chỉ chạy/confirm evaluator trong frozen Goal Acceptance Contract. Không tạo command mới sau khi đã thấy implementation. Behavior-level/browser/E2E nên được bind từ trước khi phù hợp.
4. Inspect output/runtime thật và known limits.
5. Không sửa code trong acceptance pass; nếu fail, Goal vẫn ACTIVE và tạo node sửa riêng.
6. `goal done` chỉ khi frozen contract PASS; inspection-only criterion phải được confirm rõ và được ghi là declared inspection.
