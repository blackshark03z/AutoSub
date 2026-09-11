# SUPERSEDED DRAFT — AutoSub Localization V2 Requirement & Acceptance Candidate

Status: **SUPERSEDED BY FROZEN V2 CONTRACT**
Date: 2026-09-10
Authority: research/history only. Implementation source of truth is now `TASK.md` + `docs/LOCALIZATION_QUALITY_CONTRACT.md` + ADR 0008/0009. Keep this file only to preserve the pre-freeze reasoning trail; do not implement from it when it conflicts with the frozen files.

## 1. Product goal

AutoSub V2 localizes a source video into a target-language video with materially improved quality and throughput while preserving truthful provenance and fail-closed behavior.

The product scope is broader than conventional subtitles:

1. localize spoken content into target-language timed subtitles;
2. optionally replace source speech with a selected Kokoro target-language narration;
3. detect and localize relevant source-language text anywhere in the visible frame, not only the subtitle band;
4. remove source visual text, reconstruct the background when required, and render the target text in a stable, readable, spatially appropriate form;
5. use qualified burst GPU compute only when it provides measured value; local state remains authoritative and cloud unavailability must not corrupt the run.

## 2. Primary owner journey

1. Owner selects a source video.
2. AutoSub inspects source media and determines available localization paths.
3. Owner selects target language and localization mode.
4. If voice replacement is enabled, owner selects a real Kokoro voice, configures speed, and can preview the exact selected voice before Run.
5. Owner presses Run once.
6. AutoSub extracts canonical source evidence from audio and/or visible text, performs translation/localization, constructs spoken/subtitle/visual plans, executes eligible local/cloud workloads, renders, verifies, and repairs bounded machine-detectable defects.
7. AutoSub returns a final playable video only when material content, audio, visual-text and render integrity gates pass; otherwise it surfaces a bounded actionable failure/review state rather than claiming success.

## 3. Product modes

### 3.1 Subtitle Localization

Localize spoken/dialogue content into target-language timed subtitles while preserving the chosen source-audio policy.

### 3.2 Full Visual Localization

In addition to subtitle localization, detect relevant source-language text throughout the frame and replace it with target-language visual text.

### 3.3 Full Localization + Voice

Full Visual Localization plus target-language Kokoro narration using the run-level voice configuration.

These modes share the same canonical localization data model, translation quality rules, provenance, cache, compute routing and final quality gates.

## 4. Canonical domain model

### 4.1 LocalizationUnit

The central domain is `LocalizationUnit`, not raw OCR detections or subtitle cues.

Common logical fields include:

- immutable source identity/provenance;
- source text;
- semantic target translation;
- confidence/evidence;
- temporal scope;
- localization status;
- model/provider fingerprints;
- accepted output identity.

### 4.2 SpokenLocalizationUnit

Represents spoken/dialogue meaning. It may project into:

- `translated_text`: semantic source of truth for target meaning;
- `spoken_text`: text optimized for natural narration while preserving meaning;
- `subtitle_text`: text optimized for readability while preserving meaning;
- source and final timing;
- speaker/voice configuration when applicable.

A subtitle cue is not automatically a TTS synthesis unit.

### 4.3 VisualTextUnit / TextTrack

Represents a semantically consistent visible text instance over time. It contains:

- canonical source text derived from one or more frame observations;
- temporal start/end;
- tracked geometry/polygon/mask observations;
- text class such as dialogue subtitle, title, caption, sign, label, UI text, score/stat, chapter heading, speech bubble or decorative text;
- target display translation;
- preserve/protect/localize decision;
- replacement tier and QC state.

Translation and expensive visual processing operate primarily on `TextTrack`, not independently on every frame.

## 5. Source extraction and recognition quality

### 5.1 Speech recognition

Candidate quality policy:

`Whisper large-v3-turbo fast pass -> uncertainty detection -> Whisper large-v3 escalation only for uncertain spans`.

Observed benchmark on the same 45-second Chinese fixture:

- current local AutoSubs 3.8.0 `small`: ~43.39 s, 33 segments;
- Modal T4 Whisper large-v3-turbo: ~7.10 s inference; ~20.34 s model-load + inference; cold client wall time ~49.38 s;
- spot checks against visible source captions show materially better lexical/number recognition from Whisper on several cases.

Until V2 is accepted, AutoSubs remains an existing local fallback and V1 behavior is not removed.

### 5.2 Whole-frame visual OCR

Whole-frame OCR is required for Full Visual Localization. It must not assume all relevant text exists in a fixed subtitle ROI.

Current research evidence shows PP-OCRv6 on T4 can recover subtitle text plus Chinese/English UI labels, names, numbers and other visible text across representative AutoSub frames while returning polygons and confidence.

Raw OCR observations are evidence, not canonical units. Multi-frame temporal consensus/tracking resolves unstable small/mixed/rotated detections before translation or replacement.

## 6. Translation quality policy

Source-extraction mode and translation provider are independent decisions.

Candidate default:

`Quality Auto` uses the preferred qualified high-quality translation/refinement provider when ready, with explicit provider provenance. Local/open providers may be used only where their quality/license/runtime profile has been qualified.

