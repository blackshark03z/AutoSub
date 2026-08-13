from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

pytestmark = pytest.mark.release

from app.core.hashing import sha256_file
from app.services.production_golden_path import EXPECTED_HASH, PROJECT_ID


def configure_test_root(monkeypatch, tmp_path: Path) -> None:
    root = Path.cwd()
    monkeypatch.setenv("TOOL_AUTO_SUB_ROOT", str(root))
    monkeypatch.setenv("TOOL_AUTO_SUB_DB_PATH", str(tmp_path / "data" / "test.db"))
    from app.core.config import get_settings

    get_settings.cache_clear()


async def _with_client(callback):
    from app.main import app

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
            return await callback(client)


def test_cp09_complete_operator_golden_path(monkeypatch, tmp_path):
    configure_test_root(monkeypatch, tmp_path)

    async def run(client):
        response = await client.post(f"/api/operator/projects/{PROJECT_ID}/golden-path/action", json={"action": "run_complete_golden_path"})
        assert response.status_code == 200
        state = response.json()["state"]
        assert state["localization_scope"] == "dialogue_subtitles_only"
        assert state["provider_calls"] == {"gemini": 0, "elevenlabs": 0, "youtube": 0}
        assert state["candidate_artifact"]["sha256"] == EXPECTED_HASH
        assert state["final_artifact"]["sha256"] == EXPECTED_HASH
        assert state["export"]["exported_video_sha256"] == EXPECTED_HASH
        assert state["export"]["byte_identical_result"] is True
        assert state["human_approval"]["mode"] == "human_approval_inherited_by_hash_equivalence"
        assert state["human_approval"]["new_human_viewing_claimed"] is False
        assert all(status in {"MACHINE_PASS", "HUMAN_PASS"} for status in state["stages"].values())

        summary = (await client.get(f"/api/operator/projects/{PROJECT_ID}/summary")).json()
        assert summary["project"]["completed"] is True
        assert summary["source"]["localization_scope"] == "dialogue_subtitles_only"
        assert summary["golden_path"]["export_ready"] is True
        assert summary["provider_summary"]["youtube"] == "NOT_CONFIGURED"
        assert summary["artifact"]["sha256"] == EXPECTED_HASH
        stage_ids = [stage["stage_id"] for stage in summary["stages"]]
        assert stage_ids[-2:] == ["export", "complete"]
        assert summary["stages"][-1]["label"] == "Complete"
        assert summary["stages"][-1]["alias_of"] == "export"
        assert summary["project"]["current_stage"] == "complete"

        preview = await client.get(f"/api/operator/projects/{PROJECT_ID}/accepted-preview")
        assert preview.status_code == 200
        assert preview.headers["content-type"].startswith("video/mp4")

    asyncio.run(_with_client(run))


def test_cp09_rejects_fail_non_canonical_and_hash_mismatch(monkeypatch, tmp_path):
    configure_test_root(monkeypatch, tmp_path)

    async def run(client):
        await client.post(f"/api/operator/projects/{PROJECT_ID}/golden-path/action", json={"action": "initialize"})
        await client.post(f"/api/operator/projects/{PROJECT_ID}/golden-path/action", json={"action": "run_preflight"})
        await client.post(f"/api/operator/projects/{PROJECT_ID}/golden-path/action", json={"action": "import_cached_artifact"})
        await client.post(f"/api/operator/projects/{PROJECT_ID}/golden-path/action", json={"action": "approve_final"})
        bad = await client.post(
            f"/api/operator/projects/{PROJECT_ID}/golden-path/action",
            json={"action": "select_final", "artifact_path": "data/projects/vertical_slice_cp07/renders/cp08f_selective_non_dialogue_cjk_localization_720p.mp4"},
        )
        assert bad.status_code == 400
        assert "FAIL_NON_CANONICAL" in bad.text or "approved CP08G lineage" in bad.text

    asyncio.run(_with_client(run))


