"""
API endpoints for Skew Hunter options trading strategy
"""

from datetime import datetime, timezone
from typing import Annotated, List, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, desc

from app.core.database import get_db
from app.api.v1.deps import get_current_active_user
from app.models.user import User
from app.models.strategy_signal import StrategySignal, StrategyTrade, StrategyConfig
from app.services.skew_hunter import SkewHunterStrategy
from pydantic import BaseModel, Field


router = APIRouter()


# ── Schemas ────────────────────────────────────────────────────────────────


class StrategyConfigCreate(BaseModel):
    is_enabled: bool = False
    version: str = "regular"  # "regular" or "tsl"
    symbols: list[str] = ["NIFTY", "SENSEX"]
    start_time: str = "10:15"
    end_time: str = "14:15"
    alpha1_long_call_threshold: float = 0.75
    alpha2_long_call_threshold: float = 0.8
    alpha1_long_put_threshold: float = 0.25
    alpha2_long_put_threshold: float = 0.2
    min_option_price: float = 20.0
    stop_loss_percent: float = 40.0
    trailing_stop_percent: float = 30.0
    default_quantity: int = 1
    max_positions: int = 1
    send_signal_alerts: bool = True
    send_trade_alerts: bool = True


class StrategyConfigResponse(BaseModel):
    id: int
    user_id: int
    strategy_name: str
    is_enabled: bool
    version: str
    symbols: list[str]
    start_time: str
    end_time: str
    alpha1_long_call_threshold: float
    alpha2_long_call_threshold: float
    alpha1_long_put_threshold: float
    alpha2_long_put_threshold: float
    min_option_price: float
    stop_loss_percent: float
    trailing_stop_percent: float
    default_quantity: int
    max_positions: int
    send_signal_alerts: bool
    send_trade_alerts: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class OptionData(BaseModel):
    strike: float
    volume: float
    oi: float
    oi_change: float
    iv: float
    ltp: float


class GenerateSignalRequest(BaseModel):
    spot_price: float
    atm_strike: float
    otm_call: OptionData
    itm_call: OptionData
    otm_put: OptionData
    itm_put: OptionData
    expiry_date: str


class SignalResponse(BaseModel):
    id: int
    signal_type: str
    alpha1: float
    alpha2: float
    signal_strength: float
    strike_price: float
    option_type: str
    option_price: float
    spot_price: float
    atm_strike: float
    expiry_date: str
    is_active: bool
    is_traded: bool
    signal_time: datetime

    class Config:
        from_attributes = True


class TradeResponse(BaseModel):
    id: int
    strategy_name: str
    strategy_version: str
    trade_type: str
    strike_price: float
    option_type: str
    expiry_date: str
    quantity: int
    entry_price: float
    entry_time: datetime
    exit_price: Optional[float]
    exit_time: Optional[datetime]
    exit_reason: Optional[str]
    stop_loss_price: float
    trailing_stop_price: Optional[float]
    pnl: Optional[float]
    pnl_percent: Optional[float]
    status: str
    created_at: datetime

    class Config:
        from_attributes = True


# ── Endpoints ──────────────────────────────────────────────────────────────


@router.get("/config", response_model=StrategyConfigResponse)
async def get_strategy_config(
    current_user: Annotated[User, Depends(get_current_active_user)],
    db: AsyncSession = Depends(get_db),
):
    """Get user's strategy configuration"""
    result = await db.execute(
        select(StrategyConfig).where(StrategyConfig.user_id == current_user.id)
    )
    config = result.scalar_one_or_none()
    
    if not config:
        # Create default config
        config = StrategyConfig(
            user_id=current_user.id,
            strategy_name="Skew Hunter",
            is_enabled=False,
            symbols=["NIFTY", "SENSEX"],
        )
        db.add(config)
        await db.commit()
        await db.refresh(config)
    
    return config


@router.post("/config", response_model=StrategyConfigResponse)
async def update_strategy_config(
    config_data: StrategyConfigCreate,
    current_user: Annotated[User, Depends(get_current_active_user)],
    db: AsyncSession = Depends(get_db),
):
    """Update strategy configuration"""
    result = await db.execute(
        select(StrategyConfig).where(StrategyConfig.user_id == current_user.id)
    )
    config = result.scalar_one_or_none()
    
    if not config:
        config = StrategyConfig(
            user_id=current_user.id,
            strategy_name="Skew Hunter",
            symbols=["NIFTY", "SENSEX"]
        )
        db.add(config)
    
    # Update fields
    for field, value in config_data.model_dump().items():
        setattr(config, field, value)
    
    await db.commit()
    await db.refresh(config)
    
    return config


