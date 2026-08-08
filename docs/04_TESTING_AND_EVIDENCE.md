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
