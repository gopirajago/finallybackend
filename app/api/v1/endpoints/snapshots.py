"""Snapshot endpoints – serve stored portfolio history to the dashboard."""

from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps import get_current_active_user
from app.core.database import get_db
from app.models.portfolio_snapshot import PortfolioSnapshot
from app.models.user import User

router = APIRouter()


@router.get("/latest")
async def get_latest_snapshot(
    current_user: Annotated[User, Depends(get_current_active_user)],
    db: AsyncSession = Depends(get_db),
) -> Any:
    """Return the most recent snapshot for the current user."""
    result = await db.execute(
        select(PortfolioSnapshot)
        .where(PortfolioSnapshot.user_id == current_user.id)
        .order_by(PortfolioSnapshot.snapshot_date.desc())
        .limit(1)
    )
    snap = result.scalar_one_or_none()
    if not snap:
        return None
    return _serialize(snap)


@router.get("/history")
async def get_snapshot_history(
    current_user: Annotated[User, Depends(get_current_active_user)],
    db: AsyncSession = Depends(get_db),
    days: int = 30,
) -> Any:
    """Return the last N daily snapshots (summary only, no holdings_json)."""
    result = await db.execute(
        select(PortfolioSnapshot)
        .where(PortfolioSnapshot.user_id == current_user.id)
        .order_by(PortfolioSnapshot.snapshot_date.desc())
        .limit(days)
    )
    snaps = result.scalars().all()
    # Return ascending for chart rendering
    return [_serialize_summary(s) for s in reversed(snaps)]


@router.get("/holdings-history")
async def get_holdings_history(
    current_user: Annotated[User, Depends(get_current_active_user)],
    db: AsyncSession = Depends(get_db),
    days: int = 30,
) -> Any:
    """Return per-stock daily performance across the last N snapshots."""
    result = await db.execute(
        select(PortfolioSnapshot)
        .where(PortfolioSnapshot.user_id == current_user.id)
        .order_by(PortfolioSnapshot.snapshot_date.asc())
        .limit(days)
    )
    snaps = result.scalars().all()

    # Build a dict keyed by symbol → list of daily data points
    stock_map: dict[str, list[dict]] = {}
    for snap in snaps:
        holdings = snap.holdings_json or []
        for h in holdings:
            sym = h.get("symbol", "")
            if not sym:
                continue
            stock_map.setdefault(sym, []).append({
                "date": snap.snapshot_date.isoformat(),
                "pnl": h.get("pnl", 0),
                "pnl_pct": h.get("pnl_pct", 0),
                "ltp": h.get("ltp", 0),
                "avg_price": h.get("avg_price", 0),
                "quantity": h.get("quantity", 0),
                "current_value": h.get("current_value", 0),
            })

    return stock_map


