# v1.14 Quality Contract and Anti-Entropy Hardening

v1.14 closes the remaining quality/economics gaps without making ordinary FAST/STANDARD work heavier.

## Explicit architecture decision

Protected `health check --ci` requires executable projects to either configure `architecture_boundaries` in `config/codebase_health.json` or record an explicit reason why boundaries are not meaningful yet:

```bash
python scripts/ai_os.py health architecture-decision --no-boundaries-reason "Single-module prototype has no stable internal dependency boundary yet"
```

The waiver is deliberate and visible; it is not an implicit empty configuration. Replace it with real boundaries when modules stabilize.

## Quality capabilities

`config/quality_policy.json` defines required and recommended capabilities. The default requires `test` for executable projects and recommends `lint`, `typecheck`, and `build`. `.ai/PROJECT.md` now has dedicated `Lint command` and `Typecheck command` fields plus `CI quality capabilities` for custom aggregate commands. Protected Product CI fails closed when required capabilities are absent. Capability waivers require a substantive reason and do not waive the requirement to run at least one executable product quality command.

## Structured production dependency decision

When runtime dependency count increases, `done` requires:

- `--dependency-capability`
- `--dependency-alternatives-considered`
- `--dependency-removal-cost`

The old free-form `--dependency-justification` remains an optional note but is not sufficient by itself.

## Anti-monster-file hard ratchet

`config/codebase_health.json` now has configurable hard ratchets. By default a newly created source file above 1000 LOC is rejected; an already-large file may not grow by more than 300 LOC in one task, and high-pain hotspots have a tighter growth ceiling. Existing debt is grandfathered, so small fixes in a legacy large file remain possible.

## Regression pyramid

The default regression entrypoint runs the fast layer plus the focused v1.14 matrix. Historical v1.13/v1.12/full matrices remain release/trust jobs rather than daily product-task overhead. Every subprocess has a timeout.
