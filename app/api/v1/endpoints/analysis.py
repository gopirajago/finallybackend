"""Market analysis endpoints – historical candles, live quote, strategy signals."""
import io
import sys
import asyncio
from datetime import datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps import get_current_active_user
from app.core.database import get_db
from app.models.broker_settings import BrokerSettings
from app.models.claude_settings import ClaudeSettings
from app.models.user import User

router = APIRouter()

def _near_month_fut(prefix: str, exchange: str) -> dict:
    """Return the near-month futures trading_symbol based on current month/year."""
    from calendar import month_abbr
    now = datetime.now()
    mon = month_abbr[now.month].upper()  # MAR, APR …
    yr  = str(now.year)[2:]              # 26
    seg = "FNO"
    return {"trading_symbol": f"{prefix}{yr}{mon}FUT", "exchange": exchange, "segment": seg}


def _fetch_candles_paginated(groww, trading_symbol, exchange, segment, start_dt: datetime, end_dt: datetime, interval: int) -> list:
    """Fetch candles in 365-day chunks to stay within Groww API limits."""
    # Max chunk size per interval (days)
    chunk_days = {1: 1, 3: 2, 5: 7, 15: 30, 60: 90, 1440: 365}.get(interval, 1)
    chunk = timedelta(days=chunk_days)

    all_candles: list = []
    cursor = start_dt
    while cursor < end_dt:
        chunk_end = min(cursor + chunk, end_dt)
        _o, _e = sys.stdout, sys.stderr
        sys.stdout = sys.stderr = io.StringIO()
        try:
            result = groww.get_historical_candle_data(
                trading_symbol=trading_symbol,
                exchange=exchange,
                segment=segment,
                start_time=cursor.strftime("%Y-%m-%d %H:%M:%S"),
                end_time=chunk_end.strftime("%Y-%m-%d %H:%M:%S"),
                interval_in_minutes=interval,
            )
        finally:
            sys.stdout, sys.stderr = _o, _e
        if isinstance(result, dict):
            all_candles.extend(result.get("candles", []))
        cursor = chunk_end

    # Deduplicate & sort by timestamp (candles are [ts, o, h, l, c, v])
    seen: set = set()
    unique: list = []
    for c in all_candles:
        key = c[0] if c else None
        if key and key not in seen:
            seen.add(key)
            unique.append(c)
    unique.sort(key=lambda c: c[0])
    return unique


# ── LTP cache: per (user_id, symbol), ttl = 1s ─────────────────────────────
_ltp_cache: dict[str, tuple[float, float]] = {}  # key → (ltp, fetched_at)
_LTP_TTL = 1.0  # seconds


INSTRUMENTS: dict = {}

def _build_instruments():
    global INSTRUMENTS
    INSTRUMENTS = {
        "NIFTY":      _near_month_fut("NIFTY",     "NSE"),
        "BANKNIFTY":  _near_month_fut("BANKNIFTY", "NSE"),
        "SENSEX":     _near_month_fut("SENSEX",    "BSE"),
        "NIFTY IT":   {"trading_symbol": "NIFTYIT", "exchange": "NSE", "segment": "CASH"},
        "RELIANCE":   {"trading_symbol": "RELIANCE",  "exchange": "NSE", "segment": "CASH"},
        "INFY":       {"trading_symbol": "INFY",      "exchange": "NSE", "segment": "CASH"},
        "TCS":        {"trading_symbol": "TCS",       "exchange": "NSE", "segment": "CASH"},
        "HDFCBANK":   {"trading_symbol": "HDFCBANK",  "exchange": "NSE", "segment": "CASH"},
        "ICICIBANK":  {"trading_symbol": "ICICIBANK", "exchange": "NSE", "segment": "CASH"},
        "SBIN":       {"trading_symbol": "SBIN",      "exchange": "NSE", "segment": "CASH"},
    }

_build_instruments()


async def _get_groww(user: User, db: AsyncSession):
    result = await db.execute(
        select(BrokerSettings).where(BrokerSettings.user_id == user.id)
    )
    settings = result.scalar_one_or_none()
    if not settings or not settings.access_token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
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


