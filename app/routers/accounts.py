import asyncio
import logging
import secrets
from datetime import datetime, timezone
from typing import Annotated

import jwt
from fastapi import APIRouter, Depends, HTTPException, status
from kombu.exceptions import OperationalError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.dependencies import AdminUser, CurrentUser
from app.core.security import (
    DUMMY_PASSWORD_HASH,
    TokenType,
    create_access_token,
    decode_token,
    verify_password,
)
from app.crud import accounts as crud
from app.models.accounts import User, UserGroupEnum
from app.schemas.accounts import (
    LoginRequest,
    MessageResponse,
    PasswordChangeRequest,
    RefreshTokenRequest,
    ResendActivationRequest,
    TokenPairResponse,
    UserActivationRequest,
    UserGroupUpdateRequest,
    UserRegistrationRequest,
    UserRegistrationResponse,
    UserResponse,
)
from app.tasks.email import send_activation_email

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/accounts", tags=["Accounts"])

DbSession = Annotated[AsyncSession, Depends(get_db)]

TOKEN_TTL_HOURS = settings.activation_token_expire_hours

EMAIL_TAKEN = "A user with this email already exists."
INVALID_ACTIVATION = "Invalid email or activation token."
ALREADY_ACTIVE = "Account is already active."
TOKEN_EXPIRED = "Activation token has expired. Request a new one."
INVALID_CREDENTIALS = "Invalid email or password."
ACCOUNT_NOT_ACTIVATED = "Account is not activated."
INVALID_REFRESH_TOKEN = "Invalid or expired refresh token."
USER_NOT_FOUND = "User not found."
ADMIN_GROUP_LOCKED = "Admin accounts can only be changed on the server."
WRONG_PASSWORD = "Current password is incorrect."
SAME_PASSWORD = "New password must be different from the current one."


def queue_activation_email(user: User, token: str) -> None:
    # The user is already saved, so a broker outage must not turn into a 500:
    # the user can request a new email via /resend-activation.
    try:
        send_activation_email.delay(user.email, token)
    except OperationalError:
        logger.exception("Failed to queue activation email for user %s", user.id)


@router.post(
    "/register",
    status_code=status.HTTP_201_CREATED,
    summary="Register a new user",
    description=(
        "Creates an inactive account in the `user` group and sends an "
        f"activation email with a token valid for {TOKEN_TTL_HOURS} hours. "
        "The account must be activated "
        "via `POST /accounts/activate` before it can be used.\n\n"
        "Password requirements: at least 8 characters, one uppercase letter, "
        "one lowercase letter, one digit and one special character."
    ),
    responses={
        status.HTTP_409_CONFLICT: {
            "description": "A user with this email already exists."
        },
    },
)
async def register(
    data: UserRegistrationRequest, db: DbSession
) -> UserRegistrationResponse:
    if await crud.get_user_by_email(db, data.email) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=EMAIL_TAKEN)

    try:
        user, activation_token = await crud.create_user(
            db, data.email, data.password
        )
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=EMAIL_TAKEN)

    queue_activation_email(user, activation_token.token)
    return UserRegistrationResponse.model_validate(user)


@router.post(
    "/activate",
    summary="Activate an account",
    description=(
        "Activates the account using the email and the token from the "
        "activation email. The token is single-use and expires "
        f"{TOKEN_TTL_HOURS} hours "
        "after it was issued."
    ),
    responses={
        status.HTTP_400_BAD_REQUEST: {
            "description": (
                "Invalid email or token, the account is already active, "
                "or the token has expired."
            )
        },
    },
)
async def activate(data: UserActivationRequest, db: DbSession) -> MessageResponse:
    user = await crud.get_user_by_email(db, data.email)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=INVALID_ACTIVATION
        )
    if user.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=ALREADY_ACTIVE
        )

    activation_token = await crud.get_activation_token(db, user.id)
    if activation_token is None or not secrets.compare_digest(
        activation_token.token.encode(), data.token.encode()
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=INVALID_ACTIVATION
        )
    if activation_token.expires_at < datetime.now(timezone.utc):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=TOKEN_EXPIRED
        )

    await crud.activate_user(db, user)
    return MessageResponse(message="Account activated successfully.")


