# Build OS entrypoint

Build OS v1.8 is the sole AI/control-plane system for this repository.

For every new session, begin with the repository state, not chat history:

1. Run `python scripts/ai_os.py status` and `python scripts/ai_os.py check`.
2. Read `.ai/PROJECT.md`, `.ai/STATE.md`, and `.ai/ACTIVE_TASK.md` as needed; `.ai/CONTEXT_CAPSULE.md` is the generated compact worker packet.
3. If a task is `ACTIVE`, use the supported `python scripts/ai_os.py resume` mechanism before writing, then follow the task scope, lease, and verification requirements.
4. If no task is active, use `python scripts/ai_os.py next`; do not reconstruct work from prior chats, handoffs, prompts, or historical reports.

Authority order is runtime/data, current Git/worktree, Build OS state, then chat/history. The canonical repository is the current source of truth. Product documentation is separate: start with `README.md`, then `docs/CURRENT_STATE.md`, `docs/ARCHITECTURE.md`, `docs/OPERATIONS.md`, and `docs/DECISIONS/`.

Historical reports and archived legacy material are not operational instructions. Use `00_START_HERE.md` and `MANIFEST.md` only as concise maps to Build OS; do not duplicate Build OS policy here.
