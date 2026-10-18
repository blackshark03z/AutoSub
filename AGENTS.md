# AutoSub — Kit v1.2.2 (all agents / workers)

**First-read contract for ChatGPT web, Codex and every OpenCode/DeepSeek or replacement worker.** The owner-supplied Codex Native Workflow Kit has **manifest.version=1.2.2**, SHA-256 for source ZIP **716e2c83f052cd82c3d9a9120f2aa2076a5e7b61dca66a4be873b4b46f6a3b42**. The repo contains a verified role adaptation; presence of this text does **not** imply that the native Codex skill or optional third-party packages are installed.

## One current ship goal
**AutoSub Cloud**: paste authorized video URL → auto-ingest full source → recover burned-in Chinese subtitles once → US-first context-rich local translation → mask only source-caption strip with solid black, overlay en-US captions with matching readability, preserve source audio/logo/uploader/AI badge → validated downloadable MP4. Preserve chunk checkpoint/resume for long media. vi-VN is secondary. This is the Owner's current ship target; the Windows local 1.10.0 product is an accepted historical baseline, not the current cloud release.

**Product SoT:** docs/DECISIONS/2026-10-18-kit-v1.2.2-and-cloud-ship.md. **Audit/next milestone:** docs/research/autosub_cloud_ship_gap_audit_2026-10-18.md (evidence, not a separate SoT). **Role guidance:** docs/kit/ORCHESTRATOR.md for ChatGPT; docs/kit/WORKER.md for all coding workers; docs/kit/README.md for KIT provenance. **GitHub/web:** .github/copilot-instructions.md. The Kit's original Codex skill is in the Owner-supplied ZIP; native skill activation requires actual installation/discovery, not a label in this file.

## Required approach — lightweight Kit v1.2.2
SHAPE (whole Goal/CUJ) → BOUND (scope, hard resources, authority, exit) → SLICE (proven next product blocker) → BUILD (reuse, wire, fix before adding; focused tests at useful checkpoints) → PROVE (real end-to-end; full/protected tests at final candidate) → LEARN (observed product delta and honest unfinished work). No substitute framework, endless WP/reports, invented blockers or regression loops.

At every fresh context resolve **path, branch, HEAD and dirty state**, read correct SoT and the actual accepted reports. GitHub main, D:\AutoSub, review worktrees and live runtime may differ. The 6-minute Bilibili longpilot at review/us-longpilot-20261017 commit f5f8e25 is a genuine **technical** PASS, but EDITORIAL_QA=PARTIAL, SECURITY_GATE=SUPERVISED_PASS, PUBLICATION_AUTHORIZATION=BLOCKED; **not** Cloud Product PASS.

## Mandatory boundaries
Owner controls goal, exceptional external writes/cost and publication/deployment. Do not merge, push, deploy, upload private media/credentials, extract cookies, bypass protected access, reset another worker's tree or publish without separate authority. Do not modify another worker's active/dirty files. Keep media/secrets/model/cache artifacts outside Git. Source hash + model + locale identify resumable runs; preserve audio/attribution/AI badge. Do not auto-install BMAD Method, Ponytail, UI UX Pro Max or third-party hooks.

Tech Lead uses MAR for reading/search/Git identity and **guarded documentation updates only**, not MAR runner/worker/task execution without explicit new authorization. Minimize Owner copy-paste relay: gather known facts through MAR, then issue at most one properly bounded implementation task with acceptance oracle and honest result. Workers read their own branch's AGENTS.md and docs/kit/WORKER.md before work; Codex-only native skills must not be pretended in OpenCode.

## Historical local product
The existing FastAPI/SQLite Simple UI, `Run AutoSub.cmd`, PaddleOCR/Gemini and AutoSubs/Argos baseline remain usable engineering assets. Older local workflow requirements in TASK.md, project_state.json and ARCHITECTURE.md may describe historical implementation reality only; they do not override the Cloud ship decision. Tests, technical pilots and worker reports are evidence, not independent ship authorization.
