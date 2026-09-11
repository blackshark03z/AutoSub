# Goal

Deliver **AutoSub Localization V2** as a Windows-first, local-authoritative, one-click video-localization workflow that materially improves both **quality and throughput** over the current subtitle-only path.

From one source video, the owner can choose target language and one of three product modes:

- **Subtitle Localization** — high-quality target subtitles;
- **Full Visual Localization** — subtitles plus relevant source-language text anywhere in the visible frame replaced by target-language text;
- **Full Localization + Voice** — Full Visual Localization plus target-language Kokoro narration using the run-level selected voice.

Normal use remains: **configure -> Run once -> Production Monitor -> validated final video**. AutoSub is not becoming a full subtitle/video editor. Machine-detectable problems are repaired automatically within bounded policy; material unresolved quality problems fail/review truthfully instead of producing a false `COMPLETE`.

Detailed invariants and thresholds are authoritative in `docs/LOCALIZATION_QUALITY_CONTRACT.md`. Provider/model research evidence is preserved in `docs/DRAFT_LOCALIZATION_V2_PROVIDER_QUALIFICATION.md`.

# Critical User Journey

1. Owner opens **Tạo Video**, selects a source video and target language.
2. Owner selects Subtitle Localization, Full Visual Localization, or Full Localization + Voice.
3. When voice is enabled, the same pre-Run screen exposes the real Kokoro voice, bounded speed configuration, and **Nghe thử** for the exact current voice/speed. Voice choice is not hidden only in Settings.
4. Advanced compute policy defaults to a simple owner-facing choice such as Auto / Local only / Cloud allowed, no paid overage; provider details remain secondary diagnostics.
5. Owner presses **Run** once.
6. AutoSub preserves raw source evidence, aligns/fuses speech and visible-text evidence where available, translates/localizes semantic units, constructs subtitle/speech/visual replacement plans, routes qualified compute, renders and runs closed-loop QC.
7. Production Monitor shows truthful current work, actual providers/models/fallbacks, progress/evidence and any bounded repair/escalation. It does not invent percentages or opaque quality scores.
8. On the representative golden input, the normal path reaches a playable final video without a mandatory manual edit/review step and reports the applicable content/subtitle/audio/visual/render gates as PASS.
9. After Run A completes, owner can immediately create Run B with different mode/voice/settings; Run B receives its own immutable snapshot while safe content-addressed cache reuse may persist. Run A history/artifacts remain bound to Run A.

# Acceptance

