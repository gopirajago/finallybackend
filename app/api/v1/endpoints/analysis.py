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


def _bollinger_bands(closes: list[float], period: int = 20, std_dev: float = 2.0):
    """Returns (upper, mid, lower) lists."""
    upper, mid, lower = [], [], []
    for i in range(len(closes)):
        if i < period - 1:
            upper.append(None); mid.append(None); lower.append(None)
        else:
            window = closes[i - period + 1: i + 1]
            m = sum(window) / period
            sd = (sum((x - m) ** 2 for x in window) / period) ** 0.5
            upper.append(round(m + std_dev * sd, 2))
            mid.append(round(m, 2))
            lower.append(round(m - std_dev * sd, 2))
    return upper, mid, lower


def _macd(closes: list[float], fast: int = 12, slow: int = 26, signal: int = 9):
    """Returns (macd_line, signal_line, histogram) lists."""
    ema_fast = _ema(closes, fast)
    ema_slow = _ema(closes, slow)
    macd_line = [
        round(f - s, 4) if f is not None and s is not None else None
        for f, s in zip(ema_fast, ema_slow)
    ]
    valid_macd = [v for v in macd_line if v is not None]
    if len(valid_macd) < signal:
        sig_line = [None] * len(macd_line)
    else:
        sig_raw = _ema(valid_macd, signal)
        # Pad signal line to align with macd_line
        offset = len(macd_line) - len(valid_macd)
        sig_line = [None] * offset + sig_raw
    histogram = [
        round(m - s, 4) if m is not None and s is not None else None
        for m, s in zip(macd_line, sig_line)
    ]
    return macd_line, sig_line, histogram


def _stochastic_rsi(closes: list[float], rsi_period: int = 14, stoch_period: int = 14):
    """Returns (stoch_k, stoch_d) 0-100 smoothed lists."""
    rsi = _rsi(closes, rsi_period)
    k_line, d_line = [], []
    for i in range(len(rsi)):
        window = [r for r in rsi[max(0, i - stoch_period + 1): i + 1] if r is not None]
        if len(window) < stoch_period or rsi[i] is None:
            k_line.append(None)
        else:
            lo, hi = min(window), max(window)
            k_line.append(round(100 * (rsi[i] - lo) / (hi - lo), 2) if hi != lo else 50.0)
    # Smooth %D as 3-period SMA of %K
    for i in range(len(k_line)):
        vals = [k_line[j] for j in range(max(0, i-2), i+1) if k_line[j] is not None]
        d_line.append(round(sum(vals) / len(vals), 2) if vals else None)
    return k_line, d_line


def _vwap(candles: list[list]) -> list[float | None]:
    """Intraday VWAP — resets each session."""
    result = []
    cum_pv = cum_v = 0.0
    for c in candles:
        t, o, h, l, cl, v = c[0], c[1], c[2], c[3], c[4], c[5] if len(c) > 5 else 0
        typ = (h + l + cl) / 3
        cum_pv += typ * v
        cum_v  += v
        result.append(round(cum_pv / cum_v, 2) if cum_v else None)
    return result


def _supertrend(candles: list[list], period: int = 10, multiplier: float = 3.0):
    """Returns (supertrend_line, direction) where direction 1=bullish, -1=bearish."""
    if len(candles) < period:
        return [None]*len(candles), [0]*len(candles)
    highs  = [c[2] for c in candles]
    lows   = [c[3] for c in candles]
    closes = [c[4] for c in candles]
    # ATR
    tr = [highs[0] - lows[0]]
    for i in range(1, len(candles)):
        tr.append(max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1])))
    atr_vals = [None] * (period - 1)
    atr_sum = sum(tr[:period])
    atr_vals.append(atr_sum / period)
    for i in range(period, len(tr)):
        atr_vals.append((atr_vals[-1] * (period - 1) + tr[i]) / period)  # type: ignore[operator]

    st_line = [None] * len(candles)
    direction = [0] * len(candles)
    upper_band = [None] * len(candles)
    lower_band = [None] * len(candles)

    for i in range(period - 1, len(candles)):
        mid = (highs[i] + lows[i]) / 2
        upper_band[i] = round(mid + multiplier * atr_vals[i], 2)  # type: ignore[operator]
        lower_band[i] = round(mid - multiplier * atr_vals[i], 2)  # type: ignore[operator]

    prev_st = lower_band[period - 1]
    prev_dir = 1
    for i in range(period - 1, len(candles)):
        if upper_band[i] is None:
            continue
        if prev_dir == 1:
            curr_st = max(lower_band[i], prev_st) if prev_st else lower_band[i]  # type: ignore[operator]
            if closes[i] < curr_st:  # type: ignore[operator]
                curr_dir = -1
                curr_st = upper_band[i]
            else:
                curr_dir = 1
        else:
            curr_st = min(upper_band[i], prev_st) if prev_st else upper_band[i]  # type: ignore[operator]
            if closes[i] > curr_st:  # type: ignore[operator]
                curr_dir = 1
                curr_st = lower_band[i]
            else:
                curr_dir = -1
        st_line[i] = curr_st
        direction[i] = curr_dir
        prev_st = curr_st
        prev_dir = curr_dir
    return st_line, direction


