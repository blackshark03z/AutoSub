import asyncio
import os
from pathlib import Path

import httpx


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


def test_health_and_preflight(monkeypatch, tmp_path):
    configure_test_root(monkeypatch, tmp_path)
    async def run(client):
        health = client.get("/api/health")
        health = await health
        assert health.status_code == 200
        assert health.json()["bind"] == "127.0.0.1"

        preflight = await client.get("/api/preflight")
        assert preflight.status_code == 200
        payload = preflight.json()
        assert payload["ffmpeg"] is True
        assert payload["ffprobe"] is True
        assert payload["font_exists"] is True
    asyncio.run(_with_client(run))


def test_project_create_list_and_get(monkeypatch, tmp_path):
    configure_test_root(monkeypatch, tmp_path)
    async def run(client):
        created = (await client.post("/api/projects", json={"title": "Smoke"})).json()
        project_id = created["project_id"]
        assert project_id.startswith("proj_")
        assert (await client.get(f"/api/projects/{project_id}")).status_code == 200
        projects = (await client.get("/api/projects")).json()["projects"]
        assert any(p["project_id"] == project_id for p in projects)
    asyncio.run(_with_client(run))


def test_source_import_smoke(monkeypatch, tmp_path):
    configure_test_root(monkeypatch, tmp_path)
    source = Path.cwd() / "input" / "source.mp4"
    if not source.exists():
        return
    async def run(client):
        project_id = (await client.post("/api/projects", json={"title": "Import smoke"})).json()["project_id"]
        response = await client.post(f"/api/projects/{project_id}/source/import", json={"source_path": str(source)})
        assert response.status_code == 200
        payload = response.json()
        assert payload["source"]["sha256"] == "34a304fb44f5e4c27d1a34989a69f939888ef90c89bbae0142434f43cf4db068"
        assert payload["media"]["video"]["width"] == 1920
        assert payload["media"]["video"]["height"] == 1080
    asyncio.run(_with_client(run))
