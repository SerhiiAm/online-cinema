import math
from typing import Any

from fastapi import Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


def _json_safe(value: Any) -> Any:
    # JSON has no NaN/Infinity, so a response containing them can't be
    # written -> 500. They are returned as text instead: "nan", "inf".
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return value


async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """FastAPI's 422 response, but safe for NaN/Infinity in the request.

    Python's JSON parser accepts NaN and Infinity in a request body, and the
    422 response echoes the wrong value back ("input"). FastAPI's own
    handler then fails to write it, so any endpoint answered 500.
    """
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content={"detail": _json_safe(jsonable_encoder(exc.errors()))},
    )
