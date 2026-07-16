"""App factory: init DB, mount static + templates, include routers, healthz.

Run with: uvicorn app.main:app --host 0.0.0.0 --port 8090
"""
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import config, db, discovery
from .errors import HubError, error_body, hub_error_handler
from . import (
    routes_session,
    routes_chat,
    routes_notes,
    routes_checklists,
    routes_files,
    routes_keys,
    routes_admin,
    routes_voice,
    routes_studio,
)


def create_app() -> FastAPI:
    config.ensure_dirs()
    db.init_db()

    app = FastAPI(title="Home LLM Hub", version=config.HUB_VERSION, docs_url=None, redoc_url=None)

    # --- Error handlers: always JSON envelope {"error":{message,code}} ---
    app.add_exception_handler(HubError, hub_error_handler)

    @app.exception_handler(StarletteHTTPException)
    async def _http_exc(request: Request, exc: StarletteHTTPException):
        code = {400: "bad_request", 401: "unauthorized", 403: "forbidden",
                404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "error")
        detail = exc.detail if isinstance(exc.detail, str) else "error"
        return JSONResponse(status_code=exc.status_code, content=error_body(detail, code))

    @app.exception_handler(RequestValidationError)
    async def _validation_exc(request: Request, exc: RequestValidationError):
        return JSONResponse(status_code=400, content=error_body(
            "Invalid request body or parameters", "bad_request"))

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        return JSONResponse(status_code=500, content=error_body(
            "Internal server error", "internal_error"))

    # --- Routers ---
    app.include_router(routes_session.router)
    app.include_router(routes_chat.router)
    app.include_router(routes_notes.router)
    app.include_router(routes_checklists.router)
    app.include_router(routes_files.router)
    app.include_router(routes_keys.router)
    app.include_router(routes_admin.router)
    app.include_router(routes_voice.router)
    app.include_router(routes_studio.router)

    # --- Health ---
    @app.get("/healthz")
    def healthz():
        return {"status": "ok", "service": "home-hub", "version": config.HUB_VERSION}

    # --- Capability status: which upstreams are reachable right now ------------
    # The hub stays up even when the LLM stack is stopped (e.g. to free RAM for
    # image generation). The frontend polls this to show "offline" states
    # instead of erroring.
    @app.get("/api/status")
    async def capability_status():
        import httpx

        async def ping(url: str) -> bool:
            try:
                async with httpx.AsyncClient(timeout=1.5) as c:
                    r = await c.get(url)
                    return r.status_code < 500
            except Exception:
                return False

        ai = await ping(f"{config.GATEWAY_URL}/healthz")   # gateway + Ollama
        voice = await ping(f"{config.VOICE_URL}/healthz")
        images = await ping(f"{config.IMAGES_URL}/")       # FastSD gradio root
        return {
            "chat": ai, "vision": ai, "voice": voice, "images": images,
            "images_url": config.PUBLIC_IMAGES_URL,
        }

    # --- Public discovery (CORS-open) ---------------------------------------
    # Lets the login gate (served from this or another origin) probe candidate
    # addresses to find a Home Hub on the LAN. Intentionally unauthenticated and
    # cross-origin readable; it exposes only non-sensitive identity info.
    def _discovery_payload() -> dict:
        setup_required = True
        try:
            conn = db.connect()
            try:
                setup_required = db.count_admins(conn) == 0
            finally:
                conn.close()
        except Exception:
            # If the DB is unavailable, still answer the probe (best-effort).
            setup_required = True
        base_url = config.PUBLIC_BASE_URL
        return {
            "service": "homehub",
            "name": config.HUB_NAME,
            "version": app.version,
            "base_url": base_url,
            "setup_required": setup_required,
        }

    _CORS_HEADERS = {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, OPTIONS",
        "Access-Control-Allow-Headers": "*",
        "Access-Control-Max-Age": "600",
        "Cache-Control": "no-store",
    }

    @app.get("/api/discovery")
    def discovery_endpoint():
        return JSONResponse(content=_discovery_payload(), headers=_CORS_HEADERS)

    @app.options("/api/discovery")
    def discovery_preflight():
        return JSONResponse(content={}, headers=_CORS_HEADERS)

    # --- LAN discovery (mDNS/Bonjour) lifecycle -----------------------------
    # Optional + fail-safe: discovery.register()/unregister() never raise, so a
    # missing zeroconf package or a name clash only logs a warning.
    @app.on_event("startup")
    def _start_discovery():
        discovery.register()

    @app.on_event("shutdown")
    def _stop_discovery():
        discovery.unregister()

    # --- Static assets ---
    if config.STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(config.STATIC_DIR)), name="static")
    # Studio assets (source stills, .riv, generated motion) — read-only, LAN.
    if config.STUDIO_DIR.exists():
        app.mount("/studio-files", StaticFiles(directory=str(config.STUDIO_DIR)), name="studio-files")
    # FastSD generated images (results/) — so the hub can show them directly.
    import os as _os
    if _os.path.isdir(config.FASTSD_RESULTS_DIR):
        app.mount("/generated-files",
                  StaticFiles(directory=config.FASTSD_RESULTS_DIR), name="generated-files")

    # --- Index page ---
    def _asset_version(name: str) -> str:
        """Cache-buster: the static file's mtime, so browsers refetch app.js /
        style.css whenever they change (no more stale-JS-after-update)."""
        try:
            import os
            return str(int(os.path.getmtime(config.STATIC_DIR / name)))
        except OSError:
            return config.HUB_VERSION

    @app.get("/", response_class=HTMLResponse)
    def index():
        index_html = config.TEMPLATES_DIR / "index.html"
        if index_html.exists():
            html = index_html.read_text(encoding="utf-8")
            html = html.replace("/static/app.js", f"/static/app.js?v={_asset_version('app.js')}")
            html = html.replace("/static/style.css", f"/static/style.css?v={_asset_version('style.css')}")
            # Don't let the browser cache the shell itself, so the versioned
            # asset URLs are always seen.
            return HTMLResponse(html, headers={"Cache-Control": "no-cache"})
        # Minimal fallback so the server is usable before the frontend lands.
        return HTMLResponse(
            "<!doctype html><html><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width, initial-scale=1'>"
            "<title>Home LLM Hub</title></head><body>"
            "<h1>Home LLM Hub</h1>"
            "<p>Backend is running. Frontend template not installed yet.</p>"
            "<p>API base: <code>/api</code> &middot; Health: <code>/healthz</code></p>"
            "</body></html>"
        )

    @app.get("/sw.js", include_in_schema=False)
    def service_worker():
        """Serve the PWA service worker from the ROOT so its scope covers the whole
        app (a SW only controls its own path and below). It only registers in a
        secure context (HTTPS or localhost) — a no-op over plain http://<lan-ip>."""
        sw = config.STATIC_DIR / "sw.js"
        body = sw.read_text(encoding="utf-8") if sw.exists() else "/* no service worker */"
        return PlainTextResponse(
            body, media_type="application/javascript",
            headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"})

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=config.HUB_HOST, port=config.HUB_PORT)