def _run_sync(fn, *args):
    """Run a sync growwapi call in a thread executor."""
    loop = asyncio.get_event_loop()
    return loop.run_in_executor(None, lambda: fn(*args))


# ── Strategy analysis ──────────────────────────────────────────────────────────

def _ema(closes: list[float], period: int) -> list[float | None]:
    if len(closes) < period:
        return [None] * len(closes)
    result: list[float | None] = [None] * (period - 1)
    k = 2 / (period + 1)
    sma = sum(closes[:period]) / period
    result.append(sma)
    for c in closes[period:]:
        result.append(result[-1] * (1 - k) + c * k)  # type: ignore[operator]
    return result


def _rsi(closes: list[float], period: int = 14) -> list[float | None]:
    if len(closes) < period + 1:
        return [None] * len(closes)
    result: list[float | None] = [None] * period
    gains, losses = [], []
    for i in range(1, period + 1):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    for i in range(period, len(closes) - 1):
        d = closes[i + 1] - closes[i]
        avg_gain = (avg_gain * (period - 1) + max(d, 0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-d, 0)) / period
        rs = avg_gain / avg_loss if avg_loss else float("inf")
        result.append(100 - 100 / (1 + rs))
    result.append(None)
    return result


def _find_sr_levels(candles: list[list], lookback: int = 20) -> list[float]:
    """Simple swing high/low S&R levels."""
    highs = [c[2] for c in candles]
    lows  = [c[3] for c in candles]
    levels = set()
    for i in range(2, len(candles) - 2):
        if highs[i] == max(highs[i-2:i+3]):
            levels.add(round(highs[i], 2))
        if lows[i] == min(lows[i-2:i+3]):
            levels.add(round(lows[i], 2))
    return sorted(levels)


def _find_fvg(candles: list[list]) -> list[dict]:
    """Fair Value Gaps: 3-candle pattern where gap between candle[0] high and candle[2] low (bullish) or vice versa."""
    fvgs = []
    for i in range(2, len(candles)):
        c0, c1, c2 = candles[i-2], candles[i-1], candles[i]
        # Bullish FVG: c0 high < c2 low
        if c0[2] < c2[3]:
            fvgs.append({"type": "bullish", "top": c2[3], "bottom": c0[2], "time": c1[0]})
        # Bearish FVG: c0 low > c2 high
        elif c0[3] > c2[2]:
            fvgs.append({"type": "bearish", "top": c0[3], "bottom": c2[2], "time": c1[0]})
    return fvgs[-5:] if len(fvgs) > 5 else fvgs


def _find_liquidity_sweeps(candles: list[list]) -> list[dict]:
    """Detect liquidity sweeps: price briefly breaks a prior swing high/low then reverses."""
    sweeps = []
    highs = [c[2] for c in candles]
    lows  = [c[3] for c in candles]
    for i in range(5, len(candles)):
        prev_high = max(highs[i-5:i-1])
        prev_low  = min(lows[i-5:i-1])
        c = candles[i]
        close = c[4]
        # Bearish sweep: wick above prior high but closes below it
        if c[2] > prev_high and close < prev_high:
            sweeps.append({"type": "bearish_sweep", "level": round(prev_high, 2), "time": c[0]})
        # Bullish sweep: wick below prior low but closes above it
        elif c[3] < prev_low and close > prev_low:
            sweeps.append({"type": "bullish_sweep", "level": round(prev_low, 2), "time": c[0]})
    return sweeps[-5:] if len(sweeps) > 5 else sweeps


