from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps import get_current_active_user
from app.core.database import get_db
from app.models.broker_settings import BrokerSettings
from app.models.user import User

router = APIRouter()


async def _get_groww_client(user: User, db: AsyncSession):
    result = await db.execute(
        select(BrokerSettings).where(BrokerSettings.user_id == user.id)
    )
    settings = result.scalar_one_or_none()

    if not settings or not settings.access_token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No Groww access token found. Please generate a token in Settings → Broker.",
        )

    try:
        import io, sys
        from growwapi import GrowwAPI  # type: ignore[import]
        _o, _e = sys.stdout, sys.stderr
        sys.stdout = sys.stderr = io.StringIO()
        try:
            client = GrowwAPI(settings.access_token)
        finally:
            sys.stdout, sys.stderr = _o, _e
        return client
    except ImportError:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="growwapi package is not installed.",
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to initialise Groww client: {exc}",
        )


@router.get("/holdings")
async def get_holdings(
    current_user: Annotated[User, Depends(get_current_active_user)],
    db: AsyncSession = Depends(get_db),
):
    groww = await _get_groww_client(current_user, db)
    try:
        return groww.get_holdings_for_user()
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e))


@router.get("/positions")
async def get_positions(
    current_user: Annotated[User, Depends(get_current_active_user)],
    db: AsyncSession = Depends(get_db),
):
    groww = await _get_groww_client(current_user, db)
    try:
        return groww.get_positions_for_user()
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e))


@router.get("/capital")
async def get_capital(
    current_user: Annotated[User, Depends(get_current_active_user)],
    db: AsyncSession = Depends(get_db),
):
    groww = await _get_groww_client(current_user, db)
    try:
        return groww.get_available_margin_details()
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e))


@router.get("/profile")
async def get_profile(
    current_user: Annotated[User, Depends(get_current_active_user)],
    db: AsyncSession = Depends(get_db),
):
    groww = await _get_groww_client(current_user, db)
    try:
        return groww.get_user_profile()
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e))
