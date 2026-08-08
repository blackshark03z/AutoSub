import asyncio
import json
import re
from pathlib import Path

import httpx
import shutil
import subprocess
from uuid import uuid4


def configure_test_root(monkeypatch, tmp_path: Path, *, root: Path | None = None) -> None:
    root = root or Path.cwd()
    monkeypatch.setenv("TOOL_AUTO_SUB_ROOT", str(root))
    data_dir = root / "data" if root != Path.cwd() else tmp_path / "data"
    monkeypatch.setenv("TOOL_AUTO_SUB_DATA_DIR", str(data_dir))
    monkeypatch.setenv("TOOL_AUTO_SUB_DB_PATH", str(data_dir / "test.db"))
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


def test_cp08_operator_summary_is_safe_and_complete(monkeypatch, tmp_path):
    configure_test_root(monkeypatch, tmp_path)

    async def run(client):
        response = await client.get("/api/operator/projects/vertical_slice_cp07/summary")
        assert response.status_code == 200
        payload = response.json()
        assert [stage["stage_id"] for stage in payload["stages"]] == [
            "preflight",
            "delogo",
            "transcript",
            "english",
            "voice",
            "preview",
            "complete",
        ]
        assert payload["project"]["completed"] is True
        assert payload["timeline"]["segment_count"] >= 442
        assert payload["timeline"]["tts_group_count"] == 89
        assert payload["preview"]["url"].endswith("/accepted-preview")
        assert payload["provider_summary"]["gemini_calls_on_ui_load"] == 0
        assert payload["provider_summary"]["elevenlabs_calls_on_ui_load"] == 0
        serialized = str(payload).lower()
        forbidden = ["xi-api-key", "api_key", "authorization", "secret", "request_id", "credential_ref"]
        assert not any(token in serialized for token in forbidden)

    asyncio.run(_with_client(run))


def test_cp08_approval_gates_and_issue_first_model(monkeypatch, tmp_path):
    configure_test_root(monkeypatch, tmp_path)

    async def run(client):
        payload = (await client.get("/api/operator/projects/vertical_slice_cp07/summary")).json()
        gate_ids = {gate["gate_id"] for gate in payload["approval_gates"]}
        assert {
            "source_provenance",
            "transcript",
            "english_content",
            "voice_timing",
            "subtitle_removal",
            "preview_qa",
        } <= gate_ids
        assert all(gate["state"] == "Approved" for gate in payload["approval_gates"])
        issues = payload["issues"]
        severities = [issue["severity"] for issue in issues]
        assert severities == sorted(severities, key=lambda value: {"blocker": 0, "warning": 1, "clean": 3}.get(value, 2))
        summary = payload["issue_summary"]
        assert summary["total"] == summary["unresolved"] + summary["reviewed"] + summary["clean_without_review_requirement"]
        assert summary["needs_review"] == summary["unresolved"]
        assert all(issue["needs_review"] for issue in issues if issue.get("reviewed") is False and issue["severity"] == "clean")
        issue_id = issues[0]["issue_id"]
        reviewed = await client.post(
            "/api/operator/projects/vertical_slice_cp07/issues/review",
            json={"issue_id": issue_id},
        )
        assert reviewed.status_code == 200
        assert reviewed.json() == {"issue_id": issue_id, "reviewed": True}
        updated = (await client.get("/api/operator/projects/vertical_slice_cp07/summary")).json()
        updated_issue = next(issue for issue in updated["issues"] if issue["issue_id"] == issue_id)
        assert updated_issue["reviewed"] is True
        assert updated["issue_summary"]["total"] == (
            updated["issue_summary"]["unresolved"]
            + updated["issue_summary"]["reviewed"]
            + updated["issue_summary"]["clean_without_review_requirement"]
        )

    asyncio.run(_with_client(run))