def _candlestick_patterns(candles: list[list]) -> list[dict]:
    """Detect high-probability candlestick patterns."""
    patterns = []
    if len(candles) < 3:
        return patterns

    for i in range(2, len(candles)):
        c0, c1, c2 = candles[i-2], candles[i-1], candles[i]
        o0,h0,l0,cl0 = c0[1],c0[2],c0[3],c0[4]
        o1,h1,l1,cl1 = c1[1],c1[2],c1[3],c1[4]
        o2,h2,l2,cl2 = c2[1],c2[2],c2[3],c2[4]
        body0 = abs(cl0 - o0); body1 = abs(cl1 - o1); body2 = abs(cl2 - o2)
        range1 = h1 - l1 if h1 != l1 else 0.0001
        upper_wick1 = h1 - max(o1, cl1)
        lower_wick1 = min(o1, cl1) - l1

        # Bullish Engulfing
        if cl0 < o0 and cl2 > o2 and o2 <= cl0 and cl2 >= o0:
            patterns.append({"type": "Bullish Engulfing", "direction": "LONG", "strength": "High", "candle_idx": i})

        # Bearish Engulfing
        if cl0 > o0 and cl2 < o2 and o2 >= cl0 and cl2 <= o0:
            patterns.append({"type": "Bearish Engulfing", "direction": "SHORT", "strength": "High", "candle_idx": i})

        # Hammer (bullish reversal after downtrend)
        if (lower_wick1 >= 2 * body1 and upper_wick1 <= 0.1 * range1
                and body1 > 0 and cl1 > o1):
            patterns.append({"type": "Hammer", "direction": "LONG", "strength": "Medium", "candle_idx": i-1})

        # Shooting Star (bearish reversal after uptrend)
        if (upper_wick1 >= 2 * body1 and lower_wick1 <= 0.1 * range1
                and body1 > 0 and cl1 < o1):
            patterns.append({"type": "Shooting Star", "direction": "SHORT", "strength": "Medium", "candle_idx": i-1})

        # Doji (indecision — direction from context)
        if body1 <= 0.05 * range1 and range1 > 0:
            patterns.append({"type": "Doji", "direction": "NEUTRAL", "strength": "Low", "candle_idx": i-1})

        # Morning Star (3-candle bullish reversal)
        if (cl0 < o0 and body1 <= 0.3 * body0  # small middle candle
                and cl2 > o2 and cl2 > (o0 + cl0) / 2):
            patterns.append({"type": "Morning Star", "direction": "LONG", "strength": "High", "candle_idx": i})

        # Evening Star (3-candle bearish reversal)
        if (cl0 > o0 and body1 <= 0.3 * body0
                and cl2 < o2 and cl2 < (o0 + cl0) / 2):
            patterns.append({"type": "Evening Star", "direction": "SHORT", "strength": "High", "candle_idx": i})

        # Bullish Pin Bar (long lower wick)
        if (lower_wick1 >= 2.5 * body1 and lower_wick1 >= 0.6 * range1):
            patterns.append({"type": "Bullish Pin Bar", "direction": "LONG", "strength": "High", "candle_idx": i-1})

        # Bearish Pin Bar (long upper wick)
        if (upper_wick1 >= 2.5 * body1 and upper_wick1 >= 0.6 * range1):
            patterns.append({"type": "Bearish Pin Bar", "direction": "SHORT", "strength": "High", "candle_idx": i-1})

    # Return last 10 unique patterns
    seen = set()
    unique = []
    for p in reversed(patterns):
        key = (p["type"], p["candle_idx"])
        if key not in seen:
            seen.add(key)
            unique.append(p)
        if len(unique) == 10:
            break
    return list(reversed(unique))


def _find_order_blocks(candles: list[list]) -> list[dict]:
    """Order blocks: last strong impulsive candle before a BOS move."""
    blocks = []
    if len(candles) < 5:
        return blocks
    closes = [c[4] for c in candles]
    opens  = [c[1] for c in candles]
    highs  = [c[2] for c in candles]
    lows   = [c[3] for c in candles]

    for i in range(3, len(candles) - 2):
        body = abs(closes[i] - opens[i])
        rng  = highs[i] - lows[i]
        if rng == 0:
            continue
        # Strong bullish candle followed by move up — bullish OB
        if closes[i] > opens[i] and body / rng > 0.6:
            # Subsequent candle breaks above this candle's high
            if highs[i+1] > highs[i] and closes[i+1] > highs[i]:
                blocks.append({"type": "bullish", "top": highs[i], "bottom": lows[i], "time": candles[i][0]})
        # Strong bearish candle followed by move down — bearish OB
        elif closes[i] < opens[i] and body / rng > 0.6:
            if lows[i+1] < lows[i] and closes[i+1] < lows[i]:
                blocks.append({"type": "bearish", "top": highs[i], "bottom": lows[i], "time": candles[i][0]})

    return blocks[-6:] if len(blocks) > 6 else blocks


