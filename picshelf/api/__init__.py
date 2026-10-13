"""FastAPI 应用工厂：REST API + 静态 Web 前端。"""
from __future__ import annotations

from pathlib import Path
from contextlib import asynccontextmanager
from threading import BoundedSemaphore

from fastapi import APIRouter, FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError

from .. import __version__, auth
from ..config import Config
from ..errors import ServiceError
from ..i18n import set_lang as _set_lang
from ..i18n import t
from .security import check_browser_request
from ..worker import WorkerManager
from . import routes_admin, routes_auth, routes_core, routes_import, routes_interact, routes_play, routes_jobs, routes_metadata, routes_batches

WEB_DIR = Path(__file__).resolve().parent.parent / "web"


def create_app_dev() -> FastAPI:
    """Uvicorn reload worker 的无参数工厂。"""
    return create_app(Config.resolve())


def create_app(cfg: Config) -> FastAPI:
    cfg.ensure()
    workers = WorkerManager(cfg)

    @asynccontextmanager
    async def lifespan(app):
        workers.start()
        try:
            yield
        finally:
            workers.close()

    app = FastAPI(
        title="Pic Shelf",
        version=__version__,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.cfg = cfg
    app.state.workers = workers
    app.state.secret = auth.load_secret(cfg.secret_path)
    app.state.login_slots = BoundedSemaphore(4)

    @app.exception_handler(ServiceError)
    async def _service_error(request, exc: ServiceError):
        headers = {"Retry-After": str(getattr(exc, "retry_after", 60))} if exc.status == 429 else None
        return JSONResponse(status_code=exc.status, content=exc.as_dict(), headers=headers)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request, exc):
        # Validation inputs may contain passwords, tokens or private paths.
        return JSONResponse(status_code=422, content={"detail": [
            {key: error[key] for key in ("type", "loc", "msg")} for error in exc.errors()]})

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
        try:
            if request.url.path.startswith("/api/"):
                check_browser_request(request, cfg)
                if request.method not in ("GET", "HEAD", "OPTIONS"):
                    body = bytearray()
                    async for chunk in request.stream():
                        body.extend(chunk)
                        if len(body) > 1024 * 1024:
                            raise ServiceError("request_too_large", t("请求内容过大"), status=413,
                                               next_step=t("请缩小请求范围后重试"))
                    request._body = bytes(body)
            response = await call_next(request)
        except ServiceError as exc:
            response = await _service_error(request, exc)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["X-Frame-Options"] = "DENY"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "private, no-store"
        return response

    api = APIRouter(prefix="/api/v1")
    for router in (
        routes_auth.router,
        routes_core.router,
        routes_interact.router,
        routes_play.router,
        routes_import.router,
        routes_admin.router,
        routes_jobs.router,
        routes_metadata.router,
        routes_batches.router,
    ):
        api.include_router(router)
    app.include_router(api)

    if WEB_DIR.is_dir():
        app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")
    return app
