"""Options chain endpoint – fetch live option chain, recommend OTM strikes,
detect Hero-Zero candidates on expiry day."""
import io
import sys
import asyncio
from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps import get_current_active_user
from app.core.database import get_db
from app.models.broker_settings import BrokerSettings
from app.models.user import User

router = APIRouter()

# ── Instrument config ──────────────────────────────────────────────────────────
# Maps display name → (underlying symbol for get_option_chain, exchange)
OPTION_INSTRUMENTS: dict[str, dict] = {
    "NIFTY":     {"underlying": "NIFTY",     "exchange": "NSE", "lot_size": 75,  "strike_gap": 50},
    "BANKNIFTY": {"underlying": "BANKNIFTY", "exchange": "NSE", "lot_size": 30,  "strike_gap": 100},
    "SENSEX":    {"underlying": "SENSEX",    "exchange": "BSE", "lot_size": 20,  "strike_gap": 100},
    "RELIANCE":  {"underlying": "RELIANCE",  "exchange": "NSE", "lot_size": 250, "strike_gap": 20},
    "INFY":      {"underlying": "INFY",      "exchange": "NSE", "lot_size": 400, "strike_gap": 20},
    "TCS":       {"underlying": "TCS",       "exchange": "NSE", "lot_size": 150, "strike_gap": 50},
    "HDFCBANK":  {"underlying": "HDFCBANK",  "exchange": "NSE", "lot_size": 550, "strike_gap": 10},
    "ICICIBANK": {"underlying": "ICICIBANK", "exchange": "NSE", "lot_size": 700, "strike_gap": 10},
    "SBIN":      {"underlying": "SBIN",      "exchange": "NSE", "lot_size": 1500,"strike_gap": 5},
    "NIFTY IT":  {"underlying": "NIFTYIT",   "exchange": "NSE", "lot_size": 50,  "strike_gap": 100},
}


async def _get_groww(user: User, db: AsyncSession):
    result = await db.execute(
        select(BrokerSettings).where(BrokerSettings.user_id == user.id)
    )
    settings = result.scalar_one_or_none()
    if not settings or not settings.access_token:
        raise HTTPException(
            status_code=400,
            detail="No Groww access token. Please generate one in Settings → Broker.",
        )
    try:
        from growwapi import GrowwAPI  # type: ignore[import]
        _o, _e = sys.stdout, sys.stderr
        sys.stdout = sys.stderr = io.StringIO()
        try:
            client = GrowwAPI(settings.access_token)
        finally:
            sys.stdout, sys.stderr = _o, _e
        return client
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Groww init failed: {exc}")


# ── Strategy helpers ───────────────────────────────────────────────────────────

def _get_expiries_from_df(df, underlying: str) -> list[str]:
    """Get sorted expiry dates for an underlying from instruments dataframe."""
    opts = df[
        (df["underlying_symbol"] == underlying) &
        (df["instrument_type"].isin(["CE", "PE"])) &
        (df["segment"] == "FNO")
    ]
    return sorted(opts["expiry_date"].dropna().unique().tolist())


def _is_expiry_day(expiry_date_str: str) -> bool:
    try:
        exp = date.fromisoformat(expiry_date_str)
        return exp == date.today()
    except Exception:
        return False


