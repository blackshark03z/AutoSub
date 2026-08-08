import asyncio
import json
import sqlite3
from pathlib import Path

import httpx
import pytest

from tests.test_cp10b_simple_workflow import _make_tiny_video, configure_test_root


async def _with_client(callback):
    from app.main import app

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
            return await callback(client)


def test_cp12a_migration_adds_track_tables_without_breaking_existing_runs(monkeypatch, tmp_path):
    configure_test_root(monkeypatch, tmp_path)
    from app.db.session import init_db

    init_db()
    db = tmp_path / "data" / "test.db"
    with sqlite3.connect(db) as connection:
        tables = {row[0] for row in connection.execute("select name from sqlite_master where type='table'")}
        assert "simple_workflow_runs" in tables
        assert "subtitle_tracks" in tables
        assert "subtitle_track_items" in tables


def test_cp12a_txt_json_preview_apply_switch_recovery_and_undo(monkeypatch, tmp_path):
    configure_test_root(monkeypatch, tmp_path)
    source = tmp_path / "creative.mp4"
    _make_tiny_video(source)

    async def run(client):
        created = (await client.post("/api/simple/runs", json={"source_path": str(source)})).json()["run"]
        run_id = created["run_id"]
        tracks = (await client.get(f"/api/simple/runs/{run_id}/tracks")).json()
        assert len(tracks["tracks"]) == 1
        translation = tracks["tracks"][0]
        assert translation["track_type"] == "translation"
        assert translation["active"] is True

        txt_template = (await client.get(f"/api/simple/runs/{run_id}/creative/template?format=txt")).json()
        assert "[CUE_0001]" in txt_template["content"]
        assert "TEXT:" in txt_template["content"]
        completed = txt_template["content"].replace("TEXT:\n", "TEXT: Run like the door owes you money.\n", 1)
        completed = completed.replace("TEXT:\n", "TEXT: Keep moving through the hallway.\n", 1)
        completed = completed.replace("TEXT:\n", "TEXT: The safe path was a trap all along.\n", 1)

        preview = (
            await client.post(
                f"/api/simple/runs/{run_id}/creative/import/preview",
                json={"content": completed, "format": "txt", "filename": "creative.txt", "mode": "cue_id"},
            )
        ).json()
        assert preview["status"] == "PASS"
        assert preview["state_mutated"] is False
        assert preview["matched_cues"] == preview["total_canonical_cues"]
        assert preview["unknown_cues"] == []
        assert preview["duplicate_cues"] == []
        assert len((await client.get(f"/api/simple/runs/{run_id}/tracks")).json()["tracks"]) == 1

        applied = (
            await client.post(
                f"/api/simple/runs/{run_id}/creative/import/apply",
                json={
                    "content": completed,
                    "format": "txt",
                    "filename": "creative.txt",
                    "mode": "cue_id",
                    "track_type": "creative",
                    "display_name": "Creative pass",
                    "fallback_policy": "fallback_to_translation",
                },
            )
        ).json()
        creative_id = applied["track"]["track_id"]
        import_path = Path(applied["track"]["metadata"]["import_path"])
        assert import_path.exists()
        assert import_path.read_text(encoding="utf-8") == completed

        switched = (
            await client.post(
                f"/api/simple/runs/{run_id}/tracks/active",
                json={"track_id": creative_id, "fallback_policy": "fallback_to_translation"},
            )
        ).json()
        assert switched["active_track_id"] == creative_id
        resolved = (await client.get(f"/api/simple/runs/{run_id}/tracks/resolved")).json()
        assert resolved["active_track"]["track_type"] == "creative"
        assert resolved["cues"][0]["start_ms"] == 0
        assert "Run like the door" in resolved["cues"][0]["resolved_text"]

        edited = (
            await client.post(
                f"/api/simple/runs/{run_id}/tracks/{creative_id}/items",
                json={"cue_id": "CUE_0001", "text": "Edited creative cue."},
            )
        ).json()
        assert any(item["text"] == "Edited creative cue." for item in edited["items"])

        started = (await client.post(f"/api/simple/runs/{run_id}/start")).json()["run"]
        resolved_file = Path(started["run_directory"]) / "subtitles" / "resolved_active_track.json"
        assert resolved_file.exists()
        lineage = json.loads(resolved_file.read_text(encoding="utf-8"))
        assert lineage["active_track"]["track_id"] == creative_id
        assert lineage["cues"][0]["resolved_text"] == "Edited creative cue."

        recovered = (await client.get(f"/api/simple/runs/{run_id}/tracks")).json()
        assert recovered["active_track_id"] == creative_id
        undo = (await client.post(f"/api/simple/runs/{run_id}/tracks/undo-import")).json()
        assert undo["status"] == "PASS"
        assert all(track["track_type"] == "translation" for track in undo["tracks"])

    asyncio.run(_with_client(run))


