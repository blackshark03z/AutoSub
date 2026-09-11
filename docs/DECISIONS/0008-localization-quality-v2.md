# ADR 0008 — LocalizationUnit, TextTrack and composed quality pipeline

Status: Accepted for AutoSub Localization V2 implementation
Date: 2026-09-10

## Context

AutoSub V1 can produce validated subtitle video, but subsystem PASS is not enough for the new owner outcome. The owner wants materially better subtitle/source recognition, run-level Kokoro voice, and the ability to replace relevant source-language text anywhere in the image with target-language text while keeping a one-click normal workflow.

Current code has useful source-caption detection/delogo/render/residual-CJK repair, but it is subtitle-ROI-specific. Raw subtitle cues and per-frame OCR detections are too narrow to be the V2 domain: TTS wants longer semantic speech blocks, visual text persists across frames, and spoken ASR may disagree with burned-in captions.

Live evidence on the real source showed:

- current local AutoSubs-small can make content-important recognition errors that Whisper large-v3-turbo resolves more closely;
- PP-OCRv6 whole-frame OCR can recover dialogue and UI/scene text across the screen;
- a dense 20-frame sequence contained 20 repeated CJK observations but only 2 stable dialogue identities, demonstrating a 90% reduction in translation decisions when treated as tracks;
- LaMa can remove bounded real text regions without modifying pixels outside the mask in the working image, while perceptual temporal reconstruction remains a separate quality concern.

## Decision

1. Make `LocalizationUnit` the semantic domain, with `SpokenLocalizationUnit` and `VisualTextUnit/TextTrack` projections.
2. Preserve raw ASR and visual-dialogue-caption evidence independently and align/fuse them; disagreement triggers escalation rather than destructive provider precedence.
3. Keep semantic translation separate from `spoken_text`, `subtitle_text` and visual `display_text` so timing/layout optimization cannot silently alter meaning.
4. Make TextTrack a first-class temporal identity. OCR observations establish/verify the track; translation/style/replacement planning is performed once per accepted identity. Reappearing identical text may share translation memory while remaining a separate geometry track.
5. Full Visual Localization means source-matched replacement: detect relevant source-language semantic text throughout the frame, classify localize/preserve, remove the source pixels, reconstruct as needed and render readable target text at the corresponding visual role/location.
6. Preserve target-language text, identifiers/usernames, pure numbers where appropriate, logos/brands and protected/non-semantic regions by default rather than blindly translating every OCR detection.
7. Use tiered visual replacement: deterministic Tier A -> commercially compatible reconstruction/inpainting Tier B -> difficult tracked/generative/review escalation. Reuse existing AutoSub mask/render/residual-repair concepts before adding parallel subsystems.
8. Use PP-OCRv6 small as fast whole-frame OCR candidate and medium as uncertainty escalation. Use Whisper large-v3-turbo as GPU fast-quality ASR candidate with large-v3/corroboration escalation; keep AutoSubs-small as local/offline fallback.
9. Kokoro stays local-first. Voice/speed are run-level pre-Run configuration with preview. TTS speech blocks are semantic units, not automatically one subtitle cue each; final subtitles align to generated narration when voice replacement is enabled.
10. Final quality is gate/evidence based, not a fabricated score. Material unresolved source language, subtitle hard violations, TTS overflow/alignment or visual replacement failures prevent false COMPLETE.

## Consequences

- V2 implementation must introduce coherent domain/run-state boundaries before adding isolated UI controls that imply unimplemented behavior.
- Whole-frame OCR can sample adaptively and dedupe temporally, improving both quality and cost.
- Visual text localization no longer depends on the fixed lower subtitle ROI, while subtitle-only mode can retain a conventional lower-third presentation.
- More than one evidence source may exist for dialogue; provenance/conflict becomes explicit.
- Subjective background reconstruction/flicker/voice naturalness remains an Owner real-use oracle even after machine QC passes.

## Revisit when

Revisit only if real implementation/golden-fixture evidence shows the domain split itself prevents quality/performance, or a simpler representation can preserve the same observable invariants without losing source evidence, temporal identity, run snapshot or composed acceptance.
