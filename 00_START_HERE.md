# Start Here — Build OS v1.8

This repository uses Build OS v1.8 as its only AI/control plane. Chat history and historical reports are disposable context, not authority.

## New or resumed session

1. Run `python scripts/ai_os.py status`.
2. Run `python scripts/ai_os.py check`.
3. Read `.ai/PROJECT.md`, `.ai/STATE.md`, `.ai/ACTIVE_TASK.md`, and the generated `.ai/CONTEXT_CAPSULE.md` as relevant.
4. If `ACTIVE_TASK` is active, continue through `python scripts/ai_os.py resume`; respect its lease, scope, preflight, and verification plan.
5. If there is no active task, run `python scripts/ai_os.py next` and begin only a newly authorized outcome. Do not recreate work from handoffs, prompts, reports, or chat.

Runtime/data truth and the current Git worktree outrank Build OS files; Build OS state outranks chat/history. Commands supported by this repository are discoverable through `python scripts/ai_os.py --help`.

## Product documentation

Use `README.md` for the product entrypoint, then `docs/CURRENT_STATE.md`, `docs/ARCHITECTURE.md`, `docs/OPERATIONS.md`, and `docs/DECISIONS/`. Build OS guides and templates are listed in `MANIFEST.md`.
