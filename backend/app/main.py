from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .db import settings
from .routers import health


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.auto_migrate:
        from .db import init_db

        init_db()
    yield


app = FastAPI(title="AI 闯关学习小程序后端", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router, tags=["health"])
