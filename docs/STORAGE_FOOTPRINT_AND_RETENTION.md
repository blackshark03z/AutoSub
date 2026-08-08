# Storage Footprint And Retention

MAINT-002 measured the repository and performed only explicitly safe cleanup. MAINT-007 replaced the obsolete fixed global storage gate with operation-specific gates so run, media, and package work are checked against measured needs immediately before execution.

## Result

- Verdict: `MAINT_002_STORAGE_FOOTPRINT_AUDIT_AND_SAFE_PRUNE_INSUFFICIENT`
- Project size before cleanup: `15,649,120,749` bytes (`14.574379` GiB)
- Project size after cleanup: `14,991,970,750` bytes (`13.962361` GiB)
- D: free before cleanup: `917,774,336` bytes (`0.854744` GiB)
- D: free after cleanup: `1,574,117,376` bytes (`1.466011` GiB)
- Bytes deleted by cleanup manifest: `657,766,514` bytes (`0.612593` GiB)
- Storage gate: `tiered_operation_storage_gate`
- Current measured free after MAINT-007: `13,344,829,440` bytes (`12.428341` GiB)
- Current operation gate status: `run=allowed`, `media=allowed`, `package=allowed`

## Protected Hashes

- CP12B ZIP: `PASS`
- CP12B ZIP SHA-256: `9a1c3b03a18049aca4f63fd43df2092eec35d5c36e9ec176dbaae7bc4d4a51d0`
- Accepted exported MP4: `PASS`
- Accepted exported MP4 SHA-256: `37394ab6ce036abdbebb6e7d9cebc8d3dc2661adae1324f0b635184042589646`

## Top 10 Largest Items After Cleanup

| Rank | Path | Bytes | GiB | Retention |
| --- | --- | ---: | ---: | --- |
| 1 | `release/CP12B/tool_auto_sub_windows_full_portable_cp12b.zip` | `327,816,938` | `0.305303` | `KEEP_CANONICAL` |
| 2 | `.git/objects/3d/bad2cc1dc98af46e6014def95e4c75c56be52a` | `322,732,645` | `0.300568` | `REVIEW_UNKNOWN` |
| 3 | `.git/objects/cc/bcdb1cefde55939e1dd40d59353c4735483cd6` | `322,718,778` | `0.300555` | `REVIEW_UNKNOWN` |
| 4 | `.git/objects/b4/de6b6925806341f65e81a07a19167a84014c4e` | `244,752,261` | `0.227943` | `REVIEW_UNKNOWN` |
| 5 | `.git/objects/52/bbf0db63737159ea1dc132c85c72d0f250e616` | `244,309,422` | `0.227531` | `REVIEW_UNKNOWN` |
| 6 | `data/projects/production-golden-path-cp09/source/input.mp4` | `95,374,240` | `0.088824` | `KEEP_ACTIVE_USER_DATA` |
| 7 | `data/projects/production_golden_path_cp09/source/source.mp4` | `95,374,240` | `0.088824` | `KEEP_ACTIVE_USER_DATA` |
| 8 | `data/projects/proj_026eb4c85d79/source/input.mp4` | `95,374,240` | `0.088824` | `KEEP_ACTIVE_USER_DATA` |
| 9 | `data/projects/proj_02f03ccf0a08/source/input.mp4` | `95,374,240` | `0.088824` | `KEEP_ACTIVE_USER_DATA` |
| 10 | `data/projects/proj_03ec7cdfb30e/source/input.mp4` | `95,374,240` | `0.088824` | `KEEP_ACTIVE_USER_DATA` |

## Largest Directories After Cleanup

| Rank | Path | Bytes | GiB |
| --- | --- | ---: | ---: |
| 1 | `data` | `13,027,604,483` | `12.132902` |
| 2 | `data\projects` | `13,025,400,702` | `12.130849` |
| 3 | `data\projects\production_golden_path_cp09` | `3,233,662,121` | `3.011682` |
| 4 | `data\projects\production_golden_path_cp09\exports` | `3,063,473,373` | `2.853173` |
| 5 | `data\projects\vertical_slice_cp07\renders` | `1,682,116,570` | `1.566463` |
| 6 | `.git` | `1,221,508,770` | `1.137619` |
| 7 | `.git\objects` | `1,221,355,613` | `1.137476` |
| 8 | `data\projects\vertical_slice_cp07` | `1,145,461,780` | `1.066758` |
| 9 | `release\CP12B` | `655,645,156` | `0.610622` |
| 10 | `data\projects\vertical_slice_cp07\intermediates` | `440,377,754` | `0.410144` |

## Deleted Files And Directories

Obsolete release binaries deleted after CP12B hash verification:

