# RESEARCH EVIDENCE — AutoSub Localization Quality V2 / Provider Qualification

> Status: RESEARCH CLOSED FOR INITIAL V2 FREEZE — supporting evidence, not the implementation contract.
>
> Source of truth: `TASK.md`, `docs/LOCALIZATION_QUALITY_CONTRACT.md`, `docs/DECISIONS/0008-localization-quality-v2.md`, and `docs/DECISIONS/0009-qualified-burst-compute.md`.
>
> Purpose: preserve provider/model probes, rejected assumptions, measured evidence and rationale so a new chat or worker can audit why the frozen contract was chosen. New evidence may reopen only the affected decision.

## 1. Product direction currently agreed

AutoSub is evolving from a subtitle-only workflow into a local-first video localization system.

Candidate end-state:

`source video -> source understanding -> translation/localization -> voice -> timed subtitle -> visual-text replacement -> render -> evidence-based QC`

The system should support three user-facing scopes:

1. Subtitle Localization — spoken/dialogue subtitles only.
2. Full Visual Localization — detect, translate, remove and replace source-language text visible in the image.
3. Full Localization + Voice — visual localization + subtitle localization + Kokoro narration.

The one-click product principle remains: configure before Run, press Run once, then observe truthful progress and receive a validated result. AutoSub must not become a full subtitle/video editor.

## 2. Domain model direction

Do not use “subtitle cue” as the universal processing unit.

Use a localization domain with at least two projections:

### SpokenLocalizationUnit

Carries source text/timing, translated meaning, spoken text, subtitle text, speaker, voice configuration, provenance, and final timing.

Important invariant:

`translated_text` is the semantic source of truth.

`spoken_text` and `subtitle_text` may differ for natural speech/readability but must preserve the same meaning.

Subtitle cue != TTS synthesis unit.

### VisualTextUnit

Represents a tracked piece of visible source-language text across one or more frames.

Expected data includes source OCR text + confidence/provenance, translated/display text, text type, track/frame interval, geometry/mask, style/layout constraints, protected/replace decision, replacement method, and final QC evidence.

Translation should normally happen once per stable text track, not once per frame.

## 3. Candidate quality pipeline

### Spoken/subtitle path

`audio/subtitle source -> canonical source text -> translation -> semantic segmentation -> subtitle plan + speech plan -> Kokoro -> token/audio timing -> final timed captions -> render -> QC`

Candidate principles:

- semantic/clause-aware segmentation
- no naive fixed-character splitting
- max 2 subtitle lines
- target profile around <= 42 characters/line for English
- reading speed target <= 17 CPS; >20 CPS requires repair or explicit fail
- normal event duration roughly 0.83–7 s
- no overlap/caption loss
- preserve meaning when compacting
- TTS overflow must not be silently truncated or overlapped
- final subtitle timing should align to generated target speech where voice replacement is enabled
- quality gate reports evidence, not invented scores

Exact thresholds remain candidate values until the contract is frozen.

### Full visual localization path

`frame/text detection -> temporal tracking/dedupe -> OCR + temporal consensus -> classify text -> translate once per track -> remove source pixels -> reconstruct background -> render target text -> temporal QC -> final residual-language scan`

Do not process each frame independently when the same text persists across frames.

Replacement tiers:

- Tier A — deterministic replacement for simple subtitles/UI/flat backgrounds
- Tier B — tracked inpainting for natural/moving backgrounds
- Tier C — generative visual-text editing only for hard artistic cases

Do not send most easy text through a heavy generative model.

Default protection policy should avoid blindly replacing logos/brand marks or explicitly protected regions.

Final closed-loop QC should be able to OCR the rendered result and flag residual source-language text with timestamp/region evidence.

## 4. Voice direction

Use existing Kokoro voices, not voice cloning/blending for V1.

- Kokoro uses its dedicated local runtime.
- Preview is local-first for low latency.
- The active run-level voice must be selectable on the main Create Video screen before Run, not only in Settings.
- User can preview the selected voice before Run.
- Preview is not a mandatory human approval gate.
- Run snapshot must include provider/model identity, voice_id and speed.
- Changing voice/speed invalidates an old preview representing the previous config.

Keep Kokoro local by default unless later benchmark evidence shows cloud batch synthesis has material E2E value.

## 5. Compute architecture direction

Cloud GPU is an OPTIONAL burst accelerator, never the source of truth.

Local machine remains the control plane for run state, source/final artifacts, checkpoints, acceptance/QC evidence, and recovery logic.

Candidate abstraction:

`ComputeProvider -> LocalProvider | KaggleBatchProvider | ModalProvider | ColabManualProvider`

Do not hard-code provider policy into localization business logic.

Cloud jobs should receive compact artifacts whenever practical:

- extracted audio rather than full video for ASR
- deduplicated text crops/tracks/masks rather than every full frame for OCR/inpainting
- resumable shards rather than one irreplaceable multi-hour transaction

Required cross-provider properties:

- idempotent/resumable shards
- content-addressed compute cache
- checksum/result manifests
- model/version fingerprint
- provider capability discovery
- safe fallback when provider/quota is unavailable
- no silent downgrade
- no cloud-only irreplaceable state

Compute routing should compare observed E2E cost/time, including upload, queue, cold start, inference and download — not only raw GPU inference speed.

## 6. Current provider disposition — candidate, NOT final

### Kaggle

Candidate primary free burst-compute provider. Must be qualified in practice before product freeze.

### Modal

Candidate operational fallback/serverless provider when automation semantics or predictable startup are more important than free-notebook behavior. Do not make product requirements depend on promotional/free-credit amounts.

### Google Colab Free

Candidate manual benchmark/emergency environment only. Do not treat Colab Free as an automated production backend unless current policy explicitly permits the integration pattern.

### Local

Must remain a valid execution path/fallback for supported workloads.

## 7. Provider Qualification blockers before final freeze

A provider may become a production `BurstComputeProvider` only after evidence for all relevant gates:

