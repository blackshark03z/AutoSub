# Kit v1.2.2 in AutoSub

**Origin:** Owner-supplied CODEX_NATIVE_WORKFLOW_KIT_v1.2.zip; manifest.version=1.2.2, base_version=1.2, updated_on=2026-10-16. Primary runtime=Codex. Kit itself contains no third-party BMAD/Ponytail/UI UX Pro Max installer and is not E2E-qualified by the upstream manifest.

This directory provides **repo-native role adoption**, not another SoT or scheduler:
- docs/kit/ORCHESTRATOR.md — ChatGPT Tech Lead workflow, MAR usage and anti-drift.
- docs/kit/WORKER.md — OpenCode/DeepSeek and replacement-worker behavior.
- AGENTS.md — universal first-read entry point.
- .github/copilot-instructions.md — GitHub/browser/AI contribution instructions.
- docs/DECISIONS/2026-10-18-kit-v1.2.2-and-cloud-ship.md — accepted Owner product/Kit decision (authoritative).

The kit's full original **Codex-native** skill is in the supplied ZIP at .agents/skills/codex-techlead/SKILL.md with SHA-256 addc9b5257976c4de981dacdb6a960c108ce3fd0d860a1bc28fa29490e47581a. The source ZIP SHA-256 is 716e2c83f052cd82c3d9a9120f2aa2076a5e7b61dca66a4be873b4b46f6a3b42. Do not claim the native skill is installed until its exact bytes are installed and discovered by a real Codex session. OpenCode follows WORKER.md instead. Third-party tools are opt-in, never auto-installed.

**Version discipline:** Do not edit or label a derived skill as the byte-identical official Kit. When importing native SKILL.md, preserve manifest hashes, keep a clear upstream reference and verify a fresh worker session actually discovers it. This README is not authorization for external writes or unattended worker execution.