def test_cp08_static_ui_contains_navigation_virtualization_and_no_auto_provider_controls():
    html = Path("app/static/operator/index.html").read_text(encoding="utf-8")
    js = Path("app/static/operator/app.js").read_text(encoding="utf-8")
    assert 'id="backBtn"' in html
    assert 'id="nextBtn"' in html
    assert "Issue-first review" in html
    assert "const WINDOW_SIZE = 36" in js
    assert 'data-testid="segment-list"' in js
    assert "full editors rendered" in js
    assert "localStorage.setItem" in js
    assert "/content/transform" not in js
    assert "/transcribe" not in js
    assert "xi-api-key" not in js
    assert "api_key" not in js
    assert "This project is already complete. Use View artifact details above." in js
    assert "next.textContent = \"Completed\"" in js


def test_cp08a_complete_stage_metadata_layout_is_contained_and_responsive():
    css = Path("app/static/operator/styles.css").read_text(encoding="utf-8")
    js = Path("app/static/operator/app.js").read_text(encoding="utf-8")
    assert ".complete-grid" in css
    assert "grid-template-columns: minmax(0, 2fr) minmax(260px, 0.9fr)" in css
    assert "@media (min-width: 981px) and (max-width: 1500px)" in css
    assert ".complete-grid { grid-template-columns: minmax(0, 1fr); }" in css
    assert "overflow-wrap: anywhere" in css
    assert "word-break: break-word" in css
    assert "min-width: 0" in css
    assert "copy-value" in js
    assert "Completed checkpoint" in js
    assert "SHA-256" in js


def test_cp08a_issue_filters_share_canonical_needs_review_state():
    js = Path("app/static/operator/app.js").read_text(encoding="utf-8")
    assert 'badge(`${stats.needs_review} needs review`)' in js
    assert 'badge(`${stats.clean_without_review_requirement} clean`)' in js
    assert 'return issue.needs_review && !issue.reviewed' in js
    assert 'return issue.severity === "clean" && !issue.needs_review' in js
    assert "function issueStatusLabel(issue)" in js


def test_cp08_segment_search_operates_over_full_set_but_render_window_is_bounded(monkeypatch, tmp_path):
    configure_test_root(monkeypatch, tmp_path)

    async def run(client):
        payload = (await client.get("/api/operator/projects/vertical_slice_cp07/summary")).json()
        segments = payload["segments"]
        assert len(segments) >= 442
        matches = [segment for segment in segments if "seg_0411" in segment["id"] or "Momo" in segment["spoken_text"]]
        assert any(segment["id"] == "seg_0411" for segment in matches)
        window_size = 36
        rendered = matches[:window_size]
        assert len(rendered) < len(segments)

    asyncio.run(_with_client(run))


def test_cp08_accepted_preview_stream_endpoint(monkeypatch, tmp_path):
    configure_test_root(monkeypatch, tmp_path)

    async def run(client):
        response = await client.get("/api/operator/projects/vertical_slice_cp07/accepted-preview", headers={"range": "bytes=0-99"})
        assert response.status_code in {200, 206}
        assert response.headers["content-type"].startswith("video/mp4")

    asyncio.run(_with_client(run))


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


