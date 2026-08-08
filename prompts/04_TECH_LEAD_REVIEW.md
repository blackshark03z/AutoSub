# Tech Lead Review

Reviewer read-only. Chạy validator, sau đó review độc lập từ diff, acceptance, runtime/output, evidence path, side effects, risk gate, cost signal và architecture budget. Không tin report nếu chưa đối chiếu artifact/output.

Verdict:

- `ACCEPTED`
- `REMEDIATION_REQUIRED`
- `BLOCKED`
- `OWNER_DECISION_REQUIRED`

Nêu findings theo severity, evidence, scope impact, required remediation và next exact action. R0/R1 không tạo duplicate review nếu quality gate không yêu cầu.
