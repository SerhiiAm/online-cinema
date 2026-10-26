from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, EmailStr

from app.core.validators import validate_password_strength

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
