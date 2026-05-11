import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import data_sources, home, outcomes, presets, recommendations, runs, skills, stats
from app.core.logger import setup_logging
from app.db.init_db import init_db

logger = logging.getLogger(__name__)

app = FastAPI(title="AShare Shortline System")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def catch_unhandled_exceptions(request: Request, call_next):
    try:
        return await call_next(request)
    except Exception:
        logger.exception("Unhandled request exception: %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error"},
        )


@app.on_event("startup")
def on_startup() -> None:
    setup_logging()
    init_db()


@app.get("/api/health")
def health():
    return {"ok": True}


app.include_router(recommendations.router)
app.include_router(data_sources.router)
app.include_router(home.router)
app.include_router(presets.router)
app.include_router(runs.router)
app.include_router(skills.router)
app.include_router(stats.router)
app.include_router(outcomes.router)
