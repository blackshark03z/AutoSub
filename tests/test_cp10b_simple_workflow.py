import asyncio
import json
import re
import subprocess
from pathlib import Path

import httpx

from app.core.hashing import sha256_file
from app.core.media import media_summary
from app.services.simple_workflow import _validate_subtitle_render, _write_ass_subtitles


def configure_test_root(monkeypatch, tmp_path: Path) -> None:
    root = Path.cwd()
    monkeypatch.setenv("TOOL_AUTO_SUB_ROOT", str(root))
    monkeypatch.setenv("TOOL_AUTO_SUB_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("TOOL_AUTO_SUB_DB_PATH", str(tmp_path / "data" / "test.db"))
    monkeypatch.setenv("TOOL_AUTO_SUB_ALLOW_TEST_SUBTITLE_FIXTURES", "1")
    from app.core.config import get_settings
    from app.services import production_intake

    get_settings.cache_clear()

    real_storage_preflight = production_intake.storage_preflight

    def test_storage_preflight(operation, target_path=None, **kwargs):
        return real_storage_preflight(operation, tmp_path, **kwargs)

    monkeypatch.setattr(production_intake, "storage_preflight", test_storage_preflight)


async def _with_client(callback):
    from app.main import app

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
            return await callback(client)


def _make_tiny_video(path: Path) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=160x90:rate=10:duration=1",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=1",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(path),
        ],
        check=True,
    )


def test_cp10b_default_route_loads_simple_ui_and_operator_route_remains():
    html = Path("app/static/simple/index.html").read_text(encoding="utf-8")
    js = Path("app/static/simple/app.js").read_text(encoding="utf-8")
    operator_html = Path("app/static/operator/index.html").read_text(encoding="utf-8")
    assert '<html lang="vi">' in html
    assert "Tạo video có phụ đề" in html
    assert "Mở Advanced Operator UI" in html
    assert "/operator/" in html
    assert "Chọn video" in html
    assert "Tạo video có phụ đề" in html
    assert "Video đã hoàn tất" in html
    assert "Tùy chọn nâng cao" in html
    assert "Chi tiết xử lý lỗi" in html
    assert "Tool Auto Sub Beta" in html
    assert "<details id=\"errorDetails\"" in html
    assert "percentage: null" in js
    assert 'addEventListener("input", schedulePathValidation)' in js
    assert 'event.key === "Enter"' in js
    assert 'const requestedRun = params.get("run_id")' in js
    assert 'const explicitNew = params.get("new") === "1"' in js
    assert "/api/simple/source/upload" in js
    assert "uploadAndValidate(file)" in js
    assert "/app.js?v=task36b" in html
    assert "/styles.css?v=task36b" in html
    primary_copy = html.split("<details id=\"advancedOptions\"", 1)[0].lower()
    assert "checkpoint" not in primary_copy
    assert "canonical artifact" not in primary_copy
    assert "provider gate" not in primary_copy
    assert "Guided operator review" in operator_html


def test_task36_simple_ui_has_one_button_state_layout_and_unique_ids():
    html = Path("app/static/simple/index.html").read_text(encoding="utf-8")
    js = Path("app/static/simple/app.js").read_text(encoding="utf-8")

    assert 'role="tablist"' not in html
    assert 'role="tab"' not in html
    assert 'role="tabpanel"' not in html
    assert 'class="status-rail"' not in html
    assert "const FLOW_VIEWS = [\"setup\", \"processing\", \"completed\", \"error\"]" in js
    for view, hidden in (("setup", False), ("processing", True), ("completed", True), ("error", True)):
        marker = f'data-flow-view="{view}"'
        assert marker in html
        section = html.split(marker, 1)[0].rsplit("<section", 1)[1] + html.split(marker, 1)[1].split(">", 1)[0]
        assert ("hidden" in section) is hidden
    setup = html.split('data-flow-view="setup"', 1)[1].split('data-flow-view="processing"', 1)[0]
    assert 'id="videoPicker"' in setup
    assert 'id="subtitleStyle"' in setup
    assert 'id="startBtn"' in setup
    assert setup.count('class="primary action-primary"') == 1
    assert 'id="advancedOptions"' in setup
    assert '<details id="advancedOptions"' in setup
    assert '<details id="advancedOptions" class="advanced-disclosure" open' not in setup

    ids = re.findall(r'id="([^"]+)"', html)
    assert len(ids) == len(set(ids))
    assert "function deriveFlowView(run, { explicitNew = false } = {})" in js
    assert "function setFlowView(nextView)" in js
    assert "window.location.reload" not in js
    assert "location.reload" not in js


