from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .db import settings
from .routers import daily, health, identity, mistakes, quota, review, sessions


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
app.include_router(identity.router, tags=["identity"])
app.include_router(sessions.router, tags=["sessions"])
app.include_router(review.router, tags=["review"])
app.include_router(mistakes.router, tags=["mistakes"])
app.include_router(daily.router, tags=["daily"])
app.include_router(quota.router, tags=["quota"])
