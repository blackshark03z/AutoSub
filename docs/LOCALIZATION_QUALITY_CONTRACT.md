# AutoSub Localization Quality Contract

Status: **FROZEN FOR LOCALIZATION V2 IMPLEMENTATION**
Date: 2026-09-10

This document defines the quality/runtime invariants behind `TASK.md`. It is intentionally provider-agnostic where provider facts may change. Implementation may improve algorithms without changing this contract; changing an observable invariant or material trade-off requires updating the Goal/decision record first.

## 1. Quality model

A V2 run is successful only when the gates required by its enabled mode pass:

`CONTENT_VALID && SUBTITLE_VALID && [AUDIO_VALID] && [VISUAL_LOCALIZATION_VALID] && RENDER_VALID`.

Bracketed gates apply only when that capability is enabled.

`COMPLETE` is an acceptance state, not a synonym for process exit code 0.

Quality evidence must be tied to immutable run/artifact identity. Unknown/unverified is never silently promoted to PASS.

## 2. Canonical evidence and domain identities

### 2.1 Raw evidence is immutable input evidence

Preserve raw observations separately:

- speech ASR text/timestamps/provider/model/confidence or diagnostics;
- OCR text/polygon/confidence/frame/time/provider/model;
- source media identity;
- provider/model/runtime fingerprints.

Presentation cleanup, translation and layout must not rewrite raw evidence in place.

### 2.2 Spoken evidence is multi-channel

When both source speech and burned-in dialogue captions exist, preserve them as independent evidence channels. Authored captions may be more lexically exact for visible text while ASR may better represent spoken wording; either may be condensed or imperfect.

Align the channels by time/semantic similarity. Agreement increases confidence. Material disagreement triggers recheck/escalation and remains visible in provenance rather than being discarded.

`spoken_text`, `subtitle_text` and `translated_text` are target-language projections of shared semantic intent, not required to be identical strings.

### 2.3 VisualTextUnit / TextTrack

A TextTrack is one temporally coherent visible-text instance with stable semantic identity plus changing frame geometry.

Track identity considers:

- temporal continuity;
- polygon/bbox continuity;
- normalized recognized text and language/script;
- scene/change evidence;
- text class/context.

A new sentence/text value normally creates a new semantic track even if it occupies the same screen region. A reappearing identical UI label after a scene break may be a new visual track but can reuse translation memory.

Raw frame detections are not separately translated when they belong to an accepted track.

## 3. Localizable visual-text scope

Full Visual modes classify each track before replacement.

### Localize by default

Relevant semantic source-language text such as:

- dialogue/burned-in subtitles;
- titles/captions/chapter headings;
- UI/menu/action labels;
- signs/labels/instructions;
- score/stat descriptors containing semantic source-language text;
- speech bubbles and other content-bearing text.

### Preserve by default

- text already in the target language unless normalization is explicitly needed;
- usernames, handles, IDs and opaque identifiers;
- logos/brand marks/watermarks unless explicitly classified as localizable;
- pure numbers where the number itself remains valid;
- OCR noise/non-semantic fragments;
- protected owner/system regions.

Mixed strings are handled semantically: preserve identifiers/numbers while localizing the source-language meaning where appropriate. Example: a source-language label plus `525` should not lose or invent the number merely because the label is translated.

Proper names use the translation/transliteration policy appropriate to context; they are not blindly erased.

Every preserved source-language track must have an explicit classification/provenance reason so the final residual scanner can distinguish accepted preservation from missed localization.

## 4. Recognition routing

### 4.1 ASR

Preferred qualified route when cloud is available/beneficial:

`Whisper large-v3-turbo -> uncertainty/disagreement detection -> Whisper large-v3 or corroborating evidence on difficult spans`.

Local AutoSubs-small remains a valid offline/fallback source but is not sufficient as the sole V2 quality oracle under the frozen profile.

No engine output is perfect ground truth by declaration. Source-caption OCR may corroborate speech ASR where aligned.

### 4.2 Whole-frame OCR

Full Visual modes use whole-frame discovery; fixed lower-subtitle ROI is not sufficient.

Default candidate route:

`PP-OCRv6 small -> TextTrack temporal consensus -> PP-OCRv6 medium / alternate verification for uncertain tracks`.

Escalation signals include one or more of:

- low recognition confidence (default attention threshold around 0.90, not a standalone reject threshold);
- normalized string disagreement across nearby observations;
- model disagreement;
- tiny/rotated/perspective/mixed-script text;
- inconsistent geometry/track association;
- expected text region with no stable recognition;
- final residual-language detection.

High confidence alone does not waive temporal/context consistency checks.

### 4.3 Adaptive sampling

Do not run expensive whole-frame OCR on every video frame by default.

