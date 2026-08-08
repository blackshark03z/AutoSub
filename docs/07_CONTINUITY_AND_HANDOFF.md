# Continuity and Handoff

Continuity fingerprint gồm Project ID, branch, HEAD, worktree, State Revision, Active Task ID và runtime/data fingerprint.

## Canonical và generated state

- PROJECT/DECISIONS: product authority.
- ACTIVE_TASK: execution authority.
- STATE: compact current snapshot.
- CONTEXT_CAPSULE: generated worker context, không phải canonical source.

Capsule chỉ refresh khi start, scope/decision change, first runnable, block/pause, handoff hoặc close. Không lấy decision ID từ capsule cũ khi task mới không tham chiếu.

## Takeover

Kiểm tra Git, process, artifact/data và lease trước. Không destructive Git để “làm sạch” mismatch. Worker mới chỉ claim sau khi writer cũ không còn sống hoặc có handoff rõ.

Validator phải chặn fingerprint mâu thuẫn giữa State/Task/Capsule và, khi có Git repository, đối chiếu branch/HEAD/worktree thật.
