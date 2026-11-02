"""Make an existing user an active admin.

Usage: python -m app.scripts.make_admin <email>
"""

import argparse
import asyncio
import sys

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import async_session_maker, engine
from app.crud import accounts as crud


async def make_admin(db: AsyncSession, email: str) -> bool:
    user = await crud.get_user_by_email(db, email)
    if user is None:
        return False
    await crud.promote_to_admin(db, user)
    return True


async def main(email: str) -> int:
    try:
        async with async_session_maker() as db:
            promoted = await make_admin(db, email)
    finally:
        # Close DB connections before asyncio.run() shuts the event loop down.
        await engine.dispose()

    if not promoted:
        print(f"User {email} not found.", file=sys.stderr)
        return 1
    print(f"{email} is now an active admin.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Make an existing user an active admin."
    )
    parser.add_argument("email", help="Email of an already registered user.")
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.email)))
