# AGENTS — Hiến pháp vận hành

## 1. Thứ tự tối ưu

1. User outcome.
2. Functional correctness.
3. Data/security safety.
4. Maintainability.
5. Speed.
6. Cost efficiency.
7. Extensibility khi có nhu cầu thật.

## 2. Product before architecture

Mỗi milestone phải tạo `USER_VISIBLE_BEHAVIOR` hoặc `EXECUTABLE_CAPABILITY`. Foundation task phải nêu capability nó trực tiếp mở khóa.

## 3. Vertical slice

Mặc định xây `input → core behavior → persistence nếu cần → output quan sát được`. Không xây toàn bộ layer theo chiều ngang trước workflow xuyên suốt đầu tiên.

## 4. Task class theo risk

- R0/R1: Fast Lane/LEAN, ceremony tối thiểu nhưng giữ outcome, task-delta scope, side effect, focused verification, output/diff inspection và evidence. Negative path chỉ bắt buộc khi acceptance có failure behavior thật.
- R2: STANDARD, thêm affected integration, runtime smoke, cleanup và reversible path.
- R3: DEEP, explicit approval, rehearsal, rollback proof, critical E2E, broader suite và specialist review.

Không dùng profile nhẹ hơn risk gate. R3 luôn DEEP.
Risk tier là non-downgradable: runtime suy ra minimum floor từ side effect và changed surface; khai R0 không được phép bypass R2/R3 gates.

## 5. Một task, một outcome

Task phải có outcome, success criterion, Delivery Delta, allowed/prohibited scope, acceptance, verification, preflight, lease và timing. Stop-loss cố định áp dụng từ file này; R2/R3 có cost efficiency plan đầy đủ.

## 6. Single writer

Lease status: `UNCLAIMED`, `CLAIMED`, `RELEASED`. Lifecycle task hỗ trợ `READY`, `ACTIVE`, `PAUSED`, `COMPLETED`, `ABORTED`.

- Một active task chỉ có một writer.
- Reviewer/subagent read-only.
- Worker thay thế kiểm tra process và Git trước takeover.
- Không chạy hai Worker cùng worktree.
- Parallel work phải có task, branch và worktree riêng.
- `COMPLETED` bắt buộc lease `RELEASED` và evidence/report tồn tại.

## 7. Shipping circuit breaker

Delivery Delta: `USER_VISIBLE_BEHAVIOR`, `EXECUTABLE_CAPABILITY`, `RISK_RETIREMENT`, `DOCUMENTATION_ONLY`, `NO_DELTA`.

Counter chỉ reset bằng accepted behavior/capability. Ngưỡng lấy từ `Maximum consecutive non-shipping tasks` trong Project Contract. Khi breaker ACTIVE, `begin` non-shipping bị chặn trừ explicit override có lý do; `done/close` reject shipping delta giả khi application delta rỗng hoặc chỉ docs/tests.

## 8. Architecture budget

Mặc định modular monolith, một deployable, một primary database, ít dependency/process, interface tại boundary biến động thật. Không thêm framework, database, service, queue, event bus, plugin architecture hoặc abstraction nhiều tầng khi chưa có nhu cầu được chứng minh.

## 9. Retry và stop-loss

Một approach có initial attempt và một bounded correction. Sau lần thứ hai không đạt: smallest reproduction, root-cause review, split task hoặc đổi strategy. Không stacking heuristic vô hạn.

## 10. Output thật phủ quyết PASS

Với UI, media, document hoặc generated artifact: mở/xem/nghe output khi có thể, dùng fixture đại diện và ghi provenance. Unit test PASS không phủ quyết lỗi sản phẩm.

## 11. Data, artifact và provider operation

Side effect thuộc `READ_ONLY`, `CREATE_NEW_VERSION`, `MUTATE_IN_PLACE`, `OVERWRITE`, `DELETE`. Mặc định read-only hoặc create-new-version. Mutation/delete/overwrite cần quyền rõ ràng và authorization reference có thể truy vết. Không silent fallback từ reuse→regenerate, read→write, preview→production, local→provider hoặc test→canonical data.

## 12. Deterministic lifecycle

Ưu tiên `scripts/ai_os.py` cho `begin/claim/pause/resume/amend/abort/done/check`. Không bypass validator bằng chỉnh tay checkpoint. `begin` auto-claim mặc định; dùng `--ready` khi cần tách authorize/claim. Capsule là compact worker packet generated theo lifecycle event.

## 13. Definition of Done

Outcome quan sát được; acceptance có evidence; negative path được kiểm tra khi thuộc acceptance; task delta đúng scope; output thật được kiểm tra; side effect đúng preflight; process/temp cleanup; STATE compact; cost signal ghi nhận; lease release; next exact action rõ.
