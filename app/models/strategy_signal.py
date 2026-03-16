from sqlalchemy import Column, Integer, String, Float, DateTime, Boolean, ForeignKey, JSON
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.core.database import Base


class StrategySignal(Base):
    """Model for storing options trading strategy signals"""
    __tablename__ = "strategy_signals"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    
    # Strategy info
    strategy_name = Column(String, default="Skew Hunter", nullable=False)
    symbol = Column(String, default="NIFTY", nullable=False)
    
    # Signal data
    signal_type = Column(String, nullable=False)  # "LONG_CALL" or "LONG_PUT"
    alpha1 = Column(Float, nullable=False)  # Volume ratio + OI change alpha
    alpha2 = Column(Float, nullable=False)  # IV skew alpha
    
    # Option details
    strike_price = Column(Float, nullable=False)
    option_type = Column(String, nullable=False)  # "CE" or "PE"
    expiry_date = Column(String, nullable=False)
    
    # Market data
    atm_strike = Column(Float, nullable=False)
    spot_price = Column(Float, nullable=False)
    option_price = Column(Float, nullable=False)
    
    # Metrics
    otm_call_volume_ratio = Column(Float)
    itm_put_volume_ratio = Column(Float)
    otm_call_oi_change = Column(Float)
    itm_put_oi_change = Column(Float)
    
    otm_call_iv = Column(Float)
    itm_call_iv = Column(Float)
    otm_put_iv = Column(Float)
    itm_put_iv = Column(Float)
    
    # Signal metadata
    signal_strength = Column(Float)  # Combined alpha score
    is_active = Column(Boolean, default=True)
    is_traded = Column(Boolean, default=False)
    
    # Timestamps
    signal_time = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    
    # Relationships
    user = relationship("User", back_populates="strategy_signals")
    trades = relationship("StrategyTrade", back_populates="signal")


class StrategyTrade(Base):
    """Model for storing executed strategy trades"""
    __tablename__ = "strategy_trades"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    signal_id = Column(Integer, ForeignKey("strategy_signals.id"), nullable=True)
    
    # Trade info
    strategy_name = Column(String, default="Skew Hunter", nullable=False)
    strategy_version = Column(String, default="regular")  # "regular" or "tsl"
    symbol = Column(String, default="NIFTY", nullable=False)
    
    # Position details
    trade_type = Column(String, nullable=False)  # "LONG_CALL" or "LONG_PUT"
    strike_price = Column(Float, nullable=False)
    option_type = Column(String, nullable=False)  # "CE" or "PE"
    expiry_date = Column(String, nullable=False)
    quantity = Column(Integer, nullable=False)
    
    # Entry
    entry_price = Column(Float, nullable=False)
    entry_time = Column(DateTime(timezone=True), nullable=False)
    entry_alpha1 = Column(Float)
    entry_alpha2 = Column(Float)
    
    # Exit
    exit_price = Column(Float)
    exit_time = Column(DateTime(timezone=True))
    exit_reason = Column(String)  # "STOP_LOSS", "EOD_SQUAREOFF", "TARGET", "MANUAL"
    
    # Risk management
    stop_loss_price = Column(Float, nullable=False)  # 40% below entry
    trailing_stop_price = Column(Float)  # For TSL version
    highest_price = Column(Float)  # Track highest for TSL
    
    # P&L
    pnl = Column(Float)
    pnl_percent = Column(Float)
    
    # Status
    status = Column(String, default="OPEN")  # "OPEN", "CLOSED"
    is_active = Column(Boolean, default=True)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    
    # Relationships
    user = relationship("User", back_populates="strategy_trades")
    signal = relationship("StrategySignal", back_populates="trades")


class StrategyConfig(Base):
    """Model for storing strategy configuration"""
    __tablename__ = "strategy_configs"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, unique=True)
    
    # Strategy settings
    strategy_name = Column(String, default="Skew Hunter", nullable=False)
    is_enabled = Column(Boolean, default=False)
    version = Column(String, default="regular")  # "regular" or "tsl"
    symbols = Column(JSON, default=["NIFTY", "SENSEX"])  # List of symbols to monitor
    
    # Trading hours
    start_time = Column(String, default="10:15")  # HH:MM format
    end_time = Column(String, default="14:15")  # HH:MM format
    
    # Signal thresholds
    alpha1_long_call_threshold = Column(Float, default=0.75)
    alpha2_long_call_threshold = Column(Float, default=0.8)
    alpha1_long_put_threshold = Column(Float, default=0.25)
    alpha2_long_put_threshold = Column(Float, default=0.2)
    
    # Risk management
    min_option_price = Column(Float, default=20.0)
    stop_loss_percent = Column(Float, default=40.0)
    trailing_stop_percent = Column(Float, default=30.0)  # For TSL version
    
    # Position sizing
    default_quantity = Column(Integer, default=1)
    max_positions = Column(Integer, default=1)  # Prevent multiple active trades
    
    # Notifications
    send_signal_alerts = Column(Boolean, default=True)
    send_trade_alerts = Column(Boolean, default=True)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    
    # Relationships
    user = relationship("User", back_populates="strategy_config")