Use cheap local frame/change/scene signals and tracked geometry to choose recognition anchors. Increase sampling around appearance/disappearance/text changes/uncertainty; reduce it for stable tracks. Exact fps is an implementation policy, not a product invariant.

## 5. Translation/localization integrity

Extraction method and translation provider are independent dimensions.

The system stores at least conceptually:

- source evidence;
- canonical semantic target meaning;
- subtitle presentation text;
- narration text;
- visual display text when needed.

Display condensation or narration phrasing must preserve material meaning. It may not silently change names, quantities, negation, choices, instructions, score/currency values or causal relationships merely to fit layout/timing.

Repeated translations may use a content/context-addressed translation memory. Cache identity includes source/target language plus enough semantic/class/context identity to avoid unsafe reuse of polysemous text.

Provider fallback/downgrade is explicit in run evidence. No fallback may claim the identity/quality of the preferred provider.

## 6. Default English dialogue-subtitle profile

The following applies to standard English target dialogue subtitles. Non-dialogue visual text follows the visual layout contract instead.

### Hard final gates

After bounded automatic repair, a normal final dialogue cue must satisfy:

- valid finite start/end with end > start;
- no destructive overlap with another ordinary dialogue cue;
- maximum **2 lines**;
- maximum approximately **42 characters per line**;
- final reading speed **<= 20 characters/second**;
- ordinary dialogue event duration **<= 7.0 seconds**; longer semantic content is split at a meaningful boundary;
- non-empty meaningful target text for every accepted semantic unit;
- no known dropped semantic unit;
- no target text clipped outside the supported safe frame/layout.

A known hard-gate violation prevents `SUBTITLE_VALID`.

### Preferred / escalation band

- preferred reading speed: **<= 17 CPS**;
- **17–20 CPS** is an attention/repair band: keep only when further adjustment would harm timing or meaning more than it helps readability;
- target minimum display time is approximately **0.83 s** when surrounding timing allows; a shorter brief cue is not automatically corrupt if it remains readable and cannot be safely extended without overlap/misalignment, but it must be evidence-visible;
- segmentation follows punctuation/clause/semantic boundaries rather than fixed character counts;
- line breaks avoid splitting tightly bound name/number/grammar units where practical.

### Repair order

Use the least semantic-distorting repair first:

`keep good cue -> semantic merge/split -> line reflow -> safe timing adjustment -> bounded faithful condensation -> unresolved review/fail`.

Do not solve readability by deleting meaning.

## 7. Voice / Kokoro contract

### 7.1 Configuration and preview

Kokoro voice is run-level configuration. The main pre-Run workflow exposes:

- eligible real voice ID/label;
- bounded base speed;
- preview action/player.

Settings may hold defaults/runtime readiness but cannot be the only place to choose the active run voice.

Preview is generated from the exact current voice + speed identity. Changing either makes the preview stale. User must be able to preview; preview playback itself is not mandatory to Start.

### 7.2 Snapshot and cache

At Run start, snapshot at least:

- TTS provider/model/runtime identity;
- voice ID;
- base speed;
- relevant speech-planning policy.

TTS cache identity includes spoken text + voice + speed + model/revision and other sound-affecting settings. A cache hit from another voice/speed is invalid.

### 7.3 Speech units and timing

Subtitle cues are not blindly reused as TTS synthesis units. Speech planning groups semantically continuous clauses/sentences to preserve natural prosody.

Final subtitle timing in narration mode aligns to the audio the viewer actually hears. Use reliable generated token/word timing when available, otherwise use an explicit alignment method; do not assume source-speech cue timing remains correct after target narration.

The synthesized PCM/WAV artifact is authoritative for narration duration/fit because it is the object actually mixed. Alignment metadata may be used for token placement but may not overrule a longer real PCM duration. If an initial bounded speed estimate still overflows, the engine may iterate a small bounded number of speed adjustments up to the configured maximum rather than truncate audio or silently allow overlap.

Token timestamps are speech anchors, not a requirement that display subtitles use the exact token start/end. A subtitle may use a small evidence-recorded lead/lag around an adjacent token boundary to satisfy readability while remaining perceptually synchronized. Current English V2 implementation caps such narration display-boundary adjustment at **120 ms per affected shared boundary**; larger changes require a different repair/escalation path.

### 7.4 Fit policy

If target speech exceeds the safe timing budget:

1. use safe adjacent silence where it does not collide with the next semantic unit;
2. apply a small bounded speed adjustment around the owner-selected base speed;
3. create a semantically faithful compact spoken form;
4. fail/review if natural fit still cannot be achieved.

Audio truncation, overlapping spoken blocks, extreme hidden speed-up or dropping semantic content is prohibited. Any unresolved overflow prevents `AUDIO_VALID`.

Kokoro is local-first for preview and normal production; cloud TTS is not required unless future benchmark evidence justifies it.

## 8. Visual replacement routing

