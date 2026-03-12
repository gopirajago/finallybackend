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


@router.get("/live-summary")
async def get_live_summary(
    current_user: Annotated[User, Depends(get_current_active_user)],
    db: AsyncSession = Depends(get_db),
):
    """Return live computed P&L, capital, and holdings from Groww in real time."""
    import asyncio, io, sys
    groww = await _get_groww_client(current_user, db)

    def _fetch():
        _o, _e = sys.stdout, sys.stderr
        sys.stdout = sys.stderr = io.StringIO()
        try:
            holdings_data = groww.get_holdings_for_user()
            capital_data = groww.get_available_margin_details()
        finally:
            sys.stdout, sys.stderr = _o, _e
        return holdings_data, capital_data

    try:
        loop = asyncio.get_event_loop()
        holdings_data, capital_data = await loop.run_in_executor(None, _fetch)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e))

    cap = capital_data if isinstance(capital_data, dict) else {}
    eq = cap.get("equity_margin_details") or {}
    total_capital = float(cap.get("clear_cash") or 0)
    used_margin = float(cap.get("net_margin_used") or 0)
    available_cash = float(eq.get("cnc_balance_available") or cap.get("clear_cash") or 0)

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

        # Groww doesn't return LTP after market hours — fetch via quote
        if not ltp and symbol:
            try:
                def _get_quote(sym=symbol):
                    _o, _e = sys.stdout, sys.stderr
                    sys.stdout = sys.stderr = io.StringIO()
                    try:
                        return groww.get_quote(trading_symbol=sym, exchange="NSE", segment="CASH")
                    finally:
                        sys.stdout, sys.stderr = _o, _e
                quote = await loop.run_in_executor(None, _get_quote)
                if isinstance(quote, dict):
                    ltp = float(quote.get("last_price") or quote.get("ltp") or 0)
            except Exception:
                ltp = 0.0

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
            "invested": round(invested, 2),
            "current_value": round(current_val, 2),
            "pnl": round(pnl, 2),
            "pnl_pct": round(pnl_pct, 2),
        })

    total_pnl = holdings_value - total_invested
    total_pnl_pct = (total_pnl / total_invested * 100) if total_invested else 0.0

    return {
        "total_capital": round(total_capital, 2),
        "available_cash": round(available_cash, 2),
        "used_margin": round(used_margin, 2),
        "holdings_value": round(holdings_value, 2),
        "total_invested": round(total_invested, 2),
        "total_pnl": round(total_pnl, 2),
        "total_pnl_pct": round(total_pnl_pct, 2),
        "holdings_count": len(holdings_list),
        "holdings": holdings_list,
    }
