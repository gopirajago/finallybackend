"""Run once to create an admin superuser: python create_admin.py"""
import asyncio
from sqlalchemy import select
from app.core.database import AsyncSessionLocal
from app.core.security import get_password_hash
from app.models.user import User

EMAIL = "admin@finally.com"
USERNAME = "admin"
PASSWORD = "Admin@123"
FULL_NAME = "Admin"


async def main():
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(User).where((User.email == EMAIL) | (User.username == USERNAME))
        )
        existing = result.scalar_one_or_none()

        if existing:
            existing.is_superuser = True
            existing.is_active = True
            await db.commit()
            print(f"User '{USERNAME}' already exists — promoted to superuser.")
        else:
            user = User(
                email=EMAIL,
                username=USERNAME,
                full_name=FULL_NAME,
                hashed_password=get_password_hash(PASSWORD),
                is_active=True,
                is_superuser=True,
            )
            db.add(user)
            await db.commit()
            print(f"Admin user created successfully.")

        print(f"\n  Email:    {EMAIL}")
        print(f"  Username: {USERNAME}")
        print(f"  Password: {PASSWORD}")


asyncio.run(main())
