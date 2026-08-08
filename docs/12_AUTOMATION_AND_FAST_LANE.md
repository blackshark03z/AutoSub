# Automation and Fast Lane — v1.8

## Default path

```text
init once → begin(auto risk + auto claim) → patch → focused verify → inspect → done
```

R0/R1 không yêu cầu integration/full-suite. Negative-path ở R1 chỉ bắt buộc khi acceptance thật sự có failure behavior (`--negative-required`).

## Task-delta scope

`begin` capture pre-existing dirty fingerprints. Scope validator chỉ xét file thay đổi so với baseline task. Dirty worktree cũ được phép tồn tại nhưng nếu worker sửa tiếp chúng, chúng trở thành task delta và phải nằm trong scope.

## Cross-revision stop-loss

Nếu cùng `task_id` có hai accepted revision liên tiếp được ghi `first_pass_accepted=no`, `begin` revision tiếp theo fail closed trừ khi có:

```bash
--stop-loss-ack "root cause hypothesis changed from X to Y"
```

Gate chỉ kích hoạt ở vòng lặp thật nên không thêm ceremony cho task bình thường. In-revision retry vẫn dựa vào worker discipline; enforce từng attempt sẽ cần verification wrapper/hook và cố ý chưa thêm vào core.

## Amend instead of restart

`amend` thêm `Modify/Create` hoặc side-effect/risk metadata có reason. Risk chỉ giữ nguyên hoặc tăng; escalation lên R3 đòi owner authorization.

## Shipping breaker telemetry

Breaker override vẫn cần reason. v1.8 lưu override vào immutable history; `report` in số lần/lý do và cảnh báo nếu override >20% recent accepted outcomes.

## Command execution

Verification dùng argv (`shell=False`) mặc định. Chỉ bật `--allow-shell-command` khi cần syntax shell. `--expected-output` có thể dùng để assert một marker cụ thể trong log mà không thêm lifecycle round-trip.
