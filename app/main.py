"""
fitfilemaker — FastAPI application entry point.
"""

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.routes import router as v1_router

BASE_DIR    = Path(__file__).parent
STATIC_DIR  = BASE_DIR / "static"
NOTICE_FILE = BASE_DIR.parent / "NOTICE"

app = FastAPI(
    title="fitfilemaker",
    description="Merge and convert workout files (.pwx, .fit)",
    version="0.1.0",
    docs_url="/api/docs",
    redoc_url=None,
)

# ---------------------------------------------------------------------------
# Security headers middleware
# ---------------------------------------------------------------------------

@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"]         = "DENY"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline';"
    )
    response.headers["Referrer-Policy"] = "no-referrer"
    return response

# ---------------------------------------------------------------------------
# CORS — LAN-only for now; tighten origins before any public deployment
# ---------------------------------------------------------------------------

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # TODO: restrict to specific origins before public deploy
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

app.include_router(v1_router)


@app.get("/licenses", response_class=PlainTextResponse)
async def licenses():
    """Serve the NOTICE file (open-source license attributions)."""
    if NOTICE_FILE.exists():
        return PlainTextResponse(NOTICE_FILE.read_text())
    return PlainTextResponse("NOTICE file not found.")


@app.get("/", response_class=HTMLResponse)
async def index():
    html = STATIC_DIR / "index.html"
    if html.exists():
        return HTMLResponse(html.read_text())
    return HTMLResponse("<h1>fitfilemaker</h1><p>UI not found.</p>")