def _analyze_strategies(candles: list[list], current_price: float) -> dict:
    """Run all strategies and return signals with Entry/SL/TP."""
    if len(candles) < 20:
        return {"signals": [], "indicators": {}}

    closes = [c[4] for c in candles]
    highs  = [c[2] for c in candles]
    lows   = [c[3] for c in candles]

    ema9  = _ema(closes, 9)
    ema21 = _ema(closes, 21)
    ema50 = _ema(closes, 50)
    rsi14 = _rsi(closes, 14)
    sr_levels = _find_sr_levels(candles)
    fvgs = _find_fvg(candles)
    sweeps = _find_liquidity_sweeps(candles)

    signals = []
    last = len(closes) - 1
    atr = sum(highs[i] - lows[i] for i in range(max(0, last-14), last+1)) / 14

    # ── EMA crossover ──────────────────────────────────────────────────────
    if ema9[last] and ema21[last] and ema9[last-1] and ema21[last-1]:
        if ema9[last] > ema21[last] and ema9[last-1] <= ema21[last-1]:
            signals.append({
                "strategy": "EMA Crossover",
                "direction": "LONG",
                "strength": "Medium",
                "entry": round(current_price, 2),
                "sl": round(current_price - 2 * atr, 2),
                "tp": round(current_price + 3 * atr, 2),
                "reason": "EMA 9 crossed above EMA 21 — bullish momentum",
            })
        elif ema9[last] < ema21[last] and ema9[last-1] >= ema21[last-1]:
            signals.append({
                "strategy": "EMA Crossover",
                "direction": "SHORT",
                "strength": "Medium",
                "entry": round(current_price, 2),
                "sl": round(current_price + 2 * atr, 2),
                "tp": round(current_price - 3 * atr, 2),
                "reason": "EMA 9 crossed below EMA 21 — bearish momentum",
            })

    # ── RSI extremes ───────────────────────────────────────────────────────
    if rsi14[last] is not None:
        rsi_val = rsi14[last]
        if rsi_val < 30:
            signals.append({
                "strategy": "RSI Oversold",
                "direction": "LONG",
                "strength": "High" if rsi_val < 20 else "Medium",
                "entry": round(current_price, 2),
                "sl": round(lows[last] - atr * 0.5, 2),
                "tp": round(current_price + 2 * atr, 2),
                "reason": f"RSI at {rsi_val:.1f} — oversold reversal expected",
            })
        elif rsi_val > 70:
            signals.append({
                "strategy": "RSI Overbought",
                "direction": "SHORT",
                "strength": "High" if rsi_val > 80 else "Medium",
                "entry": round(current_price, 2),
                "sl": round(highs[last] + atr * 0.5, 2),
                "tp": round(current_price - 2 * atr, 2),
                "reason": f"RSI at {rsi_val:.1f} — overbought reversal expected",
            })

    # ── Support/Resistance bounce ──────────────────────────────────────────
    for lvl in sr_levels:
        dist = abs(current_price - lvl) / current_price
        if dist < 0.003:  # within 0.3%
            direction = "LONG" if current_price > lvl else "SHORT"
            signals.append({
                "strategy": "S/R Level",
                "direction": direction,
                "strength": "High",
                "entry": round(current_price, 2),
                "sl": round(lvl - atr if direction == "LONG" else lvl + atr, 2),
                "tp": round(current_price + 2 * atr if direction == "LONG" else current_price - 2 * atr, 2),
                "reason": f"Price at key {'support' if direction == 'LONG' else 'resistance'} level {lvl}",
            })

    # ── Fair Value Gap fill ────────────────────────────────────────────────
    for fvg in fvgs:
        if fvg["bottom"] <= current_price <= fvg["top"]:
            direction = "LONG" if fvg["type"] == "bullish" else "SHORT"
            signals.append({
                "strategy": "Fair Value Gap",
                "direction": direction,
                "strength": "High",
                "entry": round(current_price, 2),
                "sl": round(fvg["bottom"] - atr * 0.5 if direction == "LONG" else fvg["top"] + atr * 0.5, 2),
                "tp": round(fvg["top"] + atr if direction == "LONG" else fvg["bottom"] - atr, 2),
                "reason": f"Price entering {fvg['type']} FVG zone {fvg['bottom']}–{fvg['top']}",
            })

    # ── Liquidity sweep reversal ───────────────────────────────────────────
    if sweeps:
        latest_sweep = sweeps[-1]
        if latest_sweep["time"] == candles[-1][0] or latest_sweep["time"] == candles[-2][0]:
            direction = "LONG" if latest_sweep["type"] == "bullish_sweep" else "SHORT"
            signals.append({
                "strategy": "Liquidity Sweep",
                "direction": direction,
                "strength": "High",
                "entry": round(current_price, 2),
                "sl": round(latest_sweep["level"] - atr if direction == "LONG" else latest_sweep["level"] + atr, 2),
                "tp": round(current_price + 3 * atr if direction == "LONG" else current_price - 3 * atr, 2),
                "reason": f"{latest_sweep['type'].replace('_', ' ').title()} at {latest_sweep['level']}",
            })

    # ── EMA trend with 50 ─────────────────────────────────────────────────
    if ema50[last] and ema21[last]:
        trend = "bullish" if closes[last] > ema50[last] else "bearish"
        if trend == "bullish" and closes[last] > ema21[last] and closes[last-1] <= ema21[last-1]:
            signals.append({
                "strategy": "EMA Trend Pullback",
                "direction": "LONG",
                "strength": "Medium",
                "entry": round(current_price, 2),
                "sl": round(ema50[last] - atr, 2),
                "tp": round(current_price + 2.5 * atr, 2),
                "reason": "Pullback to EMA 21 in uptrend (price above EMA 50)",
            })
        elif trend == "bearish" and closes[last] < ema21[last] and closes[last-1] >= ema21[last-1]:
            signals.append({
                "strategy": "EMA Trend Pullback",
                "direction": "SHORT",
                "strength": "Medium",
                "entry": round(current_price, 2),
                "sl": round(ema50[last] + atr, 2),
                "tp": round(current_price - 2.5 * atr, 2),
                "reason": "Pullback to EMA 21 in downtrend (price below EMA 50)",
            })

    return {
        "signals": signals,
        "indicators": {
            "ema9":  round(ema9[last], 2)  if ema9[last]  else None,
            "ema21": round(ema21[last], 2) if ema21[last] else None,
            "ema50": round(ema50[last], 2) if ema50[last] else None,
            "rsi14": round(rsi14[last], 2) if rsi14[last] else None,
            "atr14": round(atr, 2),
            "trend": "bullish" if ema50[last] and closes[last] > ema50[last] else "bearish" if ema50[last] else "neutral",
        },
        "sr_levels": sr_levels[-10:],
        "fvgs": fvgs,
        "liquidity_sweeps": sweeps,
    }


