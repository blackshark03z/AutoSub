# Start Here — v1.8

## Authority

```text
runtime/data
> Git application snapshot + task-start baseline
> immutable evidence manifest
> PROJECT / ACTIVE_TASK / STATE
> chat history
```

Worker chỉ sửa application code khi task `ACTIVE`, lease `CLAIMED`, identity `VERIFIED`.

## New project

Chạy `init`, rồi `check --strict`. Init v1.8 chỉ tạo SC-001 thật sự và auto-detect technical baseline khi repo có marker phổ biến.

## New task

`begin` mặc định:

- `--risk auto`;
- auto-claim writer lease;
- capture fingerprint của pre-existing dirty files;
- generate compact worker packet.

Dùng `--ready` nếu cần tách authorize và claim.

## Verification lanes

- **R0:** focused check + output/task-delta inspection phù hợp task.
- **R1:** như R0; thêm negative chỉ khi `--negative-required`/failure behavior thật.
- **R2:** focused + negative + affected integration/runtime.
- **R3:** R2 gates + rollback rehearsal + full suite/critical check + approval + independent snapshot-bound review.

Risk floor không thể hạ bằng cách khai `R0` thủ công. `done/close` còn reconcile lại actual task delta; nếu actual floor cao hơn, phải amend/escalate trước acceptance.

## Scope discovery

Nếu root cause cần thêm file, dùng `amend --add-modify/--add-create --reason ...`. Không widen scope im lặng và không restart task chỉ vì một adjacent file.

## Runnable

`runnable` là metric tùy chọn cho user-visible/executable tasks; không phải ceremony bắt buộc trước `done`.

## Done

`done` reconcile actual risk + Delivery Delta, chạy verification, kiểm **task delta** (không phải toàn dirty worktree), tạo evidence và release lease.

Sau close: `status`/`next`, định kỳ `report`, và `reconcile` khi có operational feedback.

## Shipping breaker

`status`/capsule/`next` luôn surface breaker. Khi ACTIVE, `begin` non-shipping bị chặn trừ explicit override có lý do; shipping delta giả với empty/docs/tests-only task delta bị reject lúc close.