1. Automation/headless use is permitted by current provider terms/policy.
2. Intended commercial workflow is permitted.
3. Uploaded job artifacts can be private with understood retention/deletion behavior.
4. Actual GPU/runtime compatibility is verified with target libraries/models.
5. Internet/model-download behavior is understood.
6. Input/output limits and practical batch/shard limits are measured.
7. Queue + cold start + upload + download + inference E2E time is measured.
8. Preemption/runtime termination behavior is tested.
9. Retry/idempotency/resume behavior is tested.
10. Rate limits/status polling behavior are understood.
11. Secrets are not persisted in artifacts/logs.
12. Remote cleanup is verified.
13. Model licenses are independently qualified for intended use.
14. The provider shows an actual E2E advantage for at least one eligible workload.

Important: provider permission != model license permission.

A technically usable model is not a production candidate if its license is incompatible with intended commercial use.

Do NOT freeze mutable provider facts such as fixed weekly GPU hours, a guaranteed GPU SKU, or a permanent free-credit amount into Product Requirements. Those belong in observed capability/evidence, not product invariants.

## 8. Candidate workload/model routing to research

These are candidates, not selected production dependencies.

### ASR

Research a cascade:

`fast/high-throughput pass -> uncertainty detector -> expensive quality pass only for uncertain spans`

Whisper large-v3-turbo / large-v3 are candidates to benchmark on GPU.

### OCR / visual text

Research GPU OCR plus temporal consensus and tracked text regions. The major optimization is temporal dedupe/consensus, not merely CPU OCR -> GPU OCR.

### Translation

Keep extraction mode independent from translation-provider choice.

Candidate policy:

`Quality Auto -> preferred high-quality provider; open/local model only after benchmark; offline local fallback`

Do not promote an open model solely because free GPU is available.

### Inpainting / visual replacement

Benchmark only models whose licensing is compatible with intended usage. Prefer deterministic rendering for easy text and heavy inpainting/generative editing only when required.

### TTS

Kokoro remains local-first; benchmark cloud only if large-batch evidence justifies it.

### Final render

Prefer local render/hardware encoding. Do not upload/download full video merely to consume free GPU unless measured E2E evidence justifies it.

## 9. Required benchmark style

Do not use synthetic model-only speed claims as acceptance evidence.

Use representative AutoSub videos and measure quality errors, unresolved/low-confidence regions, total wall-clock time, local preprocessing, upload, queue/cold-start, model load, inference, download, cache reuse, retry/resume, GPU/VRAM, payload sizes, and remote cleanup.

For full visual localization, count text tracks and changed regions — not only frames.

## 10. Candidate acceptance shape

Final acceptance is not yet frozen, but must cover:

### Content integrity

- canonical source text established with provenance
- semantic translation coverage
- no unintended empty/untranslated units
- no material residual source-language text after full visual localization, except protected/accepted regions

### Subtitle readability/timing

- bounded lines/read speed
- no overlap/loss
- semantic segmentation
- final alignment to chosen audio policy

### Voice

- selected Kokoro voice can be previewed before Run
- exact run voice/speed is snapshotted
- final narration, when enabled, is generated from the same configuration
- overflow/fitting failures are surfaced truthfully

### Visual replacement

- source text removal
- translated text fit
- track stability / low flicker
- acceptable background reconstruction
- geometry/perspective handling
- occlusion ordering where required
- residual-language final scan

### Compute/provider robustness

- provider unavailable/quota exhausted does not corrupt the run
- completed shards remain reusable
- checksums/manifests verified
- no secret leakage
- fallback/retry path is explicit

### Final output

- validated playable output
- truthful QC evidence
- fail-closed on material integrity failures

## 11. Explicit non-decisions / open research

Do not assume these are decided yet:

- exact production GPU provider
- exact inpainting/video tracking stack
- exact open translation model
- exact shot-boundary policy
- whether cloud TTS is worthwhile
- exact threshold values for every text/layout/timing class
- whether difficult visual occlusion becomes V1 or later tier
- provider quotas/pricing/GPU SKU guarantees

## 12. Immediate next step

Run one final provider/model qualification round before freezing Requirement/Acceptance.

Priority:

1. Qualify Kaggle on automation/policy/privacy/runtime/model compatibility.
2. Run a minimal real GPU spike: upload -> private job -> CUDA probe -> representative ASR/OCR/inpainting sample -> manifest -> download -> cleanup.
3. Measure full E2E time, failure/retry/resume behavior.
4. Independently verify candidate model licenses.
5. Compare with Local and one operational fallback where useful.
6. Convert confirmed decisions into `TASK.md`, `docs/SUBTITLE_AUDIO_QUALITY_CONTRACT.md`, an ADR for localization-quality/Kokoro, and an ADR for burst-compute routing.

Only after those are frozen should large V2 implementation begin.

## 13. Repository safety note

At creation time, the working tree already contains unrelated in-progress Production Monitor / translation-quality modifications. Do not overwrite, revert or fold them into this discovery document work without explicit technical reason.

This draft exists to preserve direction, not to supersede accepted stable behavior prematurely.
## 14. Provider/model qualification findings — 2026-09-10

These findings supersede the earlier *candidate* ranking where they conflict. They are still pre-freeze evidence, not Product Requirements.

### Compute providers

#### Kaggle — FAIL for intended commercial production use

Current Kaggle Terms of Use (effective 2025-06-22) state that the Services are to be used for the user's own internal, personal, **non-commercial** use and not on behalf of or for the benefit of a third party.

Decision:

- Do not make Kaggle a production `BurstComputeProvider` for commercial/monetized AutoSub workflows under the standard consumer terms.
- Kaggle may remain a non-commercial research/benchmark environment where the actual use complies with the terms.
- Do not spend implementation effort on a production Kaggle adapter unless a separate agreement/permission changes this constraint.

Technical note for research only: the current Kaggle CLI supports kernel push/run, status, output retrieval, deletion, private kernels, internet enablement and explicit accelerator selection. Current CLI docs warn that P100 is incompatible with the default PyTorch image; T4 is the safer default for a research spike.

Sources:

- https://www.kaggle.com/terms
- https://www.kaggle.com/aup
- https://github.com/Kaggle/kaggle-cli/blob/main/docs/kernels.md
- https://github.com/Kaggle/kaggle-cli/blob/main/docs/kernels_metadata.md

