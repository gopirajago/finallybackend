-- Create strategy tables for Skew Hunter

-- Strategy Signals Table
CREATE TABLE IF NOT EXISTS strategy_signals (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    strategy_name VARCHAR DEFAULT 'Skew Hunter' NOT NULL,
    symbol VARCHAR DEFAULT 'NIFTY' NOT NULL,
    signal_type VARCHAR NOT NULL,
    alpha1 FLOAT NOT NULL,
    alpha2 FLOAT NOT NULL,
    strike_price FLOAT NOT NULL,
    option_type VARCHAR NOT NULL,
    expiry_date VARCHAR NOT NULL,
    atm_strike FLOAT NOT NULL,
    spot_price FLOAT NOT NULL,
    option_price FLOAT NOT NULL,
    otm_call_volume_ratio FLOAT,
    itm_put_volume_ratio FLOAT,
    otm_call_oi_change FLOAT,
    itm_put_oi_change FLOAT,
    otm_call_iv FLOAT,
    itm_call_iv FLOAT,
    otm_put_iv FLOAT,
    itm_put_iv FLOAT,
    signal_strength FLOAT,
    is_active BOOLEAN DEFAULT TRUE,
    is_traded BOOLEAN DEFAULT FALSE,
    signal_time TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Strategy Trades Table
CREATE TABLE IF NOT EXISTS strategy_trades (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    signal_id INTEGER REFERENCES strategy_signals(id),
    strategy_name VARCHAR DEFAULT 'Skew Hunter' NOT NULL,
    strategy_version VARCHAR DEFAULT 'regular',
    symbol VARCHAR DEFAULT 'NIFTY' NOT NULL,
    trade_type VARCHAR NOT NULL,
    strike_price FLOAT NOT NULL,
    option_type VARCHAR NOT NULL,
    expiry_date VARCHAR NOT NULL,
    quantity INTEGER NOT NULL,
    entry_price FLOAT NOT NULL,
    entry_time TIMESTAMP WITH TIME ZONE NOT NULL,
    entry_alpha1 FLOAT,
    entry_alpha2 FLOAT,
    exit_price FLOAT,
    exit_time TIMESTAMP WITH TIME ZONE,
    exit_reason VARCHAR,
    stop_loss_price FLOAT NOT NULL,
    trailing_stop_price FLOAT,
    highest_price FLOAT,
    pnl FLOAT,
    pnl_percent FLOAT,
    status VARCHAR DEFAULT 'OPEN',
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Strategy Config Table
CREATE TABLE IF NOT EXISTS strategy_configs (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL UNIQUE REFERENCES users(id),
    strategy_name VARCHAR DEFAULT 'Skew Hunter' NOT NULL,
    is_enabled BOOLEAN DEFAULT FALSE,
    version VARCHAR DEFAULT 'regular',
    symbols JSON DEFAULT '["NIFTY", "SENSEX"]',
    start_time VARCHAR DEFAULT '10:15',
    end_time VARCHAR DEFAULT '14:15',
    alpha1_long_call_threshold FLOAT DEFAULT 0.75,
    alpha2_long_call_threshold FLOAT DEFAULT 0.8,
    alpha1_long_put_threshold FLOAT DEFAULT 0.25,
    alpha2_long_put_threshold FLOAT DEFAULT 0.2,
    min_option_price FLOAT DEFAULT 20.0,
    stop_loss_percent FLOAT DEFAULT 40.0,
    trailing_stop_percent FLOAT DEFAULT 30.0,
    default_quantity INTEGER DEFAULT 1,
    max_positions INTEGER DEFAULT 1,
    send_signal_alerts BOOLEAN DEFAULT TRUE,
    send_trade_alerts BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Create indexes for better performance
CREATE INDEX IF NOT EXISTS idx_strategy_signals_user_id ON strategy_signals(user_id);
CREATE INDEX IF NOT EXISTS idx_strategy_signals_symbol ON strategy_signals(symbol);
CREATE INDEX IF NOT EXISTS idx_strategy_signals_signal_time ON strategy_signals(signal_time);
CREATE INDEX IF NOT EXISTS idx_strategy_trades_user_id ON strategy_trades(user_id);
CREATE INDEX IF NOT EXISTS idx_strategy_trades_status ON strategy_trades(status);
CREATE INDEX IF NOT EXISTS idx_strategy_configs_user_id ON strategy_configs(user_id);
