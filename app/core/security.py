import secrets

from pwdlib import PasswordHash

password_hash = PasswordHash.recommended()

TOKEN_BYTES = 32


def hash_password(password: str) -> str:
    return password_hash.hash(password)


def verify_password(password: str, hashed_password: str) -> bool:
    return password_hash.verify(password, hashed_password)


def generate_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)
