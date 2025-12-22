from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import get_settings
from .continuous import continuous_manager
from .database import Base, engine, ensure_schema
from .exporters import setup_exporters
from .notifications import set_notification_loop, setup_notifications
from .routers import agents, falcon, scans, spectrum, stream, cellular
from .routers import ingest

settings = get_settings()

app = FastAPI(title="Sparrow Multi-Agent Controller", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(agents.router)
app.include_router(scans.router)
app.include_router(falcon.router)
app.include_router(spectrum.router)
app.include_router(cellular.router)
app.include_router(ingest.router)
app.include_router(stream.router)

frontend_dir = Path(__file__).resolve().parent.parent / "frontend"
if frontend_dir.exists():
    app.mount("/static", StaticFiles(directory=frontend_dir), name="static")


@app.on_event("startup")
async def on_startup():
    ensure_schema()
    Base.metadata.create_all(bind=engine)
    setup_exporters()
    setup_notifications()
    loop = asyncio.get_running_loop()
    set_notification_loop(loop)


@app.on_event("shutdown")
async def on_shutdown():
    await continuous_manager.shutdown()


@app.get("/")
def serve_index():
    index_file = frontend_dir / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return {"message": "Controller is running", "docs": f"{settings.controller_base_url}/docs"}
