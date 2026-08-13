# v1.16 — Lightweight State Hazard + Fast Debug

v1.16 adds a **risk-triggered state/temporal gate** without adding a new agent, phase, or always-on checklist. Stateless work should remain effectively unchanged.

## Hazard levels

- **S0 — Stateless:** no state ceremony, no extra proof.
- **S1 — Single/simple state mechanism:** record the signal only; no new required verification command.
- **S2 — Multi-mechanism state:** require a tiny pre-code contract (`authority`, one representative `transition`, one `invariant`) and one transition proof.
- **S3 — Competing/background writer:** S2 plus one temporal/background-writer proof.
- **S4 — Explicit critical state:** reserved for operator-declared critical/race-sensitive flows; use the same bounded model rather than adding global tracing.

`--state-hazard auto` is the default. Detection intentionally uses only high-signal mechanisms such as persistence, draft/dirty state, polling/background refresh, hydration/reconciliation, cache, async/race, optimistic updates, or project/session identity. Generic UI work does not become stateful merely because the code contains a variable named `state`.

## Minimal contract

For S2+, keep the declaration short:

```bash
python scripts/ai.py start \
  --task-id EFFECTS-1 \
  --outcome "preserve dirty effect selection during polling refresh" \
  --modify "src/effects/**" \
  --state-authority "persisted project.effects" \
  --state-transition "SAVED -> EDIT -> DIRTY -> SAVE -> SAVED" \
  --state-invariant "background refresh must not overwrite DIRTY" \
  --state-dependency "src/effects/store.py"
```

The contract exists to answer the expensive debugging question **before coding**: when two sources can write the same observable value, which one wins and under what transition?

## Verification cost

S2 requires `--state-transition-command`. S3+ additionally requires `--state-temporal-command`. These commands are not automatically full-browser or soak tests; choose the cheapest deterministic proof that exercises the contract.

Exact state proofs are cached across tasks using:

```text
state contract hash
+ proof kind/command
+ declared dependency fingerprint
```

If these are unchanged, the next evidence bundle records a `REUSED STATE PROOF` instead of rerunning the expensive verifier. This implements:

> Prove once, reuse until affected source changes.

Use `--state-dependency` to narrow invalidation to the real state/API/schema surface. If omitted, task modify/create scope is used conservatively.

## Fast debugging

When a state bug appears, record only the violated transition rather than tracing the whole application:

```bash
python scripts/ai_os.py debug state-failure \
  --state DIRTY \
  --event POLL \
  --expected "preserve local draft" \
  --observed "draft replaced" \
  --hazard-class competing_writer \
  --suspect hydrateProject
```

This creates a bounded JSON failure signature under `.ai/runtime/state_failures/`. It contains no raw application-state dump. The purpose is to reduce debugging from “UI sometimes changes” to “DIRTY + POLL violated invariant X; inspect writers on that transition.”

## Evidence-infrastructure stop-loss

Acceptance tooling is not the product. If the verifier/harness fails for infrastructure reasons, record it:

```bash
python scripts/ai_os.py debug evidence-infra-failure --method playwright --note "browser boot failed"
```

Two consecutive failures of the same method activate a `next` recommendation to **change acceptance method**. Do not spend another Worker cycle repairing the same verifier unless there is evidence the product itself is failing.

## What v1.16 deliberately does not add

- no new State Manager agent;
- no always-on state checklist;
- no global event/state tracing;
- no mandatory 60-second wait for every release;
- no forced browser test when a deterministic transition test is cheaper;
- no proof rerun when contract/dependencies are unchanged.

The goal is lower **feature lead time and debug time**, not a lower first-patch timer at the expense of repeated rework.