def _configure_isolated_cp07a_fixture(monkeypatch, tmp_path: Path) -> Path:
    """Create the CP07A inputs needed by the project picker inside this test only."""
    root = tmp_path / "workspace"
    source = root / "input" / "tiny.mp4"
    source.parent.mkdir(parents=True)
    _make_tiny_video(source)

    operator_dir = root / "operator"
    operator_dir.mkdir()
    (operator_dir / "run_config.json").write_text(
        json.dumps({"source": {"path": "input/tiny.mp4"}, "subtitle": {"font_path": r"C:\\Windows\\Fonts\\arial.ttf"}}),
        encoding="utf-8",
    )

    artifact = root / "data" / "projects" / "vertical_slice_cp07" / "renders" / "cp07a_targeted_human_review_repair_720p.mp4"
    artifact.parent.mkdir(parents=True)
    shutil.copy2(source, artifact)
    (artifact.parent / "cp07_full_canonical_audio_subtitle_timeline.json").write_text(
        json.dumps(
            {
                "source": {"path": str(source), "media": {"duration_seconds": 1.0, "video": {"width": 160, "height": 90, "avg_frame_rate": "10/1"}}},
                "canonical_timeline": {
                    "source_language": "zh",
                    "target_locale": "en-US",
                    "source_duration_ms": 1000,
                    "segments": [{"id": "fixture_001", "ordinal": 1, "start_ms": 0, "end_ms": 1000, "source_text": "fixture", "translated_text": "fixture", "spoken_text": "fixture", "subtitle_text": "fixture", "issues": [], "asr": {}}],
                },
            }
        ),
        encoding="utf-8",
    )
    summary = root / "evidence" / "CP07" / "cp07a_targeted_repair_summary.json"
    summary.parent.mkdir(parents=True)
    summary.write_text(
        json.dumps(
            {
                "corrected_segments": {},
                "media": {"duration_seconds": 1.0, "video": {"width": 160, "height": 90}},
                "audio_qa": {"status": "PASS", "source_audio_removed": True, "missing_spoken_units": 0, "narration_overlap_count": 0},
                "subtitle_qa": {"subtitle_progression_violations": 0, "blank_subtitle_cues": 0},
                "visual_qa": {"short_subtitle_flashes": 0, "unnecessary_delogo_toggles": 0},
                "targeted_visual_qa": {"status": "NOT_RUN", "residual_chinese_0812_0815": 0},
            }
        ),
        encoding="utf-8",
    )
    configure_test_root(monkeypatch, tmp_path, root=root)
    return source


def test_cp08c_new_project_intake_preflight_and_create(monkeypatch, tmp_path):
    source = _configure_isolated_cp07a_fixture(monkeypatch, tmp_path)
    slug = f"cp08c-{uuid4().hex[:8]}"

    async def run(client):
        preflight = await client.post("/api/operator/source/preflight", json={"source_path": str(source), "slug": slug})
        assert preflight.status_code == 200
        payload = preflight.json()
        assert payload["status"] == "PASS"
        assert payload["checks"]["ffprobe"] is True
        assert payload["checks"]["audio_stream_present"] is True

        created = await client.post(
            "/api/operator/projects/create",
            json={
                "name": "CP08C Test",
                "slug": slug,
                "source_path": str(source),
                "provenance_acknowledged": True,
            },
        )
        assert created.status_code == 200
        created_payload = created.json()
        assert created_payload["project_id"] == slug
        assert created_payload["provider_calls"] == {"gemini": 0, "elevenlabs": 0}

        projects = (await client.get("/api/operator/projects")).json()["projects"]
        assert any(project["project_id"] == slug for project in projects)
        summary = (await client.get(f"/api/operator/projects/{slug}/summary")).json()
        assert summary["project"]["completed"] is False
        assert summary["project"]["current_stage"] == "preflight"
        assert summary["provider_summary"]["gemini_calls_on_ui_load"] == 0
        assert summary["provider_summary"]["elevenlabs_calls_on_ui_load"] == 0
        assert summary["jobs"]

    asyncio.run(_with_client(run))


def test_cp08c_source_validation_rejects_unsupported_duplicate_and_traversal(monkeypatch, tmp_path):
    configure_test_root(monkeypatch, tmp_path)
    bad = tmp_path / "bad.txt"
    bad.write_text("not video", encoding="utf-8")

    async def run(client):
        unsupported = await client.post("/api/operator/source/preflight", json={"source_path": str(bad), "slug": "new-slug"})
        assert unsupported.status_code == 200
        assert unsupported.json()["checks"]["format_supported"] is False

        traversal = await client.post("/api/operator/source/preflight", json={"source_path": "..\\secret.mp4", "slug": "new-slug"})
        assert traversal.status_code == 200
        assert traversal.json()["status"] == "FAIL"

        dup = await client.post("/api/operator/source/preflight", json={"source_path": str(bad), "slug": "vertical_slice_cp07"})
        assert dup.status_code == 200
        assert dup.json()["checks"]["slug_available"] is False or dup.json()["status"] == "FAIL"

    asyncio.run(_with_client(run))


