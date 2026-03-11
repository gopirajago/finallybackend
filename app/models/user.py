from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.refresh_token import RefreshToken
    from app.models.broker_settings import BrokerSettings
    from app.models.portfolio_snapshot import PortfolioSnapshot
    from app.models.claude_settings import ClaudeSettings


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    email: Mapped[str] = mapped_column(String, unique=True, index=True, nullable=False)
    username: Mapped[str] = mapped_column(String, unique=True, index=True, nullable=False)
    full_name: Mapped[str | None] = mapped_column(String, nullable=True)
    hashed_password: Mapped[str] = mapped_column(String, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_superuser: Mapped[bool] = mapped_column(Boolean, default=False)
    reset_token: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    reset_token_expires: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    refresh_tokens: Mapped[list[RefreshToken]] = relationship(
        "RefreshToken", back_populates="user", cascade="all, delete-orphan"
    )

    broker_settings: Mapped[BrokerSettings | None] = relationship(
        "BrokerSettings", back_populates="user", uselist=False, cascade="all, delete-orphan"
    )

    portfolio_snapshots: Mapped[list[PortfolioSnapshot]] = relationship(
        "PortfolioSnapshot", back_populates="user", cascade="all, delete-orphan"
    )

    claude_settings: Mapped[ClaudeSettings | None] = relationship(
        "ClaudeSettings", back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
