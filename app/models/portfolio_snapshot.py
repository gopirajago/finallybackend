from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import Date, DateTime, Float, ForeignKey, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class PortfolioSnapshot(Base):
    """Daily snapshot of a user's portfolio — stored once per day at 9 AM IST."""

    __tablename__ = "portfolio_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    snapshot_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)

    # Capital
    available_cash: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    used_margin: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    total_capital: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    # Portfolio totals
    holdings_value: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    total_invested: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    total_pnl: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    total_pnl_pct: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    holdings_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Intraday & FNO P&L (from positions)
    intraday_pnl: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    fno_pnl: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    positions_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Full holdings list as JSON: [{symbol, quantity, avg_price, ltp, pnl, pnl_pct}]
    holdings_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    user: Mapped["User"] = relationship("User", back_populates="portfolio_snapshots")  # type: ignore[name-defined]