def test_cp08c_duplicate_slug_and_provenance_gate(monkeypatch, tmp_path):
    configure_test_root(monkeypatch, tmp_path)
    source = tmp_path / "tiny.mp4"
    _make_tiny_video(source)
    slug = f"cp08c-{uuid4().hex[:8]}"

    async def run(client):
        missing_gate = await client.post(
            "/api/operator/projects/create",
            json={"name": "No Gate", "slug": slug, "source_path": str(source), "provenance_acknowledged": False},
        )
        assert missing_gate.status_code == 400

        ok = await client.post(
            "/api/operator/projects/create",
            json={"name": "Gate OK", "slug": slug, "source_path": str(source), "provenance_acknowledged": True},
        )
        assert ok.status_code == 200
        duplicate = await client.post(
            "/api/operator/projects/create",
            json={"name": "Dup", "slug": slug, "source_path": str(source), "provenance_acknowledged": True},
        )
        assert duplicate.status_code == 400

    asyncio.run(_with_client(run))


def test_cp08c_explicit_stage_execution_only_and_duplicate_submit(monkeypatch, tmp_path):
    configure_test_root(monkeypatch, tmp_path)
    source = tmp_path / "tiny.mp4"
    _make_tiny_video(source)
    slug = f"cp08c-{uuid4().hex[:8]}"

    async def run(client):
        await client.post(
            "/api/operator/projects/create",
            json={"name": "Stage", "slug": slug, "source_path": str(source), "provenance_acknowledged": True},
        )
        summary = (await client.get(f"/api/operator/projects/{slug}/summary")).json()
        assert all(job["status"] == "not_started" for job in summary["jobs"])
        first = await client.post(f"/api/operator/projects/{slug}/stage/start", json={"stage": "asr"})
        second = await client.post(f"/api/operator/projects/{slug}/stage/start", json={"stage": "asr"})
        assert first.status_code == 200
        assert second.status_code == 200
        assert second.json()["created"] is False
        refreshed = (await client.get(f"/api/operator/projects/{slug}/summary")).json()
        assert next(job for job in refreshed["jobs"] if job["kind"] == "asr")["status"] == "queued"

    asyncio.run(_with_client(run))


def test_cp08c_static_ui_exposes_new_project_and_no_provider_autostart():
    html = Path("app/static/operator/index.html").read_text(encoding="utf-8")
    js = Path("app/static/operator/app.js").read_text(encoding="utf-8")
    assert 'id="newProjectBtn"' in html
    assert "Production Intake" in js
    assert "/api/operator/source/preflight" in js
    assert "/api/operator/projects/create" in js
    assert "data-start-stage" in js
    assert "/content/transform" not in js
    assert "/transcribe" not in js
    assert "xi-api-key" not in js
    assert "api_key" not in js


