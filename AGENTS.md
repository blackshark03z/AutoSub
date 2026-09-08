# AutoSub — CADS operating map

AutoSub follows **Convergent AI Development System (CADS)** for AI-assisted engineering. The canonical CADS source is `https://github.com/blackshark03z/Convergent-AI-Development-System`; the locally verified CADS baseline for this activation is commit `a3e24a1d28cea2a4ad0ee956dfa13ca2a2d211f5` (`Consolidate CADS around five engineering controls`).

CADS is a reasoning/engineering standard, not a second lifecycle runtime. Ordinary work stays native to Git, source inspection, focused tests, full regression when appropriate, ordinary commits, and the actual AutoSub runtime.

## Control loop

On new/stale context, reconstruct repository/runtime reality before planning. Then use the smallest applicable CADS control:

1. **Reality** — identify canonical Git HEAD, dirty state, current product behavior, runtime/config/data identity, and direct evidence.
2. **Intent / Design** — frame one bounded Product Goal, representative Critical User Journey (CUJ), acceptance, fixture, non-goals, constraints, and only material design drivers.
3. **Change** — make the minimum sufficient coherent change. Prefer `REUSE -> WIRE -> FIX -> REPLACE_AND_DELETE -> ADD`.
4. **Acceptance** — verify the predefined product outcome and the composed real-user journey. Feature/test PASS does not by itself establish Product PASS.
5. **Consequence** — use an explicit boundary only for destructive, external, privileged/security-sensitive, or explicitly high-cost effects.

For material user-facing work, apply the current CADS user-facing workflow, frontend-design, and UI-quality-review guidance when available. If the external CADS skill library is unavailable, this file plus the repository's `TASK.md`, `ARCHITECTURE.md`, tests, and runtime evidence remain the minimum operating contract; do not invent a replacement lifecycle.

## Project-specific authority

- Owner: desired product outcome, material product trade-offs, consequential authorization, and subjective real-use acceptance.
- AI Tech Lead: missing engineering-concern discovery, Goal/CUJ/acceptance framing, ordinary engineering judgment, and verification strategy within Owner intent.
- `main` Git/source: implementation reality.
- Identified live AutoSub runtime: observed behavior for the exercised source/config/environment.
- Tests: verification evidence, not a substitute for the real journey.
- `TASK.md`: current bounded Goal and progress only.
- `ARCHITECTURE.md`: durable architecture/invariants.
- `docs/DECISIONS/`: material accepted direction that must survive turnover.

## Current product boundaries

- Product target: local, single-user Windows application.
- Primary entry: double-click `Run AutoSub.cmd`; normal use must not require a terminal.
- Primary V1 journey: local Chinese-dialogue video -> local readiness -> speech transcription -> Chinese-to-English translation -> subtitle render -> verified MP4 preview/export.
- User media must not be mutated or automatically deleted.
- Gemini, ElevenLabs, upload/publish external providers remain outside the active Product Goal unless explicitly re-authorized.
- EXE/installer/release packaging remains a separate deferred lane.
- Runtime binaries, models, caches, user media, databases, secrets, and generated heavy artifacts stay out of canonical source.

Legacy Build OS lifecycle/control-plane files are not current authority and must not be used to gate ordinary AutoSub development.