def _find_bos_choch(candles: list[list]) -> list[dict]:
    """Break of Structure (BOS) and Change of Character (ChoCh) detection."""
    events = []
    if len(candles) < 10:
        return events
    highs  = [c[2] for c in candles]
    lows   = [c[3] for c in candles]
    closes = [c[4] for c in candles]

    # Track swing highs and lows
    swing_highs = []
    swing_lows  = []
    for i in range(2, len(candles) - 2):
        if highs[i] == max(highs[i-2:i+3]):
            swing_highs.append((i, highs[i]))
        if lows[i] == min(lows[i-2:i+3]):
            swing_lows.append((i, lows[i]))

    # BOS Long: close breaks above last swing high
    if swing_highs:
        last_sh_idx, last_sh_val = swing_highs[-1]
        for i in range(last_sh_idx + 1, len(candles)):
            if closes[i] > last_sh_val:
                events.append({"type": "BOS", "direction": "LONG", "level": round(last_sh_val, 2), "candle_idx": i})
                break

    # BOS Short: close breaks below last swing low
    if swing_lows:
        last_sl_idx, last_sl_val = swing_lows[-1]
        for i in range(last_sl_idx + 1, len(candles)):
            if closes[i] < last_sl_val:
                events.append({"type": "BOS", "direction": "SHORT", "level": round(last_sl_val, 2), "candle_idx": i})
                break

    # ChoCh: after a BOS long, first lower low = change of character bearish
    if len(swing_lows) >= 2:
        last_sl = swing_lows[-1][1]
        prev_sl = swing_lows[-2][1]
        if last_sl < prev_sl:  # Lower low = potential ChoCh bearish
            events.append({"type": "ChoCh", "direction": "SHORT", "level": round(last_sl, 2), "candle_idx": swing_lows[-1][0]})
        elif last_sl > prev_sl:  # Higher low = potential ChoCh bullish
            events.append({"type": "ChoCh", "direction": "LONG", "level": round(last_sl, 2), "candle_idx": swing_lows[-1][0]})

    return events[-4:]