def test_cp09a3_project_picker_is_searchable_bounded_and_not_native_select():
    html = Path("app/static/operator/index.html").read_text(encoding="utf-8")
    css = Path("app/static/operator/styles.css").read_text(encoding="utf-8")
    js = Path("app/static/operator/app.js").read_text(encoding="utf-8")
    assert 'id="projectSelect"' not in html
    assert "<select" not in html
    assert 'id="projectPickerToggle"' in html
    assert 'id="projectSearch"' in html
    assert 'role="listbox"' in html
    assert "project-picker-list" in css
    assert "styles.css?v=cp09c" in html
    assert "app.js?v=cp09c" in html
    assert 'id="runtimeBuildId"' in html
    assert "max-height: 336px" in css
    assert "overflow-x: hidden" in css
    assert "overflow-y: auto" in css
    assert ".picker-shell *" in css
    assert "project-picker-search-region" in html
    assert "project-picker-help" in html
    assert "project-picker-results-region" in html
    assert 'id="projectPickerList" class="project-picker-list" role="listbox" aria-label="Projects" tabindex="0"' in html
    assert 'id="projectSearchResultCount"' in html
    assert "grid-template-rows: auto minmax(0, 1fr)" in css
    assert "height: 104px" in css
    assert "-webkit-line-clamp: 2" in css
    assert "padding: 30px 30px 132px" in css
    assert "syncProjectListScroll" in js
    assert "list.scrollTop = 0" in js
    assert 'search.addEventListener("search", updateProjectSearch)' in js
    assert "overscroll-behavior: contain" in css
    assert "scrollbar-gutter: stable" in css
    assert "scroll-behavior: auto" in css
    assert "scroll-snap-type" not in css
    assert "scroll-snap-align" not in css
    assert "snapProjectListScroll" not in js
    assert "projectListSnapTimer" not in js
    assert "projectSearchApplyTimer" in js
    assert "scheduleProjectSearchApply(search.value)" in js
    assert "state.projectSearchDraft = search.value" in js
    assert "state.projectSearchComposing" in js
    assert 'search.addEventListener("compositionstart"' in js
    assert 'search.addEventListener("compositionend"' in js
    assert "buildProjectPickerIndex" in js
    assert "projectPickerIndexDirty" in js
    assert "selectionRequestToken" in js
    assert "pendingProjectId" in js
    assert "projectSwitching" in js
    assert "commitProjectSnapshot" in js
    assert "assertProjectStateSynchronized" in js
    assert "renderProjectSwitching" in js
    assert "renderProjectStateMismatch" in js
    assert "isOperatorSnapshotMissing" in js
    assert "renderUnreadyProject" in js
    assert "Project not ready for operator review" in js
    assert "operator_snapshot_missing" in js
    assert "loading_project" in js
    assert "operator_ready" in js
    assert "unexpected_error" in js
    assert "FRONTEND_ASSET_VERSION = \"cp09c\"" in js
    assert "/api/operator/runtime-build" in js
    assert "Project state mismatch: sidebar=" in js
    assert "await loadSummary(projectId, { token })" in js
    assert "addEventListener(\"scroll\"" not in js
    assert "addEventListener(\"wheel\"" not in js
    assert 'scrollIntoView({ behavior: "auto", block: "nearest", inline: "nearest" })' in js
    assert "Open local export review" in js
    assert "Search projects by name or ID. Stage names are not searched here." in html
    assert "filteredProjectList" in js
    assert "ArrowDown" in js
    assert "Escape" in js
    assert "selectProject(active.project_id)" in js
    assert "/content/transform" not in js
    assert "/transcribe" not in js
    assert "xi-api-key" not in js
    assert "api_key" not in js


def test_cp09a3_project_list_has_safe_labels_search_text_and_no_duplicates(monkeypatch, tmp_path):
    configure_test_root(monkeypatch, tmp_path)

    async def run(client):
        payload = (await client.get("/api/operator/projects")).json()
        projects = payload["projects"]
        ids = [project["project_id"] for project in projects]
        assert len(ids) == len(set(ids))
        assert all(project["display_name"].strip() for project in projects)
        assert all(project["project_id"] in project["search_text"] for project in projects)
        cp09 = next((project for project in projects if project["project_id"] == "production_golden_path_cp09"), None)
        if cp09 is not None:
            assert cp09["display_name"] != "production_golden_path_cp09"
            assert "production_golden_path_cp09" in cp09["search_text"]
            assert cp09["scope"] == "dialogue_subtitles_only"
            assert cp09["readiness"] == "Production golden path"
            assert cp09["is_production"] is True
        cp07 = next(project for project in projects if project["project_id"] == "vertical_slice_cp07")
        assert cp07["display_name"] == "CP07A Accepted Full Preview"
        assert cp07["readiness"] == "Accepted preview"
        serialized = str(payload).lower()
        assert "xi-api-key" not in serialized
        assert "api_key" not in serialized

    asyncio.run(_with_client(run))


