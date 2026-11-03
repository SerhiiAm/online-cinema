from typing import Annotated, Literal

from pydantic import (
    AfterValidator,
    AliasPath,
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
)

from app.core.validators import validate_password_strength
from app.models.accounts import UserGroupEnum

StrongPassword = Annotated[str, AfterValidator(validate_password_strength)]


class UserRegistrationRequest(BaseModel):
    email: EmailStr
    password: StrongPassword


class UserRegistrationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: EmailStr
    is_active: bool


class UserActivationRequest(BaseModel):
    email: EmailStr
    token: str


class ResendActivationRequest(BaseModel):
    email: EmailStr


class MessageResponse(BaseModel):
    message: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class PasswordChangeRequest(BaseModel):
    # Not StrongPassword: the old password is only compared with the hash.
    old_password: str
    new_password: StrongPassword


class RefreshTokenRequest(BaseModel):
    refresh_token: str


class TokenPairResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: EmailStr
    is_active: bool
    # Takes user.group.name, so the client gets "user" instead of a group id.
    group: UserGroupEnum = Field(validation_alias=AliasPath("group", "name"))


class UserGroupUpdateRequest(BaseModel):
    # "admin" is not accepted: admins are managed only on the server
    # (python -m app.scripts.make_admin).
    # Plain strings, so a 422 says "'user' or 'moderator'", not enum reprs.
    group: Literal["user", "moderator"]