def test_task36_simple_ui_processing_and_completed_contracts():
    html = Path("app/static/simple/index.html").read_text(encoding="utf-8")
    js = Path("app/static/simple/app.js").read_text(encoding="utf-8")

    processing = html.split('data-flow-view="processing"', 1)[1].split('data-flow-view="completed"', 1)[0]
    completed = html.split('data-flow-view="completed"', 1)[1].split('data-flow-view="error"', 1)[0]
    error = html.split('data-flow-view="error"', 1)[1]
    assert "Đang tạo video có phụ đề" in processing
    assert 'id="processingStages"' in processing
    assert "%" not in processing
    assert "Video đã hoàn tất" in completed
    assert 'id="previewVideo"' in completed
    assert "Mở thư mục kết quả" in completed
    assert "Tạo video mới" in completed
    assert "Phụ đề được tạo tự động" in completed
    assert "Chưa thể tạo video" in error
    assert "Quay lại thiết lập" in error
    assert '<details id="errorDetails"' in error
    assert 'state.startApiCalls += 1' in js
    assert 'document.body.dataset.startApiCalls' in js
    assert "setInterval(() =>" in js
    assert "percentage" not in html


def test_cp10b_simple_ui_keeps_compact_typography_contract():
    css = Path("app/static/simple/styles.css").read_text(encoding="utf-8")

    assert "font-size: clamp(2rem, 4vw, 2.35rem);" in css
    assert "line-height: 1.08;" in css
    assert '--font-ui: "Segoe UI", "Noto Sans", Arial, sans-serif;' in css
    assert "font-family: var(--font-ui);" in css
    assert "Georgia" not in css
    assert "Times New Roman" not in css
    assert ".state-header h1" in css
    assert ".flow-view" in css
    assert ".status-rail" not in css
    assert "@media (max-width: 720px)" in css
    assert "@media (max-width: 430px)" in css


def test_task27_vietnamese_primary_flow_and_result_navigation_contract():
    html = Path("app/static/simple/index.html").read_text(encoding="utf-8")
    js = Path("app/static/simple/app.js").read_text(encoding="utf-8")
    combined = f"{html}\n{js}"

    for expected in (
        "Tạo video có phụ đề",
        "Đang tạo video có phụ đề",
        "Video đã hoàn tất",
        "Tạo video mới",
        "Quay lại thiết lập",
        "Phiên âm cục bộ",
    ):
        assert expected in combined

    for prohibited in (
        "ALWAYS VISIBLE",
        "Progress stays visible",
        "Choose video",
        "Video is ready",
        "Output filename",
        "Final validation",
        "Done:",
        "Now:",
        "Local only",
        "\ufffd",
        "Ãƒ",
        "Ã‚",
    ):
        assert prohibited not in combined

    transition = js.split('function returnToSetup({ clearSelection = false } = {})', 1)[1].split(
        "async function openRunReadOnly",
        1,
    )[0]
    assert "/start" not in transition
    assert "api(" not in transition
    assert "fetch(" not in transition
    assert "location.reload" not in transition


