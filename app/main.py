from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError

from app.core.config import settings
from app.core.exception_handlers import validation_exception_handler
from app.routers import accounts, carts, movies, references

app = FastAPI(
    title=settings.app_name,
    description="Async online cinema REST API",
    version="0.1.0",
)

app.add_exception_handler(RequestValidationError, validation_exception_handler)

app.include_router(accounts.router)
app.include_router(movies.router)
for reference_router in references.routers:
    app.include_router(reference_router)
app.include_router(carts.router)


@app.get("/health", tags=["Health"])
async def health() -> dict[str, str]:
    return {"status": "ok"}