def test_cp09b_export_manifest_preserves_mp4_bytes_checksums_and_audit_log(monkeypatch, tmp_path):
    configure_test_root(monkeypatch, tmp_path)

    async def run(client):
        response = await client.post(f"/api/operator/projects/{PROJECT_ID}/golden-path/action", json={"action": "run_complete_golden_path"})
        state = response.json()["state"]
        export_root = Path(state["export_job"]["destination"])
        manifest = json.loads((export_root / "release_manifest.json").read_text(encoding="utf-8"))
        mp4 = export_root / manifest["exported_video_path"]
        assert sha256_file(mp4) == EXPECTED_HASH
        assert manifest["selected_final_candidate_sha256"] == EXPECTED_HASH
        assert manifest["provider_call_counts"] == {"gemini": 0, "elevenlabs": 0, "youtube": 0}
        assert manifest["dialogue_localization_scope"] == "dialogue_subtitles_only"
        assert manifest["cp09a_human_acceptance"]["verdict"] == "CP09A_HUMAN_VISUAL_AND_USABILITY_PASS"
        assert manifest["publish_upload_state"]["youtube_publication"] == "not_performed"
        assert (export_root / "RELEASE_NOTES.md").exists()
        assert "non-dialogue source text may remain" in (export_root / "RELEASE_NOTES.md").read_text(encoding="utf-8")
        sums = (export_root / "SHA256SUMS.txt").read_text(encoding="utf-8")
        assert f"{EXPECTED_HASH}  final_video.mp4" in sums
        for line in sums.splitlines():
            digest, name = line.split("  ", 1)
            assert sha256_file(export_root / name) == digest
        audit = Path("evidence/CP09/production_golden_path/audit_log.jsonl")
        actions = [json.loads(line)["action"] for line in audit.read_text(encoding="utf-8").splitlines() if line.strip()]
        for required in ["project_initialized", "run_preflight", "artifact_hash_verified", "human_approval_inherited", "artifact_promoted", "local_export_created"]:
            assert required in actions

    asyncio.run(_with_client(run))


def test_cp09b_export_is_idempotent_and_does_not_duplicate_releases(monkeypatch, tmp_path):
    configure_test_root(monkeypatch, tmp_path)

    async def run(client):
        first = await client.post(f"/api/operator/projects/{PROJECT_ID}/golden-path/action", json={"action": "run_complete_golden_path"})
        second = await client.post(f"/api/operator/projects/{PROJECT_ID}/golden-path/action", json={"action": "create_export_package"})
        assert first.status_code == second.status_code == 200
        assert first.json()["state"]["export"]["release_id"] == second.json()["state"]["export"]["release_id"]
        export_root = Path(first.json()["state"]["export_job"]["destination"]).parent
        assert Path(first.json()["state"]["export_job"]["destination"]).exists()
        assert not list(export_root.glob("release_cp09b_37394ab6_dir.tmp_*"))

    asyncio.run(_with_client(run))


def test_cp09b_explicit_duplicate_release_uses_collision_safe_name(monkeypatch, tmp_path):
    configure_test_root(monkeypatch, tmp_path)
    import app.services.production_golden_path as golden

    monkeypatch.setattr(golden.shutil, "disk_usage", lambda _path: SimpleNamespace(total=20_000_000_000, used=1, free=19_999_999_999))

    async def run(client):
        first = await client.post(f"/api/operator/projects/{PROJECT_ID}/golden-path/action", json={"action": "run_complete_golden_path"})
        first_release_id = first.json()["state"]["export"]["release_id"]
        duplicate = await client.post(f"/api/operator/projects/{PROJECT_ID}/golden-path/action", json={"action": "create_another_export_package"})
        assert duplicate.status_code == 200
        release_id = duplicate.json()["state"]["export"]["release_id"]
        release_root = Path("data/projects/production_golden_path_cp09/exports", release_id)
        try:
            assert release_id.startswith("release_")
            assert release_id != first_release_id
            assert (release_root / "final_video.mp4").exists()
        finally:
            golden.shutil.rmtree(release_root, ignore_errors=True)

    asyncio.run(_with_client(run))