- **A1 — Pre-Run product configuration:** target language, localization mode and all controls required to determine the run are visible/reachable before Run. Voice-enabled mode exposes real Kokoro voice + speed + preview in the main workflow.
- **A2 — Evidence-preserving source model:** raw ASR/OCR observations are retained with provenance. Speech evidence and burned-in dialogue-caption evidence are not destructively collapsed when they disagree; disagreement is an escalation signal.
- **A3 — ASR quality route:** qualified GPU Whisper large-v3-turbo is the fast quality candidate when available/beneficial; difficult/uncertain spans can escalate to large-v3 or corroborating evidence. Current local AutoSubs-small remains an offline/fallback path, not the sole V2 quality oracle.
- **A4 — Subtitle quality:** final English dialogue subtitles satisfy the hard gates in `LOCALIZATION_QUALITY_CONTRACT.md`, including semantic segmentation, valid timing, maximum two lines, line/read-speed bounds, no dropped semantic units and no unresolved TTS/subtitle alignment failure when narration is enabled.
- **A5 — Voice integrity:** changing voice/speed invalidates stale preview. Run snapshots provider/model/voice/speed. Final narration, when enabled, uses that snapshot and never truncates/overlaps speech to fake timing success.
- **A6 — Whole-frame visual discovery:** Full Visual modes detect relevant source-language text across the frame, not only a fixed lower subtitle ROI. OCR observations provide geometry/confidence and are consolidated into temporal `TextTrack` identities before translation/replacement.
- **A7 — Correct text scope:** localizable semantic source-language text (dialogue captions, UI labels, titles/captions, signs, chapter text, etc.) is localized. Existing target-language text, usernames/handles/IDs, non-semantic OCR noise, and protected logos/brand marks are preserved by policy unless explicitly classified otherwise. Mixed strings preserve identifiers/numbers while localizing semantic source-language portions as appropriate.
- **A8 — Visual replacement quality:** Full Visual mode removes the source text for each accepted localizable TextTrack and renders target text in a spatially faithful, readable form. Easy regions use deterministic Tier A; natural backgrounds may use Tier B inpainting; difficult moving/occluded/decorative cases escalate rather than silently degrade.
- **A9 — Closed-loop residual QC:** final Full Visual output is rescanned. Known material unprotected source-language residuals, target text clipping, unresolved replacement tracks or failed visual-integrity gates prevent false `COMPLETE` and link to the responsible track/time/region.
- **A10 — Translation integrity:** extraction mode and translation provider are independent. Semantic translation is kept separate from display/narration condensation. No silent provider downgrade; final evidence identifies the provider/model/fallback path actually used.
- **A11 — Qualified compute only:** cloud work runs only through a provider/model combination that passes applicable automation/commercial-use, privacy, license-chain, secret, integrity and recovery gates. Local state remains authoritative. Provider outage/quota exhaustion cannot corrupt accepted local work.
- **A12 — Idempotent/resumable shards:** accepted cloud shards have immutable input identity, model/settings fingerprint and verified output hashes. Retry/preemption reuses accepted work and does not duplicate/corrupt it. Remote temporary artifacts have explicit cleanup state.
- **A13 — Speed/economy truth:** Auto routing includes upload/startup/model-load/download overhead, not inference-only marketing numbers. A workload is routed to cloud by default only after representative evidence shows material E2E speed/capacity value; micro-workloads may stay local. Stable TextTracks are not repeatedly translated/replanned per frame.
- **A14 — Truthful monitor/QC:** UI reports measurable facts (coverage, counts, timing/readability violations, provider/cache/retry state, unresolved tracks, residual language, TTS overflow/alignment, render validation). No fabricated single-number quality score.
- **A15 — Golden E2E:** the full representative source completes through the real Simple UI in each enabled release mode being claimed, with validated MP4 and evidence-bound QC. Full Visual + Voice must prove the composed journey, not isolated OCR/TTS/model tests.
- **A16 — Repeat-use isolation:** Run A -> COMPLETE -> new Run B proves no stale source/voice/track/job state becomes active in B. Immutable A artifacts/configuration remain bound to A; only explicitly reusable caches survive.
- **A17 — Regression/release:** focused tests, relevant existing OCR/audio/monitor tests, full `python -m pytest -q`, docs/storage gates and real Product CUJ evidence pass from one canonical candidate HEAD before a stable V2 release claim. Owner real-use remains the final oracle for subjective visual/audio quality.

# Acceptance Fixture / Golden Input

Primary real fixture already present on the AutoSub machine:

- source SHA-256: `b043a858225a24c6beaa915bdc0ccb82874cac0b6363b809227519276a26bd8e`
- source size: 181,905,545 bytes
- duration: ~458.3 s
- resolution: 1920x1080
- contains Chinese speech/dialogue captions plus whole-frame game/UI text suitable for ASR, subtitle, visual-text and localization testing.

Research sub-fixtures derived from the same source are valid engineering evidence, including the 45 s audio sample, nine whole-frame OCR frames, the dense 4 s / 20-frame TextTrack sequence, and bounded real-region inpainting samples. Final Product Acceptance still requires the composed real-video journey, not only these sub-fixtures.

# Non-goals

