"""FastAPI 应用工厂：REST API + 静态 Web 前端。"""
from __future__ import annotations

from pathlib import Path
from contextlib import asynccontextmanager
from threading import BoundedSemaphore

from fastapi import APIRouter, FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException

from .. import __version__, auth
from ..config import Config
from ..errors import ServiceError
from ..i18n import set_lang as _set_lang
from ..i18n import t
from .security import check_browser_request
from ..worker import WorkerManager
from . import routes_admin, routes_auth, routes_core, routes_import, routes_interact, routes_play, routes_jobs, routes_metadata, routes_batches, routes_queries, routes_manifests

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
        body = ServiceError("invalid_arguments", t("请求参数无效"), status=422,
                            next_step=t("请按接口说明检查字段类型和范围后重试")).as_dict()
        body["detail"] = [{key: error[key] for key in ("type", "loc", "msg")} for error in exc.errors()]
        return JSONResponse(status_code=422, content=body)

    @app.exception_handler(HTTPException)
    async def _http_error(request, exc):
        codes = {400: "invalid_arguments", 401: "authentication_required", 403: "permission_denied",
                 404: "not_found", 405: "method_not_allowed", 409: "state_conflict", 410: "plan_expired",
                 413: "request_too_large", 422: "invalid_arguments", 429: "rate_limited"}
        steps = {401: "请先登录后重试", 403: "请联系管理员检查权限", 404: "请刷新列表并检查目标是否仍可访问",
                 409: "请重新读取当前状态后再试", 410: "请重新创建预演计划", 429: "请稍后重试"}
        body = ServiceError(codes.get(exc.status_code, "request_failed"), exc.detail,
                            status=exc.status_code, retryable=exc.status_code == 429,
                            next_step=t(steps.get(exc.status_code, "请按接口说明检查字段类型和范围后重试"))).as_dict()
        return JSONResponse(status_code=exc.status_code, content=body, headers=exc.headers)

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
        except Exception:
            # Never return exception text: it can include private paths or inputs.
            response = await _service_error(request, ServiceError(
                "internal_error", t("服务未能完成请求"), status=500,
                next_step=t("请重新读取当前状态；确认是否已保存后再试，持续失败时联系管理员")))
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
        routes_queries.router,
        routes_manifests.router,
    ):
        api.include_router(router)
    app.include_router(api)

    if WEB_DIR.is_dir():
        app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")
    return app