No silent downgrade is allowed. Production evidence states the actual translation provider/model path used.

The canonical semantic translation is kept separate from display condensation or narration phrasing so layout/speech optimization cannot silently change meaning.

## 7. Subtitle quality contract — candidate baseline

For standard English target subtitle presentation, candidate baseline rules are:

- maximum 2 lines per event;
- target maximum approximately 42 characters per line;
- preferred reading speed <= 17 characters/second;
- 17–20 CPS may be conditionally accepted where meaning/timing preservation warrants it;
- >20 CPS enters automatic repair/review rather than unconditional success;
- ordinary events target a bounded readable duration approximately 0.83–7.0 seconds;
- segmentation favors semantic/clause boundaries and punctuation, not fixed-character slicing;
- no invalid/negative/zero timing, destructive overlap or dropped semantic content;
- line breaking avoids splitting tightly bound grammar/name/number units where practical.

Repair order should preserve meaning first: retain good cue -> semantic split/merge -> line reflow -> safe timing adjustment -> bounded condensation -> fail/review if still invalid.

Exact class-specific thresholds may be refined by benchmark before final freeze.

## 8. Kokoro voice contract

Kokoro uses real supported voice IDs from the installed model/runtime. No fake pitch/stability controls are exposed unless the actual engine supports them.

Voice is run-level configuration and appears in the main pre-Run workflow, not only Settings.

Before Run, owner can:

- choose an eligible Kokoro voice;
- see locale/gender style metadata where known;
- configure bounded speech speed;
- preview/listen to the current voice + speed configuration.

Changing voice or speed makes the existing preview stale.

Run snapshot includes at least provider/model identity, voice ID and base speed. If narration is enabled, final narration must use that snapshotted configuration subject only to an explicitly bounded auto-fit policy.

Preview availability is required; actually pressing Preview is not a mandatory human gate.

TTS planning is by sentence/clause speech blocks rather than blindly synthesizing every subtitle cue separately. When feasible, actual generated speech/token timing should inform final subtitle alignment.

If narration cannot naturally fit its allowed temporal budget, policy is bounded in this order: safe adjacent silence -> small speed adjustment -> semantically faithful compact spoken form -> fail/review. Truncation or overlapping narration is prohibited.

## 9. Full Visual Localization contract

### 9.1 Text selection

The system detects text throughout the full frame, then classifies each TextTrack as one of:

- localize;
- preserve/protected;
- uncertain/review.

Brand marks/logos and intentionally protected regions are preserved by default unless the product policy explicitly enables localization.

### 9.2 Replacement tiers

Use the least expensive reliable tier for each TextTrack:

**Tier A — deterministic replacement**
Stable/simple UI, subtitle bands, flat labels or other backgrounds that can be safely removed/restored with deterministic methods. Reuse and generalize existing AutoSub source-caption masking/delogo/render infrastructure where appropriate.

**Tier B — tracked inpainting**
Text over natural/moving backgrounds requiring image/video-aware reconstruction, followed by deterministic target text rendering whenever possible.

**Tier C — generative visual-text editing**
Only for visually complex/decorative cases that cannot meet acceptance through A/B. Model and complete weight/dependency license chain must pass commercial-use qualification first.

A/B/C are routing tiers, not separate user journeys.

### 9.3 Target text layout

The layout solver preserves meaning and visual hierarchy while fitting target text using a bounded sequence such as:

1. preserve intended scale/style where practical;
2. use available safe box expansion;
3. wrap at semantic boundaries;
4. reduce size within bounded readability limits;
5. use an approved semantically equivalent display condensation;
6. fail/review when no acceptable fit exists.

It must not silently shrink target text until unreadable or invent a materially different translation merely to fit.

### 9.4 Temporal stability

Replacement is generated from a persistent TextTrack identity. Geometry, mask, perspective and occlusion state may vary per frame, but semantic translation and style decisions should remain stable across the track.

Frame-independent re-generation that creates visible flicker is not acceptable.

### 9.5 Occlusion/depth

Where foreground objects cross source text, translated text must respect the corresponding visual ordering where the effect is material. Difficult occlusion may route to a higher tier or bounded review instead of producing obviously incorrect overlay ordering.

## 10. Closed-loop visual QC

After visual replacement/render, AutoSub scans the final output again.

For Full Visual Localization it must identify material residual source-language text not explicitly protected/accepted. Detected residuals link back to TextTrack/time/region and may trigger bounded automatic repair.

The existing AutoSub residual-CJK cleanup pattern is accepted prior art and should be generalized rather than replaced without reason.

A final machine PASS cannot coexist with known material unresolved source-language residuals.

## 11. Compute architecture

### 11.1 Local authority

Local AutoSub remains the authoritative control plane for:

- source and accepted artifact identity;
- run state;
- manifests/checksums;
- localization decisions;
- final render and acceptance state by default.

Cloud compute is an accelerator/capacity provider, not the source of truth.

### 11.2 ComputeProvider abstraction

Provider routing must not hard-code one vendor into product/business logic.

Candidate policy modes:

- `Local only`;
- `Auto — use qualified burst compute when materially beneficial`;
- `Cloud allowed, no paid overage`.

