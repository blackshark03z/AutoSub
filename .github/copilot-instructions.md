# AutoSub repository instructions — Kit v1.2.2

Read root AGENTS.md, docs/DECISIONS/2026-10-18-kit-v1.2.2-and-cloud-ship.md and docs/kit/README.md before planning or coding. Resolve your **actual branch and commit**: web GitHub main, local D:\AutoSub, and acceptance worktrees may disagree. Never infer Cloud product status from local Windows release documentation.

**Ship goal:** AutoSub Cloud, one video URL → automatic ingest → Chinese hardcoded-caption OCR → US-English offline translation → tight black source-sub mask + English overlay, retaining original audio/logo/AI badge → verified complete video deliverable with resumable long media. vi-VN is secondary. This is not yet Cloud PRODUCT PASS.

**Kit execution:** SHAPE → BOUND → SLICE → BUILD → PROVE → LEARN. Fix the most evidenced critical-path blocker, reuse existing services, focus BUILD tests, perform protected/full checks on the exact final candidate. Stop repeated no-progress loops; distinguish technical vs product vs editorial vs security vs publication gates. Do not create new SoT/control planes/ceremonial WP chains.

**Web contributor / Tech Lead:** Read docs/kit/ORCHESTRATOR.md; inspect local/worktree evidence via MAR when connected. Draft bounded scopes with acceptance, cost/time/security limits and least manual human relay; use MAR for read/status and guarded docs sync, not commands/worker launch/deploy. If a tool is disconnected, report the connector failure precisely rather than asserting host state.

**Worker:** Follow docs/kit/WORKER.md from your own branch. A worker replacement must re-read these files on startup, and declare exactly what version of the Kit it has actually loaded; no pretend native Codex skill activation in OpenCode.

**Boundaries:** Do not touch another worker's dirty worktree. No unapproved Git push/merge, deploy, publication, external AI/video upload, credential extraction, destructive clean/reset or self-install of optional third-party skills. Historical local project_state.json and TASK.md are historical product baselines and must not be mistaken for the new Cloud ship acceptance.