def _score_otm_strike(
    strike: float,
    ltp: float,
    option_type: str,
    data: dict,
    lot_size: int,
    is_expiry: bool,
) -> dict | None:
    """Score an OTM strike for trade potential. Returns None if not worth trading."""
    premium = float(data.get("ltp") or 0)
    oi = int(data.get("open_interest") or 0)
    volume = int(data.get("volume") or 0)
    greeks = data.get("greeks") or {}
    delta = abs(float(greeks.get("delta") or 0))
    iv = float(greeks.get("iv") or 0)
    theta = float(greeks.get("theta") or 0)

    if premium <= 0:
        return None

    # OTM definition
    is_otm = (option_type == "CE" and strike > ltp) or (option_type == "PE" and strike < ltp)
    is_itm = not is_otm
    distance_pct = abs(strike - ltp) / ltp * 100

    # OTM range: prefer 0.5% – 3% OTM for indexes, up to 5% for stocks
    if not is_otm:
        return None
    if distance_pct < 0.3 or distance_pct > 6:
        return None

    # Skip if premium is too high (ITM bleed) or too low (junk)
    if premium < 1:
        return None

    # Scoring
    score = 0.0
    reasons = []

    # Distance sweet spot: 1-3% OTM is ideal
    if 0.5 <= distance_pct <= 3.0:
        score += 30
        reasons.append(f"{distance_pct:.1f}% OTM — sweet spot")
    elif distance_pct <= 0.5:
        score += 15
        reasons.append(f"{distance_pct:.1f}% OTM — near ATM")
    else:
        score += 10

    # Delta: prefer 0.15–0.40 range (meaningful but affordable)
    if 0.15 <= delta <= 0.40:
        score += 25
        reasons.append(f"Delta {delta:.2f} — good directional exposure")
    elif delta > 0.40:
        score += 15
        reasons.append(f"Delta {delta:.2f} — high delta")
    else:
        score += 5

    # IV: prefer < 30 for cheap premium
    if iv < 20:
        score += 20
        reasons.append(f"IV {iv:.1f}% — low, cheap premium")
    elif iv < 35:
        score += 12
        reasons.append(f"IV {iv:.1f}% — moderate")
    else:
        score += 3

    # Volume & OI: liquidity matters
    if volume > 1000:
        score += 15
        reasons.append("High volume — liquid")
    elif volume > 100:
        score += 8

    if oi > 5000:
        score += 10

    # Hero-Zero detection: low premium OTM — on expiry day OR pre-expiry watch
    is_hero_zero = False
    if is_expiry and premium <= 30 and distance_pct <= 3.0:
        is_hero_zero = True
        score += 40
        reasons.append(f"⚡ HERO-ZERO (Expiry): ₹{premium} premium — 10x potential!")
    elif not is_expiry and premium <= 50 and distance_pct >= 1.5 and distance_pct <= 4.0:
        is_hero_zero = True
        score += 20
        reasons.append(f"🎯 HERO-ZERO Watch: ₹{premium} cheap OTM — monitor for expiry day entry")

    # Theta decay penalty (non-expiry): high theta = expensive to hold
    if not is_expiry and theta < -5:
        score -= 10

    cost_per_lot = premium * lot_size
    max_profit_estimate = None
    if is_hero_zero:
        # Assume 10x move possible
        max_profit_estimate = premium * 10 * lot_size - cost_per_lot
    else:
        # Conservative: assume 2x
        max_profit_estimate = premium * 2 * lot_size - cost_per_lot

    return {
        "strike": strike,
        "type": option_type,
        "premium": round(premium, 2),
        "delta": round(delta, 3),
        "iv": round(iv, 2),
        "theta": round(theta, 2),
        "oi": oi,
        "volume": volume,
        "distance_pct": round(distance_pct, 2),
        "score": round(score, 1),
        "is_hero_zero": is_hero_zero,
        "cost_per_lot": round(cost_per_lot, 2),
        "max_profit_estimate": round(max_profit_estimate, 2) if max_profit_estimate else None,
        "reasons": reasons,
        "trading_symbol": data.get("trading_symbol", ""),
        "tag": "HERO-ZERO 🎯" if is_hero_zero else ("BUY CE 🟢" if option_type == "CE" else "BUY PE 🔴"),
    }


