# Active Goal

Goal ID: AUTOSUB_FULL_MEDIA_TRANSCRIPTION_RELIABILITY
Goal Status: COMPLETED
Goal Type: product
Risk Ceiling: R2
Updated: 2026-08-09T09:20:52+00:00

## Outcome

Make the retained 366.270998-second Chinese video complete source-only AutoSubs transcription through the normal AutoSub workflow with bounded, explainable reliability semantics, then complete existing local Argos translation and preview/export.

## Acceptance

- [ ] Use the canonical retained 366.270998-second Chinese video and prove normal full-media AutoSubs source-only transcription completes without fallback or timeout.
- [ ] Return non-empty timestamped Chinese source cues; AutoSubs must not translate, diarize, or force-align.
- [ ] Keep a bounded, explainable reliability policy that fails a hung provider clearly without removing timeout protection.
- [ ] Preserve provider source text and timestamps through normalization and resolved source-track creation.
- [ ] Continue the same real application workflow through the existing Argos 1.9.6 translate-zh_en-1_9 runtime to non-empty English translation cues.
- [ ] Produce and inspect a real preview/export artifact with populated timeline/layout and source/translation provenance.
- [ ] Pass relevant existing regressions plus focused coverage of the evidence-backed reliability fix.

## Acceptance Quality

- Falsifiability heuristic: no high-confidence warnings

## Goal Acceptance Contract

- Status: FROZEN 58be69b32e26
- Criterion mappings: 7/7

## Non-Goals

- Do not replace AutoSubs, alter Argos, add cloud ASR, tune recognition quality, alter OCR/layout/packaging, or use an arbitrary static timeout increase as a fix.

## Budget

- Maximum tasks: 2
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
| DIAGNOSE_AUTOSUBS_FULL_MEDIA_TIMEOUT | DONE | WORKER | auto | NO_DELTA | - | Diagnose the full-media AutoSubs timeout using the canonical source audio, bounded comparative direct probes, process/output instrumentation, and the normal-workflow evidence; classify the root cause without product changes. |
| FIX_AUTOSUBS_FULL_MEDIA_RELIABILITY | DONE | WORKER | R2 | EXECUTABLE_CAPABILITY | DIAGNOSE_AUTOSUBS_FULL_MEDIA_TIMEOUT | Implement only the smallest AutoSubs full-media reliability fix supported by Task 1, preserve source-only timestamp semantics, then prove the full normal workflow through existing Argos translation and preview/export. |

## Human Interrupt Policy

Only interrupt the owner for a genuine product decision, risk above ceiling/authorization, destructive/production authority, unresolved blocker, or final Goal acceptance. Worker reports are machine-to-machine state, not owner handoffs.