# ── API endpoints ──────────────────────────────────────────────────────────────

@router.get("/instruments")
async def list_instruments():
    return list(INSTRUMENTS.keys())


@router.get("/candles")
async def get_candles(
    symbol: str = Query(...),
    interval: int = Query(5, description="Interval in minutes: 1, 3, 5, 15, 60"),
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    instr = INSTRUMENTS.get(symbol)
    if not instr:
        raise HTTPException(status_code=400, detail=f"Unknown instrument: {symbol}")

    groww = await _get_groww(current_user, db)

    now = datetime.now()
    lookback = {
        1:  timedelta(days=1),
        3:  timedelta(days=2),
        5:  timedelta(weeks=1),
        15: timedelta(days=30),
        60: timedelta(days=90),
        1440: timedelta(days=1095),
    }.get(interval, timedelta(days=1))
    start_time = (now - lookback).strftime("%Y-%m-%d %H:%M:%S")
    end_time = now.strftime("%Y-%m-%d %H:%M:%S")

    try:
        def _fetch():
            segment  = getattr(groww, f"SEGMENT_{instr['segment']}", instr["segment"])
            exchange = getattr(groww, f"EXCHANGE_{instr['exchange']}", instr["exchange"])
            return _fetch_candles_paginated(
                groww,
                trading_symbol=instr["trading_symbol"],
                exchange=exchange,
                segment=segment,
                start_dt=now - lookback,
                end_dt=now,
                interval=interval,
            )

        loop = asyncio.get_event_loop()
        candles = await loop.run_in_executor(None, _fetch)
        return {"candles": candles, "interval": interval, "symbol": symbol}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Groww API error: {e}")


@router.get("/quote")
async def get_quote(
    symbol: str = Query(...),
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    import time as _time
    instr = INSTRUMENTS.get(symbol)
    if not instr:
        raise HTTPException(status_code=400, detail=f"Unknown instrument: {symbol}")

    cache_key = f"{current_user.id}:{symbol}"
    now_ts = _time.monotonic()

    # Serve cached value if still fresh
    if cache_key in _ltp_cache:
        cached_ltp, fetched_at = _ltp_cache[cache_key]
        if now_ts - fetched_at < _LTP_TTL:
            return {"symbol": symbol, "ltp": cached_ltp, "timestamp": int(datetime.now().timestamp()), "cached": True}

    groww = await _get_groww(current_user, db)

    try:
        def _fetch():
            _o, _e = sys.stdout, sys.stderr
            sys.stdout = sys.stderr = io.StringIO()
            try:
                segment  = getattr(groww, f"SEGMENT_{instr['segment']}", instr["segment"])
                exchange = getattr(groww, f"EXCHANGE_{instr['exchange']}", instr["exchange"])
                result = groww.get_quote(
                    trading_symbol=instr["trading_symbol"],
                    exchange=exchange,
                    segment=segment,
                )
                return result.get("last_price") if isinstance(result, dict) else None
            finally:
                sys.stdout, sys.stderr = _o, _e

        loop = asyncio.get_event_loop()
        ltp = await loop.run_in_executor(None, _fetch)
        if ltp is not None:
            _ltp_cache[cache_key] = (ltp, _time.monotonic())
        return {"symbol": symbol, "ltp": ltp, "timestamp": int(datetime.now().timestamp()), "cached": False}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Groww API error: {e}")


@router.get("/signals")
async def get_signals(
    symbol: str = Query(...),
    interval: int = Query(5),
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Fetch candles + live price, run all strategy analyses, return signals."""
    instr = INSTRUMENTS.get(symbol)
    if not instr:
        raise HTTPException(status_code=400, detail=f"Unknown instrument: {symbol}")

    groww = await _get_groww(current_user, db)

    now = datetime.now()
    # Interval → lookback window
    lookback = {
        1:  timedelta(days=1),
        3:  timedelta(days=2),
        5:  timedelta(weeks=1),
        15: timedelta(days=30),
        60: timedelta(days=90),
        1440: timedelta(days=1095),
    }.get(interval, timedelta(days=1))
    start_time = (now - lookback).strftime("%Y-%m-%d %H:%M:%S")
    end_time = now.strftime("%Y-%m-%d %H:%M:%S")

    try:
        def _fetch_all():
            segment  = getattr(groww, f"SEGMENT_{instr['segment']}", instr["segment"])
            exchange = getattr(groww, f"EXCHANGE_{instr['exchange']}", instr["exchange"])
            candles_result = _fetch_candles_paginated(
                groww,
                trading_symbol=instr["trading_symbol"],
                exchange=exchange,
                segment=segment,
                start_dt=now - lookback,
                end_dt=now,
                interval=interval,
            )
            _o, _e = sys.stdout, sys.stderr
            sys.stdout = sys.stderr = io.StringIO()
            try:
                quote = groww.get_quote(
                    trading_symbol=instr["trading_symbol"],
                    exchange=exchange,
                    segment=segment,
                )
            finally:
                sys.stdout, sys.stderr = _o, _e
            ltp_val = quote.get("last_price") if isinstance(quote, dict) else None
            return candles_result, ltp_val

        loop = asyncio.get_event_loop()
        candles, ltp_data = await loop.run_in_executor(None, _fetch_all)

        candles = candles if isinstance(candles, list) else []
        ltp = float(ltp_data) if ltp_data else (candles[-1][4] if candles else 0)

        analysis = _analyze_strategies(candles, ltp)
        return {
            "symbol": symbol,
            "interval": interval,
            "ltp": ltp,
            "candle_count": len(candles),
            "analysis": analysis,
            "timestamp": int(datetime.now().timestamp()),
        }
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Analysis error: {e}")


# ── News sentiment helper ──────────────────────────────────────────────────────

async def _fetch_market_news(symbol: str) -> list[dict]:
    """Fetch recent market news from GNews free API (no key needed for basic)."""
    import urllib.request
    import json as _json

    query = symbol.replace(" ", "+") + "+stock+market+India"
    url = f"https://gnews.io/api/v4/search?q={query}&lang=en&country=in&max=5&apikey=free"
    # Fallback to NewsData.io free tier
    newsdata_url = f"https://newsdata.io/api/1/news?q={symbol}+NSE+market&language=en&country=in"

    headlines = []
    # Try a simple free RSS/scrape approach via Google News RSS (no key needed)
    try:
        rss_url = f"https://news.google.com/rss/search?q={query}+when:1d&hl=en-IN&gl=IN&ceid=IN:en"
        req = urllib.request.Request(rss_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            content = resp.read().decode("utf-8")
        import re
        titles = re.findall(r"<title><!\[CDATA\[(.*?)\]\]></title>", content)
        headlines = [t for t in titles if "Google" not in t][:8]
    except Exception:
        pass

    return [{"title": h} for h in headlines]


def _build_claude_prompt(
    symbol: str,
    interval: int,
    ltp: float,
    candles: list,
    analysis: dict,
    news: list[dict],
) -> str:
    indicators = analysis.get("indicators", {})
    signals = analysis.get("signals", [])
    sr_levels = analysis.get("sr_levels", [])
    fvgs = analysis.get("fvgs", [])
    sweeps = analysis.get("liquidity_sweeps", [])

    # Summarise last 10 candles
    recent = candles[-10:] if len(candles) >= 10 else candles
    candle_summary = "\n".join(
        f"  [{datetime.fromtimestamp(c[0]).strftime('%d-%b %H:%M')}] O:{c[1]} H:{c[2]} L:{c[3]} C:{c[4]} V:{c[5]}"
        for c in recent
    )

    # News headlines
    news_text = "\n".join(f"  - {n['title']}" for n in news) if news else "  No recent news available."

    # Technical signals
    sig_text = "\n".join(
        f"  [{s['direction']}] {s['strategy']}: Entry={s['entry']}, SL={s['sl']}, TP={s['tp']} — {s['reason']}"
        for s in signals
    ) if signals else "  No technical signals triggered."

    interval_label = {1:"1-min",3:"3-min",5:"5-min",15:"15-min",60:"1-hour",1440:"Daily"}.get(interval,f"{interval}-min")

    return f"""You are an expert Indian stock market trader and analyst specialising in intraday and swing trading.

Analyse the following real-time market data for **{symbol}** ({interval_label} candles) and provide a precise trade recommendation.

## Current Market Data
- **Symbol**: {symbol}
- **Timeframe**: {interval_label}
- **Last Traded Price**: ₹{ltp:,.2f}
- **Trend**: {indicators.get('trend','unknown').upper()}

## Technical Indicators
- EMA 9:  {indicators.get('ema9') or 'N/A'}
- EMA 21: {indicators.get('ema21') or 'N/A'}
- EMA 50: {indicators.get('ema50') or 'N/A'}
- RSI 14: {indicators.get('rsi14') or 'N/A'}
- ATR 14: {indicators.get('atr14') or 'N/A'}

## Recent Candles (last 10)
{candle_summary}

## Support & Resistance Levels
{', '.join(f'₹{l}' for l in sr_levels[-6:]) if sr_levels else 'N/A'}

## Fair Value Gaps
{chr(10).join(f"  {f['type'].upper()} FVG: ₹{f['bottom']}–₹{f['top']}" for f in fvgs) if fvgs else '  None detected'}

## Liquidity Sweeps
{chr(10).join(f"  {s['type'].replace('_',' ').title()}: ₹{s['level']}" for s in sweeps) if sweeps else '  None detected'}

## Rule-Based Technical Signals
{sig_text}

## Recent Market News & Sentiment
{news_text}

---

Based on ALL of the above — technical structure, price action, indicators, fair value gaps, liquidity sweeps, support/resistance, AND current market news sentiment — provide your trade recommendation.

Respond ONLY in this exact JSON format (no markdown, no extra text):
{{
  "direction": "LONG" or "SHORT" or "NEUTRAL",
  "confidence": "High" or "Medium" or "Low",
  "entry": <number>,
  "sl": <number>,
  "tp": <number>,
  "rr_ratio": <number>,
  "reasoning": "<2-3 sentence explanation combining technicals + news sentiment>",
  "sentiment": "Bullish" or "Bearish" or "Neutral",
  "key_factors": ["<factor1>", "<factor2>", "<factor3>"]
}}"""


@router.post("/ai-signal")
async def get_ai_signal(
    symbol: str = Query(...),
    interval: int = Query(5),
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Use Claude AI + market news to generate an intelligent trade signal."""

    # Load Claude settings
    claude_result = await db.execute(
        select(ClaudeSettings).where(ClaudeSettings.user_id == current_user.id)
    )
    claude_settings = claude_result.scalar_one_or_none()
    if not claude_settings or not claude_settings.api_key:
        raise HTTPException(
            status_code=400,
            detail="No Claude API key configured. Add it in Settings → Claude.",
        )

    instr = INSTRUMENTS.get(symbol)
    if not instr:
        raise HTTPException(status_code=400, detail=f"Unknown instrument: {symbol}")

    groww = await _get_groww(current_user, db)

    now = datetime.now()
    lookback = {
        1: timedelta(days=1), 3: timedelta(days=2), 5: timedelta(weeks=1),
        15: timedelta(days=30), 60: timedelta(days=90), 1440: timedelta(days=1095),
    }.get(interval, timedelta(days=1))
    start_time = (now - lookback).strftime("%Y-%m-%d %H:%M:%S")
    end_time = now.strftime("%Y-%m-%d %H:%M:%S")

    # Fetch market data + news concurrently
    loop = asyncio.get_event_loop()

    def _fetch_market():
        segment  = getattr(groww, f"SEGMENT_{instr['segment']}", instr["segment"])
        exchange = getattr(groww, f"EXCHANGE_{instr['exchange']}", instr["exchange"])
        candles_result = _fetch_candles_paginated(
            groww,
            trading_symbol=instr["trading_symbol"],
            exchange=exchange,
            segment=segment,
            start_dt=now - lookback,
            end_dt=now,
            interval=interval,
        )
        _o, _e = sys.stdout, sys.stderr
        sys.stdout = sys.stderr = io.StringIO()
        try:
            quote = groww.get_quote(
                trading_symbol=instr["trading_symbol"],
                exchange=exchange,
                segment=segment,
            )
        finally:
            sys.stdout, sys.stderr = _o, _e
        ltp_val = quote.get("last_price") if isinstance(quote, dict) else None
        return {"candles": candles_result}, ltp_val, quote

    hist_data, ltp_data, quote_data = await loop.run_in_executor(None, _fetch_market)
    news = await _fetch_market_news(symbol)

    candles = hist_data.get("candles", []) if isinstance(hist_data, dict) else []
    ltp = float(ltp_data) if ltp_data else (candles[-1][4] if candles else 0)
    analysis = _analyze_strategies(candles, ltp)

    # Add quote metadata to analysis
    if isinstance(quote_data, dict):
        analysis["quote"] = {
            "day_change": quote_data.get("day_change"),
            "day_change_perc": quote_data.get("day_change_perc"),
            "open": quote_data.get("ohlc", {}).get("open"),
            "high": quote_data.get("ohlc", {}).get("high"),
            "low": quote_data.get("ohlc", {}).get("low"),
            "volume": quote_data.get("volume"),
        }

    prompt = _build_claude_prompt(symbol, interval, ltp, candles, analysis, news)

    # Call Claude
    def _call_claude():
        import anthropic  # type: ignore[import]
        client = anthropic.Anthropic(api_key=claude_settings.api_key)
        msg = client.messages.create(
            model=claude_settings.model,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        return msg.content[0].text

    try:
        raw = await loop.run_in_executor(None, _call_claude)
        import json as _json
        # Strip markdown code fences if present
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        ai_signal = _json.loads(raw.strip())
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Claude AI error: {e}")

    return {
        "symbol": symbol,
        "interval": interval,
        "ltp": ltp,
        "candle_count": len(candles),
        "analysis": analysis,
        "ai_signal": ai_signal,
        "news": news,
        "timestamp": int(datetime.now().timestamp()),
    }
