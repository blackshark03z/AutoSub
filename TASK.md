# Goal

Release a stable AutoSub build from the canonical `D:\AutoSub` source under current CADS, with two truthful user-facing caption workflows and no active Build OS lifecycle/control plane:

- **Speech mode:** local AutoSubs transcription -> local Argos Chinese-to-English translation -> verified subtitle render.
- **Embedded-caption mode:** local PaddleOCR -> Gemini text/multimodal correction + translation -> verified subtitle render.

The user must be able to configure Gemini from the Simple UI without editing files, understand that OCR+Gemini uses Internet/quota and may incur provider charges, and fail before expensive frame analysis when OCR or Gemini prerequisites are unavailable.

# Critical User Journeys

## CUJ-A — Local speech

1. Launch `D:\AutoSub\Run AutoSub.cmd` and reach the Simple UI.
2. Select a supported Chinese-dialogue video.
3. Keep `Tự nhận dạng lời nói cục bộ` selected and run once.
4. AutoSub verifies/prepares AutoSubs + Argos, transcribes, translates, renders, validates, and exposes the real MP4 preview/output folder.
5. Start a new video without stale state leaking from the completed run.

## CUJ-B — OCR + Gemini

1. Select `Dịch phụ đề có sẵn (OCR + Gemini)`.
2. UI clearly shows that PaddleOCR is local while Gemini uses Internet/API quota and may incur charges.
3. If no Gemini key exists, Run remains blocked and the user can open Settings, paste 1–n keys (one per line), and append them to `secrets\\gemini_api.txt`. Existing keys are preserved, duplicates are ignored, and the API/UI expose only sanitized counts/status rather than key contents. At runtime, auth/quota failure on the active key advances once through the configured list and sticks to the first working key; network/provider-wide failures stay bounded rather than cycling indefinitely.
4. Before frame extraction, AutoSub verifies the local OCR runtime and verifies Gemini credential/connectivity/model availability.
5. AutoSub reads embedded Chinese captions with PaddleOCR, uses Gemini text/multimodal resolution for correction/translation, renders the English subtitles, validates the final MP4, and records real Gemini provider usage.
6. OCR/Gemini failures remain fail-closed with retry/back behavior and no eligible output.

# Acceptance

- **A1 — CADS / Build OS:** ordinary development/release uses current CADS + native Git/test/runtime evidence. No active Build OS lifecycle/adoption/control-plane artifact gates normal work.
- **A2 — D-root convergence:** active source, DB/data, OCR runtime, managed AutoSubs/Argos runtime, models and accepted run paths resolve from D. Any unique AutoSub legacy data remaining on C is preserved to D before removal.
- **A3 — Truthful modes:** Simple UI exposes speech-local and OCR+Gemini. It does not expose the legacy direct Gemini caption mode as a separate user choice. Primary copy does not imply OCR+Gemini is offline.
- **A4 — Gemini key management:** missing `operator/translation_config.env` is not fatal; non-secret Gemini defaults are built in. Settings accepts 1–n Gemini keys (one per line), appends only new unique keys to ignored local file `secrets\\gemini_api.txt`, preserves the existing list, clears the input after save, and readiness/API expose only sanitized status/count/model/source. Legacy Credential Manager entries remain readable as a compatibility fallback.
- **A5 — Mode-aware readiness / fail-fast:** speech readiness depends on AutoSubs + Argos. OCR readiness depends on PaddleOCR + Gemini credential. OCR/Gemini preflight occurs before caption frame extraction; missing/invalid prerequisites cause no expensive caption-analysis work.
- **A6 — Gemini behavior:** OCR path reuses the existing guarded PaddleOCR + Gemini implementation, including text correction, multimodal resolution for uncertain intervals, semantic guards, caching and provider usage accounting. A compatible selected Gemini model may run under the Owner-authorized provider policy even when historical free-tier evidence is absent.
- **A7 — Mode-accurate UX/recovery:** processing labels, helper copy, readiness cards and retry actions match the selected mode; OCR and Gemini failures preserve safe context and never expose a false completed result.
- **A8 — Real E2E evidence:** one representative speech fixture completes through real AutoSubs + Argos; one representative embedded-caption fixture completes through real PaddleOCR + Gemini with `provider_calls.gemini > 0` unless an exact provider cache hit is explicitly evidenced, and produces a validated final MP4.
- **A9 — Regression:** focused tests, storage preflight, canonical docs validation and normal full `python -m pytest -q` pass from the candidate Product HEAD. Release-specific verification is run for the new candidate package.
- **A10 — Stable release:** one clean canonical `main` Product HEAD contains the accepted work, is pushed to `origin/main`, tagged `v1.9.0`, and an exact-commit source bundle is generated with manifest/checksum evidence. Historical CP12B/CP13A packages are not relabeled as this release.

# Acceptance Fixtures

- Speech: `D:\AutoSub\data\operator_uploads\8f5454dd0b448583a7a497b79bcc34f8903d017090cd4f8fb2e8308fdaa5a442.mp4`.
- OCR: prefer the preserved historical short OCR acceptance clip after D migration; otherwise create a short D-local acceptance excerpt from the already-migrated OCR source. Acceptance media remains outside Git.

# Constraints

- Windows-first, local single-user product.
- Source/user media is never mutated or auto-deleted to solve storage pressure.
- Gemini is an explicit external provider boundary only for OCR+Gemini; ElevenLabs/YouTube publication remain outside this Goal.
- Secrets never enter Git, logs, response payloads or release archives.
- Keep the FastAPI modular monolith + SQLite/run isolation unless direct evidence requires otherwise.
- Prefer `REUSE -> WIRE -> FIX -> REPLACE_AND_DELETE -> ADD`; do not create a new translation provider when the existing Gemini caption path already satisfies the Goal.
- No cosmetic redesign chain after material journey/usability blockers are closed.

# Current Findings

- Canonical D migration commit is `35589b73133bc4d414c1b6424669e3f4bde8eaf9` on `main`/`origin/main` before this Goal.
- Current CADS baseline verified at `a3e24a1d28cea2a4ad0ee956dfa13ca2a2d211f5`.
- The prior OCR failure was caused by missing OCR discovery/readiness and occurred only after expensive frame/crop work.
- The existing full embedded-caption implementation already contains PaddleOCR + Gemini text/multimodal correction/translation and quality guards; it is being reused rather than replaced.
- The machine currently has no Gemini credential configured for AutoSub, so real Gemini E2E requires one Owner-provided API key entered locally in the new UI.

# Release Closure

Both real CUJs now PASS. Final closure is bounded to release identity/docs validation, one final normal regression, storage gates, commit/push, tag `v1.9.0`, and exact-tag source bundle/checksum generation. No new product scope is opened in this release.
