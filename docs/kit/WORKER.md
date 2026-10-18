# Coding worker adapter — Unified AI Tech Lead Standard v1.0

**Canonical policy:** `AGENTS.md` at the Owner-accepted `POLICY_REF`. This adapter applies to Codex, OpenCode, DeepSeek and successor workers; it is not a competing policy and does not install Codex-only skills.

## Mandatory preflight before source edits
1. Obtain exact repository, worktree, branch, baseline HEAD and POLICY_REF in the assigned task. Inspect live Git and dirty files. If the task omits POLICY_REF or the accepted Product SoT pointer, stop and request a corrected *single* brief through the orchestrator.
2. Verify POLICY_REF in Git and compare `AGENTS.md` and the applicable worker adapter to their contents at POLICY_REF; read the accepted Product SoT. Do not treat the branch-local AGENTS.md, chat history or another worker's report as new authority.
3. If policies differ, repo is ambiguous, or the baseline is unexpectedly dirty, **stop before implementation** and report exact mismatched path/ref/hash. Do not reset/merge/cherry-pick/copy files over another worktree to fix it.
4. Within a clear scoped task, reuse existing code, make minimal critical-path changes, keep own artifacts and commit boundaries isolated and obey authorized network/time/disk/expense limits.

## Proof / stop conditions
- Focused checks during work; required protected/full checks at the accepted checkpoint/candidate. A green test suite alone is not Product PASS.
- Preserve artifact and source identities, report real source/config hashes, implemented diff and commands actually run. Distinguish TECHNICAL/PRODUCT/EDITORIAL/SECURITY/PUBLICATION statuses where relevant.
- On completion state **Product Delta**, first remaining blocker and COMPLETE_VERIFIED/PARTIAL_DELIVERY/BLOCKED. No evidence means no PASS. Two no-delta cycles require escalation/rethink rather than another speculative task.
- No unauthorized external provider/media uploads, secret retrieval, destructive cleanup, merge/push/deploy or public release.

If the exact native Workflow Kit skill is not installed in this worker environment, report that fact and follow the repo policy without pretending the skill was invoked.
