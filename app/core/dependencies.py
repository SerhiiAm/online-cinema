from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import TokenType, decode_token
from app.crud import accounts as crud
from app.models.accounts import User

INVALID_ACCESS_TOKEN = "Invalid or expired access token."
INACTIVE_ACCOUNT = "Account is inactive."

# Adds the "Authorize" button to Swagger; a missing header -> 401.
bearer_scheme = HTTPBearer(
    description="Paste the `access_token` from `POST /accounts/login`."
)


def _invalid_access_token() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=INVALID_ACCESS_TOKEN,
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(bearer_scheme)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    try:
        payload = decode_token(credentials.credentials, TokenType.ACCESS)
    except jwt.InvalidTokenError:
        raise _invalid_access_token()

    # Loaded from the DB on every request (not trusted from the token),
    # so a role change or deactivation applies immediately.
    user = await crud.get_user_by_id(db, int(payload["sub"]))
    if user is None:
        raise _invalid_access_token()
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=INACTIVE_ACCOUNT
        )
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