def _analyze_strategies(candles: list[list], current_price: float) -> dict:
    """Run all strategies and return signals with Entry/SL/TP + confluence scoring."""
    if len(candles) < 20:
        return {"signals": [], "indicators": {}, "patterns": [], "order_blocks": [], "bos_choch": []}

    closes = [c[4] for c in candles]
    opens  = [c[1] for c in candles]
    highs  = [c[2] for c in candles]
    lows   = [c[3] for c in candles]
    vols   = [c[5] if len(c) > 5 else 0 for c in candles]

    # ── Indicators ────────────────────────────────────────────────────────
    ema9   = _ema(closes, 9)
    ema21  = _ema(closes, 21)
    ema50  = _ema(closes, 50)
    ema200 = _ema(closes, 200)
    rsi14  = _rsi(closes, 14)
    bb_up, bb_mid, bb_lo = _bollinger_bands(closes, 20, 2.0)
    macd_line, sig_line, histogram = _macd(closes)
    stoch_k, stoch_d = _stochastic_rsi(closes)
    vwap_line = _vwap(candles)
    st_line, st_dir = _supertrend(candles, 10, 3.0)

    # Structural helpers
    sr_levels  = _find_sr_levels(candles)
    fvgs       = _find_fvg(candles)
    sweeps     = _find_liquidity_sweeps(candles)
    patterns   = _candlestick_patterns(candles)
    ob_blocks  = _find_order_blocks(candles)
    bos_choch  = _find_bos_choch(candles)

    last = len(closes) - 1
    prev = last - 1
    atr  = sum(highs[i] - lows[i] for i in range(max(0, last-14), last+1)) / 14
    avg_vol = sum(vols[max(0, last-20):last]) / 20 if vols[last] else 1
    vol_spike = vols[last] > 1.5 * avg_vol if avg_vol > 0 else False

    trend_bias = "bullish" if (ema50[last] and closes[last] > ema50[last]) else "bearish" if ema50[last] else "neutral"

    signals = []

    def _sig(strategy, direction, entry, sl, tp, reason, strength="Medium", confluence=0):
        rr = abs(tp - entry) / abs(entry - sl) if abs(entry - sl) > 0 else 1.0
        # Boost strength based on confluence count
        if confluence >= 3:
            strength = "High"
        elif confluence == 2 and strength == "Medium":
            strength = "High"
        return {
            "strategy": strategy,
            "direction": direction,
            "strength": strength,
            "entry": round(entry, 2),
            "sl": round(sl, 2),
            "tp": round(tp, 2),
            "reason": reason,
            "rr": round(rr, 2),
            "confluence": confluence,
        }

    # ── 1. EMA Crossover 9/21 ──────────────────────────────────────────────
    if all(x is not None for x in [ema9[last], ema21[last], ema9[prev], ema21[prev]]):
        if ema9[last] > ema21[last] and ema9[prev] <= ema21[prev]:
            c = 1 + (1 if trend_bias == "bullish" else 0) + (1 if vol_spike else 0)
            signals.append(_sig("EMA 9/21 Crossover", "LONG",
                current_price, current_price - 2*atr, current_price + 3*atr,
                f"EMA9 crossed above EMA21 · RSI {rsi14[last]:.0f}" if rsi14[last] else "EMA9 crossed above EMA21",
                confluence=c))
        elif ema9[last] < ema21[last] and ema9[prev] >= ema21[prev]:
            c = 1 + (1 if trend_bias == "bearish" else 0) + (1 if vol_spike else 0)
            signals.append(_sig("EMA 9/21 Crossover", "SHORT",
                current_price, current_price + 2*atr, current_price - 3*atr,
                f"EMA9 crossed below EMA21 · RSI {rsi14[last]:.0f}" if rsi14[last] else "EMA9 crossed below EMA21",
                confluence=c))

    # ── 2. EMA 21/50 Crossover ────────────────────────────────────────────
    if all(x is not None for x in [ema21[last], ema50[last], ema21[prev], ema50[prev]]):
        if ema21[last] > ema50[last] and ema21[prev] <= ema50[prev]:
            signals.append(_sig("EMA 21/50 Cross", "LONG",
                current_price, current_price - 2.5*atr, current_price + 4*atr,
                "EMA21 crossed above EMA50 — medium-term trend shift bullish",
                strength="High", confluence=2))
        elif ema21[last] < ema50[last] and ema21[prev] >= ema50[prev]:
            signals.append(_sig("EMA 21/50 Cross", "SHORT",
                current_price, current_price + 2.5*atr, current_price - 4*atr,
                "EMA21 crossed below EMA50 — medium-term trend shift bearish",
                strength="High", confluence=2))

    # ── 3. MACD Signal Cross ──────────────────────────────────────────────
    if all(x is not None for x in [macd_line[last], sig_line[last], macd_line[prev], sig_line[prev]]):
        if macd_line[last] > sig_line[last] and macd_line[prev] <= sig_line[prev]:
            c = 1 + (1 if trend_bias == "bullish" else 0) + (1 if histogram[last] and histogram[last] > 0 else 0)
            signals.append(_sig("MACD Cross", "LONG",
                current_price, current_price - 2*atr, current_price + 3*atr,
                f"MACD line crossed above signal · histogram {histogram[last]:.1f}" if histogram[last] else "MACD bullish cross",
                confluence=c))
        elif macd_line[last] < sig_line[last] and macd_line[prev] >= sig_line[prev]:
            c = 1 + (1 if trend_bias == "bearish" else 0) + (1 if histogram[last] and histogram[last] < 0 else 0)
            signals.append(_sig("MACD Cross", "SHORT",
                current_price, current_price + 2*atr, current_price - 3*atr,
                f"MACD line crossed below signal · histogram {histogram[last]:.1f}" if histogram[last] else "MACD bearish cross",
                confluence=c))

    # ── 4. RSI Extremes with divergence ───────────────────────────────────
    if rsi14[last] is not None:
        r = rsi14[last]
        if r < 30:
            c = 1 + (1 if stoch_k[last] and stoch_k[last] < 20 else 0) + (1 if vol_spike else 0)
            signals.append(_sig("RSI Oversold", "LONG",
                current_price, lows[last] - atr*0.5, current_price + 2.5*atr,
                f"RSI={r:.0f} oversold · StochRSI={stoch_k[last]:.0f}" if stoch_k[last] else f"RSI={r:.0f} — oversold",
                strength="High" if r < 20 else "Medium", confluence=c))
        elif r > 70:
            c = 1 + (1 if stoch_k[last] and stoch_k[last] > 80 else 0) + (1 if vol_spike else 0)
            signals.append(_sig("RSI Overbought", "SHORT",
                current_price, highs[last] + atr*0.5, current_price - 2.5*atr,
                f"RSI={r:.0f} overbought · StochRSI={stoch_k[last]:.0f}" if stoch_k[last] else f"RSI={r:.0f} — overbought",
                strength="High" if r > 80 else "Medium", confluence=c))

    # ── 5. Stochastic RSI Crossover ───────────────────────────────────────
    if all(x is not None for x in [stoch_k[last], stoch_d[last], stoch_k[prev], stoch_d[prev]]):
        if stoch_k[last] > stoch_d[last] and stoch_k[prev] <= stoch_d[prev] and stoch_k[last] < 30:
            signals.append(_sig("StochRSI Cross", "LONG",
                current_price, current_price - 1.5*atr, current_price + 2.5*atr,
                f"StochRSI %K crossed above %D in oversold zone ({stoch_k[last]:.0f})",
                confluence=2))
        elif stoch_k[last] < stoch_d[last] and stoch_k[prev] >= stoch_d[prev] and stoch_k[last] > 70:
            signals.append(_sig("StochRSI Cross", "SHORT",
                current_price, current_price + 1.5*atr, current_price - 2.5*atr,
                f"StochRSI %K crossed below %D in overbought zone ({stoch_k[last]:.0f})",
                confluence=2))

    # ── 6. Bollinger Band Squeeze / Breakout ──────────────────────────────
    if bb_up[last] and bb_lo[last] and bb_mid[last]:
        band_width = (bb_up[last] - bb_lo[last]) / bb_mid[last]
        # Price touches lower band — bullish bounce
        if current_price <= bb_lo[last] * 1.002:
            c = 1 + (1 if rsi14[last] and rsi14[last] < 40 else 0) + (1 if trend_bias == "bullish" else 0)
            signals.append(_sig("BB Lower Band Bounce", "LONG",
                current_price, bb_lo[last] - atr*0.5, bb_mid[last],
                f"Price at lower Bollinger Band ({bb_lo[last]}) — mean reversion LONG",
                confluence=c))
        # Price touches upper band — bearish rejection
        elif current_price >= bb_up[last] * 0.998:
            c = 1 + (1 if rsi14[last] and rsi14[last] > 60 else 0) + (1 if trend_bias == "bearish" else 0)
            signals.append(_sig("BB Upper Band Rejection", "SHORT",
                current_price, bb_up[last] + atr*0.5, bb_mid[last],
                f"Price at upper Bollinger Band ({bb_up[last]}) — mean reversion SHORT",
                confluence=c))
        # BB Squeeze breakout
        if band_width < 0.02 and vol_spike:
            direction = "LONG" if closes[last] > bb_mid[last] else "SHORT"
            signals.append(_sig("BB Squeeze Breakout", direction,
                current_price,
                current_price - atr if direction == "LONG" else current_price + atr,
                current_price + 2.5*atr if direction == "LONG" else current_price - 2.5*atr,
                f"Bollinger squeeze breakout with volume spike — {direction}",
                strength="High", confluence=3))

    # ── 7. VWAP Cross ─────────────────────────────────────────────────────
    if vwap_line[last] and vwap_line[prev]:
        if closes[last] > vwap_line[last] and closes[prev] <= vwap_line[prev]:
            c = 1 + (1 if trend_bias == "bullish" else 0) + (1 if vol_spike else 0)
            signals.append(_sig("VWAP Cross", "LONG",
                current_price, vwap_line[last] - atr*0.5, current_price + 2*atr,
                f"Price crossed above VWAP ({vwap_line[last]}) — institutional bias LONG",
                confluence=c))
        elif closes[last] < vwap_line[last] and closes[prev] >= vwap_line[prev]:
            c = 1 + (1 if trend_bias == "bearish" else 0) + (1 if vol_spike else 0)
            signals.append(_sig("VWAP Cross", "SHORT",
                current_price, vwap_line[last] + atr*0.5, current_price - 2*atr,
                f"Price crossed below VWAP ({vwap_line[last]}) — institutional bias SHORT",
                confluence=c))

    # ── 8. Supertrend flip ────────────────────────────────────────────────
    if st_dir[last] != 0 and st_dir[prev] != 0 and st_dir[last] != st_dir[prev]:
        direction = "LONG" if st_dir[last] == 1 else "SHORT"
        c = 2 + (1 if trend_bias == ("bullish" if direction == "LONG" else "bearish") else 0)
        signals.append(_sig("Supertrend Flip", direction,
            current_price,
            st_line[last] - atr if direction == "LONG" else st_line[last] + atr,  # type: ignore[operator]
            current_price + 3*atr if direction == "LONG" else current_price - 3*atr,
            f"Supertrend flipped {direction} at {st_line[last]} — strong trend change",
            strength="High", confluence=c))

    # ── 9. S/R Level Bounce ───────────────────────────────────────────────
    for lvl in sr_levels:
        dist = abs(current_price - lvl) / current_price
        if dist < 0.003:
            direction = "LONG" if current_price >= lvl else "SHORT"
            c = 1 + (1 if vwap_line[last] and abs(vwap_line[last] - lvl) / lvl < 0.003 else 0) + (1 if vol_spike else 0)
            signals.append(_sig("S/R Bounce", direction,
                current_price,
                lvl - atr if direction == "LONG" else lvl + atr,
                current_price + 2*atr if direction == "LONG" else current_price - 2*atr,
                f"Price at key {'support' if direction == 'LONG' else 'resistance'} {round(lvl,0)}",
                confluence=c))

    # ── 10. Fair Value Gap Entry ──────────────────────────────────────────
    for fvg in fvgs:
        if fvg["bottom"] <= current_price <= fvg["top"]:
            direction = "LONG" if fvg["type"] == "bullish" else "SHORT"
            c = 2 + (1 if trend_bias == ("bullish" if direction == "LONG" else "bearish") else 0)
            signals.append(_sig("Fair Value Gap", direction,
                current_price,
                fvg["bottom"] - atr*0.5 if direction == "LONG" else fvg["top"] + atr*0.5,
                fvg["top"] + atr if direction == "LONG" else fvg["bottom"] - atr,
                f"Price in {fvg['type']} FVG {round(fvg['bottom'],0)}–{round(fvg['top'],0)}",
                strength="High", confluence=c))

    # ── 11. Liquidity Sweep Reversal ──────────────────────────────────────
    if sweeps:
        latest = sweeps[-1]
        if latest["time"] in (candles[-1][0], candles[-2][0] if len(candles) > 1 else 0):
            direction = "LONG" if latest["type"] == "bullish_sweep" else "SHORT"
            c = 2 + (1 if vol_spike else 0)
            signals.append(_sig("Liquidity Sweep", direction,
                current_price,
                latest["level"] - atr if direction == "LONG" else latest["level"] + atr,
                current_price + 3*atr if direction == "LONG" else current_price - 3*atr,
                f"{latest['type'].replace('_',' ').title()} at {latest['level']}",
                strength="High", confluence=c))

    # ── 12. Order Block Entry ─────────────────────────────────────────────
    for ob in ob_blocks:
        if ob["bottom"] <= current_price <= ob["top"]:
            direction = "LONG" if ob["type"] == "bullish" else "SHORT"
            c = 2 + (1 if trend_bias == ("bullish" if direction == "LONG" else "bearish") else 0) + (1 if vol_spike else 0)
            signals.append(_sig("Order Block", direction,
                current_price,
                ob["bottom"] - atr*0.3 if direction == "LONG" else ob["top"] + atr*0.3,
                ob["top"] + 2*atr if direction == "LONG" else ob["bottom"] - 2*atr,
                f"Price in {ob['type']} order block {round(ob['bottom'],0)}–{round(ob['top'],0)}",
                strength="High", confluence=c))

    # ── 13. BOS / ChoCh Signal ───────────────────────────────────────────
    for event in bos_choch:
        if event["candle_idx"] >= last - 2:
            direction = event["direction"]
            label = event["type"]
            c = 2 + (1 if trend_bias == ("bullish" if direction == "LONG" else "bearish") else 0)
            signals.append(_sig(f"{label} Breakout", direction,
                current_price,
                current_price - 2*atr if direction == "LONG" else current_price + 2*atr,
                current_price + 3.5*atr if direction == "LONG" else current_price - 3.5*atr,
                f"{label} at {event['level']} — {'bullish' if direction == 'LONG' else 'bearish'} structure break",
                strength="High", confluence=c))

    # ── 14. EMA Trend Pullback ────────────────────────────────────────────
    if ema50[last] and ema21[last]:
        if trend_bias == "bullish" and closes[last] > ema21[last] and closes[prev] <= ema21[prev]:
            c = 1 + (1 if rsi14[last] and 40 < rsi14[last] < 60 else 0) + (1 if vol_spike else 0)
            signals.append(_sig("EMA Pullback", "LONG",
                current_price, ema50[last] - atr, current_price + 2.5*atr,
                f"Pullback to EMA21 in uptrend (above EMA50={round(ema50[last],0)})",
                confluence=c))
        elif trend_bias == "bearish" and closes[last] < ema21[last] and closes[prev] >= ema21[prev]:
            c = 1 + (1 if rsi14[last] and 40 < rsi14[last] < 60 else 0) + (1 if vol_spike else 0)
            signals.append(_sig("EMA Pullback", "SHORT",
                current_price, ema50[last] + atr, current_price - 2.5*atr,
                f"Pullback to EMA21 in downtrend (below EMA50={round(ema50[last],0)})",
                confluence=c))

    # ── 15. Candlestick Pattern Signals ──────────────────────────────────
    recent_patterns = [p for p in patterns if p["candle_idx"] >= last - 1]
    for pat in recent_patterns:
        if pat["direction"] in ("LONG", "SHORT"):
            direction = pat["direction"]
            c = 1 + (1 if trend_bias == ("bullish" if direction == "LONG" else "bearish") else 0) + (1 if vol_spike else 0)
            signals.append(_sig(pat["type"], direction,
                current_price,
                lows[last] - atr*0.5 if direction == "LONG" else highs[last] + atr*0.5,
                current_price + 2*atr if direction == "LONG" else current_price - 2*atr,
                f"{pat['type']} pattern — {direction.lower()} reversal signal",
                strength=pat["strength"], confluence=c))

    # ── 16. Volume Spike + Trend Continuation ────────────────────────────
    if vol_spike and ema21[last]:
        body = abs(closes[last] - opens[last])
        rng  = highs[last] - lows[last]
        if rng > 0 and body / rng > 0.6:
            direction = "LONG" if closes[last] > opens[last] else "SHORT"
            if (direction == "LONG" and trend_bias == "bullish") or (direction == "SHORT" and trend_bias == "bearish"):
                signals.append(_sig("Volume Spike Continuation", direction,
                    current_price,
                    lows[last] - atr*0.3 if direction == "LONG" else highs[last] + atr*0.3,
                    current_price + 2.5*atr if direction == "LONG" else current_price - 2.5*atr,
                    f"Strong {direction.lower()} candle with volume spike ({int(vols[last]/avg_vol*100)}% of avg)",
                    strength="High", confluence=3))

    # ── 17. Trend Following (ADX-proxy: consistent EMA slope) ────────────
    # Strong trend = EMA9 > EMA21 > EMA50 (bull) or EMA9 < EMA21 < EMA50 (bear)
    # with RSI in momentum zone (45-70 bull, 30-55 bear) — ride the trend
    if ema9[last] and ema21[last] and ema50[last] and rsi14[last]:
        rsi_val = rsi14[last]
        bull_stack = ema9[last] > ema21[last] > ema50[last]
        bear_stack = ema9[last] < ema21[last] < ema50[last]
        # Measure slope: EMA21 rising/falling over last 5 bars
        slope_period = min(5, last)
        ema21_slope = (ema21[last] - ema21[last - slope_period]) / slope_period if ema21[last - slope_period] else 0
        slope_pct = abs(ema21_slope) / current_price * 100

        if bull_stack and 45 <= rsi_val <= 75 and slope_pct > 0.01:
            c = 2 + (1 if vol_spike else 0) + (1 if st_dir[last] == 1 else 0)
            signals.append(_sig("Trend Following", "LONG",
                current_price,
                ema21[last] - atr * 0.5,
                current_price + 3 * atr,
                f"Bull stack EMA9>21>50 · RSI={rsi_val:.0f} momentum · slope {slope_pct:.3f}%/bar",
                strength="High", confluence=c))
        elif bear_stack and 25 <= rsi_val <= 55 and slope_pct > 0.01:
            c = 2 + (1 if vol_spike else 0) + (1 if st_dir[last] == -1 else 0)
            signals.append(_sig("Trend Following", "SHORT",
                current_price,
                ema21[last] + atr * 0.5,
                current_price - 3 * atr,
                f"Bear stack EMA9<21<50 · RSI={rsi_val:.0f} momentum · slope {slope_pct:.3f}%/bar",
                strength="High", confluence=c))

    # ── 18. Range / Sideways Mean Reversion ──────────────────────────────
    # Detect sideways: EMA21 slope near flat + BB narrow width
    # Buy at lower BB + RSI < 40; Sell at upper BB + RSI > 60
    if bb_up[last] and bb_lo[last] and bb_mid[last] and ema21[last]:
        band_width = (bb_up[last] - bb_lo[last]) / bb_mid[last]
        slope_5 = abs(ema21[last] - ema21[last - 5]) / current_price * 100 if last >= 5 and ema21[last-5] else 1.0
        is_ranging = band_width < 0.04 and slope_5 < 0.05  # flat EMA + narrow bands

        if is_ranging and rsi14[last]:
            rsi_val = rsi14[last]
            if current_price < bb_mid[last] and rsi_val < 45:
                # Price in lower half of range — LONG back to midline
                c = 2 + (1 if stoch_k[last] and stoch_k[last] < 30 else 0) + (1 if rsi_val < 35 else 0)
                signals.append(_sig("Range Mean Reversion", "LONG",
                    current_price,
                    bb_lo[last] - atr * 0.3,
                    bb_mid[last],
                    f"Ranging market (BB width {band_width:.2%}) · price below midline · RSI={rsi_val:.0f}",
                    strength="High", confluence=c))
            elif current_price > bb_mid[last] and rsi_val > 55:
                # Price in upper half of range — SHORT back to midline
                c = 2 + (1 if stoch_k[last] and stoch_k[last] > 70 else 0) + (1 if rsi_val > 65 else 0)
                signals.append(_sig("Range Mean Reversion", "SHORT",
                    current_price,
                    bb_up[last] + atr * 0.3,
                    bb_mid[last],
                    f"Ranging market (BB width {band_width:.2%}) · price above midline · RSI={rsi_val:.0f}",
                    strength="High", confluence=c))

    # ── 19. Opening Range Breakout (ORB) ─────────────────────────────────
    # First 15 minutes of market (9:15–9:30 IST) form the Opening Range.
    # Break above ORH → LONG; break below ORL → SHORT with volume confirmation.
    # ORB is most relevant for 1m, 3m, 5m intervals.
    if interval <= 15 and len(candles) >= 3:
        IST_OFFSET_S = 19800  # 5h30m
        MARKET_OPEN_S = 9 * 3600 + 15 * 60  # 09:15 IST = 33300s into day
        ORB_END_S    = 9 * 3600 + 30 * 60   # 09:30 IST = 34200s

        # Collect candles in opening range window (09:15–09:30 IST)
        orb_candles = []
        for c_raw in candles:
            ts_ist = c_raw[0] + IST_OFFSET_S
            secs_in_day = ts_ist % 86400
            if MARKET_OPEN_S <= secs_in_day < ORB_END_S:
                orb_candles.append(c_raw)

        if orb_candles:
            orb_high = max(c[2] for c in orb_candles)
            orb_low  = min(c[3] for c in orb_candles)
            orb_range = orb_high - orb_low

            # Current close breaks out of ORB with volume
            if closes[last] > orb_high and vol_spike:
                c = 2 + (1 if trend_bias == "bullish" else 0) + (1 if vwap_line[last] and current_price > vwap_line[last] else 0)
                signals.append(_sig("ORB Breakout", "LONG",
                    current_price,
                    orb_high - orb_range * 0.3,          # SL just below ORH
                    current_price + orb_range * 2,        # TP = 2× ORB range
                    f"Opening Range Breakout above {orb_high:.0f} · ORB range={orb_range:.0f} pts",
                    strength="High", confluence=c))
            elif closes[last] < orb_low and vol_spike:
                c = 2 + (1 if trend_bias == "bearish" else 0) + (1 if vwap_line[last] and current_price < vwap_line[last] else 0)
                signals.append(_sig("ORB Breakout", "SHORT",
                    current_price,
                    orb_low + orb_range * 0.3,            # SL just above ORL
                    current_price - orb_range * 2,        # TP = 2× ORB range
                    f"Opening Range Breakdown below {orb_low:.0f} · ORB range={orb_range:.0f} pts",
                    strength="High", confluence=c))
            # ORB Fade: price rejected at ORH/ORL without breakout
            elif abs(current_price - orb_high) / current_price < 0.001 and rsi14[last] and rsi14[last] > 65:
                signals.append(_sig("ORB Fade", "SHORT",
                    current_price,
                    orb_high + atr * 0.3,
                    orb_low,
                    f"ORB fade — price rejected at ORH {orb_high:.0f} · RSI={rsi14[last]:.0f}",
                    confluence=2))
            elif abs(current_price - orb_low) / current_price < 0.001 and rsi14[last] and rsi14[last] < 35:
                signals.append(_sig("ORB Fade", "LONG",
                    current_price,
                    orb_low - atr * 0.3,
                    orb_high,
                    f"ORB fade — price supported at ORL {orb_low:.0f} · RSI={rsi14[last]:.0f}",
                    confluence=2))

    # ── 20. VWAP Strategy Suite ───────────────────────────────────────────
    # Four VWAP sub-setups:
    #  A. VWAP Reclaim (price dips below VWAP, reclaims it) → LONG
    #  B. VWAP Rejection (price spikes above VWAP, fails) → SHORT
    #  C. VWAP Trend Continuation (price bounces off VWAP in direction of trend) → trend direction
    #  D. VWAP + S/R Confluence (VWAP near a key S/R level)
    if vwap_line[last] and vwap_line[prev]:
        vwap_now  = vwap_line[last]
        vwap_prev = vwap_line[prev]

        # A. VWAP Reclaim: prev close below VWAP, current close above VWAP with volume
        if closes[prev] < vwap_prev and closes[last] > vwap_now and vol_spike:
            c = 3 + (1 if trend_bias == "bullish" else 0)
            signals.append(_sig("VWAP Reclaim", "LONG",
                current_price,
                vwap_now - atr * 0.5,
                current_price + 2.5 * atr,
                f"Price reclaimed VWAP ({vwap_now:.0f}) with volume — institutional buyers active",
                strength="High", confluence=c))

        # B. VWAP Rejection: prev close above VWAP, current close below VWAP with volume
        elif closes[prev] > vwap_prev and closes[last] < vwap_now and vol_spike:
            c = 3 + (1 if trend_bias == "bearish" else 0)
            signals.append(_sig("VWAP Rejection", "SHORT",
                current_price,
                vwap_now + atr * 0.5,
                current_price - 2.5 * atr,
                f"Price rejected below VWAP ({vwap_now:.0f}) with volume — institutional sellers active",
                strength="High", confluence=c))

        # C. VWAP Trend Bounce: price pulls back to VWAP and bounces in trend direction
        vwap_dist_pct = abs(current_price - vwap_now) / current_price * 100
        if vwap_dist_pct < 0.15:  # within 0.15% of VWAP
            if trend_bias == "bullish" and closes[last] >= vwap_now and closes[prev] < vwap_prev:
                c = 2 + (1 if st_dir[last] == 1 else 0) + (1 if rsi14[last] and 40 < rsi14[last] < 65 else 0)
                signals.append(_sig("VWAP Trend Bounce", "LONG",
                    current_price,
                    vwap_now - atr * 0.4,
                    current_price + 2 * atr,
                    f"Bullish bounce off VWAP ({vwap_now:.0f}) in uptrend — high-probability long",
                    confluence=c))
            elif trend_bias == "bearish" and closes[last] <= vwap_now and closes[prev] > vwap_prev:
                c = 2 + (1 if st_dir[last] == -1 else 0) + (1 if rsi14[last] and 35 < rsi14[last] < 60 else 0)
                signals.append(_sig("VWAP Trend Bounce", "SHORT",
                    current_price,
                    vwap_now + atr * 0.4,
                    current_price - 2 * atr,
                    f"Bearish bounce off VWAP ({vwap_now:.0f}) in downtrend — high-probability short",
                    confluence=c))

        # D. VWAP + S/R Confluence: VWAP sits on top of a key S/R level
        for lvl in sr_levels:
            vwap_sr_dist = abs(vwap_now - lvl) / lvl * 100
            price_sr_dist = abs(current_price - lvl) / current_price * 100
            if vwap_sr_dist < 0.2 and price_sr_dist < 0.3:
                direction = "LONG" if current_price >= lvl else "SHORT"
                signals.append(_sig("VWAP+S/R Confluence", direction,
                    current_price,
                    lvl - atr if direction == "LONG" else lvl + atr,
                    current_price + 2.5 * atr if direction == "LONG" else current_price - 2.5 * atr,
                    f"VWAP {vwap_now:.0f} confluent with S/R {lvl:.0f} — double magnet level",
                    strength="High", confluence=3))
                break  # only the nearest confluence

    # ── De-duplicate: keep highest-confluence signal per strategy ─────────
    seen: dict = {}
    for s in signals:
        key = s["strategy"]
        if key not in seen or s["confluence"] > seen[key]["confluence"]:
            seen[key] = s
    signals = sorted(seen.values(), key=lambda x: (-x["confluence"], x["strategy"]))

    # Mark top-3 as "High" strength
    for i, s in enumerate(signals):
        if i < 3 and s["confluence"] >= 2:
            s["strength"] = "High"

    ema200_val = ema200[last] if ema200[last] else None
    vwap_val   = vwap_line[last]
    bb_pct     = round((current_price - bb_lo[last]) / (bb_up[last] - bb_lo[last]) * 100, 1) if bb_up[last] and bb_lo[last] and bb_up[last] != bb_lo[last] else None

    return {
        "signals": signals,
        "indicators": {
            "ema9":    round(ema9[last], 2)   if ema9[last]   else None,
            "ema21":   round(ema21[last], 2)  if ema21[last]  else None,
            "ema50":   round(ema50[last], 2)  if ema50[last]  else None,
            "ema200":  round(ema200_val, 2)   if ema200_val   else None,
            "rsi14":   round(rsi14[last], 2)  if rsi14[last]  else None,
            "atr14":   round(atr, 2),
            "vwap":    vwap_val,
            "bb_upper": bb_up[last],
            "bb_lower": bb_lo[last],
            "bb_pct":  bb_pct,
            "macd":    round(macd_line[last], 2) if macd_line[last] else None,
            "macd_signal": round(sig_line[last], 2) if sig_line[last] else None,
            "macd_hist":   round(histogram[last], 2) if histogram[last] else None,
            "stoch_k": stoch_k[last],
            "stoch_d": stoch_d[last],
            "supertrend": st_line[last],
            "supertrend_dir": st_dir[last],
            "vol_spike": vol_spike,
            "trend": trend_bias,
        },
        "sr_levels": sr_levels[-10:],
        "fvgs": fvgs,
        "liquidity_sweeps": sweeps,
        "patterns": patterns[-5:],
        "order_blocks": ob_blocks,
        "bos_choch": bos_choch,
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

        # Compute overlay data for chart drawing
        closes = [c[4] for c in candles]
        ema9_vals  = _ema(closes, 9)
        ema21_vals = _ema(closes, 21)
        ema50_vals = _ema(closes, 50)
        vwap_vals  = _vwap(candles)
        bb_up, bb_mid, bb_lo = _bollinger_bands(closes, 20)
        st_line, st_dir = _supertrend(candles, 10, 3.0)
        IST = 5.5 * 3600

        def _overlay(vals):
            return [
                {"time": int(candles[i][0] + IST), "value": round(v, 2)}
                for i, v in enumerate(vals) if v is not None
            ]

        return {
            "candles": candles,
            "interval": interval,
            "symbol": symbol,
            "overlays": {
                "ema9":       _overlay(ema9_vals),
                "ema21":      _overlay(ema21_vals),
                "ema50":      _overlay(ema50_vals),
                "vwap":       _overlay(vwap_vals),
                "bb_upper":   _overlay(bb_up),
                "bb_lower":   _overlay(bb_lo),
                "bb_mid":     _overlay(bb_mid),
                "supertrend": [
                    {"time": int(candles[i][0] + IST), "value": round(st_line[i], 2), "dir": st_dir[i]}
                    for i in range(len(candles)) if st_line[i] is not None
                ],
            },
        }
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
