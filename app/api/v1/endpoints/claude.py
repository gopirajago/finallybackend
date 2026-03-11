from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps import get_current_active_user
from app.core.database import get_db
from app.models.claude_settings import ClaudeSettings
from app.models.user import User
from app.schemas.claude import ClaudeSettingsResponse, ClaudeSettingsSave

router = APIRouter()

CLAUDE_MODELS = [
    "claude-opus-4-5",
    "claude-sonnet-4-5",
    "claude-haiku-4-5",
    "claude-3-5-sonnet-20241022",
    "claude-3-5-haiku-20241022",
    "claude-3-opus-20240229",
]


@router.get("", response_model=ClaudeSettingsResponse | None)
async def get_claude_settings(
    current_user: Annotated[User, Depends(get_current_active_user)],
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(ClaudeSettings).where(ClaudeSettings.user_id == current_user.id)
    )
    return result.scalar_one_or_none()


@router.put("", response_model=ClaudeSettingsResponse)
async def save_claude_settings(
    payload: ClaudeSettingsSave,
    current_user: Annotated[User, Depends(get_current_active_user)],
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(ClaudeSettings).where(ClaudeSettings.user_id == current_user.id)
    )
    settings = result.scalar_one_or_none()

    if settings is None:
        settings = ClaudeSettings(user_id=current_user.id)
        db.add(settings)

    if payload.api_key is not None:
        settings.api_key = payload.api_key
    if payload.model is not None:
        if payload.model not in CLAUDE_MODELS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid model. Choose from: {', '.join(CLAUDE_MODELS)}",
            )
        settings.model = payload.model

    await db.commit()
    await db.refresh(settings)
    return settings


@router.post("/verify", response_model=dict)
async def verify_claude_key(
    current_user: Annotated[User, Depends(get_current_active_user)],
    db: AsyncSession = Depends(get_db),
):
    """Send a minimal test request to Claude to verify the API key is valid."""
    result = await db.execute(
        select(ClaudeSettings).where(ClaudeSettings.user_id == current_user.id)
    )
    settings = result.scalar_one_or_none()

    if not settings or not settings.api_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No Claude API key saved. Save your key first.",
        )

    try:
        import asyncio
        import anthropic  # type: ignore[import]

        def _verify():
            client = anthropic.Anthropic(api_key=settings.api_key)
            client.messages.create(
                model=settings.model,
                max_tokens=10,
                messages=[{"role": "user", "content": "hi"}],
            )

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _verify)
        return {"valid": True, "model": settings.model}
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Claude API key verification failed: {exc}",
        )


@router.get("/models", response_model=list[str])
async def list_models(
    current_user: Annotated[User, Depends(get_current_active_user)],
):
    return CLAUDE_MODELS
