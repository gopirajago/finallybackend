from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps import get_current_active_user
from app.core.database import get_db
from app.models.broker_settings import BrokerSettings
from app.models.user import User
from app.schemas.broker import BrokerSettingsResponse, BrokerSettingsSave, GenerateTokenResponse

router = APIRouter()


@router.get("", response_model=BrokerSettingsResponse | None)
async def get_broker_settings(
    current_user: Annotated[User, Depends(get_current_active_user)],
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(BrokerSettings).where(BrokerSettings.user_id == current_user.id)
    )
    settings = result.scalar_one_or_none()
    return settings


@router.put("", response_model=BrokerSettingsResponse)
async def save_broker_settings(
    payload: BrokerSettingsSave,
    current_user: Annotated[User, Depends(get_current_active_user)],
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(BrokerSettings).where(BrokerSettings.user_id == current_user.id)
    )
    settings = result.scalar_one_or_none()

    if settings is None:
        settings = BrokerSettings(user_id=current_user.id, broker="groww")
        db.add(settings)

    if payload.api_key is not None:
        settings.api_key = payload.api_key
    if payload.api_secret is not None:
        settings.api_secret = payload.api_secret

    await db.commit()
    await db.refresh(settings)
    return settings


@router.post("/generate-token", response_model=GenerateTokenResponse)
async def generate_groww_token(
    current_user: Annotated[User, Depends(get_current_active_user)],
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(BrokerSettings).where(BrokerSettings.user_id == current_user.id)
    )
    settings = result.scalar_one_or_none()

    if settings is None or not settings.api_key or not settings.api_secret:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Groww API key and secret must be saved before generating a token.",
        )

    try:
        from growwapi import GrowwAPI  # type: ignore[import]
        access_token = GrowwAPI.get_access_token(
            api_key=settings.api_key,
            secret=settings.api_secret,
        )
    except ImportError:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="growwapi package is not installed. Run: pip install growwapi",
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Groww token generation failed: {str(e)}",
        )

    now = datetime.now(timezone.utc)
    settings.access_token = access_token
    settings.token_generated_at = now
    await db.commit()

    return GenerateTokenResponse(access_token=access_token, token_generated_at=now)
