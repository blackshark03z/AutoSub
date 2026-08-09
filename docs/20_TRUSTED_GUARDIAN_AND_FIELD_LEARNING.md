# v1.15 — Trusted Guardian + Field Learning

v1.15 closes two gaps that repository-local governance cannot solve by adding more prompt rules:

1. **Trust must exist outside Worker write authority.**
2. **The OS must learn from real project failures instead of only from design-time redteam reviews.**

## Assurance levels

`python scripts/ai_os.py assurance` reports the configured/observed assurance level and now also exposes `attestation_basis`. A2–A4 isolation/merge properties remain trusted-runtime claims; the repo kernel cannot independently prove them.

- **A0 — Advisory:** prompt/convention only.
- **A1 — Repo Enforced:** lifecycle, scope, evidence, health and validator exist inside the repository. Useful against mistakes; not a security boundary against an actor with unrestricted repo write authority.
- **A2 — External Guardian attested:** the public key is configured **and** trusted runtime attests that Guardian authority is external to Worker control. A public key alone keeps assurance at A1 because verification capability is not the same thing as signer independence.
- **A3 — Protected Merge:** A2 plus trusted CI/runtime attests required checks + protected merge.
- **A4 — Isolated Trust:** A3 plus runtime isolation attests that Worker/reviewer cannot access signer authority.

The package does **not** claim A3/A4 merely because a GitHub workflow file exists. The trusted runtime must explicitly attest those properties.

## Guardian protocol

Generate keys outside the repository:

```bash
python scripts/guardian.py keygen \
  --private ~/.ai-build-os/guardian/private.pem \
  --public ~/.ai-build-os/guardian/public.pem

export AI_BUILD_OS_GUARDIAN_PUBLIC_KEY=~/.ai-build-os/guardian/public.pem
# Set this only from a trusted outer runtime after verifying signer authority is external.
export AI_BUILD_OS_GUARDIAN_EXTERNAL_ATTESTED=true
```

For R2 review-triggered and R3, prefer launching the reviewer through the Guardian:

```bash
python scripts/guardian.py run-reviewer \
  --private ~/.ai-build-os/guardian/private.pem \
  --public ~/.ai-build-os/guardian/public.pem \
  --out /tmp/review-attestation.json \
  --task-id TASK-123 --task-revision 1 \
  --snapshot-sha256 <snapshot> \
  --writer-session-id AI-WORKER \
  --review-report "$PWD/.ai/reviews/TASK-123.md" \
  --cwd "$PWD" -- \
  <fresh-reviewer-command>
```

`run-reviewer` creates a fresh reviewer session ID, starts a distinct OS process, strips Guardian-private-key environment authority from the child, and emits a signed attestation only when the reviewer process exits successfully. Schema v3 attestations also bind the SHA256 of the review report, so post-signature report edits fail validation. `done` and the validator verify the signature against the **external** public key.

This is stronger than a self-declared `Independent from writer: yes`, but it is still only A2 unless process/account isolation prevents the Worker from reaching signer credentials.

## Compact agent-facing CLI

The full kernel remains available for exceptional workflows, but ordinary agents should use:

```bash
python scripts/ai.py start ...
python scripts/ai.py finish ...
python scripts/ai.py status
python scripts/ai.py next
```

The compact facade intentionally returns `ACTION_REQUIRED` instead of guessing missing verification semantics. The 30+ command kernel is an internal/admin surface, not something every Worker must memorize.

## Risk uncertainty is first-class

`config/risk_semantics.json` supplements known path/content rules with high-signal unknown-side-effect patterns. When code calls mutation-like abstractions that the generic classifier cannot semantically identify, v1.15 can classify the delta as **uncertain** and conservatively raise the acceptance-time floor to R2.

This is not a claim of semantic understanding. It is a fail-closed response to uncertainty. Repeated `UNEXPECTED_RISK_ESCALATION` events should drive project-specific risk-map improvements rather than an ever-growing global regex list.

## Decision visibility without owner spam

Goal mode now supports:

```bash
python scripts/ai_os.py goal decision \
  --type AUTO_DECISION \
  --text "Reuse existing SQLite store" \
  --confidence 0.91 \
  --reversibility HIGH \
  --owner-impact LOW

python scripts/ai_os.py goal digest
```

Decision types are `ASSUMPTION`, `AUTO_DECISION`, `OWNER_DECISION`, `RISK_ESCALATION`, and `POLICY_EXCEPTION`. Goal completion writes `owner_digest.json`; low-confidence, high-impact, risk-escalation and policy-exception items are surfaced for attention without forcing an owner interrupt for every reversible choice.

## Field Learning Loop

Local normalized events live in the generated `.ai/field/events.jsonl` (Git-ignored by default). They intentionally avoid source-code payloads and product-commit noise. Examples:

- CLI/workflow retry;
- unexpected risk escalation;
- first-pass failure;
- escaped defect / later rework;
- owner interrupt / auto-decision override;
- guard false positive / false negative;
- Scout waste;
- context rediscovery;
- regression timeout.

Use:

```bash
python scripts/ai_os.py field report
```

The report ranks pain by frequency × severity × observed cost impact, computes governance token/wall overhead when runtime telemetry exists, derives empirical p50/p75/p95 budgets by role/work-class after enough samples, reports whether the configured field window/project-count is sufficient even to consider a stable-core promotion, and generates **upgrade candidates**. Candidates are recommendations only:

```text
observe -> detect pattern -> candidate -> bounded experiment
        -> compare before/after -> owner promotion
```

Field data must never automatically edit the stable kernel or quality policy.

### Optional cross-project learning

Set `config/assurance.json -> field_learning.global_enabled=true` to mirror normalized events into `~/.ai-build-os/field/events.jsonl`. Project IDs are hashed by default and metadata is aggressively reduced. Then:

```bash
python scripts/ai_os.py field report --global
```

This enables a personal Build OS installation to learn recurring friction across projects without centralizing source code.

## Stable core vs tunable policy

`config/kernel_contract.json` distinguishes stable core from tunable policy. The release discipline is intentionally conservative: field evidence and experiments should usually change policy first; stable-core promotion should wait for a real field window and multiple projects. This is the antidote to rapid version churn driven by isolated redteam findings.
