# Testing and Evidence

Evidence hierarchy:

```text
runtime/demo
> integration/E2E
> focused unit
> static check
> written claim
```

Bug workflow:

```text
smallest reproduction
→ regression fixture
→ root-cause fix
→ focused verification
→ inspect final output
```

Test rẻ trước, broader suite tại đúng gate. Không rerun full suite nếu code/config/input liên quan không đổi.

Evidence close phải ghi:

- Task ID, success criterion và accepted outcome khớp task;
- timestamp không trước task start;
- command/path, kind, exit code, verdict và output hash;
- focused/negative/integration/full-suite kinds phù hợp risk;
- artifact path và SHA256 khi có output file;
- side effect, cleanup và rollback status.

`done` tự sinh schema này. Evidence viết tay phải theo `templates/EVIDENCE_INDEX.md`. Output sai phủ quyết PASS dù command exit code 0.


## v1.10 builder/judge separation

For Goal-linked R2/R3 work, the acceptance command must be declared on the Goal node before the Worker starts. `goal start` freezes the contract; `done` automatically runs it. Optional `--probe-file` hashes are checked before verification, so the Worker cannot silently rewrite the acceptance probe used as the judge.

R0/R1 stay self-verified. R2 reviewer is trigger-based rather than universal; R3 independent review remains mandatory.