@router.post("/signals/generate", response_model=SignalResponse)
async def generate_signal(
    request: GenerateSignalRequest,
    current_user: Annotated[User, Depends(get_current_active_user)],
    db: AsyncSession = Depends(get_db),
):
    """Generate trading signal based on options data"""
    # Get user config
    result = await db.execute(
        select(StrategyConfig).where(StrategyConfig.user_id == current_user.id)
    )
    config = result.scalar_one_or_none()
    
    if not config or not config.is_enabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Strategy is not enabled"
        )
    
    # Initialize strategy
    strategy = SkewHunterStrategy({
        'alpha1_long_call_threshold': config.alpha1_long_call_threshold,
        'alpha2_long_call_threshold': config.alpha2_long_call_threshold,
        'alpha1_long_put_threshold': config.alpha1_long_put_threshold,
        'alpha2_long_put_threshold': config.alpha2_long_put_threshold,
        'min_option_price': config.min_option_price,
        'stop_loss_percent': config.stop_loss_percent,
    })
    
    # Prepare options data
    options_data = {
        'otm_call': request.otm_call.model_dump(),
        'itm_call': request.itm_call.model_dump(),
        'otm_put': request.otm_put.model_dump(),
        'itm_put': request.itm_put.model_dump(),
    }
    
    # Generate signal
    signal_data = strategy.generate_signal(
        spot_price=request.spot_price,
        atm_strike=request.atm_strike,
        options_data=options_data
    )
    
    if not signal_data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No trading signal generated"
        )
    
    # Save signal to database
    signal = StrategySignal(
        user_id=current_user.id,
        strategy_name="Skew Hunter",
        symbol="NIFTY",
        signal_type=signal_data['signal_type'],
        alpha1=signal_data['alpha1'],
        alpha2=signal_data['alpha2'],
        signal_strength=signal_data['signal_strength'],
        strike_price=signal_data['strike_price'],
        option_type=signal_data['option_type'],
        expiry_date=request.expiry_date,
        atm_strike=request.atm_strike,
        spot_price=request.spot_price,
        option_price=signal_data['option_price'],
        **{f"{k}": v for k, v in signal_data['metrics'].items()}
    )
    
    db.add(signal)
    await db.commit()
    await db.refresh(signal)
    
    return signal


@router.get("/signals", response_model=List[SignalResponse])
async def get_signals(
    current_user: Annotated[User, Depends(get_current_active_user)],
    db: AsyncSession = Depends(get_db),
    limit: int = 50,
    active_only: bool = False,
):
    """Get trading signals"""
    query = select(StrategySignal).where(StrategySignal.user_id == current_user.id)
    
    if active_only:
        query = query.where(StrategySignal.is_active == True)
    
    query = query.order_by(desc(StrategySignal.signal_time)).limit(limit)
    
    result = await db.execute(query)
    signals = result.scalars().all()
    
    return signals


@router.get("/trades", response_model=List[TradeResponse])
async def get_trades(
    current_user: Annotated[User, Depends(get_current_active_user)],
    db: AsyncSession = Depends(get_db),
    limit: int = 50,
    active_only: bool = False,
):
    """Get strategy trades"""
    query = select(StrategyTrade).where(StrategyTrade.user_id == current_user.id)
    
    if active_only:
        query = query.where(StrategyTrade.status == "OPEN")
    
    query = query.order_by(desc(StrategyTrade.entry_time)).limit(limit)
    
    result = await db.execute(query)
    trades = result.scalars().all()
    
    return trades


@router.get("/trades/active-count")
async def get_active_trades_count(
    current_user: Annotated[User, Depends(get_current_active_user)],
    db: AsyncSession = Depends(get_db),
):
    """Get count of active trades"""
    result = await db.execute(
        select(StrategyTrade).where(
            and_(
                StrategyTrade.user_id == current_user.id,
                StrategyTrade.status == "OPEN"
            )
        )
    )
    trades = result.scalars().all()
    
    return {"active_count": len(trades)}


@router.get("/stats")
async def get_strategy_stats(
    current_user: Annotated[User, Depends(get_current_active_user)],
    db: AsyncSession = Depends(get_db),
):
    """Get strategy statistics"""
    # Get all closed trades
    result = await db.execute(
        select(StrategyTrade).where(
            and_(
                StrategyTrade.user_id == current_user.id,
                StrategyTrade.status == "CLOSED"
            )
        )
    )
    closed_trades = result.scalars().all()
    
    if not closed_trades:
        return {
            "total_trades": 0,
            "winning_trades": 0,
            "losing_trades": 0,
            "win_rate": 0,
            "total_pnl": 0,
            "avg_pnl": 0,
            "max_profit": 0,
            "max_loss": 0,
        }
    
    winning_trades = [t for t in closed_trades if t.pnl and t.pnl > 0]
    losing_trades = [t for t in closed_trades if t.pnl and t.pnl <= 0]
    
    total_pnl = sum(t.pnl for t in closed_trades if t.pnl)
    
    return {
        "total_trades": len(closed_trades),
        "winning_trades": len(winning_trades),
        "losing_trades": len(losing_trades),
        "win_rate": len(winning_trades) / len(closed_trades) * 100 if closed_trades else 0,
        "total_pnl": total_pnl,
        "avg_pnl": total_pnl / len(closed_trades) if closed_trades else 0,
        "max_profit": max((t.pnl for t in closed_trades if t.pnl), default=0),
        "max_loss": min((t.pnl for t in closed_trades if t.pnl), default=0),
    }
