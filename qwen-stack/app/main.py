"""FastAPI application entrypoint.

Builds the app, initialises the SQLite database on startup, and mounts all
routers. Run with::

    uvicorn app.main:app --host 0.0.0.0 --port 8080
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import admin_routes, anthropic_routes, db, openai_routes
from .config import ALIAS_MAP, settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialise the database schema before serving requests."""
    db.init_db()
    # Seed the managed-model registry. Idempotent, and seeded entries start
    # 'running' so models already in service are never blocked by the serve-gate.
    #   * chat aliases come from ALIAS_MAP;
    #   * the bundled vision + embedding models (used by the Home Hub for photo
    #     captions and RAG) are registered here so the dashboard can control them
    #     too. They are addressed by these short names in the hub's requests.
    db.seed_models(
        [{"alias": alias, "ollama_tag": tag, "display_name": alias, "role": "chat"}
         for alias, tag in ALIAS_MAP.items()]
        + [
            {"alias": "moondream", "ollama_tag": "moondream:latest",
             "display_name": "moondream (vision)", "role": "vision"},
            {"alias": "nomic-embed-text", "ollama_tag": "nomic-embed-text:latest",
             "display_name": "nomic-embed-text", "role": "embed"},
        ]
    )
    yield


app = FastAPI(
    title="Qwen Stack Gateway",
    version="0.1.0",
    description=(
        "Auth + rate-limiting gateway in front of an Ollama OpenAI-compatible "
        "upstream, with an Anthropic Messages compatibility shim."
    ),
    lifespan=lifespan,
)

# --------------------------------------------------------------------------- #
# CORS
# --------------------------------------------------------------------------- #
# STRICT CORS POLICY: CORS middleware is intentionally NOT enabled by default.
# This gateway is a server-to-server API (clients send secret API keys), so
# browser cross-origin access should remain blocked. The bundled admin UI is
# served same-origin from /admin/ and therefore needs no CORS. If you must
# expose this to trusted browser origins, add CORSMiddleware here with an
# explicit allow_list (never "*") and allow_credentials=False.


# --------------------------------------------------------------------------- #
# Exception handlers — keep all error bodies OpenAI-style.
# --------------------------------------------------------------------------- #
@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    """Render HTTPExceptions; pass through already-shaped error bodies."""
    detail = exc.detail
    if isinstance(detail, dict) and "error" in detail:
        return JSONResponse(status_code=exc.status_code, content=detail)
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"message": str(detail), "type": "api_error",
                           "code": None}},
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request,
                                       exc: RequestValidationError):
    """Render request-validation failures as OpenAI-style 400 errors."""
    return JSONResponse(
        status_code=400,
        content={"error": {"message": "Invalid request body.", "type":
                           "invalid_request_error", "code": "invalid_request"}},
    )


# --------------------------------------------------------------------------- #
# Routers
# --------------------------------------------------------------------------- #
app.include_router(openai_routes.router)
app.include_router(anthropic_routes.router)
app.include_router(admin_routes.router)


@app.get("/", include_in_schema=False)
async def root():
    """Tiny landing payload pointing at the useful endpoints."""
    return {
        "service": "qwen-stack-gateway",
        "version": "0.1.0",
        "endpoints": {
            "openai_chat": "/v1/chat/completions",
            "openai_completions": "/v1/completions",
            "models": "/v1/models",
            "anthropic_messages": "/v1/messages",
            "health": "/healthz",
            "admin_ui": "/admin/",
        },
        "upstream": settings.ollama_base_url,
    }