def test_cp12a_validation_failures_and_json_import(monkeypatch, tmp_path):
    configure_test_root(monkeypatch, tmp_path)
    source = tmp_path / "json.mp4"
    _make_tiny_video(source)

    async def run(client):
        run_id = (await client.post("/api/simple/runs", json={"source_path": str(source)})).json()["run"]["run_id"]
        template = (await client.get(f"/api/simple/runs/{run_id}/creative/template?format=json")).json()
        payload = json.loads(template["content"])
        payload["cues"][0]["creative_text"] = "A clean creative line."
        json_preview = (
            await client.post(
                f"/api/simple/runs/{run_id}/creative/import/preview",
                json={"content": json.dumps(payload), "format": "json", "filename": "creative.json"},
            )
        ).json()
        assert json_preview["status"] == "PASS"
        assert any(w["code"] == "external_timing_ignored" for w in json_preview["warnings"])

        duplicate_txt = "[CUE_0001]\nTEXT: One\n\n[CUE_0001]\nTEXT: Two\n"
        duplicate = (
            await client.post(
                f"/api/simple/runs/{run_id}/creative/import/preview",
                json={"content": duplicate_txt, "format": "txt", "filename": "dup.txt"},
            )
        ).json()
        assert duplicate["status"] == "FAIL"
        assert duplicate["duplicate_cues"] == ["CUE_0001"]

        line_mismatch = (
            await client.post(
                f"/api/simple/runs/{run_id}/creative/import/preview",
                json={"content": "Only one line", "format": "txt", "filename": "lines.txt", "mode": "line_by_line"},
            )
        ).json()
        assert line_mismatch["status"] == "FAIL"
        assert line_mismatch["malformed_blocks"][0]["code"] == "cue_count_mismatch"

        html = (
            await client.post(
                f"/api/simple/runs/{run_id}/creative/import/preview",
                json={"content": "<script>alert(1)</script>", "format": "txt", "filename": "bad.txt"},
            )
        ).json()
        assert html["status"] == "FAIL"
        assert html["malformed_blocks"][0]["code"] == "html_script_content"

        traversal = await client.post(
            f"/api/simple/runs/{run_id}/creative/import/preview",
            json={"content": "x", "format": "txt", "filename": "..\\secret.txt"},
        )
        assert traversal.status_code == 400

    asyncio.run(_with_client(run))


def test_cp12a_cp11d_and_accepted_media_immutability():
    import hashlib

    def sha(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    root = Path.cwd()
    cp11d = root / "release" / "CP11D" / "tool_auto_sub_windows_full_portable_cp11d.zip"
    if not cp11d.exists():
        pytest.skip("CP11D binary artifact was pruned by maintenance; CP12B/CP13A are the retained release artifacts.")
    assert sha(cp11d) == "2c48ec39a345c4278f8f6c316fcc04cd546a2c7aff86139022511ef93d307f3c"
    assert sha(root / "data" / "projects" / "production_golden_path_cp09" / "exports" / "release_20260718_050055_88c16e_37394ab6_dir" / "final_video.mp4") == "37394ab6ce036abdbebb6e7d9cebc8d3dc2661adae1324f0b635184042589646"