@router.post(
    "/resend-activation",
    summary="Resend the activation email",
    description=(
        f"Issues a new activation token (valid for {TOKEN_TTL_HOURS} hours), "
        "invalidates the previous one and sends it by email. "
        "Always returns the same response, "
        "so it cannot be used to check whether an email is registered."
    ),
)
async def resend_activation(
    data: ResendActivationRequest, db: DbSession
) -> MessageResponse:
    user = await crud.get_user_by_email(db, data.email)
    if user is not None and not user.is_active:
        activation_token = await crud.refresh_activation_token(db, user)
        queue_activation_email(user, activation_token.token)

    return MessageResponse(
        message=(
            "If the account exists and is not active, "
            "a new activation link has been sent."
        )
    )


@router.post(
    "/login",
    summary="Log in and get a token pair",
    description=(
        "Checks the email and password and returns a pair of JWT tokens:\n\n"
        f"- `access_token` (valid for {settings.access_token_expire_minutes} "
        "minutes) — send it as `Authorization: Bearer <token>` "
        "to access protected endpoints;\n"
        f"- `refresh_token` (valid for {settings.refresh_token_expire_days} "
        "days) — use it to get a new pair when the access token expires.\n\n"
        "Each login creates a separate session, so logging in on one device "
        "does not log out the others."
    ),
    responses={
        status.HTTP_401_UNAUTHORIZED: {"description": "Invalid email or password."},
        status.HTTP_403_FORBIDDEN: {
            "description": "The password is correct, but the account is not "
            "activated yet."
        },
    },
)
async def login(data: LoginRequest, db: DbSession) -> TokenPairResponse:
    user = await crud.get_user_by_email(db, data.email)
    hashed_password = user.hashed_password if user else DUMMY_PASSWORD_HASH
    password_ok = await asyncio.to_thread(
        verify_password, data.password, hashed_password
    )
    if user is None or not password_ok:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=INVALID_CREDENTIALS
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=ACCOUNT_NOT_ACTIVATED
        )

    return TokenPairResponse(
        access_token=create_access_token(user.id),
        refresh_token=await crud.issue_refresh_token(db, user.id),
    )


@router.post(
    "/refresh",
    summary="Get a new token pair using a refresh token",
    description=(
        "Exchanges a valid refresh token for a new pair of tokens. "
        "The refresh token is single-use: it is revoked as soon as it is "
        f"used, and the new one is valid for {settings.refresh_token_expire_days} "
        "days from now.\n\n"
        "If an already used refresh token is sent again, it is treated as "
        "stolen: all sessions of the user are revoked and they must log in "
        "again."
    ),
    responses={
        status.HTTP_401_UNAUTHORIZED: {
            "description": "The refresh token is invalid, expired, revoked "
            "or has already been used."
        },
    },
)
async def refresh(data: RefreshTokenRequest, db: DbSession) -> TokenPairResponse:
    try:
        payload = decode_token(data.refresh_token, TokenType.REFRESH)
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=INVALID_REFRESH_TOKEN
        )

    rotated = await crud.rotate_refresh_token(db, payload["jti"])
    if rotated is None:
        stored = await crud.get_refresh_token(db, payload["jti"])
        if stored is not None and stored.revoked:
            # Reuse of a revoked token means it was stolen:
            # kill every session of the user, the thief's included.
            await crud.revoke_all_refresh_tokens(db, stored.user_id)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=INVALID_REFRESH_TOKEN
        )

    user_id, refresh_token = rotated
    return TokenPairResponse(
        access_token=create_access_token(user_id), refresh_token=refresh_token
    )


@router.post(
    "/logout",
    summary="Log out of the current session",
    description=(
        "Revokes the given refresh token, so it can no longer be used to get "
        "new tokens. Other sessions of the user stay active. The access token "
        "is not stored and stays valid until it expires (at most "
        f"{settings.access_token_expire_minutes} minutes), so the client "
        "should delete both tokens.\n\n"
        "Calling it again with the same token returns the same response."
    ),
    responses={
        status.HTTP_401_UNAUTHORIZED: {
            "description": "The refresh token is invalid or expired."
        },
    },
)
async def logout(data: RefreshTokenRequest, db: DbSession) -> MessageResponse:
    try:
        payload = decode_token(data.refresh_token, TokenType.REFRESH)
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=INVALID_REFRESH_TOKEN
        )

    # Idempotent: an already revoked token is not treated as reuse here,
    # so a double click or a retried request does not end other sessions.
    await crud.revoke_refresh_token(db, payload["jti"])
    return MessageResponse(message="Logged out successfully.")