- Full subtitle/video timeline editor or mandatory manual approval queue.
- Cloud-hosted source of truth or cloud-only product operation.
- Uploading/rendering the entire source video in cloud merely to consume free GPU when compact artifacts/local render suffice.
- Hard dependency on one provider, free quota, GPU SKU or current provider pricing.
- Translating every detected glyph, username, ID, existing target-language string, logo or brand mark blindly.
- Running the heaviest OCR/segmentation/inpainting/generative model on every frame/track.
- Mandatory shot-boundary CV subsystem in the first V2 release unless implementation evidence proves it necessary for acceptance.
- Non-commercial-restricted model weights in the default monetized/commercial path.
- Fake quality percentages or provider-success claims unsupported by artifact evidence.

# Constraints

- Windows-first, local single-user product; local run/artifact state is authoritative.
- Preserve the accepted one-click normal journey and progressive disclosure. Exceptional quality failures may enter bounded repair/review, but the golden normal path must not require manual editing.
- Run configuration becomes immutable at Start; terminal runs do not leak active state into the next run.
- Secrets/tokens never enter Git, run artifacts, captured provider payloads or user-visible raw logs.
- Provider terms and model/weight/dependency licenses are independent qualification gates; intended commercial/monetized use must be compatible.
- `Cloud allowed, no paid overage` must fail closed if the provider-side budget/spend protection cannot be verified/configured; it must never silently authorize paid overage.
- Cloud transfer minimizes data: audio/chunks, selected frames/crops/masks/manifests rather than full video when technically sufficient.
- Use `REUSE -> WIRE -> FIX -> ADD`: generalize existing source-caption mask/render/residual-QC capabilities before building parallel replacements.
- Pre-existing dirty Production Monitor/translation-quality changes in the working tree predate this V2 freeze. Do not overwrite/revert/fold them into V2 accidentally; reconcile them before the first V2 production-code change.

# Material Decisions

- Central domain becomes `LocalizationUnit` with `SpokenLocalizationUnit` and temporal `VisualTextUnit/TextTrack` projections; raw OCR frame detections and subtitle cues are evidence/presentation units, not the product domain.
- Speech ASR and burned-in dialogue captions remain independent evidence channels. Align/fuse them semantically; do not arbitrarily choose one when they materially disagree.
- Full Visual Localization is source-matched replacement by default: erase relevant source text and place target text at the corresponding visual role/location. Subtitle-only mode may use the standard readable lower-third presentation.
- PP-OCRv6 small is the whole-frame fast pass; medium is escalation for uncertainty/disagreement/difficult text.
- Whisper large-v3-turbo is the GPU fast-quality ASR candidate; large-v3 is escalation; AutoSubs-small stays local/offline fallback.
- Kokoro remains local-first for preview and normal narration. Voice is run-level configuration on the pre-Run surface.
- Visual cleanup uses tier routing: deterministic Tier A -> commercially compatible inpainting/temporal reconstruction Tier B -> difficult higher-tier/review. Heavy generative editing is not the baseline.
- `TextTrack` dedupe/consensus is mandatory; repeated observations establish confidence but translation/style/replacement planning is performed once per accepted identity. Non-contiguous identical text may reuse translation memory without becoming the same geometry track.
- Local control plane + qualified optional burst compute. Modal is currently the primary automated cloud candidate from live evidence; Kaggle standard consumer use is research/non-commercial only for this project; Colab Free is manual research only.
- Cloud shard protocol is hash-bound, idempotent, resumable and explicitly cleaned after local verification.

Durable rationale is recorded in `docs/DECISIONS/0008-localization-quality-v2.md` and `docs/DECISIONS/0009-qualified-burst-compute.md`.

# Progress