@router.get("/pnl-report")
async def get_pnl_report(
    current_user: Annotated[User, Depends(get_current_active_user)],
    db: AsyncSession = Depends(get_db),
    days: int = 90,
) -> Any:
    """Return full daily P&L report: per-day intraday/fno breakdown + per-holding P&L history."""
    result = await db.execute(
        select(PortfolioSnapshot)
        .where(PortfolioSnapshot.user_id == current_user.id)
        .order_by(PortfolioSnapshot.snapshot_date.desc())
        .limit(days)
    )
    snaps = list(reversed(result.scalars().all()))

    # ── Daily summary rows (for the Intraday tab) ───────────────────────────
    daily_rows = []
    for s in snaps:
        positions = s.positions_json or []
        intraday_pos = [p for p in positions if p.get("segment") != "FNO"]
        fno_pos      = [p for p in positions if p.get("segment") == "FNO"]
        daily_rows.append({
            "date":            s.snapshot_date.isoformat(),
            "total_capital":   s.total_capital,
            "holdings_value":  s.holdings_value,
            "total_invested":  s.total_invested,
            "equity_pnl":      s.total_pnl,
            "equity_pnl_pct":  s.total_pnl_pct,
            "intraday_pnl":    s.intraday_pnl,
            "fno_pnl":         s.fno_pnl,
            "total_day_pnl":   (s.intraday_pnl or 0) + (s.fno_pnl or 0),
            "intraday_trades": len(intraday_pos),
            "fno_trades":      len(fno_pos),
            "positions":       positions,
        })

    # ── Per-holding history (for the Holdings tab) ──────────────────────────
    holding_map: dict[str, dict] = {}
    for s in snaps:
        for h in (s.holdings_json or []):
            sym = h.get("symbol", "")
            if not sym:
                continue
            if sym not in holding_map:
                holding_map[sym] = {
                    "symbol":    sym,
                    "quantity":  h.get("quantity", 0),
                    "avg_price": h.get("avg_price", 0),
                    "ltp":       h.get("ltp", 0),
                    "invested":  h.get("invested", 0),
                    "current_value": h.get("current_value", 0),
                    "pnl":       h.get("pnl", 0),
                    "pnl_pct":   h.get("pnl_pct", 0),
                    "history":   [],
                }
            else:
                # Update to latest values
                holding_map[sym].update({
                    "quantity":      h.get("quantity", holding_map[sym]["quantity"]),
                    "avg_price":     h.get("avg_price", holding_map[sym]["avg_price"]),
                    "ltp":           h.get("ltp", holding_map[sym]["ltp"]),
                    "invested":      h.get("invested", holding_map[sym]["invested"]),
                    "current_value": h.get("current_value", holding_map[sym]["current_value"]),
                    "pnl":           h.get("pnl", holding_map[sym]["pnl"]),
                    "pnl_pct":       h.get("pnl_pct", holding_map[sym]["pnl_pct"]),
                })
            holding_map[sym]["history"].append({
                "date":          s.snapshot_date.isoformat(),
                "pnl":           h.get("pnl", 0),
                "pnl_pct":       h.get("pnl_pct", 0),
                "ltp":           h.get("ltp", 0),
                "current_value": h.get("current_value", 0),
            })

    return {
        "daily":    daily_rows,
        "holdings": list(holding_map.values()),
    }


@router.post("/capture-now")
async def capture_snapshot_now(
    current_user: Annotated[User, Depends(get_current_active_user)],
    db: AsyncSession = Depends(get_db),
) -> Any:
    """Manually trigger a snapshot capture for the current user (useful for testing)."""
    from sqlalchemy import select as sa_select
    from app.models.broker_settings import BrokerSettings
    from app.core.scheduler import _capture_snapshot_for_user

    result = await db.execute(
        sa_select(BrokerSettings).where(BrokerSettings.user_id == current_user.id)
    )
    bs = result.scalar_one_or_none()
    if not bs or not bs.access_token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No Groww access token. Configure it in Settings → Broker first.",
        )

    try:
        await _capture_snapshot_for_user(current_user.id, bs.access_token, raise_errors=True)
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))

    # Return the freshly saved snapshot
    result2 = await db.execute(
        sa_select(PortfolioSnapshot)
        .where(PortfolioSnapshot.user_id == current_user.id)
        .order_by(PortfolioSnapshot.snapshot_date.desc())
        .limit(1)
    )
    snap = result2.scalar_one_or_none()
    return _serialize(snap) if snap else {"detail": "Snapshot saved"}


# ── helpers ────────────────────────────────────────────────────────────────

def _serialize(s: PortfolioSnapshot) -> dict:
    return {
        "id": s.id,
        "snapshot_date": s.snapshot_date.isoformat(),
        "available_cash": s.available_cash,
        "used_margin": s.used_margin,
        "total_capital": s.total_capital,
        "holdings_value": s.holdings_value,
        "total_invested": s.total_invested,
        "total_pnl": s.total_pnl,
        "total_pnl_pct": s.total_pnl_pct,
        "holdings_count": s.holdings_count,
        "holdings_json": s.holdings_json,
        "intraday_pnl": s.intraday_pnl,
        "fno_pnl": s.fno_pnl,
        "positions_json": s.positions_json,
        "captured_at": s.captured_at.isoformat(),
    }


def _serialize_summary(s: PortfolioSnapshot) -> dict:
    return {
        "snapshot_date": s.snapshot_date.isoformat(),
        "available_cash": s.available_cash,
        "used_margin": s.used_margin,
        "total_capital": s.total_capital,
        "holdings_value": s.holdings_value,
        "total_invested": s.total_invested,
        "total_pnl": s.total_pnl,
        "total_pnl_pct": s.total_pnl_pct,
        "holdings_count": s.holdings_count,
        "intraday_pnl": s.intraday_pnl,
        "fno_pnl": s.fno_pnl,
    }
