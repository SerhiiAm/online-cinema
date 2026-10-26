from fastapi import FastAPI

from app.core.config import settings
from app.routers import accounts

app = FastAPI(
    title=settings.app_name,
    description="Async online cinema REST API",
    version="0.1.0",
)

app.include_router(accounts.router)


@app.get("/health", tags=["Health"])
async def health() -> dict[str, str]:
    return {"status": "ok"}
