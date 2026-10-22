"""The Sentinel API. Run with: uvicorn services.api.main:app --reload"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from services.api.routers import cameras, health, segments, videos
from shared.config import get_settings
from shared.crypto import check_key


@asynccontextmanager
async def lifespan(app: FastAPI):
    check_key()  # refuse to start with a missing or invalid FERNET_KEY
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="Sentinel API", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=get_settings().cors_origin_list,
        # The dev server moves to another port when 3000 is taken; the API only listens on this machine.
        allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:\d+)?",
        allow_methods=["*"],
        allow_headers=["*"],
    )
    for module in (health, cameras, videos, segments):
        app.include_router(module.router)
    return app


app = create_app()
