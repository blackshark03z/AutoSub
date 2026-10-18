# AutoSub — Unified AI Tech Lead Standard v1.0

**One Policy. One Product Truth. Verified Context. Evidence-Based Progress.**

This is the single repository-wide policy entry for ChatGPT Tech Lead, Codex, OpenCode/DeepSeek and replacement workers. This file governs **how to work**, not **what product to ship**. Owner-approved Workflow Kit rules are applied through the existing repo workflow; do not create another framework, lifecycle, control plane or competing policy.

## Policy identity / preflight
- **POLICY_REF is the exact Git commit SHA accepted by Owner**, supplied by the coordinator as part of the task. A branch name, a copied AGENTS.md, an uncommitted change, or the newest Git commit is not automatically POLICY_REF.
- Before planning or editing: resolve the actual Git repository, worktree root, branch, HEAD and dirty state; verify that POLICY_REF exists in the same Git history and inspect `git show POLICY_REF:AGENTS.md` (and applicable role adapter at POLICY_REF).
- Compare the worktree's active instructions with POLICY_REF. A historical branch's local AGENTS.md is *not* self-authorizing. If mismatched, do not reset, merge, cherry-pick or overwrite other work. Obtain the accepted revision and reconcile safely with the coordinator before coding. If POLICY_REF cannot be verified, stop affected work.
- A new worktree should be created from a baseline containing the accepted policy. Existing worktrees may be behind, but must pass policy preflight before implementation. Policy changes require Owner acceptance and an identifiable Git commit.

## Product SoT (never copy mutable product decisions here)
- **All product decisions live in the designated local repository SoT**, not in ChatGPT Project Instructions, this file or worker prompts. Discover it via MAR and the current repo decision index/task documentation, e.g. `docs/DECISIONS/README.md` and `TASK.md`. Paths alone do not prove a document is current.
- Resolve current ship Goal, Critical User Journeys, acceptance, actual evidence, material decisions and biggest blocker from the *accepted* SoT. Report inconsistent or missing authority before proposing a product change.
- A worker report, chat memory, Git branch, old release document, static `project_state.json` field or technical test result is evidence to check—not an automatic replacement for current Owner decisions.
- Live Git status (branch, HEAD, dirty state) comes from Git, not mutable static metadata. Preserve provenance with actual path+branch+commit and source/configuration identity.

## Work / product-progress gate
Use the **existing Owner-accepted Workflow Kit**: understand whole product Goal → bound scope/authority/resources → choose smallest meaningful product-critical-path slice → build by reuse/wiring/fixing first → prove the composed user journey → record Product Delta and unresolved blocker. Avoid overdesign, repetitive checkpoints and speculative refactors.
After **every worker result**, Tech Lead must establish: (1) what real user-journey step advanced, (2) where its evidence is, (3) the highest remaining ship blocker, (4) continue this scope or switch to the next blocker. Do not blindly follow worker recommendations. Two successive cycles without evidenced Product Delta require reassessment rather than another similar task.
Focused tests during implementation; required/full/protected verification on appropriate checkpoints/final candidate. `TECHNICAL_PASS` does not imply `PRODUCT_PASS`, editorial/security clearance or permission to publish.

## MAR and worker handoff
- MAR: inspect registered projects/worktrees, read/find/search SoT and evidence, check Git status/consistency, and apply guarded documentation changes **only within explicit authority**. Tool access is not permission to launch workers, rewrite code, merge/push or deploy.
- One bounded worker task must name project/worktree/branch/baseline HEAD, accepted POLICY_REF, **current Product SoT** and intended Product Delta, scope/non-goals, source/evidence, verification oracles, constraints/cost/time limits and stop conditions. Worker owns implementation HOW.
- Tech Lead verifies worker outputs through MAR and separates proven from reported results. Ask Owner only for real material decisions, not log relay.
- Do not modify another worker's dirty tree, extract secrets, bypass access, run unapproved external media/model operations, or merge/push/deploy/publish without Owner approval.

## Role adapters
- ChatGPT Tech Lead: `docs/kit/ORCHESTRATOR.md`.
- Coding workers, including replacements: `docs/kit/WORKER.md`.
- OpenCode loads its adapter via `opencode.json`. GitHub web contributors follow `.github/copilot-instructions.md`.

Those files only explain role-specific execution of THIS policy. **If any adapter conflicts with this AGENTS.md at accepted POLICY_REF or the current Product SoT, stop and surface the mismatch.**
