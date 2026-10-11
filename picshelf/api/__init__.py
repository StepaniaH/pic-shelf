"""FastAPI 应用工厂：REST API + 静态 Web 前端。"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, FastAPI
from fastapi.staticfiles import StaticFiles

from .. import __version__, auth
from ..config import Config
from . import routes_admin, routes_auth, routes_core, routes_import, routes_interact, routes_play

WEB_DIR = Path(__file__).resolve().parent.parent / "web"


def create_app(cfg: Config) -> FastAPI:
    cfg.ensure()
    app = FastAPI(
        title="Pic Shelf",
        version=__version__,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        redoc_url=None,
    )
    app.state.cfg = cfg
    app.state.secret = auth.load_secret(cfg.secret_path)

    api = APIRouter(prefix="/api/v1")
    for router in (
        routes_auth.router,
        routes_core.router,
        routes_interact.router,
        routes_play.router,
        routes_import.router,
        routes_admin.router,
    ):
        api.include_router(router)
    app.include_router(api)

    if WEB_DIR.is_dir():
        app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")
    return app