def test_cp10b_source_validation_run_processing_preview_approval_and_recovery(monkeypatch, tmp_path):
    configure_test_root(monkeypatch, tmp_path)
    source = tmp_path / "sample.mp4"
    _make_tiny_video(source)

    async def run(client):
        validation = await client.post("/api/simple/source/validate", json={"source_path": str(source)})
        assert validation.status_code == 200
        validation_payload = validation.json()
        assert validation_payload["status"] == "PASS"
        assert ".mp4" in validation_payload["supported_formats"]
        assert validation_payload["source"]["filename"] == "sample.mp4"
        assert validation_payload["source"]["resolution"] == {"width": 160, "height": 90}
        assert validation_payload["disk"]["estimated_working_bytes"] >= 1_000_000_000
        assert validation_payload["disk"]["safe_margin_bytes"] > 0

        created = await client.post("/api/simple/runs", json={"source_path": str(source)})
        assert created.status_code == 200
        run_payload = created.json()["run"]
        assert run_payload["source"]["path"] == str(source.resolve())
        assert run_payload["settings"]["copy_source_into_workspace"] is False
        run_dir = Path(run_payload["run_directory"])
        assert (run_dir / "source_reference.json").exists()
        assert (run_dir / "work").is_dir()
        assert (run_dir / "subtitles").is_dir()
        assert (run_dir / "output").is_dir()
        assert (run_dir / "logs").is_dir()
        assert (run_dir / "run_manifest.json").exists()
        assert not any((run_dir / "work").glob("source_copy*"))
        assert run_payload["progress"]["mode"] == "stage"
        assert run_payload["progress"]["percentage"] is None

        duplicate = await client.post("/api/simple/runs", json={"source_path": str(source)})
        assert duplicate.status_code == 200
        assert duplicate.json()["run"]["run_id"] == run_payload["run_id"]
        assert duplicate.json()["run"]["reused"] is True

        started = await client.post(f"/api/simple/runs/{run_payload['run_id']}/start")
        assert started.status_code == 200
        accepted = started.json()["run"]
        assert accepted["internal_state"] == "processing"
        assert accepted["start_accepted"] is True
        completed = (await client.get(f"/api/simple/runs/{run_payload['run_id']}")).json()["run"]
        assert completed["internal_state"] == "completed"
        assert completed["output"]["url"].endswith("/output")
        output = Path(completed["output"]["path"])
        assert output.exists()
        assert sha256_file(output) != sha256_file(source)
        output_media = media_summary(output)
        assert output_media["video"]["width"] == 160
        assert output_media["video"]["height"] == 90
        assert output_media["audio"]["codec"] is not None
        ass = run_dir / "subtitles" / "dialogue_subtitles_en.ass"
        assert ass.exists()
        ass_text = ass.read_text(encoding="utf-8")
        assert ass_text.count("\nDialogue:") == 3
        assert "Translation line 1." in ass_text
        assert "Translation line 2." in ass_text
        assert "Translation line 3." in ass_text

        preview = await client.get(completed["output"]["url"], headers={"range": "bytes=0-99"})
        assert preview.status_code in {200, 206}
        assert preview.headers["content-type"].startswith("video/mp4")
        location = await client.get(f"/api/simple/runs/{run_payload['run_id']}/output-location")
        assert location.status_code == 200
        assert Path(location.json()["folder"]) == run_dir / "output"

        approved = await client.post(f"/api/simple/runs/{run_payload['run_id']}/approve")
        assert approved.status_code == 200
        assert approved.json()["run"]["approval_state"] == "approved"

        current = await client.get("/api/simple/runs/current")
        assert current.json()["run"]["run_id"] == run_payload["run_id"]
        recent = await client.get("/api/simple/runs/recent")
        assert len(recent.json()["runs"]) <= 5
        assert all(not item["project_id"].startswith("proj_cp04_fake_") for item in recent.json()["runs"])

    asyncio.run(_with_client(run))


def test_cp10b_browser_file_picker_upload_fallback_creates_local_source(monkeypatch, tmp_path):
    configure_test_root(monkeypatch, tmp_path)
    source = tmp_path / "browser-selected.mp4"
    _make_tiny_video(source)

    async def run(client):
        upload = await client.post(
            "/api/simple/source/upload",
            headers={"content-type": "application/octet-stream", "x-filename": source.name},
            content=source.read_bytes(),
        )
        assert upload.status_code == 200
        payload = upload.json()
        uploaded_path = Path(payload["uploaded_path"])
        assert uploaded_path.exists()
        assert uploaded_path.name == f"{payload['sha256']}.mp4"
        assert payload["validation"]["status"] == "PASS"
        assert payload["validation"]["source"]["sha256"] == payload["sha256"]

        created = await client.post("/api/simple/runs", json={"source_path": payload["uploaded_path"]})
        assert created.status_code == 200
        run_payload = created.json()["run"]
        assert run_payload["source"]["path"] == str(uploaded_path)
        assert run_payload["source"]["filename"] == uploaded_path.name

    asyncio.run(_with_client(run))


def test_cp10b_resolved_cues_serialize_to_ass_dialogue_lines(tmp_path):
    ass = tmp_path / "dialogue_subtitles_en.ass"
    resolved = {
        "cues": [
            {"cue_id": "CUE_0001", "start_ms": 0, "end_ms": 450, "resolved_text": "Translation line 1."},
            {"cue_id": "CUE_0002", "start_ms": 500, "end_ms": 950, "resolved_text": "Translation line 2."},
            {"cue_id": "CUE_0003", "start_ms": 1000, "end_ms": 1500, "resolved_text": "Translation line 3."},
        ]
    }

    dialogue_count = _write_ass_subtitles(ass, resolved)
    ass_text = ass.read_text(encoding="utf-8")

    assert dialogue_count == 3
    assert ass_text.count("\nDialogue:") == 3
    assert "0:00:00.00" in ass_text
    assert "0:00:01.50" in ass_text