#### Google Colab Free — FAIL as automated production backend

Google's current Colab FAQ says free managed runtimes are not guaranteed and may terminate; it explicitly lists remote control, bypassing the notebook UI to interact primarily through another web UI, and distributed computing workers among activities disallowed on free managed runtimes without a positive compute-unit balance.

Decision:

- Manual benchmark/prototyping only for the Free tier.
- Do not design AutoSub's automated control plane around Colab Free.

Source:

- https://research.google.com/colaboratory/faq.html

#### Modal Starter — PASS technical/policy candidate; FAIL strict no-paid-overage gate on current Starter plan

Current Modal terms permit use of the Service for the Customer's internal business, with No-Fee Use subject to its plan limitations. Modal states that the customer retains Customer Data rights, processed Customer Data is expunged promptly after processing, and Modal will not train AI models on Customer Data without prior written consent.

Current Starter pricing advertises included monthly compute credit, GPU concurrency, scheduled/web functions, and per-second GPU pricing. The current pricing matrix advertises environment-level budgets on Team/Enterprise, not Starter. Included credit therefore cannot be treated as a hard no-overage control. These are mutable pricing facts and must not become product invariants.

Live technical qualification has now passed for authentication, T4 execution, Whisper, PP-OCRv6, staged hash-bound artifact transfer, retry/idempotent cache behavior and remote cleanup. The remaining Starter blocker is the frozen provider-side hard-budget requirement, not compute functionality.

Decision:

- Keep Modal as the primary automated burst-compute technical candidate for commercial AutoSub work.
- Keep `no_paid_overage_verified=false` on Starter unless a real provider-side hard budget/spend protection becomes available and is verified. Do not promote remaining free credit into a safety guarantee.

Sources:

- https://modal.com/legal/terms
- https://modal.com/pricing

#### Lightning AI Free — HOLD pending terms clarification

Current pricing/docs advertise free credits, GPU access, SDK automation, private models/data controls and 4-hour restart behavior for Free Studios. However the current terms combine an internal-business-purpose license with a restriction on some commercial activities/sales without prior written consent. This is not clear enough to promote the Free tier into a commercial production provider without clarification.

Decision:

- Keep Lightning as a high-value technical candidate for benchmark/research.
- Do not mark the Free tier production-qualified for monetized workflows until the applicable current terms are clarified or written permission is obtained.

Sources:

- https://lightning.ai/pricing/
- https://lightning.ai/pages/terms-of-service/
- https://lightning.ai/legal/terms-and-conditions

### Local provider tooling probe

At this checkpoint, neither `kaggle` nor `modal` CLI is installed/available on PATH on the AutoSub machine. No provider credentials were accessed or exposed.

Therefore no live remote GPU spike has been run yet.

### Candidate model/license matrix

License qualification is independent from compute-provider qualification.

| Workload | Candidate | Current license finding | Disposition |
|---|---|---|---|
| ASR fast pass | Whisper large-v3-turbo | MIT on official HF model card | Commercial-friendly candidate |
| ASR quality pass | Whisper large-v3 | Apache-2.0 on official HF model card | Commercial-friendly candidate |
| OCR | PaddleOCR / PP-OCR family | Apache-2.0 | Commercial-friendly candidate |
| Video masks/tracking | SAM 2 | Apache-2.0 | Commercial-friendly candidate |
| Translation experiment | Qwen3-8B | Apache-2.0 | Commercial-friendly candidate; quality benchmark still required |
| TTS | Kokoro-82M | Apache-2.0 | Commercial-friendly candidate; local-first |
| Image inpainting | LaMa | Apache-2.0 upstream | Candidate; benchmark quality on text removal |
| Video inpainting | STTN | MIT repository license | Candidate; verify exact pretrained-weight provenance before freeze |
| Video inpainting | ProPainter | S-Lab License 1.0, non-commercial unless separately licensed | REJECT for standard commercial production path |
| Generative text editing | AnyText2 | Apache-2.0 code | HOLD: must qualify pretrained weights/base-model/dependency license chain, not code license alone |

Model sources:

- https://huggingface.co/openai/whisper-large-v3-turbo
- https://huggingface.co/openai/whisper-large-v3
- https://github.com/PaddlePaddle/PaddleOCR
- https://github.com/facebookresearch/sam2
- https://huggingface.co/Qwen/Qwen3-8B
- https://huggingface.co/hexgrad/Kokoro-82M
- https://github.com/advimman/lama
- https://github.com/researchmm/STTN
- https://github.com/sczhou/ProPainter/blob/main/LICENSE
- https://github.com/tyxsspa/AnyText2

### Updated provider decision after qualification

Do not use the earlier assumption `Kaggle = primary production free burst`.

Updated candidate routing:

`Local = guaranteed control/fallback`

`Modal Starter = primary automated cloud candidate`

`Lightning Free = pending terms clarification / benchmark candidate`

`Kaggle = non-commercial research only under standard terms`

`Colab Free = manual research only`

This provider change does **not** change the Product Requirement. It only changes which adapter may satisfy the optional burst-compute capability.

### Remaining blocker before final Requirement/Acceptance freeze

The remaining provider blocker is now narrower:

1. Run a real Modal technical spike, if the owner chooses/creates an account and authenticates the CLI/SDK.
2. Measure representative AutoSub GPU workloads E2E, not inference-only.
3. Verify budget/fail-closed behavior when free credits are exhausted.
4. Finish license-chain checks for whichever inpainting/generative model actually wins the quality benchmark.

The architecture must remain valid even if Modal is unavailable or not configured.

## 15. Modal deep qualification — architecture constraints and spike contract

Research date: 2026-09-10. Official Modal documentation was used for these observations. These are provider observations, not permanent Product Requirements.

### Confirmed operational characteristics