def _process_chain(
    chain: dict,
    ltp: float,
    lot_size: int,
    strike_gap: int,
    expiry_str: str,
    num_strikes: int = 20,
) -> dict:
    """Process raw option chain into structured data with recommendations."""
    is_expiry = _is_expiry_day(expiry_str)
    strikes_raw = chain.get("strikes", {})

    all_options = []
    chain_table = []

    # Collect strikes near ATM
    atm = round(ltp / strike_gap) * strike_gap
    relevant_strikes = []
    for s_str, s_data in strikes_raw.items():
        try:
            s = float(s_str)
        except ValueError:
            continue
        if abs(s - atm) <= strike_gap * num_strikes:
            relevant_strikes.append((s, s_data))
    relevant_strikes.sort(key=lambda x: x[0])

    for strike, s_data in relevant_strikes:
        ce_data = s_data.get("CE") or {}
        pe_data = s_data.get("PE") or {}

        ce_ltp = float(ce_data.get("ltp") or 0)
        pe_ltp = float(pe_data.get("ltp") or 0)
        ce_oi  = int(ce_data.get("open_interest") or 0)
        pe_oi  = int(pe_data.get("open_interest") or 0)
        ce_iv  = float((ce_data.get("greeks") or {}).get("iv") or 0)
        pe_iv  = float((pe_data.get("greeks") or {}).get("iv") or 0)
        ce_delta = float((ce_data.get("greeks") or {}).get("delta") or 0)
        pe_delta = float((pe_data.get("greeks") or {}).get("delta") or 0)

        is_atm = abs(strike - atm) <= strike_gap / 2
        chain_table.append({
            "strike": strike,
            "is_atm": is_atm,
            "ce_ltp": round(ce_ltp, 2),
            "ce_oi": ce_oi,
            "ce_iv": round(ce_iv, 1),
            "ce_delta": round(ce_delta, 3),
            "ce_symbol": ce_data.get("trading_symbol", ""),
            "pe_ltp": round(pe_ltp, 2),
            "pe_oi": pe_oi,
            "pe_iv": round(pe_iv, 1),
            "pe_delta": round(abs(pe_delta), 3),
            "pe_symbol": pe_data.get("trading_symbol", ""),
        })

        # Score OTM opportunities
        for opt_type, opt_data in [("CE", ce_data), ("PE", pe_data)]:
            scored = _score_otm_strike(strike, ltp, opt_type, opt_data, lot_size, is_expiry)
            if scored:
                all_options.append(scored)

    # Sort by score desc
    all_options.sort(key=lambda x: x["score"], reverse=True)

    # Separate hero-zero from regular
    hero_zero = [o for o in all_options if o["is_hero_zero"]]
    top_otm = [o for o in all_options if not o["is_hero_zero"]][:6]

    # PCR
    total_ce_oi = sum(r["ce_oi"] for r in chain_table)
    total_pe_oi = sum(r["pe_oi"] for r in chain_table)
    pcr = round(total_pe_oi / total_ce_oi, 2) if total_ce_oi else 0

    # Max pain: strike where max OI overlap is greatest loss for option buyers
    max_pain = _calc_max_pain(chain_table)

    return {
        "underlying_ltp": ltp,
        "atm_strike": atm,
        "expiry": expiry_str,
        "is_expiry_day": is_expiry,
        "pcr": pcr,
        "pcr_sentiment": "Bullish" if pcr > 1.2 else "Bearish" if pcr < 0.8 else "Neutral",
        "max_pain": max_pain,
        "chain_table": chain_table,
        "recommended": top_otm,
        "hero_zero": hero_zero,
    }


def _calc_max_pain(chain_table: list[dict]) -> float | None:
    """Max pain = strike where total OI value loss for buyers is maximum."""
    if not chain_table:
        return None
    strikes = [r["strike"] for r in chain_table]
    pain_map = {}
    for s in strikes:
        pain = 0.0
        for r in chain_table:
            k = r["strike"]
            # CE holders lose if spot (=s) < strike
            pain += r["ce_oi"] * max(0, k - s)
            # PE holders lose if spot (=s) > strike
            pain += r["pe_oi"] * max(0, s - k)
        pain_map[s] = pain
    if not pain_map:
        return None
    return min(pain_map, key=lambda x: pain_map[x])


