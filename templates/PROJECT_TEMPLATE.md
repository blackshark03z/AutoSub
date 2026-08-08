# Project Contract

Updated: YYYY-MM-DD
Project ID: UNSET
Owner: UNSET
Project Status: DRAFT

## Product Problem

[Điền vấn đề người dùng thật sự cần giải quyết.]

## Target User

[Điền user chính.]

## Primary User Workflow

1. [Input/action]
2. [Core behavior]
3. [Observable result]

## MVP Goal

[Outcome nhỏ nhất có giá trị.]

## Success Criteria

### SC-001
- User: UNSET
- Action: UNSET
- Observable result: UNSET
- Acceptance threshold: UNSET
- Demo method: UNSET

## In Scope

- UNSET

## Out of Scope / Later

- UNSET

## Supported Cases

- UNSET

## Explicitly Unsupported Cases

- UNSET

## Current Milestone

- Milestone ID: UNSET
- User outcome: UNSET
- Success Criterion: SC-001
- Demonstrable outcome: UNSET
- Time-to-first-demo expectation: UNSET
- Maximum consecutive non-shipping tasks: 3

## Technical Baseline

- Language/runtime: UNSET
- Framework: UNSET
- Database: UNSET
- Package manager: UNSET
- Entry point: UNSET
- Run command: UNSET
- Test command: UNSET
- Build command: UNSET
- Important directories: UNSET

## Risk Surface Map

Optional project-specific floors used by acceptance-time actual-delta reconciliation.

- R2 paths: NONE
- R3 paths: NONE
- Sensitive business terms: NONE

## Architecture Budget

- Default architecture: modular monolith
- New deployables without owner approval: 0
- New databases without owner approval: 0
- New framework requires owner approval: yes
- New production dependency requires justification: yes
- Abstraction requires real variation or tested boundary: yes

## Quality Priorities

1. Functional acceptance.
2. Data/security safety.
3. Evidence phù hợp risk tier.
4. Maintainability đủ cho milestone kế tiếp.

## Owner Authorization Policy

### Pre-authorized

- Read-only inspection.
- Local focused tests dùng fixture không canonical.
- Create-new-version artifacts trong phạm vi task đã duyệt.

### Explicit approval required

- MVP/scope change
- production mutation
- migration
- delete/overwrite
- secrets
- paid provider above approved amount
- deploy/publish
- push/merge
- architecture change

## Data and Artifact Policy

- Canonical data: UNSET
- Test/clone data: UNSET
- Default operation: READ_ONLY
- In-place mutation: explicit approval required
- Delete: explicit approval required
- Regeneration overwrite: prohibited unless explicitly authorized
- Backup/rollback: UNSET

## Constraints

- Time: UNSET
- Financial: UNSET
- Privacy: UNSET
- Platform: UNSET
- Licensing: UNSET
- External services: UNSET

## Scope Guard

Không tự mở rộng MVP vì một pathological fixture. Owner quyết định scope; manual correction có thể là product behavior hợp lệ.