def test_cp09b_blocks_final_hash_mismatch_and_cleans_partial_package(monkeypatch, tmp_path):
    configure_test_root(monkeypatch, tmp_path)

    async def run(client):
        from app.services.production_golden_path import _read_json, _save_state, _state_path

        await client.post(f"/api/operator/projects/{PROJECT_ID}/golden-path/action", json={"action": "initialize"})
        await client.post(f"/api/operator/projects/{PROJECT_ID}/golden-path/action", json={"action": "run_preflight"})
        await client.post(f"/api/operator/projects/{PROJECT_ID}/golden-path/action", json={"action": "import_cached_artifact"})
        await client.post(f"/api/operator/projects/{PROJECT_ID}/golden-path/action", json={"action": "approve_final"})
        await client.post(
            f"/api/operator/projects/{PROJECT_ID}/golden-path/action",
            json={"action": "select_final", "artifact_path": "data/projects/vertical_slice_cp07/renders/cp08g_dialogue_subtitle_only_final_720p.mp4"},
        )
        state = _read_json(_state_path())
        state["final_artifact"]["sha256"] = "0" * 64
        _save_state(state)
        blocked = await client.post(f"/api/operator/projects/{PROJECT_ID}/golden-path/action", json={"action": "create_export_package"})
        assert blocked.status_code == 400
        assert "accepted canonical hash" in blocked.text
        export_root = Path("data/projects/production_golden_path_cp09/exports")
        assert not list(export_root.glob("*.tmp_*"))

    asyncio.run(_with_client(run))


def test_cp09b_zip_is_optional_and_disk_gate_blocks_without_partial_archive(monkeypatch, tmp_path):
    configure_test_root(monkeypatch, tmp_path)

    import app.services.production_golden_path as golden

    async def run(client):
        await client.post(f"/api/operator/projects/{PROJECT_ID}/golden-path/action", json={"action": "initialize"})
        await client.post(f"/api/operator/projects/{PROJECT_ID}/golden-path/action", json={"action": "run_preflight"})
        await client.post(f"/api/operator/projects/{PROJECT_ID}/golden-path/action", json={"action": "import_cached_artifact"})
        await client.post(f"/api/operator/projects/{PROJECT_ID}/golden-path/action", json={"action": "approve_final"})
        await client.post(
            f"/api/operator/projects/{PROJECT_ID}/golden-path/action",
            json={"action": "select_final", "artifact_path": "data/projects/vertical_slice_cp07/renders/cp08g_dialogue_subtitle_only_final_720p.mp4"},
        )
        monkeypatch.setattr(golden.shutil, "disk_usage", lambda _path: SimpleNamespace(total=1000, used=900, free=64))
        blocked = await client.post(f"/api/operator/projects/{PROJECT_ID}/golden-path/action", json={"action": "create_export_zip"})
        assert blocked.status_code == 400
        assert "Insufficient free disk" in blocked.text
        export_root = Path("data/projects/production_golden_path_cp09/exports")
        assert not list(export_root.glob("*.zip"))
        assert not list(export_root.glob("*.tmp_*"))

    asyncio.run(_with_client(run))


def test_cp09b_export_hidden_for_unready_project_summary(monkeypatch, tmp_path):
    configure_test_root(monkeypatch, tmp_path)

    async def run(client):
        from app.db.session import session_scope
        from app.domain.models import Project

        with session_scope() as session:
            session.add(Project(project_id="cp04_elevenlabs_contract", title="CP04 ElevenLabs contract"))

        payload = (await client.get("/api/operator/projects/cp04_elevenlabs_contract/summary")).json()
        assert payload["state"] == "operator_snapshot_missing"
        assert "golden_path" not in payload
        assert "local_export" not in str(payload)

    asyncio.run(_with_client(run))