def test_cp09a10_runtime_build_endpoint_reports_frontend_and_backend(monkeypatch, tmp_path):
    configure_test_root(monkeypatch, tmp_path)

    async def run(client):
        response = await client.get("/api/operator/runtime-build")
        assert response.status_code == 200
        payload = response.json()
        assert payload["backend_version"] == "0.2.0"
        assert payload["frontend_asset_version"] == "cp09c"
        assert isinstance(payload["git_commit"], str)
        serialized = str(payload).lower()
        assert "api_key" not in serialized
        assert "xi-api-key" not in serialized

    asyncio.run(_with_client(run))


def test_cp11a_runtime_build_uses_packaged_commit_without_git(monkeypatch):
    from app.api import routes

    def _missing_git(*_args, **_kwargs):
        raise FileNotFoundError("git is not available in portable package")

    monkeypatch.setenv("TOOL_AUTO_SUB_BUILD_COMMIT", "c3a3dcf")
    monkeypatch.setattr(routes.subprocess, "check_output", _missing_git)

    payload = routes.operator_runtime_build()

    assert payload["git_commit"] == "c3a3dcf"
    assert payload["backend_version"] == "0.2.0"
    assert payload["simple_frontend_asset_version"] == "cp12b"
    assert payload["operator_frontend_asset_version"] == "cp09c"


def test_cp09b2_legacy_fixture_repair_marks_fixture_projects_and_records_audit(monkeypatch, tmp_path):
    configure_test_root(monkeypatch, tmp_path)

    async def run(client):
        from app.db.session import session_scope
        from app.domain.models import Project

        with session_scope() as session:
            session.add(Project(project_id="proj_cp04_reuse_hidden", title="Reuse hidden", is_test_fixture=False))
            session.add(Project(project_id="cp08c-smoke-81716", title="CP08C Smoke", is_test_fixture=False))
            session.add(Project(project_id="real_operator_project", title="Real Operator Project", is_test_fixture=False))

        payload = (await client.get("/api/operator/projects")).json()
        ids = {project["project_id"] for project in payload["projects"]}
        assert "proj_cp04_reuse_hidden" not in ids
        assert "cp08c-smoke-81716" not in ids
        assert "real_operator_project" in ids

        with session_scope() as session:
            hidden = session.query(Project).filter(Project.project_id == "proj_cp04_reuse_hidden").one()
            smoke = session.query(Project).filter(Project.project_id == "cp08c-smoke-81716").one()
            real = session.query(Project).filter(Project.project_id == "real_operator_project").one()
            assert hidden.is_test_fixture is True
            assert smoke.is_test_fixture is True
            assert real.is_test_fixture is False

        audit = Path("evidence/CP09B2/review_discoverability_fixture_cleanup/legacy_fixture_cleanup_audit.json")
        assert audit.exists()
        audit_payload = json.loads(audit.read_text(encoding="utf-8"))
        repaired_ids = {item["project_id"] for item in audit_payload["repaired_projects"]}
        assert "proj_cp04_reuse_hidden" in repaired_ids
        assert "cp08c-smoke-81716" in repaired_ids
        retained_ids = {item["project_id"] for item in audit_payload["retained_projects"]}
        assert "real_operator_project" in retained_ids

    asyncio.run(_with_client(run))


def test_cp09a9_created_project_summary_fallback_keeps_workspace_selectable(monkeypatch, tmp_path):
    configure_test_root(monkeypatch, tmp_path)

    async def run(client):
        from app.db.session import session_scope
        from app.domain.models import Project

        with session_scope() as session:
            session.add(Project(project_id="vertical_slice_cp02", title="CP02 vertical slice"))

        response = await client.get("/api/operator/projects/vertical_slice_cp02/summary")
        assert response.status_code == 200
        payload = response.json()
        assert payload["project"]["project_id"] == "vertical_slice_cp02"
        assert payload["project"]["title"] == "CP02 vertical slice"
        assert payload["overall_status"] == "Project not ready for operator review"
        assert payload["state"] == "operator_snapshot_missing"
        assert payload["operator_state"]["state"] == "operator_snapshot_missing"
        assert payload["operator_state"]["project_state"] == "project_created"
        assert payload["operator_state"]["available_status"] == "Project created"
        assert "accepted operator snapshot" in payload["operator_state"]["reason"]
        assert payload["provider_summary"]["gemini_calls_on_ui_load"] == 0
        assert payload["provider_summary"]["elevenlabs_calls_on_ui_load"] == 0
        assert payload["stages"][0]["stage_id"] == "preflight"
        assert "golden_path" not in payload

    asyncio.run(_with_client(run))