1. GPU Functions are preemptible. Modal states that all Functions are subject to preemption by default, and `nonpreemptible` is not supported for GPU Functions. Long work therefore MUST be split into safely retryable/idempotent shards with frequent durable checkpoints.
2. Asynchronous invocation is supported. A spawned FunctionCall can be polled and its result retrieved later. This matches AutoSub's local-control-plane model better than holding one blocking HTTP request for a multi-hour job.
3. Modal supports retry policies, but provider retry is not sufficient by itself. AutoSub still needs content-addressed shard identity so a restarted input cannot duplicate or corrupt accepted work.
4. Modal Function inputs/outputs are retained for up to 7 days before deletion. Small and large payloads are both covered by that retention rule. Therefore media/crops should NOT be passed as large ordinary function arguments/return values by default.
5. Persistent Volumes remain until explicitly deleted. Ephemeral Volumes can exist only for a bounded context/lifetime and can be explicitly closed/deleted. AutoSub should prefer short-lived, explicitly cleaned remote storage for job payloads/results.
6. Customer data is encrypted in transit and at rest according to Modal's security documentation. AutoSub nevertheless remains responsible for backup/recovery and must keep the authoritative copy locally.
7. Workspace budget is a hard usage cap. Spend limit is a separate cap on net out-of-pocket charges after credits. When the spend limit is reached, Modal stops workloads that would create additional out-of-pocket charges while work still covered by applicable credits may continue. This supports a strict `free-credit-only / no surprise charge` owner policy.
8. Current Starter pricing advertises $30/month credits, 10 GPU concurrency, T4 at $0.000164/s and L4 at $0.000222/s. At current listed GPU-only rates that is roughly 50.8 T4 GPU-hours or 37.5 L4 GPU-hours before CPU/memory/other charges. These values are observations only and MUST NOT be encoded as acceptance invariants.
9. Local authentication setup requires a Modal account/token. `modal token new` uses an authenticated web session; `modal token set` can set credentials. Credentials must stay outside Git, run artifacts and logs.
10. Modal Volumes support file/directory upload and download operations and batched upload. This is suitable for explicit job artifact transfer rather than embedding video/crop bytes in RPC payloads.

Official references:

- https://modal.com/docs/guide/preemption
- https://modal.com/docs/guide/security
- https://modal.com/docs/guide/budgets
- https://modal.com/docs/sdk/py/latest/FunctionCall
- https://modal.com/docs/sdk/py/latest/Volume
- https://modal.com/docs/cli/latest/token
- https://modal.com/pricing

### Candidate AutoSub transfer protocol

For a production cloud shard, prefer this contract:

`LOCAL_PREPARED -> REMOTE_STAGED -> REMOTE_RUNNING -> REMOTE_RESULT_READY -> LOCAL_VERIFIED -> REMOTE_CLEANED -> COMMITTED`

Rules:

- Local creates immutable `shard_manifest.json` with content hashes, workload type, model fingerprint, parameters and expected input hashes.
- Large media artifacts are uploaded to explicit temporary storage; RPC arguments contain only small identifiers/hashes/paths and non-secret configuration.
- Remote worker verifies all input hashes before inference.
- Remote worker writes result artifacts plus `result_manifest.json` with output hashes, model/runtime fingerprint, timing and bounded diagnostics.
- Local downloads result, verifies hashes/schema and only then promotes the shard to accepted local cache.
- Remote cleanup is attempted after local verification and cleanup evidence is persisted locally.
- A timeout/preemption/provider disappearance before `LOCAL_VERIFIED` can be retried using the same shard identity without corrupting the run.
- No remote shard may be the only copy of source state or previously accepted output.

### Strict budget policy candidate

AutoSub should expose provider policy, not provider pricing details:

- `Local only`
- `Auto — use qualified burst compute when beneficial`
- `Cloud allowed, no paid overage`

For Modal, `Cloud allowed, no paid overage` requires a provider-side spend limit compatible with zero out-of-pocket charges, plus AutoSub-side handling for `budget/quota unavailable`. AutoSub must not silently convert a no-paid-overage run into paid compute.

### Real technical spike — acceptance before provider promotion

The first authenticated spike is deliberately small and disposable. It does NOT implement the production provider adapter.

Test package:

- one short extracted audio sample representative of AutoSub ASR
- a small set of deduplicated subtitle/visual-text crops
- a small masked visual-text inpainting sample
- immutable test manifest + SHA-256 hashes

Candidate GPU: start with T4 for compatibility/cost, optionally repeat on L4 if quality/time warrants it.

Spike stages:

1. Authenticate Modal without placing credentials in the repository.
2. Verify workspace budget/spend-limit settings before GPU work.
3. Create disposable remote app/storage.
4. Upload test artifact bundle and verify remote hashes.
5. CUDA/runtime probe: GPU name, VRAM, CUDA/PyTorch compatibility.
6. Run one ASR candidate pass.
7. Run one OCR candidate pass.
8. Run one inpainting candidate pass whose model/license chain has already passed policy review.
9. Record queue/cold-start/model-load/inference/download/total wall-clock times separately.
10. Force/reproduce one retry/resume path where practical and verify idempotent result identity.
11. Download result manifest/artifacts and verify SHA-256 locally.
12. Delete disposable remote storage/app artifacts and record cleanup evidence.
13. Confirm no credentials/secrets appear in repo, result manifests or captured logs.

Promotion gate:

`ModalProvider = QUALIFIED` only if the spike passes security/integrity/recovery gates AND at least one representative workload has a material E2E quality/speed advantage over the current local path.

If the speed advantage is weak, Modal remains available as an optional capacity provider but must not become Auto routing default for that workload.

### Design implication for 1–2 hour batch sessions

A 1–2 hour owner session is acceptable, but a single 1–2 hour GPU Function is not the desired fault domain. AutoSub should target short independently committed shards within that session. Session length is an orchestration window; shard length is a recovery boundary.

## 16. Local Modal qualification bootstrap status

The qualification environment has been prepared without modifying AutoSub's application Python environment:

- isolated venv: `data/provider_qualification/modal_venv` (ignored by Git via `data/`)
- Modal client installed: `1.5.5`
- system/global AutoSub application environment was not changed
- `modal token info` confirms that no Modal token/account authentication is currently configured on this machine
- no provider credential was read, printed, stored in the repository or added to run artifacts