# ── API endpoints ──────────────────────────────────────────────────────────────

@router.get("/chain")
async def get_option_chain(
    symbol: str = Query(...),
    expiry: str = Query(None, description="Expiry date YYYY-MM-DD, omit for nearest"),
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    instr = OPTION_INSTRUMENTS.get(symbol)
    if not instr:
        raise HTTPException(status_code=400, detail=f"Unknown instrument: {symbol}. Available: {list(OPTION_INSTRUMENTS.keys())}")

    groww = await _get_groww(current_user, db)
    loop = asyncio.get_event_loop()

    def _fetch():
        _o, _e = sys.stdout, sys.stderr
        sys.stdout = sys.stderr = io.StringIO()
        try:
            # Get instruments df to find expiries
            df = groww.get_all_instruments()

            # Find expiries
            underlying_sym = instr["underlying"]
            opts = df[
                (df["underlying_symbol"] == underlying_sym) &
                (df["instrument_type"].isin(["CE", "PE"])) &
                (df["segment"] == "FNO")
            ]
            expiries = sorted(opts["expiry_date"].dropna().unique().tolist())
            if not expiries:
                raise ValueError(f"No option expiries found for {symbol}")

            target_expiry = expiry if expiry and expiry in expiries else expiries[0]

            # Get live LTP of underlying
            try:
                from app.api.v1.endpoints.analysis import INSTRUMENTS as ANALYSIS_INSTRUMENTS
                ainstr = ANALYSIS_INSTRUMENTS.get(symbol, {})
                if ainstr:
                    seg = getattr(groww, f"SEGMENT_{ainstr['segment']}", ainstr["segment"])
                    exch = getattr(groww, f"EXCHANGE_{ainstr['exchange']}", ainstr["exchange"])
                    quote = groww.get_quote(
                        trading_symbol=ainstr["trading_symbol"],
                        exchange=exch,
                        segment=seg,
                    )
                    ltp = float(quote.get("last_price") or 0)
                else:
                    ltp = 0
            except Exception:
                ltp = 0

            # Fetch option chain
            chain = groww.get_option_chain(
                exchange=instr["exchange"],
                underlying=underlying_sym,
                expiry_date=target_expiry,
            )

            # If ltp is 0, use underlying_ltp from chain
            if ltp == 0:
                ltp = float(chain.get("underlying_ltp") or 0)

            return chain, ltp, expiries, target_expiry
        finally:
            sys.stdout, sys.stderr = _o, _e

    try:
        chain_data, ltp, expiries, target_expiry = await loop.run_in_executor(None, _fetch)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Options fetch error: {e}")

    result = _process_chain(
        chain_data,
        ltp,
        instr["lot_size"],
        instr["strike_gap"],
        target_expiry,
    )
    result["symbol"] = symbol
    result["expiries"] = expiries[:6]
    result["lot_size"] = instr["lot_size"]

    return result


@router.get("/expiries")
async def get_expiries(
    symbol: str = Query(...),
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    instr = OPTION_INSTRUMENTS.get(symbol)
    if not instr:
        raise HTTPException(status_code=400, detail=f"Unknown instrument: {symbol}")

    groww = await _get_groww(current_user, db)
    loop = asyncio.get_event_loop()

    def _fetch():
        _o, _e = sys.stdout, sys.stderr
        sys.stdout = sys.stderr = io.StringIO()
        try:
            df = groww.get_all_instruments()
            opts = df[
                (df["underlying_symbol"] == instr["underlying"]) &
                (df["instrument_type"].isin(["CE", "PE"])) &
                (df["segment"] == "FNO")
            ]
            return sorted(opts["expiry_date"].dropna().unique().tolist())[:8]
        finally:
            sys.stdout, sys.stderr = _o, _e

    expiries = await loop.run_in_executor(None, _fetch)
    return {"symbol": symbol, "expiries": expiries}