def test_cp09a9_generic_project_without_operator_data_is_typed_unready(monkeypatch, tmp_path):
    configure_test_root(monkeypatch, tmp_path)

    async def run(client):
        from app.db.session import session_scope
        from app.domain.models import Project

        with session_scope() as session:
            session.add(Project(project_id="arbitrary_unready_project", title="Arbitrary Unready Project"))

        list_payload = (await client.get("/api/operator/projects")).json()
        picker = next(project for project in list_payload["projects"] if project["project_id"] == "arbitrary_unready_project")
        assert picker["display_name"] == "Arbitrary Unready Project"
        assert picker["readiness"] == "Not ready - Project created"

        response = await client.get("/api/operator/projects/arbitrary_unready_project/summary")
        assert response.status_code == 200
        payload = response.json()
        assert payload["project"]["project_id"] == "arbitrary_unready_project"
        assert payload["project"]["title"] == "Arbitrary Unready Project"
        assert payload["overall_status"] == "Project not ready for operator review"
        assert payload["state"] == "operator_snapshot_missing"
        assert payload["operator_state"]["state"] == "operator_snapshot_missing"
        assert payload["operator_state"]["available_stage"] == "preflight"
        assert payload["operator_state"]["unresolved_issue_count"] == 0
        assert payload["provider_summary"]["gemini_calls_on_ui_load"] == 0
        assert payload["provider_summary"]["elevenlabs_calls_on_ui_load"] == 0
        serialized = str(payload).lower()
        assert "production golden path" not in serialized
        assert "37394ab6ce036abdbebb6e7d9cebc8d3dc2661adae1324f0b635184042589646" not in serialized
        assert "accepted operator project data not found" not in serialized

    asyncio.run(_with_client(run))


def test_cp09a10_http_contract_returns_typed_unready_instead_of_404(monkeypatch, tmp_path):
    configure_test_root(monkeypatch, tmp_path)

    async def run(client):
        from app.db.session import session_scope
        from app.domain.models import Project

        with session_scope() as session:
            session.add(Project(project_id="cp04_elevenlabs_contract", title="CP04 ElevenLabs contract"))

        response = await client.get("/api/operator/projects/cp04_elevenlabs_contract/summary")
        assert response.status_code == 200
        payload = response.json()
        assert payload["state"] == "operator_snapshot_missing"
        assert payload["operator_state"]["state"] == "operator_snapshot_missing"
        assert payload["project"]["project_id"] == "cp04_elevenlabs_contract"
        assert payload["project"]["title"] == "CP04 ElevenLabs contract"
        assert payload["overall_status"] == "Project not ready for operator review"
        assert "accepted operator snapshot" in payload["operator_state"]["reason"]
        assert "golden_path" not in payload
        assert "Accepted operator project data not found" not in str(payload)

    asyncio.run(_with_client(run))


def test_cp09a10_test_fixture_projects_are_excluded_from_normal_picker(monkeypatch, tmp_path):
    configure_test_root(monkeypatch, tmp_path)

    async def run(client):
        from app.db.session import session_scope
        from app.domain.models import Project

        with session_scope() as session:
            session.add(Project(project_id="proj_cp04_fake_hidden", title="Fixture hidden", is_test_fixture=True))
            session.add(Project(project_id="real_operator_project", title="Real Operator Project", is_test_fixture=False))

        payload = (await client.get("/api/operator/projects")).json()
        ids = {project["project_id"] for project in payload["projects"]}
        assert "proj_cp04_fake_hidden" not in ids
        assert "real_operator_project" in ids

    asyncio.run(_with_client(run))
