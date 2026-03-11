from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps import get_current_active_user
from app.core.database import get_db
from app.core.security import (
    create_access_token,
    create_password_reset_token,
    create_refresh_token,
    get_password_hash,
    verify_password,
)
from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.schemas.auth import (
    ForgotPasswordRequest,
    LoginResponse,
    RefreshRequest,
    RegisterRequest,
    ResetPasswordRequest,
    Token,
)
from app.schemas.user import UserResponse

router = APIRouter()


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register(
    data: RegisterRequest,
    db: AsyncSession = Depends(get_db),
):
    existing = await db.execute(
        select(User).where((User.email == data.email) | (User.username == data.username))
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Email or username already registered")

    user = User(
        email=data.email,
        username=data.username,
        full_name=data.full_name,
        hashed_password=get_password_hash(data.password),
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


@router.post("/login", response_model=LoginResponse)
async def login(
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(User).where(
            (User.username == form_data.username) | (User.email == form_data.username)
        )
    )
    user = result.scalar_one_or_none()
    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not user.is_active:
        raise HTTPException(status_code=400, detail="Inactive user")

    access_token = create_access_token(subject=user.id)
    raw_refresh, refresh_expires = create_refresh_token()
    db.add(RefreshToken(token=raw_refresh, user_id=user.id, expires_at=refresh_expires))
    await db.commit()

    return LoginResponse(access_token=access_token, refresh_token=raw_refresh, user=user)


@router.post("/refresh", response_model=Token)
async def refresh_token(
    data: RefreshRequest,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(RefreshToken).where(
            RefreshToken.token == data.refresh_token,
            RefreshToken.is_revoked == False,  # noqa: E712
        )
    )
    db_token = result.scalar_one_or_none()
    if not db_token:
        raise HTTPException(status_code=401, detail="Invalid or revoked refresh token")
    if db_token.expires_at.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
        raise HTTPException(status_code=401, detail="Refresh token expired")

    db_token.is_revoked = True
    raw_refresh, refresh_expires = create_refresh_token()
    db.add(RefreshToken(token=raw_refresh, user_id=db_token.user_id, expires_at=refresh_expires))
    await db.commit()

    return Token(access_token=create_access_token(subject=db_token.user_id))


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    data: RefreshRequest,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(RefreshToken).where(RefreshToken.token == data.refresh_token)
    )
    db_token = result.scalar_one_or_none()
    if db_token:
        db_token.is_revoked = True
        await db.commit()


@router.post("/forgot-password")
async def forgot_password(
    data: ForgotPasswordRequest,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(User).where(User.email == data.email))
    user = result.scalar_one_or_none()
    if not user:
        return {"message": "If that email exists, a reset link has been sent."}

    token, expires_at = create_password_reset_token()
    user.reset_token = token
    user.reset_token_expires = expires_at
    await db.commit()

    return {
        "message": "If that email exists, a reset link has been sent.",
        "debug_reset_token": token,
    }


@router.post("/reset-password")
async def reset_password(
    data: ResetPasswordRequest,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(User).where(User.reset_token == data.token)
    )
    user = result.scalar_one_or_none()
    if not user or not user.reset_token_expires:
        raise HTTPException(status_code=400, detail="Invalid or expired reset token")
    if user.reset_token_expires.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail="Reset token expired")

    user.hashed_password = get_password_hash(data.new_password)
    user.reset_token = None
    user.reset_token_expires = None
    await db.commit()
    return {"message": "Password reset successfully"}


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: Annotated[User, Depends(get_current_active_user)]):
    return current_user
