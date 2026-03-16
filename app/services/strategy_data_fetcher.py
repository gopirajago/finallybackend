"""
Fetches options data from Groww API for strategy signal generation
"""

import asyncio
import io
import sys
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.broker_settings import BrokerSettings
from app.models.user import User
from app.models.strategy_signal import StrategySignal, StrategyConfig

logger = logging.getLogger(__name__)


class StrategyDataFetcher:
    """Fetches and processes options data for strategy"""
    
    def __init__(self, user: User, db: AsyncSession):
        self.user = user
        self.db = db
        self.groww_client = None
        
    async def _get_groww_client(self):
        """Initialize Groww API client"""
        if self.groww_client:
            return self.groww_client
            
        result = await self.db.execute(
            select(BrokerSettings).where(BrokerSettings.user_id == self.user.id)
        )
        settings = result.scalar_one_or_none()
        
        if not settings or not settings.access_token:
            raise ValueError(f"No Groww access token for user {self.user.id}")
            
        try:
            from growwapi import GrowwAPI
            _o, _e = sys.stdout, sys.stderr
            sys.stdout = sys.stderr = io.StringIO()
            try:
                self.groww_client = GrowwAPI(settings.access_token)
            finally:
                sys.stdout, sys.stderr = _o, _e
            return self.groww_client
        except Exception as e:
            raise ValueError(f"Failed to initialize Groww client: {e}")
            
    async def fetch_options_data(self, symbol: str) -> Optional[Dict]:
        """
        Fetch options chain data from Groww for signal generation
        
        Returns dict with:
        - symbol: str
        - spot_price: float
        - atm_strike: float
        - otm_call_data: dict (strike, price, volume, oi, iv)
        - itm_call_data: dict
        - otm_put_data: dict
        - itm_put_data: dict
        """
        try:
            groww = await self._get_groww_client()
            loop = asyncio.get_event_loop()
            
            # Map symbol to Groww instrument
            instrument_map = {
                "NIFTY": {
                    "underlying": "NIFTY",
                    "exchange": "NFO",
                    "segment": "FNO"
                },
                "SENSEX": {
                    "underlying": "SENSEX",
                    "exchange": "BFO",
                    "segment": "FNO"
                }
            }
            
            if symbol not in instrument_map:
                logger.warning(f"Unknown symbol: {symbol}")
                return None
                
            instr = instrument_map[symbol]
            
            # Fetch data in thread pool to avoid blocking
            def _fetch():
                _o, _e = sys.stdout, sys.stderr
                sys.stdout = sys.stderr = io.StringIO()
                try:
                    # Get current spot price
                    # For now, we'll use a placeholder - you can enhance this with actual quote fetching
                    spot_price = 0
                    
                    # Get option chain for nearest expiry
                    df = groww.get_all_instruments()
                    
                    # Find nearest expiry
                    opts = df[
                        (df["underlying_symbol"] == instr["underlying"]) &
                        (df["instrument_type"].isin(["CE", "PE"]))
                    ]
                    
                    if opts.empty:
                        return None
                        
                    expiries = sorted(opts["expiry_date"].unique())
                    if not expiries:
                        return None
                        
                    nearest_expiry = expiries[0]
                    
                    # Get option chain
                    chain = groww.get_option_chain(
                        exchange=instr["exchange"],
                        underlying=instr["underlying"],
                        expiry_date=nearest_expiry
                    )
                    
                    if chain.empty:
                        return None
                    
                    # Find ATM strike (closest to spot)
                    # For now, use middle strike as approximation
                    strikes = sorted(chain["strike_price"].unique())
                    if not strikes:
                        return None
                        
                    atm_strike = strikes[len(strikes) // 2]
                    spot_price = atm_strike  # Approximate spot as ATM
                    
                    # Find OTM and ITM strikes
                    otm_call_strike = atm_strike + (strikes[1] - strikes[0]) * 2  # 2 strikes OTM
                    itm_call_strike = atm_strike - (strikes[1] - strikes[0]) * 2  # 2 strikes ITM
                    otm_put_strike = atm_strike - (strikes[1] - strikes[0]) * 2
                    itm_put_strike = atm_strike + (strikes[1] - strikes[0]) * 2
                    
                    # Extract option data
                    def get_option_data(strike, opt_type):
                        opt = chain[
                            (chain["strike_price"] == strike) &
                            (chain["instrument_type"] == opt_type)
                        ]
                        if opt.empty:
                            return None
                        row = opt.iloc[0]
                        return {
                            "strike": strike,
                            "price": row.get("ltp", 0),
                            "volume": row.get("volume", 0),
                            "oi": row.get("open_interest", 0),
                            "iv": row.get("implied_volatility", 0),
                        }
                    
                    return {
                        "symbol": symbol,
                        "spot_price": spot_price,
                        "atm_strike": atm_strike,
                        "expiry_date": nearest_expiry,
                        "otm_call_data": get_option_data(otm_call_strike, "CE"),
                        "itm_call_data": get_option_data(itm_call_strike, "CE"),
                        "otm_put_data": get_option_data(otm_put_strike, "PE"),
                        "itm_put_data": get_option_data(itm_put_strike, "PE"),
                    }
                finally:
                    sys.stdout, sys.stderr = _o, _e
                    
            result = await loop.run_in_executor(None, _fetch)
            return result
            
        except Exception as e:
            logger.error(f"Error fetching options data for {symbol}: {e}", exc_info=True)
            return None
            
    async def save_signal(self, signal_data: Dict, config: StrategyConfig):
        """Save generated signal to database"""
        try:
            signal = StrategySignal(
                user_id=self.user.id,
                strategy_name=config.strategy_name,
                strategy_version=config.version,
                symbol=signal_data["symbol"],
                signal_type=signal_data["signal_type"],
                signal_strength=signal_data["signal_strength"],
                alpha1_value=signal_data["alpha1"],
                alpha2_value=signal_data["alpha2"],
                spot_price=signal_data["spot_price"],
                recommended_strike=signal_data["recommended_strike"],
                recommended_option_type=signal_data["recommended_option_type"],
                recommended_entry_price=signal_data.get("recommended_entry_price", 0),
                stop_loss_price=signal_data.get("stop_loss_price", 0),
                expiry_date=signal_data.get("expiry_date"),
                signal_time=datetime.now(timezone.utc),
                is_active=True,
            )
            
            self.db.add(signal)
            await self.db.commit()
            await self.db.refresh(signal)
            
            logger.info(f"Saved signal {signal.id} for user {self.user.id}")
            return signal
            
        except Exception as e:
            logger.error(f"Error saving signal: {e}", exc_info=True)
            await self.db.rollback()
            return None