### 8.1 Full Visual presentation rule

Full Visual modes are **source-matched localization** by default: remove source text and render the target replacement at the corresponding visual role/location/geometry, rather than leaving source text and adding an unrelated lower-third translation.

Subtitle-only mode may use AutoSub's standard readable lower-third style without full source-matched visual localization.

### 8.2 Tier A — deterministic

Use for simple/stable regions such as subtitle bands, flat UI labels and backgrounds where existing deterministic cleanup/delogo/fill plus target rendering passes QC.

Reuse existing AutoSub source-caption interval/mask/render/residual-repair concepts where possible.

### 8.3 Tier B — reconstruction/inpainting

Use when source pixels behind text require reconstruction. Commercially compatible LaMa is a qualified candidate for bounded still/stable-region inpainting from current evidence.

For moving backgrounds, do not accept independent per-frame LaMa solely because each frame removes the glyph. Prefer temporal/background reconstruction, tracked masks/geometry and consistency checks; escalate when per-frame processing would flicker.

### 8.4 Higher tier / difficult visual cases

Perspective, foreground occlusion, decorative typography or complex moving backgrounds may require tracked segmentation/reconstruction or generative text editing. Only commercially compatible code + exact pretrained weight/base-model/dependency license chains can enter the default monetized path.

If no qualified method meets quality, expose bounded review/failure rather than substituting known non-commercial weights or obviously bad compositing.

### 8.5 Spatial boundedness

A replacement operator should not mutate unrelated pixels/regions beyond what its method/render encoding requires. Working-image inpainting/mask stages record their intended mask/region. Final video re-encoding differences are not interpreted as source-region edits by pixel equality alone.

## 9. Visual target-text layout

For each localizable track, preserve meaning and hierarchy while fitting target text using this order:

1. preserve appropriate source role/anchor/style class;
2. use safe box expansion if available;
3. wrap at semantic boundaries;
4. reduce font size only within the readability floor/profile;
5. use an approved semantically equivalent display condensation;
6. fail/review if no acceptable fit exists.

Never shrink text to illegibility or invent a shorter but materially different translation.

Stable track style/translation decisions remain stable across its lifetime. Geometry may move with the tracked source surface.

Where a foreground object occludes the source text, replacement must respect material visual ordering when required. Text rendered over a foreground person/object that should cover it is a visual-integrity failure.

## 10. Closed-loop residual and temporal QC

After replacement/render, rescan the final output using source-language/script detection plus track evidence.

A residual is actionable when it is material source-language text in a region not classified as protected/preserved. Final scanner output references time/region/track so repair is targeted.

`VISUAL_LOCALIZATION_VALID` requires:

- every detected material localizable track is translated/replaced or explicitly unresolved;
- unresolved material localizable track count = 0 for machine COMPLETE;
- material unprotected source-language residual count = 0 after bounded repair;
- target text not materially clipped/out of its intended layout;
- track replacement lifetime covers the intended source-text lifetime without leaving obvious empty cleanup boxes;
- no known material foreground-occlusion ordering error;
- no known material temporal flicker/drift/reconstruction defect on machine-detectable checks.

Not every perceptual artifact has a trustworthy universal numeric oracle. Representative before/after playback/contact-sheet review by the Owner is the final oracle for subjective background reconstruction, typography naturalness and flicker before Product Acceptance.

## 11. Quality severity model

### Hard failure / blocks COMPLETE

Examples:

- invalid/missing output;
- known semantic unit lost/untranslated without preserve policy;
- final dialogue >2 lines / >42 chars line / >20 CPS after repair;
- invalid/destructive subtitle timing;
- unresolved narration overflow/alignment;
- known material unprotected source-language visual residual;
- unresolved localizable TextTrack;
- target visual text clipped/unreadable;
- corrupt/mismatched provider artifact hashes;
- use of an unqualified provider/model for a path requiring qualification.

### Attention / escalation, not automatic rejection

Examples:

- subtitle 17–20 CPS;
- unusually short but readable cue;
- OCR confidence below the attention threshold;
- cross-frame/model disagreement;
- one-frame/very brief text with insufficient temporal evidence;
- difficult perspective/occlusion/background;
- suspicious inpaint seam/texture metric;
- cloud route whose predicted benefit is marginal.

These trigger more evidence, a stronger method or bounded review. They do not become PASS merely because processing continued.

## 12. Compute/provider contract

### Local authority

Local AutoSub owns source identity, run state, configuration snapshot, accepted shard/artifact identity, caches and final acceptance. Remote state is disposable/recoverable capacity.

### Qualification is two-dimensional

Compute-provider permission/terms and model/code/weight/dependency license permission are independent. Both must pass for intended commercial/monetized production use.

### Current provider disposition

