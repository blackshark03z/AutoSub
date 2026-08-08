import asyncio
import json
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.core.hashing import sha256_file
from app.db.session import init_db, session_scope
from app.domain.models import Project, SimpleWorkflowRun
from tests.test_cp10b_simple_workflow import _make_tiny_video, _with_client, configure_test_root


def _write_ass(path: Path, *, dialogue: bool) -> None:
    lines = [
        "[Script Info]",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    if dialogue:
        lines.append("Dialogue: 0,0:00:00.00,0:00:00.80,Default,,0,0,0,,Visible subtitle")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _insert_completed_run(
    tmp_path: Path,
    *,
    run_id: str,
    source: Path,
    output: Path,
    dialogue: bool,
    updated_at: datetime,
) -> Path:
    project_id = f"project_{run_id}"
    run_dir = tmp_path / "data" / "projects" / project_id / "runs" / run_id
    for child in ("subtitles", "output", "work", "logs"):
        (run_dir / child).mkdir(parents=True, exist_ok=True)
    final_output = run_dir / "output" / "final_video.mp4"
    shutil.copy2(output, final_output)
    _write_ass(run_dir / "subtitles" / "dialogue_subtitles_en.ass", dialogue=dialogue)
    source_metadata = {
        "path": str(source),
        "filename": source.name,
        "sha256": sha256_file(source),
        "duration_seconds": 1.0,
        "resolution": {"width": 160, "height": 90},
    }
    with session_scope() as session:
        session.add(Project(project_id=project_id, title=project_id))
        session.add(
            SimpleWorkflowRun(
                run_id=run_id,
                project_id=project_id,
                source_path=str(source),
                source_hash=sha256_file(source),
                source_metadata_json=json.dumps(source_metadata),
                requested_settings_json=json.dumps({"subtitle_mode": "burned_into_video"}),
                current_phase="Preview",
                internal_state="completed",
                run_directory=str(run_dir),
                output_path=str(final_output),
                output_hash=sha256_file(final_output),
                approval_state="not_reviewed",
                is_test_fixture=False,
                completed_at=updated_at,
                updated_at=updated_at,
            )
        )
    return final_output


def _make_distinct_video(source: Path, output: Path) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(source),
            "-vf",
            "eq=brightness=0.02",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "copy",
            str(output),
        ],
        check=True,
    )


def test_task33_repairs_empty_ass_and_identical_output_but_preserves_valid_run(monkeypatch, tmp_path):
    configure_test_root(monkeypatch, tmp_path)
    init_db()
    source = tmp_path / "source.mp4"
    distinct = tmp_path / "distinct.mp4"
    _make_tiny_video(source)
    _make_distinct_video(source, distinct)
    now = datetime.now(timezone.utc)

    empty_output = _insert_completed_run(
        tmp_path,
        run_id="run_invalid_empty_ass",
        source=source,
        output=distinct,
        dialogue=False,
        updated_at=now + timedelta(seconds=2),
    )
    identical_output = _insert_completed_run(
        tmp_path,
        run_id="run_invalid_identical",
        source=source,
        output=source,
        dialogue=True,
        updated_at=now + timedelta(seconds=1),
    )
    valid_output = _insert_completed_run(
        tmp_path,
        run_id="run_valid",
        source=source,
        output=distinct,
        dialogue=True,
        updated_at=now,
    )

    async def run(client):
        current = await client.get("/api/simple/runs/current")
        assert current.status_code == 200
        assert current.json()["run"]["run_id"] == "run_valid"
        assert current.json()["run"]["result_validation"]["status"] == "PASS"
        assert current.json()["run"]["output"]["url"]

        recent = (await client.get("/api/simple/runs/recent")).json()["runs"]
        by_id = {item["run_id"]: item for item in recent}
        assert by_id["run_invalid_empty_ass"]["failure_category"] == "invalid_completed_result"
        assert by_id["run_invalid_empty_ass"]["result_validation"]["reason_code"] == "subtitle_dialogue_missing"
        assert by_id["run_invalid_identical"]["result_validation"]["reason_code"] == "output_identical_to_input"
        assert by_id["run_invalid_empty_ass"]["output"]["url"] is None
        assert by_id["run_invalid_identical"]["output"]["url"] is None

        blocked = await client.get("/api/simple/runs/run_invalid_empty_ass/output")
        assert blocked.status_code == 409
        assert blocked.json()["detail"]["title"] == "Kết quả không hợp lệ"
        assert "Không có phụ đề" in blocked.json()["detail"]["message"]

        valid = await client.get("/api/simple/runs/run_valid/output")
        assert valid.status_code == 200
        assert valid.headers["content-type"].startswith("video/mp4")

    asyncio.run(_with_client(run))

    assert empty_output.exists()
    assert identical_output.exists()
    assert valid_output.exists()
    with session_scope() as session:
        rows = {row.run_id: row for row in session.query(SimpleWorkflowRun).all()}
        assert rows["run_invalid_empty_ass"].internal_state == "blocked"
        assert rows["run_invalid_identical"].internal_state == "blocked"
        assert rows["run_valid"].internal_state == "completed"


def test_task33_history_ui_warns_without_result_link_for_invalid_rows():
    js = Path("app/static/simple/app.js").read_text(encoding="utf-8")
    recent_block = js.split("async function renderRecent()", 1)[1].split("async function restoreRun()", 1)[0]

    assert "Kết quả không hợp lệ - Không có phụ đề" in recent_block
    assert "run.output?.url && run.result_eligible" in recent_block
    assert 'run.result_validation?.status === "FAIL"' in js
