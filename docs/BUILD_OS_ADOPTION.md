# Build OS v1.22 Lifecycle Adoption

## Active authority

The sole normal lifecycle executor is the external package resolved by `.buildos-authority.json`. Its immutable Build OS generation and CURRENT pointer remain the lifecycle authority. The Project Lifecycle Kit supplies the Knowledge Pack and the documentation-handoff-continuity sidecar supplies active operational intent; neither replaces frozen-kernel proof.

## Lifecycle-validation task

`BUILD_OS_V122_LIFECYCLE_ADOPTION/r001` is limited to recording and validating
this control-plane transition. It may update this adoption record and the
Developer Workflow authority only; it does not change AutoSub product behavior.

## Legacy cleanup manifest

| Previous path | Previous role | Action | Recovery location / reason |
| --- | --- | --- | --- |
| `.ai/` | v1.16 active state, Goal projections, evidence, and history | ARCHIVE | `archives/senior-ai-build-os-v1.16/.ai/`; preserves evidence but removes active discovery. |
| `scripts/` | v1.16 lifecycle facade, kernel, support modules, and self-tests | ARCHIVE | `archives/senior-ai-build-os-v1.16/scripts/`; no product entrypoint or application import uses these scripts. |
| `00_START_HERE.md`, `OWNER_QUICKSTART.md`, `MANIFEST.md` | legacy worker/owner control instructions | ARCHIVE | `archives/senior-ai-build-os-v1.16/`; replaced by this authority record and `AGENTS.md`. |
| `docs/01_*.md` through `docs/21_*.md` | legacy Build OS policy and operating guidance | ARCHIVE | `archives/senior-ai-build-os-v1.16/docs/`; accepted product architecture and operations documents remain in `docs/`. |
| `prompts/` and `templates/` | legacy worker, Goal, evidence, and CI control material | ARCHIVE | `archives/senior-ai-build-os-v1.16/`; no product runtime consumes them. |
| `config/assurance.json`, `config/codebase_health.json`, `config/gates.json`, `config/kernel_contract.json`, `config/quality_policy.json`, `config/risk_semantics.json` | v1.16 governance policy | ARCHIVE | `archives/senior-ai-build-os-v1.16/config/`; product runtime configuration is unaffected. |
| `.github/workflows/ai-build-os.yml` | legacy Build OS CI | ARCHIVE | `archives/senior-ai-build-os-v1.16/.github/workflows/`; it only invokes archived lifecycle scripts. |
| `C:\ToolAutoSub\.build-os-v1.22-portable-field-proven\build-os-v1.22-portable` | prior external v1.22 package | KEEP (provenance only) | Remains external and is no longer named by the authority record or worker instructions. |

No AutoSub product source, providers, UI, translations, runtime binaries, machine-local assets, accepted product evidence, or Field Study ledger is deleted by this transition.