- Provider/model qualification round completed sufficiently for architecture freeze.
- Real Modal T4 CUDA, Whisper, PP-OCRv6 whole-frame OCR, LaMa text-removal, dense TextTrack dedupe and staged Volume integrity/idempotency/cleanup probes completed.
- Frozen Goal/Acceptance, quality contract and ADRs are persisted in the repository; research drafts remain supporting evidence only.
- V2 implementation has begun from foundations rather than UI-only controls: normalized run-level localization/compute/voice settings, deterministic immutable run snapshot identity, output-reuse compatibility, evidence-based English subtitle hard gates, a provider-agnostic hash-bound `ComputeShard` contract, and temporal `TextTrack` identity/consensus primitives are implemented with focused tests.
- Local Kokoro runtime discovery is wired against the installed `kokoro 0.9.4` / `hexgrad/Kokoro-82M` snapshot. The catalog exposes the 28 official English voices with cached state. Real `bm_george` preview synthesis produced 24 kHz WAV + token timings; repeated preview hit the content-addressed cache in ~0.14 s. Explicit on-demand preparation of an uncached official voice (`bf_emma`) also passed.
- Kokoro catalog/preview backend API is implemented, but the main pre-Run UI and final narration binding are not yet accepted; do not expose a mode as complete until composed processing exists.
- Whole-frame OCR provider payloads now have a production-domain adapter that requires authoritative video timestamps, converts polygons into immutable observations, consolidates them into temporal TextTracks, and applies conservative source-script classification. Provider inference timing is never mistaken for media time.
- English V2 subtitle quality evidence is now persisted per run and surfaced through run/monitor evidence. It remains intentionally non-enforcing until the bounded automatic repair path is wired, so stricter V2 rules cannot silently break the existing one-click workflow.
- Public run creation now fails closed for Full Visual / Full Localization + Voice unless the explicit experimental gate is enabled; the product cannot claim an uncomposed mode and then silently emit subtitle-only output.
- The TTS timeline schema was reconciled with `segment_text_spans`, enabling Kokoro token-time retiming while preserving existing timeline validation. Full repository `python -m pytest -q` passed after this reconciliation, and the public-mode guard focused suite also passed.
- Pre-existing Production Monitor/translation-quality working-tree changes were preserved and their focused tests pass.
- Real **12 s Full Visual composed mini E2E PASS** on the golden-derived fixture: 84 source TextTracks, 29 localizable, 29 translated, 29 replaced, and 0 final source-language residuals.
- Real **12 s Full Localization + Voice composed mini E2E PASS** with `bm_george` at base speed `1.0`: Kokoro PCM fit PASS at bounded effective speed `1.036`, all 7 cues token-aligned, subtitle hard gates PASS after one bounded 62 ms display-boundary adjustment, 29/29 visual replacements PASS, 0 residual source tracks, and final output/evidence acceptance bindings match the immutable run snapshot.
- Full repository `python -m pytest -q` plus `git diff --check` PASS after the composed mini fixes.
- The main remaining engineering issue before public V2 exposure is end-to-end throughput on longer/full runs; architecture and 12 s composed quality are already proven.
- Throughput checkpoint: conservative visual-fingerprint reuse was implemented behind the existing 0.5 s authoritative sampling timeline and contract-tested, but a real 45 s golden-source probe selected all 90/90 frames (0% reuse). Dynamic game footage therefore proves whole-frame fingerprint dedupe is **not** the primary golden-path speed solution; it remains only a conservative optimization for sufficiently stable footage.
- Local OCR startup overhead was then removed as the next primary bottleneck. On the same nine golden full frames, the old 3-frame/process route took 19.18 s while one persistent worker took 8.47 s (~2.27x faster). A real 90-frame / 45 s golden full-frame production probe completed 90/90 frames, 1,191 OCR items, in 94.22 s with one bounded batch and zero fallback splits. Production now uses a 90-frame bounded persistent batch and recursively bisects only on OCR runtime failure, retaining <=3 frames as the known-stable floor.
- Cloud OCR remains bounded into independent 64-frame hash-bound shards before Modal execution. Large workloads therefore retain per-shard cache/retry/resume granularity, but cloud is optional rather than required for release throughput.
- Live Modal runtime is available, automated and commercially allowed, but current Starter pricing exposes included credits without Starter environment-level budgets. Therefore `no_paid_overage_verified` remains false under the frozen fail-closed policy; no paid-capable cloud run is authorized merely because free credits remain. Auto stays local unless a real provider-side hard budget/spend protection is verified.
- Public Simple UI was reconciled back to the accepted V1 one-click contract after regression caught premature V2 exposure. V2 mode/compute controls remain non-public while the backend experimental path and tests stay intact behind the explicit gate.
- Current checkpoint verification on 2026-09-11: the four previously failing public-UI contract tests PASS, the focused Localization V2 suite PASSes 123/123, full repository `python -m pytest -q` PASSes, and `git diff --check` PASSes.
- First real **45 s Full Localization + Voice** production attempt intentionally failed closed at the narration quality gate after ~404 s. Exactly one final TTS synthesis unit exceeded its real PCM slot by only 83 ms after bounded speed/slack repair; AutoSub correctly refused to truncate, overlap, or fake completion.
- The failure exposed a missing V2 wiring seam rather than a relaxed-quality requirement: semantic translation, subtitle display text and narration `spoken_text` are distinct projections, but Simple Workflow had been feeding the subtitle wording directly into Kokoro. A bounded narration-only repair now reprojects only the failed TTS unit through the qualified Gemini caption translator, keeps `resolved_text`/`translation_text` unchanged, preserves numeric material facts, refuses >15% automatic condensation, and remains fail-closed if the repaired unit still cannot fit.
- Real 45 s repair evidence PASS: failed unit `ttsu_4f5778919d50` condensed from 123 to 115 joined characters against a 116-character unit budget while preserving `2500`; Kokoro then produced 8,025 ms PCM inside an 8,027 ms slot at bounded speed 1.2, zero overflow, and 26/26 cues were token-aligned. All three narration units PASS. Narration repair telemetry is persisted separately and surfaced in V2 monitor evidence/provider-call accounting.
- Full repository `python -m pytest -q` plus `git diff --check` PASS after the bounded narration-projection repair and telemetry wiring.
- The next real 45 s child run exposed and fixed a deterministic FFmpeg `delogo` edge contract: `delogo` rejects masks touching any input boundary. Tier A now pads a one-pixel guard frame, smears source-edge pixels into it, shifts all masks by +1, crops back to exact source geometry, then renders the existing ASS. Real 45 s graph validation PASSed all 152 replacements with zero escalation, and full repository regression PASSed at commit `fbde4885324c7c8e9cf9879630abe0d65d90dcd5`.
- That child then reached final residual OCR/QC rather than failing render. First-pass QC exposed two further contract gaps: moving-track segment replacements were not credited to their `parent_track_id`, and replacement lifetime used only half the 0.5 s sampling uncertainty while QC matched across the full 0.5 s tolerance. Both are corrected: moving segments satisfy parent plan coverage and Tier-A replacement lifetime is now ±`sample_seconds`.
- Real 45 s replay after the temporal/QC fix reduced final source-language residuals from 12 to 8. A bounded diagnostic second pass derived mask-only repairs from those eight verified final residual OCR regions/times, re-rendered from the same pre-visual source with the original target-language ASS only once, then rescanned. Result: `quality_status=PASS`, `eligible=true`, residual source tracks `8 -> 0`, failure count `0`.
- Production now implements that behavior as a single bounded residual repair pass: it runs only when first-pass QC failures are exclusively `SOURCE_LANGUAGE_RESIDUAL`, caps repair masks, never duplicates target text, never loops, rescans once, and still fails closed on persistent/non-residual failures. Focused visual pipeline/QC/replacement tests PASS 17/17; full repository `python -m pytest -q` and `git diff --check` PASS.
- Fresh immutable **45 s Full Localization + Voice production child PASS**: run `run_20260911144528575539_c60b7006` completed through the real Simple Workflow. Visual QC PASSed 142 expected localized tracks / 152 planned replacements / 0 unresolved replacements / 0 final residual source tracks. First-pass residual count was 8; the single bounded repair pass used 8 mask-only repairs and reduced residuals `8 -> 0`. Narration PASSed 3/3 TTS units with 26/26 cues token-aligned and zero overflow; subtitle content/caption-integrity gates PASSed 26/26. Acceptance binding source/settings/output hashes are present and agree with the run manifest.
- Independent final-media verification for that 45 s child PASS: `final_video.mp4` is H.264 + AAC, 1920x1080, 45.067 s, 27,539,551 bytes, SHA-256 `12bfd9f2505294d3383c3b604d7d49006c207209067a86ba254cc80648348e08`, exactly matching the V2 acceptance binding.
- Real **120 s Full Localization + Voice** retry after subtitle-display repair advanced beyond the previous subtitle-quality failure but failed later in `Localize visual text`. The exact semantic-guard root cause was OCR placeholder noise: source tracks containing only `口` / repeated `口` were misclassified as semantic CJK, causing Gemini to return `[Unintelligible]`. `classify_visual_track_text` now preserves `口+` placeholder candidates while real text such as `入口`, `跳`, and `强化身体` remains localizable. Production-equivalent replay reduced the visual translation request set from 88 to 86 unique source strings and the corrected failed batch PASSed Gemini semantic validation.
- The same 120 s run exposed the dominant local throughput costs by artifact timestamps: ASR ~4.5 min, narration ~16 min, subtitle-stage render ~2 min, and source+refinement OCR ~12 min before visual translation. Narration produced 20 Kokoro generation attempts for 7 final units because each attempt cold-loaded the model/voice. A bounded per-narration persistent Kokoro worker now keeps the model warm without changing TTS cache/DB/single-flight semantics; benchmark evidence: model load + first real unit 21.29 s, next same-process unit 9.13 s, versus roughly 30-55 s spacing per previous cold attempt.
- Local PaddleOCR now has an analogous bounded per-Full-Visual persistent worker while retaining the 90-frame batch contract and recursive <=3-frame fallback. Dense-fixture benchmark: 20 frames cold 10.945 s, repeated same-process pass 4.312 s. This removes batch reload overhead but does not weaken OCR coverage.
- Recognition refinement remains intentionally unchanged for now: the 120 s evidence refined 84 tracks / 252 crop variants; 42/84 canonical readings changed and 29/84 ended as ignore/preserve, so the 0.90 attention threshold and three-variant consensus are materially useful and will not be reduced without new comparative evidence.
- **No V2 stable release or Product Acceptance is claimed yet.** The composed 45 s proof is now complete; remaining evidence is a longer real slice, the full 458 s golden source, and Run A -> Run B repeat-use/isolation before public V2 exposure.

