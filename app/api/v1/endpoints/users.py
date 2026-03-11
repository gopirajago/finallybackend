from typing import Annotated, List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps import get_current_active_user, get_current_superuser
from app.core.database import get_db
from app.core.security import get_password_hash
from app.models.user import User
from app.schemas.user import UserCreate, UserResponse, UserUpdate

router = APIRouter()


@router.get("/", response_model=List[UserResponse])
async def list_users(
    skip: int = 0,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_superuser),
):
    result = await db.execute(select(User).offset(skip).limit(limit))
    return result.scalars().all()


@router.post("/", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_user(
    user_in: UserCreate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_superuser),
):
    result = await db.execute(
        select(User).where(or_(User.email == user_in.email, User.username == user_in.username))
    )
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Email or username already registered")

    user = User(
        email=user_in.email,
        username=user_in.username,
        full_name=user_in.full_name,
        hashed_password=get_password_hash(user_in.password),
        is_active=user_in.is_active,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: Annotated[User, Depends(get_current_active_user)]):
    return current_user


@router.patch("/me", response_model=UserResponse)
async def update_me(
    user_in: UserUpdate,
    current_user: Annotated[User, Depends(get_current_active_user)],
    db: AsyncSession = Depends(get_db),
):
    if user_in.email:
        current_user.email = user_in.email
    if user_in.username:
        current_user.username = user_in.username
    if user_in.full_name is not None:
        current_user.full_name = user_in.full_name
    if user_in.password:
        current_user.hashed_password = get_password_hash(user_in.password)
    await db.commit()
    await db.refresh(current_user)
    return current_user


@router.get("/{user_id}", response_model=UserResponse)
async def get_user(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_superuser),
):
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@router.patch("/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: int,
    user_in: UserUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_superuser),
):
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user_in.email:
        user.email = user_in.email
    if user_in.username:
        user.username = user_in.username
    if user_in.full_name is not None:
        user.full_name = user_in.full_name
    if user_in.password:
        user.hashed_password = get_password_hash(user_in.password)
    if user_in.is_active is not None:
        user.is_active = user_in.is_active
    await db.commit()
    await db.refresh(user)
    return user


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_superuser),
):
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.id == current_user.id:
        raise HTTPException(status_code=400, detail="Cannot delete yourself")
    await db.delete(user)
    await db.commit()
