from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.core.config import get_settings
from app.db.session import init_db
from app.services.operator_ui import repair_legacy_test_fixture_projects
from app.services.simple_workflow import repair_invalid_completed_results


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    repair_legacy_test_fixture_projects()
    repair_invalid_completed_results()
    yield


app = FastAPI(title="Tool Auto Sub", version="0.2.0", lifespan=lifespan)
app.include_router(router, prefix="/api")

settings = get_settings()
operator_static = settings.root / "app" / "static" / "operator"
if operator_static.exists():
    app.mount("/operator", StaticFiles(directory=operator_static, html=True), name="operator")

simple_static = settings.root / "app" / "static" / "simple"
if simple_static.exists():
    app.mount("/", StaticFiles(directory=simple_static, html=True), name="simple")
