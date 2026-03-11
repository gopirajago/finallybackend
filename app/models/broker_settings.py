from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class BrokerSettings(Base):
    __tablename__ = "broker_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False, index=True)
    broker: Mapped[str] = mapped_column(String(50), nullable=False, default="groww")
    api_key: Mapped[str | None] = mapped_column(String, nullable=True)
    api_secret: Mapped[str | None] = mapped_column(String, nullable=True)
    access_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    user: Mapped["User"] = relationship("User", back_populates="broker_settings")  # type: ignore[name-defined]
