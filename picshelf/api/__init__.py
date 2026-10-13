"""FastAPI 应用工厂：REST API + 静态 Web 前端。"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse

from .. import __version__, auth
from ..config import Config
from ..errors import ServiceError
from ..i18n import set_lang as _set_lang
from . import routes_admin, routes_auth, routes_core, routes_import, routes_interact, routes_play

WEB_DIR = Path(__file__).resolve().parent.parent / "web"


def create_app_dev() -> FastAPI:
    """Uvicorn reload worker 的无参数工厂。"""
    return create_app(Config.resolve())


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

    @app.exception_handler(ServiceError)
    async def _service_error(request, exc: ServiceError):
        return JSONResponse(status_code=exc.status, content=exc.as_dict())

    @app.middleware("http")
    async def _lang_from_header(request, call_next):
        # ContextVar 按请求隔离，线程池会继承当前请求的语言。
        header = request.headers.get("accept-language", "")
        first = header.split(",")[0].strip().lower()
        if first.startswith("zh"):
            _set_lang("zh")
        elif first.startswith("en"):
            _set_lang("en")
        elif not header:
            _set_lang(None)  # 回退：PICSHELF_LANG / locale / 默认
        return await call_next(request)

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
