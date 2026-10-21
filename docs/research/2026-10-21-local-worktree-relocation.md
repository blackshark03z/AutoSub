# AutoSub local workspace relocation — 2026-10-21

**Status:** completed, verified, non-destructive local filesystem reorganization requested by Owner. This is an operational change, not a product/acceptance/release decision; defer to the active Product SoT and accepted `POLICY_REF=caea6f255aa16ce36827a875bcdbca0f8b57cb1e`.

## New layout

- **Primary Git repository (historical pilot checkout):** `D:\AutoSub`, branch `feat/pilot-e2e-local-sub-overlay`, HEAD `0d0c28a`. Its **280 pre-existing uncommitted status entries** were preserved exactly.
- **Linked worktree parent:** `D:\AutoSub\_worktrees`, excluded from primary checkout via **local** `.git/info/exclude` (not committed).
- **Active authoritative policy/Product SoT worktree:** `D:\AutoSub\_worktrees\AutoSub-policy-main`, branch `main`.
- **M1:** `D:\AutoSub\_worktrees\AutoSub-M1-local-url-mp4`.
- **M2:** `D:\AutoSub\_worktrees\AutoSub-M2-longmedia`.
- **M3:** `D:\AutoSub\_worktrees\AutoSub-M3-multisource`.
- **Other linked worktrees:** `AutoSub-acceptance-longpilot`, `AutoSub-acceptance-us`, `AutoSub-integration`, `AutoSub-lane-b`, `AutoSub-lane-c`, `AutoSub-lane-d`, `AutoSub-lane-e1`, `AutoSub-lane-e2-runtime`, `AutoSub-live-verification` under the same `_worktrees` directory. The longpilot checkout's 17 uncommitted status entries were preserved unchanged.
- Historical MAR-owned worktrees under `D:\MAR` and tool-temp on C: were **not** moved, as they are managed by MAR.

## Verification and retention

Exactly 13 linked Git worktrees were moved using `git worktree move` on the same D: volume, never raw cut/paste. The original branch, full HEAD and SHA-256 of the Git porcelain status output were individually checked for **all 14 primary+linked AutoSub checkouts** before and after. Worktree-prune dry-run reports no stale registrations. No branches, worktrees, source files, model weights or artifacts were deleted or reset.

The following local output MP4 SHA-256 values were reverified **after moving**:
- M1: `e535b521ce286cf85dfb35ac2ed18d869f75212b964245adac1557efd4d0586b`.
- M2: `74b3e6374bca9b0957c168ed0794fafddd1ff2cc17cb02742f779edf9d109e0e`.
- M3 Hongguo: `287bb090891c2a441045948db311819292d7036ed3f47f6802438834be28bdd4`.

Read-only local audit and relocation mapping are **NOT Git tracked**: `D:\AutoSub\_worktrees\_relocation-audit.json`, `README_WORKTREES.md`, `DRIVE_D_AUDIT_2026-10-21.md`. Original Hongguo episodes remain unchanged in `D:\Youtube\Youtube Package Crawl\output_hongguo`.

**Path migration warning:** Earlier WPs, manifests, source provenance logs and OpenCode/MAR session configuration may contain absolute pre-move paths, which are historical evidence, **not currently executable worktree locations**. Do not mass rewrite signed/hashed old manifests, or run old commands without rebinding checked source hashes and current paths. For any future work, reattach/match exact new worktree, branch and HEAD through MAR before action.

## Ownership boundary

Other D: projects, OpenCode cache directories, YouTube media and `D:\MAR` were not reorganized. Storage scan identified several large directories but **did not delete duplicates or cache**. The previously accepted STOP after M3 / publishing-effectiveness decision remains in force.
