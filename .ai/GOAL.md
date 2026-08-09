# Active Goal

Goal ID: AUTOSUB_EXTERNAL_TRANSCRIPTION_SHIP
Goal Status: COMPLETED
Goal Type: product
Risk Ceiling: R2
Updated: 2026-08-09T05:50:02+00:00

## Outcome

Validate and ship the preserved AutoSubs external-transcription MVP using the already verified Git-object reconstruction, real Chinese-media execution, and frozen product acceptance.

## Acceptance

- [ ] Approved local external engine transcribes representative Chinese local-speech video into deterministic timestamped Chinese source cues.
- [ ] Normalized external transcript enters source-track resolution without downstream semantic mutation.
- [ ] Chinese-to-English translation is separate from the Chinese source track, and same-language behavior does not translate.
- [ ] One-click local video plus target language reaches external transcription, source track, translation, existing layout/timeline, and preview/export with actionable runtime/model failures.
- [ ] Accepted subtitle presentation/layout/timeline and source/translation regressions remain intact.

## Acceptance Quality

- Falsifiability heuristic: no high-confidence warnings

## Goal Acceptance Contract

- Status: FROZEN 49c8056e08cc
- Criterion mappings: 5/5

## Non-Goals

- No redesign, cloud ASR, noncommercial alignment assets, diarization, forced alignment, Faster-Whisper removal, or Vibe exploration.

## Budget

- Maximum tasks: 1
- Maximum parallel writers: 1
- Maximum consecutive non-shipping tasks: 2
- Maximum revisions per task before stop-loss: 2
- Scope growth limit: 30%
- Scout input budget: 24000 tokens
- Scout wall budget: 5.0 minutes
- Scout provider-cost budget: 0.0 (0 = unbounded/unavailable)

## Task Graph

| Node | Status | Agent | Risk | Delivery Delta | Depends On | Outcome |
|---|---|---|---|---|---|---|
| SHIP_EXTERNAL_TRANSCRIPTION_MVP | DONE | WORKER | R2 | EXECUTABLE_CAPABILITY | - | Materialize the externally Git-verified AutoSubs MVP, run real Chinese-media source-only AutoSubs acceptance and the frozen 5/5 suite, then ship the intentional product delta without redesign. |

## Human Interrupt Policy

Only interrupt the owner for a genuine product decision, risk above ceiling/authorization, destructive/production authority, unresolved blocker, or final Goal acceptance. Worker reports are machine-to-machine state, not owner handoffs.
