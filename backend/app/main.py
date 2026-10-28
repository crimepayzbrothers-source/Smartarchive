"""Web-App: API und Auslieferung der PWA.

Start: uvicorn app.main:app --host 0.0.0.0 --port 8080
"""
from __future__ import annotations

import logging
import os
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Route

from . import config
from .api import auth, bank, docs, meta, realestate
from .db import connect, migrate
from .seed import seed

log = logging.getLogger("smartarchive")


def init_db() -> None:
    conn = connect()
    try:
        migrate(conn)
        seed(conn)
    finally:
        conn.close()


def _inline_worker() -> None:
    from . import worker
    import time

    conn = connect()
    worker.recover_stale(conn)
    while True:
        try:
            if worker.run_pending(conn, limit=5) == 0:
                time.sleep(2)
        except Exception:  # noqa: BLE001
            log.exception("Fehler im eingebauten Worker")
            time.sleep(5)


@asynccontextmanager
async def lifespan(app: Starlette):
    config.settings.data_dir.mkdir(parents=True, exist_ok=True)
    init_db()
    if os.environ.get("INLINE_WORKER") == "1":
        threading.Thread(target=_inline_worker, name="worker", daemon=True).start()
        log.info("Eingebauter Worker gestartet")
    yield


class SecurityHeaders(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response: Response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Permissions-Policy", "camera=(self), microphone=(), geolocation=()")
        if not request.url.path.startswith("/api/") or request.url.path.endswith(("/file", "/thumb")):
            response.headers.setdefault(
                "Content-Security-Policy",
                "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; "
                "script-src 'self'; connect-src 'self'; frame-src 'self'; object-src 'self'; base-uri 'none'; "
                "frame-ancestors 'self'")
        if request.url.path.startswith("/api/") and "cache-control" not in response.headers:
            response.headers["Cache-Control"] = "no-store"
        return response


async def health(request: Request) -> Response:
    return JSONResponse({"ok": True})


def _static_dir() -> Path:
    return Path(config.settings.static_dir)


async def spa(request: Request) -> Response:
    """Liefert die PWA aus; unbekannte Pfade landen bei index.html (Client-Routing)."""
    root = _static_dir()
    rel = request.path_params.get("path", "")
    candidate = (root / rel).resolve()
    if rel and root.resolve() in candidate.parents and candidate.is_file():
        headers = {"Cache-Control": "public, max-age=31536000, immutable"} if rel.startswith("assets/") else {"Cache-Control": "no-cache"}
        return FileResponse(candidate, headers=headers)
    index = root / "index.html"
    if not index.exists():
        return Response("Frontend nicht gebaut. Bitte im Ordner frontend „npm run build“ ausführen.", status_code=503,
                        media_type="text/plain; charset=utf-8")
    return FileResponse(index, headers={"Cache-Control": "no-cache"})


async def share_fallback(request: Request) -> Response:
    """Ohne aktiven Service Worker landet „Teilen“ hier: freundlich in den Eingang umleiten."""
    from starlette.responses import RedirectResponse
    return RedirectResponse("/#/eingang", status_code=303)


async def api_not_found(request: Request) -> Response:
    return JSONResponse({"error": "Unbekannter API-Pfad."}, status_code=404)


routes = [
    Route("/api/health", health),
    *auth.routes, *docs.routes, *meta.routes, *realestate.routes, *bank.routes,
    Route("/api/{rest:path}", api_not_found, methods=["GET", "POST", "PATCH", "DELETE", "PUT"]),
    Route("/share-target", share_fallback, methods=["POST"]),
    Route("/{path:path}", spa, methods=["GET", "HEAD"]),
]

app = Starlette(routes=routes, lifespan=lifespan, middleware=[Middleware(SecurityHeaders)])

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