# Discoveries / Blockers

- Cold cloud startup is material; useful batching/prebuilt images/model cache are required for speed.
- PP-OCRv6 medium is not enough better on the current fixture to justify always-on use; use escalation.
- Per-frame inpainting/removal success does not prove temporal visual quality. Human real-use remains required for flicker/background reconstruction on representative difficult tracks.
- Modal Starter currently lacks the provider-side hard budget feature required by the frozen `no paid overage` contract. This is a provider-plan constraint, not a reason to weaken the product safety gate.

# Next Safe Action

1. Keep whole-frame fingerprint reuse as a secondary optimization only; the real 45 s golden probe showed 0% reuse on dynamic footage.
2. Treat the new bounded persistent local OCR path as the release baseline. The 45 s composed child now PASSes; preserve its source/settings/output hashes and repair evidence as the short golden checkpoint.
3. Run a longer real golden-derived slice without changing the authoritative 0.5 s sampling interval or compute policy. Require the same narration/visual/subtitle/media/binding gates and verify that bounded residual repair remains a single pass with a safe mask count.
4. If the longer slice passes, run the full 458 s golden source locally. Only after full-golden evidence is captured should sampling/throughput policy be reconsidered. Modal cloud may be benchmarked later only when provider-side hard budget/spend protection is actually available and verified.
5. Only after longer/full throughput and quality gates pass should the public guard be removed and pre-Run mode/voice controls be exposed.
6. Preserve clean Git checkpoints and do not regress the accepted V1 public UI while V2 remains experimental.