The next live-spike step is blocked only on owner authentication/account setup. After authentication, continue from the Real technical spike contract above; do not redesign the product direction from chat context.

## 17. Modal authenticated live-spike evidence — 2026-09-10

Owner authentication completed successfully outside the repository. The Modal client reports a verified workspace token and stored it in the user's machine-level Modal config, not under `D:\AutoSub`. No credential value was copied into this document, Git, run artifacts or application logs.

### GPU capability probe — PASS

A disposable Modal Function was created on a T4 and executed a real CUDA/PyTorch workload.

Observed evidence:

- GPU: Tesla T4
- VRAM reported by PyTorch: 15,637,086,208 bytes (~15.6 GB decimal)
- compute capability: 7.5
- CUDA available: true
- PyTorch: 2.14.0+cu130
- CUDA runtime: 13.0
- Python: 3.11.12
- 2048x2048 GPU matmul: ~0.036 s
- remote function work measured after startup: ~0.303 s
- local client wall time for the already-built probe invocation: ~10.25 s

Interpretation:

- Modal T4 is technically compatible with the intended GPU inference role.
- Cold/startup overhead is material compared with tiny tasks, so Auto routing must batch useful work and must not send micro-workloads individually.
- Provider qualification must measure total wall time, not GPU inference alone.

### Representative AutoSub fixture preparation

A real existing AutoSub source video was used as the qualification fixture:

- source size: 181,905,545 bytes
- local FFmpeg available
- extracted ASR payload: 45 s mono audio, 1,440,122 bytes
- extracted visual payload: nine representative JPEG frames, roughly 117–163 KB each
- ASR audio SHA-256: `1A50A2BFD3D51BA49F746CD57117AAD4A015CA41245B83E495F31F70890EB2FC`

This validates the architectural choice to preprocess locally and send compact task artifacts rather than upload the complete ~182 MB source video for model inference.

### Whisper large-v3-turbo on T4 — PASS

Candidate `openai/whisper-large-v3-turbo` was executed remotely against the real 45 s Chinese speech fixture.

Observed timings:

- model load: ~13.23 s
- inference: ~7.10 s for ~45 s audio
- remote total measured inside function: ~20.34 s
- local client wall time including provider startup/orchestration: ~49.38 s

Observed result:

- non-empty Chinese transcript produced
- 28 timestamped chunks returned
- timestamps covered the sample through approximately 44.98 s
- no full-video upload was required

Interpretation:

- the GPU ASR candidate is technically viable and substantially faster than real-time at inference level on T4 (~6.3x audio-duration/inference-time for this sample)
- cold-provider overhead dominates a single short sample; batching/model-image caching and longer shards are necessary for E2E advantage
- this is capability evidence, not yet a final quality winner; accuracy must be compared against current AutoSub source/caption truth on a representative corpus

### OCR probe status — IN PROGRESS

The first PaddleOCR container attempt failed before inference because `libGL.so.1` was absent from the minimal image. This is an environment-packaging failure, not evidence against the OCR model or GPU provider. The image definition was corrected to install the required system library and the real-frame probe was restarted.

The OCR workload must not be marked PASS until the corrected run returns actual detection/recognition results and timings.

## 18. New evidence-based implementation constraints

The live spike adds the following candidate constraints before final freeze:

1. `ComputeRouter` must have a minimum useful batch/work estimate; tiny isolated cloud calls can be slower than local due to startup/orchestration overhead.
2. GPU worker images/models should be prebuilt/cached. Environment construction is not part of normal per-run inference.
3. Local preprocessing that shrinks payloads is mandatory when it preserves model-relevant information (for example audio extraction and visual ROI/frame dedupe).
4. Provider success is workload-specific: Modal may qualify for ASR while another workload remains local or requires a different model stack.
5. A provider/model pair is promoted only from real result evidence, not because CUDA is available.
6. Qualification failures caused by container dependencies must be classified separately from model-quality/runtime incompatibility.

The product direction remains `local authoritative control plane + optional qualified burst compute`; the live evidence strengthens rather than changes that architecture.

## 19. OCR candidate refresh — PP-OCRv6 supersedes the initial v5 assumption

A fresh official-source check on 2026-09-10 found that PaddleOCR 3.7.0 / PP-OCRv6 was released on 2026-06-11 and is now the current universal scene-OCR generation. Therefore the initial PP-OCRv5 mobile probe must be treated as an environment/capability experiment, not the final OCR model selection.

Official PaddleOCR claims for PP-OCRv6:

- tiny / small / medium tiers (approximately 1.5M / 7.7M / 34.5M parameters)
- small/medium single models support 50 languages including Chinese, English and Japanese
- medium reports +4.6% detection Hmean and +5.1% recognition accuracy versus PP-OCRv5_server on Paddle's multi-scenario benchmark
- medium reports 2.37x GPU inference speedup versus PP-OCRv5_server in the official technical documentation
- official quick-start supports either PaddlePaddle or Transformers inference engines
- PaddleOCR repository/code remains Apache-2.0; exact model artifacts used in production still require pinned provenance/license recording

Official references:

- https://github.com/PaddlePaddle/PaddleOCR
- https://github.com/PaddlePaddle/PaddleOCR/releases
- https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/algorithm/PP-OCRv6/PP-OCRv6.en.md
- https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/quick_start.en.md

Updated benchmark decision:

`PP-OCRv6 small = fast/default candidate`

`PP-OCRv6 medium = quality/escalation candidate`

The final route should be evidence-driven in the same pattern as ASR: fast universal OCR first, then escalate only low-confidence/disagreement/difficult visual-text tracks to the medium model when that improves quality enough to justify compute.

This discovery also means AutoSub must not hard-code a provider/model version from earlier research. Model identity and revision are runtime/provider capability data and must be snapshotted per accepted shard.

## 20. Tracking/mask candidate refresh — latest is not automatically the default

A fresh current-model check found Meta SAM 3.1 (released 2026-03-27), which improves multi-object video tracking throughput versus the original SAM 3. However, the official repository now uses the custom SAM License rather than SAM 2's Apache-2.0 license, and real issue reports show substantial GPU-memory pressure for long video/batched usage, including OOM reports on 16 GB and 24 GB GPUs and very high memory growth on long propagation runs.

