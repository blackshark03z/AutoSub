# Execution lanes, fail-closed CI and runtime telemetry

## Product Goal != Goal Mode

Orchestrator route trước khi tạo lifecycle:

- **FAST** — R0/R1, clear root cause, ~1–2 explicit files, one Worker.
- **STANDARD** — bounded single outcome, thường ~2–5 files/R1–R2, one Worker + task kernel.
- **GOAL** — dependency DAG, uncertainty, multiple acceptance surfaces hoặc useful parallelism.

```bash
python scripts/ai_os.py route --outcome "..." --accept "..." --modify "src/a.py,src/b.py" --risk R2 --json
```

## Fail-closed Product CI

`.ai/PROJECT.md` có canonical `Install command`, `Test command`, `Build command`, `CI quality command`. Protected CI ưu tiên contract; autodetection chỉ là convenience. Executable project mà không có runnable quality check **FAIL**, không return green vì pytest/tool chưa được cài.

## Provider-neutral telemetry

Outer runtime ghi normalized usage records rồi ingest:

```json
{"goal_id":"G-12","node_id":"S1","role":"SCOUT","model":"cheap","work_class":"BUG_UNKNOWN:BROAD","input_tokens":8000,"cached_input_tokens":5000,"output_tokens":280,"provider_cost":0.004,"wall_seconds":25}
```

```bash
python scripts/ai_os.py telemetry ingest --file usage.jsonl
python scripts/ai_os.py telemetry report
```

Delegation feedback chỉ thay đổi routing khi có đủ sample. Không có telemetry = UNKNOWN, không phải zero. Closed-loop hiện conservative: historical Scout class chỉ được boost khi strong-Worker input giảm rõ; bị downgrade khi Scout không tạo saving quan sát được.

## Conservative closed-loop delegation

After at least three comparable with-Scout and without-Scout Worker observations, the planner compares strong-Worker input reduction and, when available, **delegated total provider cost (Scout + Worker)**. Cost telemetry overrides optimistic token-only conclusions when delegation materially increases total spend. Wall time is reported as a diagnostic signal; parallelism is still treated as a speed optimization, not a token-saving assumption.


## v1.14 quality capabilities

Product CI distinguishes `test`, `lint`, `typecheck`, and `build`. Dedicated Project Contract commands are preferred and deduplicated against aggregate `CI quality command` so the same verification is not paid twice. `config/quality_policy.json` requires `test` by default for executable projects and recommends the static/build capabilities. A capability waiver must be explicit and substantive; it does not turn an executable project with zero runnable checks green.