@router.get(
    "/me",
    summary="Get the current user",
    description=(
        "Returns the account of the user the access token belongs to, "
        "including their group (`user`, `moderator` or `admin`).\n\n"
        "Requires `Authorization: Bearer <access_token>` — in Swagger, "
        "click **Authorize** and paste the token."
    ),
    responses={
        status.HTTP_401_UNAUTHORIZED: {
            "description": "The access token is missing, invalid or expired, "
            "or the user no longer exists."
        },
        status.HTTP_403_FORBIDDEN: {"description": "The account is inactive."},
    },
)
async def read_me(user: CurrentUser) -> UserResponse:
    return UserResponse.model_validate(user)


@router.post(
    "/change-password",
    summary="Change the password",
    description=(
        "Sets a new password after checking the current one. "
        "All sessions of the user, including the current one, are logged out "
        "and a pending password reset link stops working, so the user has to "
        "log in again with the new password. The access token stays valid "
        f"until it expires (at most {settings.access_token_expire_minutes} "
        "minutes).\n\n"
        "Password requirements: at least 8 characters, one uppercase letter, "
        "one lowercase letter, one digit and one special character. "
        "The new password must differ from the current one."
    ),
    responses={
        status.HTTP_400_BAD_REQUEST: {
            "description": "The current password is wrong, or the new password "
            "is the same as the current one."
        },
        status.HTTP_401_UNAUTHORIZED: {
            "description": "The access token is missing, invalid or expired."
        },
        status.HTTP_403_FORBIDDEN: {"description": "The account is inactive."},
    },
)
async def change_password(
    data: PasswordChangeRequest, user: CurrentUser, db: DbSession
) -> MessageResponse:
    # 400, not 401: a 401 would make the client think its token has expired.
    if not await asyncio.to_thread(
        verify_password, data.old_password, user.hashed_password
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=WRONG_PASSWORD
        )
    # The old password is known to be correct here, so comparing the plain
    # strings is enough (no second slow hash check).
    if data.new_password == data.old_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=SAME_PASSWORD
        )

    await crud.set_password(db, user, data.new_password)
    return MessageResponse(
        message="Password changed successfully. Log in again with the new password."
    )


@router.patch(
    "/users/{user_id}/group",
    summary="Change a user's group (admin only)",
    description=(
        "Moves a user between the `user` and `moderator` groups. "
        "The new group applies to the user's very next request, "
        "no new login is needed.\n\n"
        "The `admin` group can't be assigned or taken away here, and admin "
        "accounts (including your own) can't be changed: admins are managed "
        "only on the server with `python -m app.scripts.make_admin <email>`."
    ),
    responses={
        status.HTTP_401_UNAUTHORIZED: {
            "description": "The access token is missing, invalid or expired."
        },
        status.HTTP_403_FORBIDDEN: {
            "description": "You are not an admin, or the target user is an admin."
        },
        status.HTTP_404_NOT_FOUND: {"description": "The user does not exist."},
    },
)
async def change_user_group(
    user_id: int, data: UserGroupUpdateRequest, admin: AdminUser, db: DbSession
) -> UserResponse:
    group = UserGroupEnum(data.group)
    if not await crud.change_user_group(db, user_id, group):
        # Nothing was updated: find out why.
        if await crud.get_user_by_id(db, user_id) is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=USER_NOT_FOUND
            )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=ADMIN_GROUP_LOCKED
        )

    return UserResponse.model_validate(await crud.get_user_by_id(db, user_id))


@router.post(
    "/users/{user_id}/activate",
    summary="Activate a user's account manually (admin only)",
    description=(
        "Activates the account without the email token, e.g. when the "
        "activation email never arrived. The user's pending activation "
        "token is deleted.\n\n"
        "Activating an already active account changes nothing and returns "
        "the same response."
    ),
    responses={
        status.HTTP_401_UNAUTHORIZED: {
            "description": "The access token is missing, invalid or expired."
        },
        status.HTTP_403_FORBIDDEN: {"description": "You are not an admin."},
        status.HTTP_404_NOT_FOUND: {"description": "The user does not exist."},
    },
)
async def activate_user_by_admin(
    user_id: int, admin: AdminUser, db: DbSession
) -> UserResponse:
    user = await crud.get_user_by_id(db, user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=USER_NOT_FOUND
        )
    if not user.is_active:
        await crud.activate_user(db, user)
    return UserResponse.model_validate(user)