Official/current sources:

- https://github.com/facebookresearch/sam3
- https://github.com/facebookresearch/sam3/blob/main/RELEASE_SAM3p1.md
- https://github.com/facebookresearch/sam3/blob/main/LICENSE
- https://github.com/facebookresearch/sam2

Candidate decision for AutoSub:

- do not promote SAM 3.1 merely because it is newer
- use cheap deterministic geometry/optical-flow-style propagation for easy, stable text tracks where evidence says it is sufficient
- keep SAM 2 as the lower-risk commercial-friendly segmentation/tracking candidate for difficult masks on modest cloud GPUs; code/checkpoints are Apache-2.0 according to the official repository
- keep SAM 3.1 as an experimental high-tier candidate only if a later real AutoSub benchmark demonstrates enough quality advantage and its custom license is explicitly accepted for the intended deployment/distribution pattern

This preserves the main performance principle: expensive segmentation is an escalation path, not a mandatory per-frame stage.

## 21. Initial PaddleOCR v5 container probe — FAIL as packaged, model verdict NOT DRAWN

The corrected second PP-OCRv5 mobile probe did not reach OCR inference. After approximately 1,381 s client/job wall time it failed while importing OpenCV/PaddleX because the minimal container still lacked `libgthread-2.0.so.0` (GLib runtime dependency). The earlier attempt had already exposed a missing `libGL.so.1` dependency.

Classification:

- provider/GPU failure: NO
- OCR quality failure: NO
- production-ready image packaging: FAIL
- evidence that on-demand environment assembly is too fragile/slow for normal AutoSub runs: YES

Decision:

- do not spend more qualification time repairing the obsolete v5 probe
- continue with the current PP-OCRv6 small/medium candidate path
- any production cloud worker image must be prebuilt and smoke-tested at release time, including all native system dependencies and model weights; normal jobs must not discover missing shared libraries after admission
- environment/image identity must be part of the remote worker fingerprint and acceptance evidence

This failure is useful evidence for the provider contract: `CONTAINER_READY` is a distinct preflight gate before payload admission, and environment failures must never be misreported as OCR/model-quality failures.

## 22. Existing AutoSub visual-cleanup capability — REUSE before new subsystem

Repository audit found that AutoSub already contains a useful but subtitle-specific Tier-A cleanup path rather than starting from zero.

Current `app/services/clean_subtitle_render.py`:

- samples the video every 0.25 s
- detects bright/edged source text only inside a fixed lower subtitle ROI on a 1280x720 detection frame
- merges detections into temporal intervals
- associates valid target cues with source intervals
- applies FFmpeg `delogo` only while the corresponding target cue is active
- renders the target subtitle separately with ASS and a stable lower-third plate
- preserves source audio in this path

Current `tools/run_cp08d3_closed_loop_cjk_cleanup.py` already demonstrates a valuable repair/QC pattern: detect residual source CJK -> OCR representative crops -> apply cleanup interval -> rescan final output -> retry a widened cleanup interval if residual text remains.

Decision:

- preserve and generalize these proven concepts instead of creating an unrelated visual-localization engine
- the current fixed lower-subtitle ROI detector is NOT sufficient for Full Visual Localization
- rectangular `delogo` is a valid fast Tier-A option only when its reconstructed background passes visual QC; it must not be used blindly for arbitrary scene text
- Full Visual Localization must evolve from `source caption intervals` to tracked `VisualTextUnit` / `TextTrack` geometry over the whole frame
- the existing residual-language closed loop should become a general final-localization QC/repair mechanism
- source cleanup timing must remain tied to the target replacement lifetime; never leave empty cleanup boxes between valid target units

This materially reduces implementation scope: existing detection/render/QC concepts are reusable, while the new work is whole-frame text discovery, track identity/consensus, style/layout replacement and quality-tier routing.

## 23. PP-OCRv6 small whole-frame T4 probe — PASS

A live Modal T4 run using `PP-OCRv6_small_det` + `PP-OCRv6_small_rec` with the Transformers engine successfully processed all nine representative 1280px frames from the real AutoSub fixture.

Observed timing from the persisted result:

- upload payload: 1,240,802 bytes for nine JPEG frames
- GPU: Tesla T4
- model initialization: ~19.59 s
- OCR inference across all nine frames: ~3.41 s
- remote total: ~22.99 s
- client wall time for the persisted run: ~66.12 s
- most post-first-frame per-frame inference measurements were roughly 0.13–0.21 s; the first frame paid additional warm-up cost (~1.96 s)

Observed capability:

- whole-frame OCR detected not only bottom dialogue subtitles but also game/UI text, Chinese labels, Latin usernames, numeric values and mixed-script content at many screen locations
- returned recognition confidence and polygon geometry for each text instance, which is sufficient input for `TextTrack` association and mask/layout planning
- representative high-confidence Chinese detections included `乞丐脸`, `动漫脸部`, `所有东西`, `地毯`, `管理员`, `前50名获胜数`, `强化身体`, and long bottom-dialogue strings
- tiny/angled/mixed UI text also produced low-confidence or inconsistent readings; confidence alone is not enough to establish canonical text for every region

Decision:

- whole-frame visual-text localization is technically feasible on a modest T4 with compact frame payloads
- PP-OCRv6 small is promoted to the **fast first-pass candidate**, not yet the final quality winner
- canonical `VisualTextUnit` text must use temporal/track consensus across multiple observations rather than trusting one frame, especially for tiny, moving, angled or mixed-script text
- likely-stable text tracks should be OCRed/translated once per identity, not once per video frame
- low-confidence or cross-frame-disagreeing tracks are the natural escalation set for PP-OCRv6 medium / alternate recognition / multimodal verification
- provider cold/model startup remains much larger than raw inference, reinforcing prebuilt images, model caching and useful batching

The persisted ignored evidence artifact is `data/provider_qualification/modal_ocrv6_small_result.json`; it is qualification evidence only, not a release artifact.

