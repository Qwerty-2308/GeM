"""FastAPI app factory. Serves the API under /api and the frontend at /."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from ..container import Container
from .routes import bids, chat, discovery, profile, safety

LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost"})

# Everything is served from this app; nothing may frame it (clickjacking on "Resume
# fetching"). Inline styles are allowed because views set style attributes; inline scripts
# are not.
SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
        "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    ),
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}


def _host_only(value: str) -> str:
    value = value.split("://", 1)[-1]
    return value.rsplit(":", 1)[0] if value.count(":") == 1 else value


async def _local_only(request: Request, call_next, allowed_hosts: frozenset[str]):
    # This server holds seller data and can resume fetching. Refuse requests that don't come
    # from this machine's own browser tab (blocks DNS rebinding and other websites posting
    # to localhost).
    host = _host_only(request.headers.get("host", ""))
    if host not in allowed_hosts:
        return JSONResponse({"detail": "Local access only"}, status_code=403)
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("origin")
        if origin is not None and _host_only(origin) not in allowed_hosts:
            return JSONResponse({"detail": "Cross-site request refused"}, status_code=403)
    return await call_next(request)


def create_app(container: Container, allowed_hosts: frozenset[str] = LOCAL_HOSTS) -> FastAPI:
    # No Swagger/ReDoc pages: they load scripts from a CDN. The OpenAPI JSON stays.
    app = FastAPI(title="GeM Assistant", docs_url=None, redoc_url=None, openapi_url="/api/openapi.json")
    app.state.container = container

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        response = await _local_only(request, call_next, allowed_hosts)
        response.headers.update(SECURITY_HEADERS)
        return response

    app.include_router(bids.router)
    app.include_router(profile.router)
    app.include_router(safety.router)
    app.include_router(discovery.router)
    app.include_router(chat.router)

    frontend = container.settings.frontend_dir
    if frontend.is_dir():
        app.mount("/", StaticFiles(directory=frontend, html=True), name="frontend")
    return app