- `release\CP11A\tool_auto_sub_windows_portable_cp11a.zip` (`79,060,001` bytes)
- `release\CP11C\tool_auto_sub_ocr_runtime_addon_cp11c.zip` (`248,123,355` bytes)
- `release\CP11D\tool_auto_sub_windows_full_portable_cp11d.zip` (`327,802,871` bytes)

Regenerable local cache directories deleted:

- `alembic\__pycache__`
- `alembic\versions\__pycache__`
- `app\__pycache__`
- `app\api\__pycache__`
- `app\core\__pycache__`
- `app\db\__pycache__`
- `app\domain\__pycache__`
- `app\providers\__pycache__`
- `app\providers\asr\__pycache__`
- `app\providers\translation\__pycache__`
- `app\providers\tts\__pycache__`
- `app\services\__pycache__`
- `app\worker\__pycache__`
- `tests\__pycache__`
- `tools\__pycache__`
- `.pytest_cache`

No validation or staging extraction path was deleted by MAINT-002. No duplicate test or evidence file was deleted automatically.

## Retained Items

- `release\CP12B\tool_auto_sub_windows_full_portable_cp12b.zip` remains the only retained release ZIP.
- CP11A, CP11C, and CP11D manifest/checksum metadata remain.
- `data\projects\vertical_slice_cp07` is the only retained development project after the MAINT-004 operator-approved whitelist cleanup.
- `data\` remains protected because it contains SQLite state and retained project data.
- `D:\tool_auto_sub_ocr_runtime` is retained as `KEEP_DEVELOPMENT_DEPENDENCY`; measured size is `1,163,822,603` bytes (`1.083894` GiB).
- `.git` was not rewritten. `.git` size is `1,221,465,995` bytes (`1.137579` GiB); object database size is `1,221,355,613` bytes (`1.137476` GiB). Large loose blob objects remain for CP12B, CP11D, CP11C, CP11A, and one unmapped historical blob. No history rewrite was performed.

## MAINT-004 Project Data Prune

The operator-approved whitelist was:

- `vertical_slice_cp07`

MAINT-004 deleted only exact direct child directories under `data\projects` that were outside the whitelist and passed pre-deletion verification.

- Project directories before: `1,112`
- Project directories after: `374`
- Deleted project directories: `738`
- Deleted bytes: `8,365,413,168` bytes (`7.790898` GiB)
- Deleted files: `1,284`
- D: free before MAINT-004: `1,525,374,976` bytes (`1.420616` GiB)
- D: free after MAINT-004 final documentation: `9,889,660,928` bytes (`9.210464` GiB)
- Historical fixed-gate shortfall recorded at MAINT-004: `6,216,466,432` bytes (`5.789536` GiB)

Deleted classifications:

- `DELETE_DUPLICATE_TEST_PROJECT`: `87`
- `DELETE_ORPHAN_PROJECT`: `51`
- `DELETE_SYNTHETIC_TEST_PROJECT`: `600`

Skipped:

- DB-known projects skipped because no application-level retirement service exists: `51`
- Uncertain projects skipped: `322`

## MAINT-005 Residual Project Retirement

MAINT-005 added a minimal `ProjectRetirementService` and used it to retire DB-known synthetic/test projects transactionally. It also resolved the remaining uncertain directories using scope, ownership, symlink, accepted-hash, and project-generated layout checks.

- Project directories before MAINT-005: `374`
- Project directories after MAINT-005: `2`
- Canonical retained project bytes: `1,145,461,648`
- DB-known group before retirement: `51` directories, `3,236,486,723` bytes
- Uncertain group before cleanup: `322` directories, `5,341,196` bytes
- Uncertain directories deleted: `322`
- Uncertain bytes deleted: `5,341,196`
- DB-known projects retired: `50`
- DB-known bytes retired: `275,526,074`
- Remaining skipped directory: `production_golden_path_cp09`
- Remaining skip reason: contains protected accepted media hash.
- D: free after MAINT-005 final validation: `10,133,532,672` bytes (`9.437588` GiB)
- Historical fixed-gate shortfall recorded at MAINT-005: `5,972,594,688` bytes (`5.562412` GiB)

Remaining project directories:

- `vertical_slice_cp07`: retained by operator whitelist.
- `production_golden_path_cp09`: retained because it contains protected accepted release media copies.

## MAINT-006 Accepted Media Dedup And Legacy Project Retirement

MAINT-006 verified the preferred canonical accepted artifact and retired the legacy CP09 project after confirming its accepted media copies were byte-identical duplicates.

- Canonical accepted artifact: `data\projects\vertical_slice_cp07\renders\cp08e2_decoupled_suppression_english_plate_720p.mp4`
- Canonical accepted SHA-256: `37394ab6ce036abdbebb6e7d9cebc8d3dc2661adae1324f0b635184042589646`
- Legacy project retired: `production_golden_path_cp09`
- Legacy project size before retirement: `3,233,658,616` bytes (`3.011579` GiB)
- Legacy accepted duplicate copies: `44`
- Remaining project directories after retirement: `1`
- Remaining project directory: `vertical_slice_cp07`
- Current project-root size: `3,120,358,669` bytes (`2.906060` GiB)
- Current `data\projects` size: `1,145,461,648` bytes (`1.066794` GiB)
- D: free after MAINT-006 validation: `13,357,748,224` bytes (`12.440372` GiB)
- Historical fixed-gate shortfall recorded at MAINT-006: `2,748,379,136` bytes (`2.559628` GiB)

Measured storage gate recommendations for operator review:

- `RUN_ONLY_MIN_FREE`: `1,073,741,824` bytes (`1.000000` GiB)
- `MEDIA_PROCESSING_MIN_FREE`: `2,147,483,648` bytes (`2.000000` GiB)
- `PACKAGE_BUILD_MIN_FREE`: `4,294,967,296` bytes (`4.000000` GiB)

MAINT-007 retired the fixed 15 GiB global gate. The replacement measured thresholds are:

- `RUN_ONLY_MIN_FREE`: `1,073,741,824` bytes (`1.000000` GiB)
- `MEDIA_PROCESSING_MIN_FREE`: `2,147,483,648` bytes (`2.000000` GiB)
- `PACKAGE_BUILD_MIN_FREE`: `4,294,967,296` bytes (`4.000000` GiB)

Package builds use a dynamic safety check: require the greater of 4 GiB and projected temporary workspace plus configured safety reserve. Build/render tools must run storage preflight immediately before execution. Temporary artifacts should be removed after acceptance when they are exact project-owned staging/extraction paths with no unique user data. Low disk space never auto-deletes user media.

## Duplicate Findings

The audit found duplicate media groups, including:

- Source video hash `34a304fb44f5e4c27d1a34989a69f939888ef90c89bbae0142434f43cf4db068`: `90` copies of `95,374,240` bytes.
- Accepted MP4 hash `37394ab6ce036abdbebb6e7d9cebc8d3dc2661adae1324f0b635184042589646`: `46` copies of `71,147,086` bytes.

These were not deleted because they are project/run media and require an operator-approved archive or deduplication plan.

## Runtime And Model Duplicate Analysis

- CP12B remains the only retained local Full Portable ZIP in `release\`.
- CP11A, CP11C, and CP11D historical release ZIP/add-on binaries were deleted, leaving only manifests/checksums.
- No package staging or external validation extraction directory was deleted in MAINT-002; none was proven safe and present inside the authorized scope during this run.
- `D:\tool_auto_sub_ocr_runtime` remains a separate development OCR runtime and is not treated as disposable because development launch dependency has not been retired.
- Git history still contains large loose objects; no history rewrite was performed.

## Git Large-Object Finding

The audit identified large loose Git blobs over 10 MiB:

- `release/CP12B/tool_auto_sub_windows_full_portable_cp12b.zip`: `327,816,938` bytes
- `release/CP11D/tool_auto_sub_windows_full_portable_cp11d.zip`: `327,802,871` bytes
- `release/CP11C/tool_auto_sub_ocr_runtime_addon_cp11c.zip`: `248,123,355` bytes
- Unmapped historical blob `52bbf0db63737159ea1dc132c85c72d0f250e616`: `247,680,897` bytes
- `release/CP11A/tool_auto_sub_windows_portable_cp11a.zip`: `79,060,001` bytes

These remain in Git history/objects and require a separate explicit history-cleaning decision if removal is desired.

## Retention Policy

- Keep the canonical CP12B release package, manifest, checksums, and accepted media.
- Keep SQLite databases, Alembic state, secrets, source code, and active project data.
- Delete historical release binaries from the working drive after a newer canonical release is verified, while retaining their manifest/checksum evidence.
- Delete only deterministic local caches and abandoned staging/extraction directories when scope is exact and no active process depends on them.
- Do not delete duplicate media automatically unless an operator approves a copy-hash-verify-remove plan.
- Test/smoke projects are disposable after their tests finish.
- Active real-user projects must never be deleted automatically.
- Future project cleanup requires explicit user confirmation.
- Project/run media must not accumulate across maintenance checkpoints.
- Do not rewrite Git history as part of storage recovery without a separate explicit decision.

## Validation

- SQLite quick_check: `ok`
- Alembic schema: `0009_subtitle_tracks`
- Canonical documentation validator: `PASS`
- Storage audit script: `PASS`
- Cleanup dry-run before apply: `PASS`
- Cleanup apply: `PASS`
- MAINT-004 protected hash validation: `PASS`
- MAINT-004 SQLite validation: `PASS`

## Next Action

The project should continue to use operation-specific storage preflight before run, media, package, render, or validation work. External-machine beta remains pending, not blocked by the retired fixed global storage gate at the current measured free space.