## 24. Same-fixture local AutoSubs vs Modal Whisper ASR — strong evidence for quality cascade

The existing production local path (`AutoSubs 3.8.0`, model `small`, CPU / `--no-gpu`) was run on the exact same 45 s audio fixture used by the Modal Whisper probe.

Local AutoSubs observed:

- elapsed wall time: ~43.39 s
- 33 source segments
- local cached model preflight: passed

Modal Whisper large-v3-turbo observed earlier:

- GPU inference: ~7.10 s
- remote model load + inference: ~20.34 s
- cold client/provider wall time for the single short sample: ~49.38 s
- 28 timestamped chunks

Speed interpretation:

- warm/inference-level GPU ASR is much faster than the current CPU path
- for one isolated 45 s sample, cloud cold/orchestration overhead makes total client time similar to or slower than local
- for a useful batch or warm model worker, the observed inference gap is large enough to justify an `Auto` burst-compute route; the router must amortize startup overhead rather than dispatch individual tiny audio jobs

Quality spot-check using visually OCRed source subtitles from the same video shows several content-important errors in the current local `small` transcript that Whisper resolves correctly or much more closely. Examples include the visible source subtitle `乞丐脸` where local produced `起干脸`, visible `2,500` where local produced `6500`, `跑酷` where local produced `跑过`, and the road/walking sentence where the local transcript diverged materially while Whisper stayed much closer to the visible subtitle. Both engines still show occasional recognition mistakes, so neither output should be treated as perfect ground truth from a single pass.

Decision:

- the current local AutoSubs-small path is no longer sufficient as the sole quality source for the target V2 quality profile
- retain it as offline/local fallback and potentially a second-opinion signal
- promote `Whisper large-v3-turbo GPU` to the fast high-quality ASR candidate
- retain `Whisper large-v3` as an uncertainty/escalation candidate to benchmark on spans where turbo confidence/consensus is weak
- where reliable burned-in source dialogue subtitles are available, visual OCR and ASR should be allowed to corroborate/disagree as independent evidence rather than discarding one channel prematurely
- canonical spoken source text should be evidence-fused and provenance-bearing; disagreement becomes an escalation signal, not an arbitrary provider winner

Persisted ignored local evidence: `data/provider_qualification/local_autosubs_45s_result.json`.

## 25. PP-OCRv6 small vs medium on identical whole-frame fixture — cascade decision

Both PP-OCRv6 tiers were executed on the same nine 1280px real AutoSub frames and the same ~1.24 MB payload.

Observed persisted results:

| Metric | Small | Medium |
|---|---:|---:|
| detections | 189 | 200 |
| mean recognition confidence | 0.9359 | 0.9390 |
| model init | ~19.59 s | ~13.62 s |
| inference, 9 frames | ~3.41 s | ~4.88 s |
| remote total | ~22.99 s | ~18.50 s |
| client wall | ~66.12 s | ~36.81 s |

The init/client-wall values are not a fair intrinsic speed comparison because provider/model cache state differed between runs. The comparable inference measurement shows medium using roughly 43% more inference time on this small fixture. It also returned ~5.8% more detections and a slightly higher mean confidence.

For several important visible terms (`乞丐脸`, `动漫脸部`, `强化身体`, `管理员`, `所有东西`, `跑酷`, `2500`), both tiers recovered the same count on this fixture. Medium sometimes recovered additional small text but also still produced uncertain/incorrect tiny or mixed-script readings; it is not a universal truth oracle.

Decision:

- keep **PP-OCRv6 small as the default first-pass whole-frame OCR**
- use **PP-OCRv6 medium as an escalation/recheck tier**, triggered by low confidence, cross-frame disagreement, suspicious source-language residuals, difficult orientation/size or failed final QC
- do not pay the medium-model cost for every stable high-confidence track
- compare tiers at `TextTrack`/canonical-text level, not by raw detection count alone

Persisted ignored evidence: `data/provider_qualification/modal_ocrv6_small_result.json` and `data/provider_qualification/modal_ocrv6_medium_result.json`.

## 26. LaMa T4 real-region inpainting spike — capability PASS, human-quality acceptance still separate

A commercially compatible LaMa candidate was executed on the real `frame_01.jpg` fixture using two actual source-text regions: one Chinese UI label and one bottom dialogue subtitle region.

Observed Modal T4 timings after the worker started:

- model initialization: ~1.76 s
- UI-label inpaint: ~2.79 s
- bottom-dialogue inpaint: ~2.46 s
- remote total for both cases: ~7.57 s
- cold client wall including provider orchestration/build/startup for this first run: ~47.50 s

The worker returned full PNG artifacts with SHA-256 identities. Automated collateral-change analysis found **0.0 mean absolute pixel difference outside the explicitly masked box** for both outputs and 0% outside-mask pixels changing by >2 intensity units. This demonstrates bounded modification rather than uncontrolled whole-frame mutation for this wrapper/model path.

The existing local PaddleOCR runtime was then run on the original and both inpainted full frames. The source image OCR contained the target UI label (locally read approximately as `乞写脸`) and the bottom dialogue (`然后第二个是艺写脸`, local OCR approximation). In the corresponding inpainted result each targeted string/region disappeared from OCR while unrelated Chinese text elsewhere in the frame remained detectable, as expected because only one region was erased per test.

Interpretation:

- LaMa is technically viable as a Tier-B still/image-region text-removal candidate on T4
- the target text can be removed without collateral changes outside the mask in this test
- `contains_cjk=true` on the full inpainted frame is not a failure because many non-target CJK regions intentionally remain; residual QC must be scoped to the intended TextTrack/region and then separately perform a whole-frame final residual scan after all localizable tracks are processed
- pixel/OCR evidence proves removal and bounded mutation, but **does not by itself prove human-perceived background reconstruction quality**; visual reconstruction still needs machine seam/texture checks plus later human UAT on representative difficult backgrounds
- first cold execution again shows that provider startup dominates tiny workloads; inpainting should be batched by useful shards/regions

Simple seam/gradient measurements around the two masked boxes did not show an extreme discontinuity versus the immediately adjacent boundary in this sample, but those metrics are diagnostic evidence only and must not be promoted to a universal perceptual threshold without a larger corpus.