def test_cp09b1_local_export_review_endpoints_are_read_only_and_safe(monkeypatch, tmp_path):
    configure_test_root(monkeypatch, tmp_path)

    async def run(client):
        await client.post(f"/api/operator/projects/{PROJECT_ID}/golden-path/action", json={"action": "run_complete_golden_path"})
        review = await client.get(f"/api/operator/projects/{PROJECT_ID}/local-export/review")
        assert review.status_code == 200
        payload = review.json()
        assert payload["state"] in {"CP09B_HUMAN_REVIEW_REQUIRED", "CP09B_LOCAL_EXPORT_PACKAGE_HUMAN_PASS"}
        assert payload["final_video"]["sha256"] == EXPECTED_HASH
        assert payload["final_video"]["byte_identical"] is True
        assert payload["checksums"]["verified"] is True
        assert payload["release_notes"]["placeholder_free"] is True
        assert payload["subtitle_inclusion"]["ass"] is True
        assert payload["publish_upload_state"] == {"youtube_publication": "not_performed", "upload": "not_performed", "publish": "not_performed"}
        assert payload["actions"] == {"upload": "not_available", "publish": "not_available"}
        names = {item["name"] for item in payload["packaged_files"]}
        assert {"final_video.mp4", "release_manifest.json", "SHA256SUMS.txt", "RELEASE_NOTES.md", "subtitles_en.ass"}.issubset(names)

        video = await client.get(f"/api/operator/projects/{PROJECT_ID}/local-export/files/final_video.mp4", headers={"range": "bytes=0-99"})
        assert video.status_code in {200, 206}
        assert video.headers["content-type"].startswith("video/mp4")
        assert video.content

        manifest = await client.get(f"/api/operator/projects/{PROJECT_ID}/local-export/files/release_manifest.json")
        assert manifest.status_code == 200
        assert manifest.json()["exported_video_sha256"] == EXPECTED_HASH

        traversal = await client.get(f"/api/operator/projects/{PROJECT_ID}/local-export/files/..%2Fsecrets%2Fgemini_api.txt")
        assert traversal.status_code in {400, 404}

    asyncio.run(_with_client(run))


def test_cp09c_release_closeout_persists_handoff_index_and_immutability(monkeypatch, tmp_path):
    configure_test_root(monkeypatch, tmp_path)
    monkeypatch.setenv("TOOL_AUTO_SUB_ENFORCE_CLOSEOUT_GUARD_IN_TESTS", "1")

    async def run(client):
        await client.post(f"/api/operator/projects/{PROJECT_ID}/golden-path/action", json={"action": "run_complete_golden_path"})
        accepted = await client.post(f"/api/operator/projects/{PROJECT_ID}/golden-path/action", json={"action": "record_export_human_acceptance"})
        assert accepted.status_code == 200
        assert accepted.json()["state"]["cp09b_human_acceptance"]["verdict"] == "CP09B_LOCAL_EXPORT_PACKAGE_HUMAN_PASS"

        closed = await client.post(f"/api/operator/projects/{PROJECT_ID}/golden-path/action", json={"action": "closeout_manual_publication"})
        assert closed.status_code == 200
        state = closed.json()["state"]
        assert state["project_state"] == "READY_FOR_MANUAL_PUBLICATION"
        assert state["release_closeout"]["state"] == "READY_FOR_MANUAL_PUBLICATION"
        assert state["release_closeout"]["final_video_sha256"] == EXPECTED_HASH
        assert state["release_closeout"]["publication_state"] == "not_performed"
        assert state["release_closeout"]["upload_state"] == "not_performed"
        assert state["release_closeout"]["provider_call_counts"] == {"gemini": 0, "elevenlabs": 0, "youtube": 0}

        release_root = Path(state["release_closeout"]["accepted_release_path"])
        manifest_before = sha256_file(release_root / "release_manifest.json")
        checksums_before = sha256_file(release_root / "SHA256SUMS.txt")
        assert sha256_file(release_root / "final_video.mp4") == EXPECTED_HASH
        assert "READY_FOR_MANUAL_PUBLICATION.md" not in {path.name for path in release_root.iterdir()}

        index_path = Path("data/projects/production_golden_path_cp09/release_index.json")
        handoff_path = Path("data/projects/production_golden_path_cp09/READY_FOR_MANUAL_PUBLICATION.md")
        index = json.loads(index_path.read_text(encoding="utf-8"))
        assert index["latest_accepted_release_id"] == state["release_closeout"]["accepted_release_id"]
        accepted_release = index["accepted_releases"][0]
        assert accepted_release["sha256"] == EXPECTED_HASH
        assert accepted_release["publication_state"] == "not_performed"
        assert accepted_release["upload_state"] == "not_performed"
        handoff = handoff_path.read_text(encoding="utf-8")
        assert "READY_FOR_MANUAL_PUBLICATION" in handoff
        assert "Do not invent title, description, tags or thumbnail content" in handoff
        assert "SRT availability: no" in handoff

        blocked = await client.post(
            f"/api/operator/projects/{PROJECT_ID}/golden-path/action",
            json={"action": "select_final", "artifact_path": "data/projects/vertical_slice_cp07/renders/cp08g_dialogue_subtitle_only_final_720p.mp4"},
        )
        assert blocked.status_code == 400
        assert "immutable" in blocked.text.lower()
        assert sha256_file(release_root / "release_manifest.json") == manifest_before
        assert sha256_file(release_root / "SHA256SUMS.txt") == checksums_before

        handoff_response = await client.get(f"/api/operator/projects/{PROJECT_ID}/manual-publication-handoff")
        assert handoff_response.status_code == 200
        assert "READY_FOR_MANUAL_PUBLICATION" in handoff_response.text

    asyncio.run(_with_client(run))


