import pytest

from app.core.validators import MIN_PASSWORD_LENGTH, validate_password_strength

SHORTEST_VALID = "Aa1!" + "a" * (MIN_PASSWORD_LENGTH - 4)
TOO_SHORT = SHORTEST_VALID[:-1]


@pytest.mark.parametrize(
    "password",
    [SHORTEST_VALID, "Str0ng!pass", "Very$ecure2026Password"],
    ids=["minimum-length", "regular", "long"],
)
def test_valid_password_is_returned_unchanged(password: str) -> None:
    assert validate_password_strength(password) == password


@pytest.mark.parametrize(
    ("password", "error"),
    [
        ("", f"at least {MIN_PASSWORD_LENGTH} characters"),
        (TOO_SHORT, f"at least {MIN_PASSWORD_LENGTH} characters"),
        ("str0ng!pass", "uppercase letter"),
        ("STR0NG!PASS", "lowercase letter"),
        ("Strong!pass", "digit"),
        ("Str0ngpass", "special character"),
    ],
    ids=[
        "empty",
        "one-char-too-short",
        "no-uppercase",
        "no-lowercase",
        "no-digit",
        "no-special-character",
    ],
)
def test_invalid_password_raises_value_error(password: str, error: str) -> None:
    with pytest.raises(ValueError, match=error):
        validate_password_strength(password)
