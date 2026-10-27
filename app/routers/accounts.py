import logging
import secrets
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from kombu.exceptions import OperationalError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.crud import accounts as crud
from app.models.accounts import User
from app.schemas.accounts import (
    MessageResponse,
    ResendActivationRequest,
    UserActivationRequest,
    UserRegistrationRequest,
    UserRegistrationResponse,
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