- Modal: primary automated burst candidate from live technical/policy qualification evidence;
- Kaggle standard consumer terms: non-commercial research/benchmark only for this project unless separate permission changes the condition;
- Colab Free: manual research/benchmark only, not AutoSub automated backend;
- Lightning Free: not production-qualified until applicable commercial-use terms are clarified.

These are provider observations and may change without changing product architecture.

### Routing

Owner-facing policies:

- `Local only`;
- `Auto`;
- `Cloud allowed, no paid overage`.

Auto route estimates/learns **E2E wall time** including preprocessing, upload, queue/cold start, model load, inference, download and cache reuse. It does not dispatch cloud because GPU inference alone looks fast.

Cloud is useful for meaningful batches/capacity. Micro-workloads may remain local.

### Data minimization

Send the smallest technically sufficient artifact:

- ASR: audio/chunks rather than video;
- OCR: sampled/deduplicated frames/crops rather than every full frame;
- inpainting: target region/context frames + masks;
- translation: canonical text/context.

Do not make a remote copy the only copy of source or accepted output.

### Shard state and integrity

Candidate lifecycle:

`LOCAL_PREPARED -> REMOTE_STAGED -> REMOTE_RUNNING -> REMOTE_RESULT_READY -> LOCAL_VERIFIED -> REMOTE_CLEANED -> COMMITTED`.

Each shard identity binds input hash, workload, model/runtime revision and relevant parameters. Result manifest binds output hashes and bounded timing/provenance.

Only `LOCAL_VERIFIED` output can enter accepted local cache. Retry/preemption with the same shard identity returns/reuses the same accepted output when valid rather than duplicating work.

Remote cleanup is attempted after local verification and recorded separately. Cleanup failure is an operational issue; it does not invalidate an already hash-verified local result.

### Budget

`Cloud allowed, no paid overage` requires provider-side spend/budget protection to be configured/verified at provider enablement. If it cannot be verified, cloud execution under that policy is unavailable. Never silently convert it into paid compute.

## 13. Performance/economy invariants

Optimization order is:

`avoid work -> dedupe/cache -> cheap first pass -> uncertainty escalation -> qualified acceleration -> concurrency`.

Specific invariants:

- stable TextTracks are translated/styled/planned once per identity, not every frame;
- content-addressed cache prevents recompute when input/model/settings identity is unchanged;
- changing a downstream setting does not invalidate unrelated upstream accepted work;
- cloud worker images/models are prebuilt/smoke-tested where practical; normal jobs do not discover missing core native dependencies after long admission;
- long owner batch sessions are composed from shorter independently recoverable shards;
- provider outage/quota exhaustion is `PROVIDER_UNAVAILABLE`, not `RUN_CORRUPTED`;
- Auto route must be calibratable from measured representative local/cloud timings and must include a margin against noisy provider startup rather than routing on a single optimistic sample.

Observed research values belong in the provider-qualification evidence document, not as permanent product SLOs.

## 14. Run lifecycle / repeated use

At Start, freeze the run snapshot: source identity, mode, target language, translation policy, subtitle quality profile, voice configuration when enabled, compute policy and relevant model/provider identities/policies.

Runtime jobs may add evidence/results but may not retroactively change the run's owner-selected configuration.

Terminal Run A becomes historical. Starting Run B creates a fresh active run context. UI/session/cache must not resurrect A as B's active configuration. Reusable content-addressed artifacts may be referenced only when their hash/config identity is valid.

## 15. Truthful QC evidence

Do not collapse quality into one opaque score.

Expose applicable real facts such as:

- source evidence coverage/disagreement;
- semantic translation coverage;
- empty/unresolved units;
- subtitle cue count, overlap/line/CPS/duration violations;
- TTS blocks, voice identity, overflow/alignment;
- whole-frame OCR observations and accepted TextTrack counts;
- localized/preserved/unresolved visual-track counts;
- residual source-language count;
- replacement tier and repair attempts;
- provider/model/cache/retry identities;
- shard hash verification/cleanup state;
- rendered cue/track count;
- media/output validation.

Unknown data is shown as unknown/not applicable, not guessed.

## 16. Product acceptance oracle

Engineering tests/model probes establish subsystem evidence, not Product Acceptance.

Before `PRODUCT_READY_FOR_OWNER_ACCEPTANCE`:

1. run the full golden video through the real supported UI for each V2 mode being claimed;
2. prove one-click normal completion on the golden path;
3. inspect/play representative subtitle, voice and visual replacement windows, including moving/difficult text;
4. verify final residual scan and media output;
5. perform Run A -> new Run B isolation;
6. run focused + full regression from the candidate Product HEAD;
7. preserve all material direction in repo/Git, not chat only.

The Owner's real-use judgment is the final oracle for subjective visual/audio naturalness. Acceptance must not be weakened to match whatever implementation happened to pass tests.