def test_cp10b_final_validation_rejects_empty_ass_and_identical_output(tmp_path):
    source = tmp_path / "source.mp4"
    output = tmp_path / "final_video.mp4"
    ass = tmp_path / "dialogue_subtitles_en.ass"
    _make_tiny_video(source)
    output.write_bytes(source.read_bytes())
    ass.write_text("[Script Info]\n[V4+ Styles]\n[Events]\n", encoding="utf-8")

    try:
        _validate_subtitle_render(
            source=source,
            output=output,
            ass_path=ass,
            expected_dialogues=3,
            render_command=["ffmpeg", "-i", str(source), str(output)],
        )
    except ValueError as exc:
        assert "ASS Dialogue count" in str(exc)
    else:
        raise AssertionError("validation unexpectedly accepted an empty ASS sidecar")

    ass.write_text(
        "[Script Info]\n"
        "[V4+ Styles]\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        "Dialogue: 0,0:00:00.00,0:00:00.50,Default,,0,0,0,,Visible text\n",
        encoding="utf-8",
    )
    try:
        _validate_subtitle_render(
            source=source,
            output=output,
            ass_path=ass,
            expected_dialogues=1,
            render_command=["ffmpeg", "-i", str(source), "-vf", "subtitles=dialogue_subtitles_en.ass", str(output)],
        )
    except ValueError as exc:
        assert "byte-identical" in str(exc)
    else:
        raise AssertionError("validation unexpectedly accepted byte-identical output")


def test_cp10b_copy_source_option_retry_save_copy_and_user_errors(monkeypatch, tmp_path):
    configure_test_root(monkeypatch, tmp_path)
    source = tmp_path / "copyable.mp4"
    _make_tiny_video(source)
    bad = tmp_path / "bad.txt"
    bad.write_text("not video", encoding="utf-8")
    destination = tmp_path / "saved"

    async def run(client):
        unsupported = await client.post("/api/simple/source/validate", json={"source_path": str(bad)})
        assert unsupported.status_code == 200
        assert unsupported.json()["status"] == "FAIL"
        assert unsupported.json()["error"]["title"] == "Unsupported video format"
        assert "Traceback" not in json.dumps(unsupported.json())

        created = await client.post(
            "/api/simple/runs",
            json={"source_path": str(source), "settings": {"copy_source_into_workspace": True}},
        )
        payload = created.json()["run"]
        run_dir = Path(payload["run_directory"])
        assert any((run_dir / "work").glob("source_copy*"))

        started = (await client.post(f"/api/simple/runs/{payload['run_id']}/start")).json()["run"]
        saved = await client.post(
            f"/api/simple/runs/{started['run_id']}/save-copy",
            json={"destination_folder": str(destination)},
        )
        assert saved.status_code == 200
        saved_payload = saved.json()
        assert saved_payload["byte_identical"] is True
        assert saved_payload["overwrote_existing"] is False
        assert Path(saved_payload["destination"]).exists()

        saved_again = await client.post(
            f"/api/simple/runs/{started['run_id']}/save-copy",
            json={"destination_folder": str(destination)},
        )
        assert saved_again.status_code == 200
        assert saved_again.json()["destination"] != saved_payload["destination"]

        retry = await client.post(
            "/api/simple/runs/retry",
            json={"source_path": str(source), "retry_parent_run_id": payload["run_id"]},
        )
        assert retry.status_code == 200
        retry_payload = retry.json()["run"]
        assert retry_payload["run_id"] != payload["run_id"]
        assert retry_payload["retry_parent_run_id"] == payload["run_id"]

        deleted = tmp_path / "deleted.mp4"
        subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=160x90:rate=10:duration=1", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(deleted)], check=True)
        deleted_run = (
            await client.post(
                "/api/simple/runs/retry",
                json={"source_path": str(deleted), "retry_parent_run_id": payload["run_id"]},
            )
        ).json()["run"]
        deleted.unlink()
        deleted_start = await client.post(f"/api/simple/runs/{deleted_run['run_id']}/start")
        assert deleted_start.status_code == 400
        detail = deleted_start.json()["detail"]
        assert detail["title"] == "Processing was interrupted"
        assert "Video file is no longer available" in detail["message"]
        assert "WinError" not in detail["message"]
        assert "Traceback" not in detail["message"]

    asyncio.run(_with_client(run))


def test_cp10b_operator_console_regression_and_no_provider_or_publish_calls(monkeypatch, tmp_path):
    configure_test_root(monkeypatch, tmp_path)

    async def run(client):
        projects = await client.get("/api/operator/projects")
        assert projects.status_code == 200
        assert "projects" in projects.json()
        capabilities = await client.get("/api/simple/capabilities")
        assert capabilities.status_code == 200
        assert capabilities.json()["provider_calls_on_ui_load"] == {"gemini": 0, "elevenlabs": 0, "youtube": 0}
        serialized = json.dumps(capabilities.json()).lower()
        assert "upload" not in serialized
        assert "publish" not in serialized
        assert "xi-api-key" not in serialized
        assert "api_key" not in serialized

    asyncio.run(_with_client(run))
