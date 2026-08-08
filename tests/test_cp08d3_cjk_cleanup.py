from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx

from app.services.cjk_cleanup import (
    build_cjk_cleanup_summary,
    cleanup_issue_summary,
    default_cleanup_gate,
    mark_cleanup_issue_reviewed,
    save_cjk_cleanup_state,
    set_cleanup_approval,
)


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


def _cleanup_state() -> dict:
    return {
        "schema_version": 1,
        "project_id": "vertical_slice_cp07",
        "machine_verdict": "PASS",
        "human_review_state": "REQUIRED",
        "issues": [
            {
                "issue_id": "cjk_0406_punctuation_residual",
                "severity": "clean",
                "category": "residual source punctuation",
                "timestamp": 246.5,
                "needs_review": True,
                "reviewed": False,
            },
            {
                "issue_id": "cjk_0812_chinese_residual",
                "severity": "clean",
                "category": "residual source subtitle",
                "timestamp": 492.5,
                "needs_review": True,
                "reviewed": False,
            },
        ],
        "approval_gate": default_cleanup_gate(),
        "approvals": {"cleanup": False, "preservation": False},
        "provider_calls": {"gemini": 0, "elevenlabs": 0},
    }


def test_cp08d3_cleanup_summary_counts_are_canonical_and_numeric():
    state = build_cjk_cleanup_summary(_cleanup_state())
    summary = state["issue_summary"]
    assert summary == {
        "total": 2,
        "blockers": 0,
        "warnings": 0,
        "needs_review": 2,
        "reviewed": 0,
        "unresolved": 2,
        "clean_without_review_requirement": 0,
    }
    assert all(isinstance(value, int) and value >= 0 for value in summary.values())
    assert state["provider_calls"] == {"gemini": 0, "elevenlabs": 0}


def test_cp08d3_mark_reviewed_and_approval_gate_do_not_hide_unreviewed_state():
    state = mark_cleanup_issue_reviewed(_cleanup_state(), "cjk_0406_punctuation_residual")
    summary = cleanup_issue_summary(state)
    assert summary["reviewed"] == 1
    assert summary["needs_review"] == 1
    state = set_cleanup_approval(state, cleanup=True)
    assert state["approval_gate"]["state"] == "Pending human review"
    state = set_cleanup_approval(state, preservation=True)
    assert state["approval_gate"]["state"] == "Approved"


def test_cp08d3_state_round_trip_preserves_reviewed_ids(tmp_path):
    state = mark_cleanup_issue_reviewed(_cleanup_state(), "cjk_0812_chinese_residual")
    path = save_cjk_cleanup_state(state, tmp_path)
    loaded = json.loads(path.read_text(encoding="utf-8"))
    summary = build_cjk_cleanup_summary(loaded)
    assert summary["reviewed_issue_ids"] == ["cjk_0812_chinese_residual"]
    assert summary["issue_summary"]["reviewed"] == 1
    assert summary["issue_summary"]["needs_review"] == 1


def test_cp08d3_operator_summary_exposes_cleanup_without_provider_controls(monkeypatch, tmp_path):
    configure_test_root(monkeypatch, tmp_path)

    async def run(client):
        response = await client.get("/api/operator/projects/vertical_slice_cp07/summary")
        assert response.status_code == 200
        payload = response.json()
        cleanup = payload["cjk_cleanup"]
        assert cleanup["machine_verdict"] == "PASS"
        assert cleanup["provider_calls"] == {"gemini": 0, "elevenlabs": 0}
        assert cleanup["issue_summary"]["total"] == len(cleanup["issues"])
        assert cleanup["issue_summary"]["needs_review"] >= 1
        assert payload["preview"]["filename"] == "cp07a_targeted_human_review_repair_720p.mp4"
        serialized = json.dumps(payload, ensure_ascii=False).lower()
        assert "xi-api-key" not in serialized
        assert "api_key" not in serialized
        assert "authorization" not in serialized

    asyncio.run(_with_client(run))


def test_cp08d3_static_ui_has_required_closed_loop_controls():
    js = Path("app/static/operator/app.js").read_text(encoding="utf-8")
    for label in [
        "Analyze source text regions",
        "Run cleanup pass",
        "Scan repaired output",
        "Retry selected interval",
        "Open next residual issue",
        "Previous issue",
        "Seek to issue timestamp",
        "View before/after",
        "Mark reviewed",
        "Approve cleanup",
        "Approve preservation",
    ]:
        assert label in js
    assert "/cleanup/action" in js
    assert "/content/transform" not in js
    assert "/transcribe" not in js
    assert "xi-api-key" not in js
