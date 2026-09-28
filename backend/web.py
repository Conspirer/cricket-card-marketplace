"""The root ASGI app: the JSON API at /api plus the built React app, on one
origin (no CORS). Production guards are switched on by environment variables:

  SITE_PASSWORD   optional, off by default (the live site is open). If set,
                  the whole site sits behind HTTP basic auth (any username,
                  this password), e.g. to close it for maintenance.
  FORCE_HTTPS     on unless "0": requests a proxy reports as plain HTTP
                  (X-Forwarded-Proto: http, as Render's does) are redirected
                  to HTTPS, so passwords and session cookies never travel
                  in the clear.
                  Direct local requests carry no such header and pass.

/healthz is always open and returns only "ok", for the host's health check.
"""

import base64
import binascii
import os
import secrets
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

FRONTEND_DIST = Path(__file__).resolve().parents[1] / "frontend" / "dist"


class SharedPasswordGate(BaseHTTPMiddleware):
    """HTTP basic auth with one shared password; the username is ignored."""

    def __init__(self, app, password):
        super().__init__(app)
        self.password = password.encode()

    async def dispatch(self, request, call_next):
        if request.url.path == "/healthz":
            return await call_next(request)
        header = request.headers.get("authorization", "")
        if header.startswith("Basic "):
            try:
                _, _, supplied = base64.b64decode(header[6:]).decode().partition(":")
            except (binascii.Error, UnicodeDecodeError):
                supplied = ""
            if secrets.compare_digest(supplied.encode(), self.password):
                return await call_next(request)
        return PlainTextResponse(
            "Password required",
            status_code=401,
            headers={"WWW-Authenticate": 'Basic realm="Crease", charset="UTF-8"'},
        )


class HttpsRedirect(BaseHTTPMiddleware):
    """The hosting proxy terminates TLS and reports the original scheme in X-Forwarded-Proto."""

    async def dispatch(self, request, call_next):
        if request.headers.get("x-forwarded-proto") == "http":
            return RedirectResponse(str(request.url.replace(scheme="https")), status_code=301)
        return await call_next(request)


def build_app(api, password=None, dist=FRONTEND_DIST, force_https=None):
    password = os.environ.get("SITE_PASSWORD") if password is None else password
    force_https = os.environ.get("FORCE_HTTPS", "1") != "0" if force_https is None else force_https

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    # Middleware added last runs first: redirect to HTTPS before asking for the password.
    if password:
        app.add_middleware(SharedPasswordGate, password=password)
    if force_https:
        app.add_middleware(HttpsRedirect)

    @app.get("/healthz", include_in_schema=False)
    def healthz():
        return PlainTextResponse("ok")

    app.mount("/api", api)

    dist = Path(dist)
    if (dist / "index.html").exists():
        if (dist / "assets").is_dir():
            # Vite fingerprints these file names, so they can be cached hard.
            app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def frontend(path: str):
            # Real files (favicon.svg, ...) as-is; every other path is a
            # client-side route, so hand back the app shell.
            candidate = (dist / path).resolve()
            if path and candidate.is_file() and dist.resolve() in candidate.parents:
                return FileResponse(candidate)
            return FileResponse(dist / "index.html", headers={"Cache-Control": "no-cache"})
    else:
        @app.get("/", include_in_schema=False)
        def no_frontend():
            return PlainTextResponse(
                "The frontend isn't built. Run `npm --prefix frontend run build`, "
                "or use the Vite dev server (http://localhost:5173) during development.",
                status_code=503,
            )

    return app
