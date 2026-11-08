import re

MIN_PASSWORD_LENGTH = 8
NUL_NOT_ALLOWED = "Text can't contain the NUL character (\\u0000)."


def reject_nul(text: str) -> str:
    # Valid in JSON and Python, but PostgreSQL can't store or compare text
    # with it: the query fails -> 500. Hashed values (passwords, tokens)
    # never reach the DB as text, so only stored/searched text needs this.
    if "\x00" in text:
        raise ValueError(NUL_NOT_ALLOWED)
    return text


def validate_password_strength(password: str) -> str:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(
            f"Password must be at least {MIN_PASSWORD_LENGTH} characters long."
        )
    if not re.search(r"[A-Z]", password):
        raise ValueError("Password must contain at least one uppercase letter.")
    if not re.search(r"[a-z]", password):
        raise ValueError("Password must contain at least one lowercase letter.")
    if not re.search(r"\d", password):
        raise ValueError("Password must contain at least one digit.")
    if not re.search(r"[^A-Za-z0-9]", password):
        raise ValueError(
            "Password must contain at least one special character."
        )
    return password
