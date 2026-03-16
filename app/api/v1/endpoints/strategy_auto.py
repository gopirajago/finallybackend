"""
Manual trigger endpoint for testing automated signal generation
"""

from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.api.v1.deps import get_current_active_user
from app.models.user import User
from app.services.strategy_scheduler import strategy_scheduler

router = APIRouter()


@router.post("/trigger-scan")
async def trigger_manual_scan(
    current_user: Annotated[User, Depends(get_current_active_user)],
    db: AsyncSession = Depends(get_db),
):
    """
    Manually trigger a strategy scan for testing purposes
    This will run the signal generation process immediately
    """
    try:
        await strategy_scheduler.scan_and_generate_signals()
        return {"message": "Strategy scan triggered successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Scan failed: {str(e)}")


@router.get("/scheduler-status")
async def get_scheduler_status(
    current_user: Annotated[User, Depends(get_current_active_user)],
):
    """Get the current status of the strategy scheduler"""
    return {
        "is_running": strategy_scheduler.is_running,
        "message": "Scheduler is running" if strategy_scheduler.is_running else "Scheduler is stopped"
    }
