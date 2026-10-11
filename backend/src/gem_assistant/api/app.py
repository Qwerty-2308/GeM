"""FastAPI app factory. Serves the API under /api and the frontend at /."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from ..container import Container
from .routes import bids, discovery, profile, safety

LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost"})


def _host_only(value: str) -> str:
    value = value.split("://", 1)[-1]
    return value.rsplit(":", 1)[0] if value.count(":") == 1 else value


def create_app(container: Container, allowed_hosts: frozenset[str] = LOCAL_HOSTS) -> FastAPI:
    app = FastAPI(title="GeM Assistant", docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.state.container = container

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        # This server holds seller data and can resume fetching. Refuse requests
        # that don't come from this machine's own browser tab (blocks DNS
        # rebinding and other websites posting to localhost).
        host = _host_only(request.headers.get("host", ""))
        if host not in allowed_hosts:
            return JSONResponse({"detail": "Local access only"}, status_code=403)
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("origin")
            if origin is not None and _host_only(origin) not in allowed_hosts:
                return JSONResponse({"detail": "Cross-site request refused"}, status_code=403)
        return await call_next(request)

    app.include_router(bids.router)
    app.include_router(profile.router)
    app.include_router(safety.router)
    app.include_router(discovery.router)

    frontend = container.settings.frontend_dir
    if frontend.is_dir():
        app.mount("/", StaticFiles(directory=frontend, html=True), name="frontend")
    return app
