"""APScheduler – runs a daily portfolio snapshot at 09:00 IST for every user
who has a Groww access token stored."""

import io
import logging
import sys
from datetime import date, datetime, timezone

import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.broker_settings import BrokerSettings
from app.models.portfolio_snapshot import PortfolioSnapshot

logger = logging.getLogger(__name__)

IST = pytz.timezone("Asia/Kolkata")

scheduler = AsyncIOScheduler(timezone=IST)


async def _capture_snapshot_for_user(user_id: int, access_token: str, raise_errors: bool = False) -> None:
    """Fetch live Groww data and upsert a snapshot row for today.
    
    When raise_errors=True (used by the API endpoint), exceptions bubble up
    so the caller can return a meaningful HTTP error to the client.
    """
    try:
        from growwapi import GrowwAPI  # type: ignore[import]
        # GrowwAPI.__init__ calls colorama.init() which can raise OSError/[Errno 22]
        # on Windows when stdout is not a real console (e.g. inside uvicorn workers).
        # Redirect stdout to a buffer during construction to avoid that.
        _old_stdout = sys.stdout
        _old_stderr = sys.stderr
        sys.stdout = io.StringIO()
        sys.stderr = io.StringIO()
        try:
            groww = GrowwAPI(access_token)
        finally:
            sys.stdout = _old_stdout
            sys.stderr = _old_stderr
    except Exception as exc:
        logger.error("Cannot init GrowwAPI for user %s: %s", user_id, exc)
        if raise_errors:
            raise RuntimeError(f"Failed to initialise Groww client: {exc}") from exc
        return

    try:
        _old_stdout = sys.stdout
        _old_stderr = sys.stderr
        sys.stdout = io.StringIO()
        sys.stderr = io.StringIO()
        try:
            holdings_data = groww.get_holdings_for_user()
            capital_data = groww.get_available_margin_details()
        finally:
            sys.stdout = _old_stdout
            sys.stderr = _old_stderr
    except Exception as exc:
        logger.error("Groww API call failed for user %s: %s", user_id, exc)
        if raise_errors:
            raise RuntimeError(f"Groww API error: {exc}") from exc
        return

    # ── parse capital ──────────────────────────────────────────────────────
    # Groww fields:
    #   clear_cash        = total funds in account (cash + margin used + free balance)
    #   net_margin_used   = portion of clear_cash currently locked in positions
    #   cnc_balance_available = free equity cash available to trade
    cap = capital_data if isinstance(capital_data, dict) else {}
    eq = cap.get("equity_margin_details") or {}

    total_capital = float(cap.get("clear_cash") or 0)
    used_margin = float(cap.get("net_margin_used") or 0)
    available_cash = float(eq.get("cnc_balance_available") or cap.get("clear_cash") or 0)

    # ── parse holdings ─────────────────────────────────────────────────────
    raw_holdings = []
    if isinstance(holdings_data, dict):
        raw_holdings = holdings_data.get("holdings", []) or []
    elif isinstance(holdings_data, list):
        raw_holdings = holdings_data

    holdings_list = []
    total_invested = 0.0
    holdings_value = 0.0

    for h in raw_holdings:
        symbol = h.get("trading_symbol") or h.get("tradingSymbol") or h.get("symbol", "")
        qty = float(h.get("quantity", h.get("holdingQuantity", 0)) or 0)
        avg_price = float(h.get("average_price", h.get("averagePrice", 0)) or 0)
        ltp = float(h.get("ltp", h.get("last_traded_price", 0)) or 0)
        invested = qty * avg_price
        current_val = qty * ltp if ltp else invested
        pnl = current_val - invested
        pnl_pct = (pnl / invested * 100) if invested else 0.0

        total_invested += invested
        holdings_value += current_val

        holdings_list.append({
            "symbol": symbol,
            "quantity": qty,
            "avg_price": avg_price,
            "ltp": ltp,
            "invested": invested,
            "current_value": current_val,
            "pnl": pnl,
            "pnl_pct": round(pnl_pct, 2),
        })

    total_pnl = holdings_value - total_invested
    total_pnl_pct = (total_pnl / total_invested * 100) if total_invested else 0.0
    today = date.today()

    async with AsyncSessionLocal() as db:
        # Upsert: delete existing snapshot for today then insert fresh
        result = await db.execute(
            select(PortfolioSnapshot).where(
                PortfolioSnapshot.user_id == user_id,
                PortfolioSnapshot.snapshot_date == today,
            )
        )
        for existing in result.scalars().all():
            await db.delete(existing)
        await db.flush()

        snapshot = PortfolioSnapshot(
            user_id=user_id,
            snapshot_date=today,
            available_cash=available_cash,
            used_margin=used_margin,
            total_capital=total_capital,
            holdings_value=round(holdings_value, 2),
            total_invested=round(total_invested, 2),
            total_pnl=round(total_pnl, 2),
            total_pnl_pct=round(total_pnl_pct, 2),
            holdings_count=len(holdings_list),
            holdings_json=holdings_list,
            captured_at=datetime.now(timezone.utc),
        )
        db.add(snapshot)
        await db.commit()
        logger.info(
            "Snapshot saved for user %s on %s: pnl=%.2f", user_id, today, total_pnl
        )


async def daily_snapshot_job() -> None:
    """Called by the scheduler at 09:00 IST – iterates all users with tokens."""
    logger.info("Running daily portfolio snapshot job")
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(BrokerSettings).where(BrokerSettings.access_token.isnot(None))
        )
        all_settings = result.scalars().all()

    for bs in all_settings:
        await _capture_snapshot_for_user(bs.user_id, bs.access_token, raise_errors=False)  # type: ignore[arg-type]

    logger.info("Daily snapshot job complete for %d users", len(all_settings))


def start_scheduler() -> None:
    scheduler.add_job(
        daily_snapshot_job,
        trigger=CronTrigger(hour=9, minute=0, timezone=IST),
        id="daily_portfolio_snapshot",
        replace_existing=True,
    )
    scheduler.start()
    logger.info("Scheduler started – daily snapshot at 09:00 IST")


def stop_scheduler() -> None:
    scheduler.shutdown(wait=False)
