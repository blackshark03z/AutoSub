# ADR 0009 — Local authority with qualified burst compute

Status: Accepted for AutoSub Localization V2 implementation
Date: 2026-09-10

## Context

The owner often processes large batches in short 1–2 hour work sessions and wants to use available GPU capacity for better models and higher throughput. Free/low-cost notebook/cloud providers differ materially in automation terms, commercial-use rules, privacy/retention, GPU/runtime behavior and reliability. Provider quotas/pricing can change, so product architecture cannot depend on a specific current free allowance.

Live qualification demonstrated:

- Modal authentication and Tesla T4 CUDA/PyTorch execution works from the AutoSub machine;
- a 45 s, 1.44 MB locally extracted audio payload ran Whisper large-v3-turbo at ~7.1 s GPU inference, while the same current local AutoSubs-small path took ~43.4 s;
- whole-frame PP-OCRv6 runs on T4 using compact JPEG payloads;
- first/cold cloud invocations have material startup/model overhead, so inference-only comparisons are misleading;
- a staged Modal Volume probe verified exact input/output SHA-256 round-trip, deterministic idempotent second invocation/cache hit, local verification, and remote Volume deletion;
- GPU Functions may be preempted, so a 1–2 hour owner session cannot be one irreplaceable remote transaction.

Policy research also found the standard Kaggle consumer terms unsuitable for the intended commercial/monetized production path, while Colab Free is not suitable as an automated product backend. Modal is currently the strongest automated candidate but must remain replaceable.

## Decision

1. Local AutoSub remains the authoritative control plane and source of truth for source identity, run state, configuration snapshots, accepted shard/artifact identity, cache and final acceptance.
2. Introduce a `ComputeProvider` boundary. Product/business logic asks for capabilities/workloads; it does not hard-code Modal, Kaggle or another provider.
3. Owner-facing policy is simple: `Local only`, `Auto`, or `Cloud allowed, no paid overage`. Provider details are diagnostics/settings, not the main workflow vocabulary.
4. Auto routing uses measured/estimated E2E cost including preprocessing, upload, queue/cold-start, model load, inference, download, cache reuse and recovery risk. Tiny work stays local when cloud overhead dominates.
5. Send minimum technically sufficient artifacts instead of full video whenever possible: audio/chunks for ASR, sampled/deduped frames/crops for OCR, bounded context/masks for inpainting, text/context for translation.
6. Remote work is split into short immutable resumable shards inside the longer owner session. Shard identity binds input hash, model/runtime identity and relevant parameters.
7. Use staged artifact transfer for meaningful media rather than large ordinary RPC payloads. Remote worker verifies input hashes and writes a result manifest with output hashes/provenance/timing. Only locally downloaded and hash-verified results enter accepted cache.
8. Retry/preemption with the same shard identity must be idempotent. Completed verified shards survive provider failure and are not recomputed unless their identity becomes invalid.
9. Remote temporary data has explicit cleanup after local verification. Cleanup failure is recorded separately; it cannot corrupt an already verified local artifact.
10. Provider terms and model/code/weight/dependency license chain are independent qualification gates for intended commercial/monetized use.
11. `Cloud allowed, no paid overage` requires provider-side budget/spend protection to be configured/verified. If protection cannot be verified, the cloud path is unavailable under that policy; AutoSub never silently incurs paid overage.
12. Current disposition: Modal = primary automated candidate; Kaggle standard consumer = research/non-commercial only; Colab Free = manual research only; Lightning Free = hold pending terms clarification. These dispositions may change without changing the architecture.

## Consequences

- Cloud GPU can accelerate/enable high-quality ASR/OCR/inpainting without making AutoSub cloud-dependent.
- Cold starts make batching/prebuilt images/model caches important; provider worker readiness becomes an explicit preflight.
- Content-addressed cache and TextTrack dedupe often save more compute than simply choosing a faster GPU.
- A provider outage/quota limit becomes `PROVIDER_UNAVAILABLE`, not run corruption. Local/fallback/wait behavior remains explicit.
- Provider pricing/quota/GPU SKU values remain runtime observations and do not appear as permanent Product Acceptance invariants.

## Revisit when

Revisit provider disposition when terms, pricing, available GPU/runtime or production benchmark evidence changes. Revisit the local-authority/shard-integrity decision only if a future deployment model intentionally moves the authoritative product state to a managed server and redefines the Product Goal accordingly.