def test_cp09_restart_resume_retry_and_idempotency(monkeypatch, tmp_path):
    configure_test_root(monkeypatch, tmp_path)

    async def run(client):
        await client.post(f"/api/operator/projects/{PROJECT_ID}/golden-path/action", json={"action": "initialize"})
        first = await client.post(f"/api/operator/projects/{PROJECT_ID}/golden-path/action", json={"action": "run_preflight"})
        second = await client.post(f"/api/operator/projects/{PROJECT_ID}/golden-path/action", json={"action": "run_preflight"})
        assert first.status_code == second.status_code == 200
        await client.post(f"/api/operator/projects/{PROJECT_ID}/golden-path/action", json={"action": "import_cached_artifact"})
        interrupted = await client.post(f"/api/operator/projects/{PROJECT_ID}/golden-path/action", json={"action": "simulate_interruption"})
        assert interrupted.json()["state"]["stages"]["preview"] == "RUNNING"
        resumed = await client.post(f"/api/operator/projects/{PROJECT_ID}/golden-path/action", json={"action": "resume"})
        assert resumed.json()["state"]["stages"]["preview"] == "MACHINE_PASS"
        failed = await client.post(f"/api/operator/projects/{PROJECT_ID}/golden-path/action", json={"action": "simulate_failure"})
        assert failed.json()["state"]["stages"]["final_selection"] == "FAILED"
        retried = await client.post(f"/api/operator/projects/{PROJECT_ID}/golden-path/action", json={"action": "retry_failed_stage"})
        assert retried.json()["state"]["stages"]["final_selection"] == "READY"

    asyncio.run(_with_client(run))


def test_cp09_static_ui_exposes_release_gate_without_publishing_controls():
    js = Path("app/static/operator/app.js").read_text(encoding="utf-8")
    assert "Production Run Dashboard" in js
    assert "Run Preflight" in js
    assert "CP09B Local Export" in js
    assert "CP09B Human Review Package" in js
    assert "Open local export review" in js
    assert "Create local export package" in js
    assert "Verify Checksums" in js
    assert "Open final_video.mp4" in js
    assert "View release notes" in js
    assert "Not published" in js
    assert "Not uploaded" in js
    assert "Local release accepted" in js
    assert "Ready for manual publication" in js
    assert "View manual-publication handoff" in js
    assert "Publishing is NOT_CONFIGURED" in js
    assert "youtube" not in js.lower() or "NOT_CONFIGURED".lower() in js.lower()
    assert "/golden-path/action" in js
    assert "xi-api-key" not in js
    assert "api_key" not in js
    assert "resolveStageSelection" in js
    assert "export: renderComplete" in js
    assert "human_review: renderComplete" in js