Persisted ignored evidence: `data/provider_qualification/modal_lama_result.json`, `data/provider_qualification/lama_probe/local_ocr_qc.json`, and `data/provider_qualification/lama_probe/pixel_qc.json`.

### Inpainting route after this spike

Candidate route is now:

`Tier A deterministic cleanup -> Tier B LaMa for still/stable natural-background regions -> difficult moving/occluded regions escalate to tracked/video-aware reconstruction or review`.

Do not force LaMa onto every text track, and do not introduce non-commercial video-inpainting weights into the monetized/default production path merely to remove the review state.

## 27. Dense temporal TextTrack / dedupe proof — PASS on real sequence

A dense 4-second sequence from the real AutoSub source was sampled at 5 fps (20 frames, ~2.24 MB total JPEG payload) and processed with PP-OCRv6 small whole-frame OCR.

Observed source-language dialogue evidence:

- 20 CJK dialogue detections across 20 frames
- only 2 exact canonical dialogue strings across the sequence
- first dialogue observed in 11 consecutive frames with mean confidence ~0.9874 and median consecutive polygon IoU ~0.956
- second dialogue observed in 9 consecutive frames with mean confidence ~0.9997 and median consecutive polygon IoU ~0.999
- treating stable repeated observations as two TextTracks rather than 20 independent translation jobs reduces translation/localization decisions by **90%** for these two stable tracks

This is direct real-video evidence for temporal identity rather than a synthetic assumption.

Decision:

- `TextTrack` is a required first-class V2 abstraction
- stable high-IoU, semantically consistent observations are fused into one track and canonical text decision
- OCR may still sample multiple frames to establish confidence/consensus, but translation, style selection and expensive replacement planning happen once per accepted track identity
- text change creates a new semantic track/unit even when geometry remains nearly identical; temporal identity must not merge two consecutive subtitle sentences solely because their polygons overlap
- low-confidence/string-disagreement/geometry-jump observations trigger additional sampling or model escalation rather than immediate new translation
- dense frame sampling rate is an adaptive recognition policy, not a hard permanent 5-fps requirement

Persisted ignored evidence: `data/provider_qualification/modal_ocrv6_dense_small_result.json` and `data/provider_qualification/dense_texttrack_analysis.json`.

## 28. Modal staged-artifact integrity / idempotency / cleanup proof — PASS

A disposable named Modal Volume was used to exercise the candidate production transfer semantics with the same 45-second AutoSub audio fixture.

Observed flow:

1. local source SHA-256 was computed before upload;
2. the 1,440,122-byte audio artifact was uploaded into an isolated remote Volume;
3. a remote function mounted the Volume and independently verified the input hash before processing;
4. the function wrote a deterministic result artifact plus `result_manifest.json` with shard ID, input hash, output hash and output size;
5. the same logical shard was invoked a second time; it returned `cache_hit=true` and the exact same output identity instead of recomputing/duplicating output;
6. result artifact and manifest were downloaded locally;
7. local input hash matched the remote manifest input hash exactly;
8. downloaded output SHA-256 matched the remote manifest output hash exactly;
9. the disposable remote Volume was deleted;
10. a subsequent provider Volume listing confirmed the qualification Volume was absent.

Observed timing for this tiny integrity workload:

- first remote invocation client wall: ~15.75 s;
- second idempotent/cache-hit invocation client wall: ~1.14 s;
- deterministic processing itself: ~0.0034 s.

Hash evidence:

- input: `1a50a2bfd3d51ba49f746cd57117aad4a015ca41245b83e495f31f70890eb2fc`
- output: `c698ebcb7af0ca1f91b1629e8514f4d6ea00f096793e274e8abf71710b29c7ed`
- input round-trip match: true
- output round-trip match: true
- remote cleanup verification: `VOLUME_PRESENT=False`

Decision:

- the candidate `LOCAL_PREPARED -> REMOTE_STAGED -> REMOTE_RUNNING -> REMOTE_RESULT_READY -> LOCAL_VERIFIED -> REMOTE_CLEANED -> COMMITTED` state model is technically viable on Modal
- immutable shard identity + result manifest + hash verification are promoted from research idea to required architecture
- retry/idempotency must be owned by AutoSub shard identity even when a provider also offers retry primitives
- remote cleanup is a required lifecycle step after local verification; cleanup failure is recorded separately and must not invalidate an already hash-verified local result
- large artifacts should use staged storage rather than ordinary function arguments/returns in the production adapter

Persisted ignored local download evidence lives under `data/provider_qualification/volume_integrity_download/`. The remote qualification Volume itself was deleted after verification.

## 29. Pre-freeze research closure decision

The bounded research items capable of changing the V2 architecture are now sufficiently answered:

- whole-frame GPU OCR: feasible;
- PP-OCRv6 small vs medium: small-first / medium-escalation supported by same-fixture evidence;
- GPU ASR: Whisper turbo materially improves inference speed and source-text quality over current local small on observed cases;
- visual text removal: LaMa demonstrates bounded real-region removal on T4; difficult perceptual/video cases remain tiered rather than assumed solved;
- TextTrack temporal dedupe: real dense sequence demonstrates stable identity and 90% fewer translation decisions for repeated dialogue observations;
- cloud staged transfer: input/output hash verification, idempotent shard reuse and remote cleanup demonstrated;
- commercial/provider constraints: provider and model license qualification remain independent gates;
- cloud cold start: repeatedly observed as material, so Auto routing must amortize it rather than dispatch micro-workloads.

No further provider/model exploration is required before Requirement/Acceptance freeze unless a new blocker is discovered during implementation.

One provider configuration remains an owner/environment gate rather than an architecture unknown: `Cloud allowed, no paid overage` requires the applicable provider-side spend/budget controls to be configured and verified when the provider is enabled. AutoSub must fail closed to local/wait/provider-unavailable if that policy cannot be verified; it must not silently authorize paid overage.

Proceed to freeze stable Product Requirement, Localization Quality Contract and the material ADRs before modifying V2 production code.