Provider pricing/quota/GPU SKU facts are runtime observations, never stable Product Requirements.

### 11.3 Current provider disposition

- Modal Starter: current primary automated commercial burst-compute candidate; live T4 and ASR/OCR feasibility demonstrated, final promotion gate still pending integrity/recovery/inpainting evidence.
- Kaggle standard consumer terms: research/non-commercial only for this project unless separate permission/agreement changes the constraint.
- Colab Free: manual research/benchmark only, not an automated production backend.
- Lightning Free: hold pending terms clarification before commercial production qualification.

### 11.4 Cloud data minimization

Do not upload full source video when a compact derived artifact can satisfy the workload.

Examples:

- ASR: locally extracted 16 kHz mono audio/chunks;
- OCR: selected/deduplicated frames or text-region crops;
- inpainting: bounded region/context frames + masks;
- translation: canonical text/context only.

Remote provider receives only the minimum payload necessary for the selected workload.

### 11.5 Resumable shards

A 1–2 hour owner batch session is an orchestration window, not one fault-domain transaction.

Remote work is split into independently identifiable, retryable shards. Each accepted shard has immutable input identity, model/parameter fingerprint, output manifest/checksum and local acceptance state.

Provider preemption/outage after some shards complete must not force accepted shards to recompute.

### 11.6 Content-addressed cache

Expensive deterministic/reproducible work uses content-addressed identities where practical, including input hash + model/revision + relevant settings.

Changing a downstream setting does not invalidate unrelated upstream work.

## 12. Provider security/integrity acceptance — candidate

A production cloud adapter cannot be marked qualified until it proves:

- intended automation is permitted under applicable provider terms;
- commercial use is compatible with the selected plan/use case;
- complete model/weight/dependency license chain is compatible with intended use;
- credentials stay outside Git/artifacts/logs;
- remote inputs are private according to the provider mode selected;
- input/output hashes are verified across the boundary;
- provider failure/preemption can be retried without duplicate/corrupt acceptance;
- local state remains recoverable when provider disappears;
- remote temporary artifacts have an explicit cleanup lifecycle;
- budget/quota exhaustion is surfaced as provider unavailability, not run corruption;
- `no paid overage` never silently becomes billable compute;
- at least one representative workload demonstrates material E2E quality/capacity/speed value before Auto routing defaults to that provider for that workload.

## 13. Quality-gate evidence

Do not invent a single opaque score such as `Subtitle Quality 94%`.

Final evidence reports measurable facts relevant to the enabled mode, for example:

- source/semantic localization coverage;
- empty/untranslated unit count;
- unresolved source-language unit count;
- cue overlap/invalid timing count;
- subtitle lines/read-speed violations;
- TTS overflow/alignment state;
- detected/translated/protected/unresolved visual TextTrack counts;
- final residual source-language count;
- visual replacement failures/drift/flicker/fit/occlusion issues where measurable;
- rendered cue/track counts;
- output media validation;
- actual providers/models/fallbacks used.

Machine evidence is truthful and tied to artifacts/run identity.

## 14. Completion invariant

A run may report `COMPLETE` only when all material gates required by its enabled mode pass.

Conceptually:

`CONTENT_VALID && SUBTITLE_VALID && (AUDIO_VALID when narration enabled) && (VISUAL_LOCALIZATION_VALID when full visual mode enabled) && RENDER_VALID`.

Material unresolved failures cause a bounded FAIL/REVIEW state, not false COMPLETE.

## 15. Non-goals for the first V2 release

Unless later evidence demonstrates necessity, first V2 does not require:

- replacing local control plane with a cloud backend;
- cloud render of the complete final video;
- cloud Kokoro for ordinary preview/production;
- dependence on any one free quota/provider;
- mandatory scene/shot-boundary computer-vision subsystem;
- perfect localization of every logo/brand/decorative mark;
- a heavy generative model for text cases solvable deterministically;
- non-commercial-restricted model weights in the default monetized/commercial production path;
- a mandatory human Preview click before Run.

## 16. Remaining pre-freeze evidence

Before converting this draft into `TASK.md` + stable quality contract/ADRs, finish only the bounded evidence still capable of changing architecture:

1. compare PP-OCRv6 small vs medium on the same representative whole-frame fixture and choose cascade/default criteria;
2. run a commercially compatible inpainting candidate on real source-text regions and scan the output for residual text / obvious reconstruction failure;
3. prove a minimal temporal TextTrack/dedupe strategy on a dense sequence, including reduction in repeated OCR/translation work and canonical-text stability;
4. demonstrate one remote integrity/retry/cleanup flow or document the exact external configuration that must be owner-verified at provider enablement;
5. decide which quality thresholds are hard gates vs warning/escalation using observed fixture behavior.

After these items, stop provider/model exploration unless a blocker emerges. Freeze the contract and implement in bounded stages.

## 17. Repository safety

The working tree already contains unrelated in-progress Production Monitor / translation-quality changes. V2 research must not overwrite, revert or accidentally commit them.

All spike code/results under `data/provider_qualification/` are disposable/ignored. Durable architectural conclusions belong in tracked docs.
