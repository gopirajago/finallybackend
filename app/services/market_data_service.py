"""
Market data service for fetching VIX, trend, and other market indicators
"""

import asyncio
import io
import sys
import logging
from typing import Dict, Optional
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class MarketDataService:
    """Service for fetching market data and calculating indicators"""
    
    def __init__(self, groww_client=None):
        self.groww_client = groww_client
        self.cache = {}
        self.cache_expiry = {}
        
    async def get_india_vix(self) -> float:
        """
        Get India VIX (Volatility Index)
        
        Returns:
            Current VIX value (default 15 if unavailable)
        """
        cache_key = 'india_vix'
        
        # Check cache (5 minute expiry)
        if cache_key in self.cache:
            if datetime.now() < self.cache_expiry.get(cache_key, datetime.min):
                return self.cache[cache_key]
        
        try:
            if self.groww_client:
                loop = asyncio.get_event_loop()
                
                def _fetch_vix():
                    _o, _e = sys.stdout, sys.stderr
                    sys.stdout = sys.stderr = io.StringIO()
                    try:
                        # Try to get VIX from instruments
                        # India VIX is typically available on NSE
                        # For now, return a default value
                        # TODO: Implement actual VIX fetching from NSE/Groww
                        return 15.0
                    finally:
                        sys.stdout, sys.stderr = _o, _e
                
                vix = await loop.run_in_executor(None, _fetch_vix)
                
                # Cache for 5 minutes
                self.cache[cache_key] = vix
                self.cache_expiry[cache_key] = datetime.now() + timedelta(minutes=5)
                
                return vix
        except Exception as e:
            logger.error(f"Error fetching VIX: {e}")
        
        # Default VIX value
        return 15.0
    
    async def calculate_trend_strength(self, symbol: str, historical_data: Optional[Dict] = None) -> float:
        """
        Calculate trend strength for a symbol
        
        Args:
            symbol: NIFTY or SENSEX
            historical_data: Optional historical price data
            
        Returns:
            Trend strength from -1 (strong bearish) to +1 (strong bullish)
        """
        cache_key = f'trend_{symbol}'
        
        # Check cache (15 minute expiry)
        if cache_key in self.cache:
            if datetime.now() < self.cache_expiry.get(cache_key, datetime.min):
                return self.cache[cache_key]
        
        try:
            if self.groww_client and historical_data:
                # Calculate simple trend based on price movement
                # TODO: Implement more sophisticated trend calculation (EMA, ADX, etc.)
                
                # For now, use a simple approach
                current_price = historical_data.get('current_price', 0)
                sma_20 = historical_data.get('sma_20', current_price)
                
                if sma_20 > 0:
                    trend = (current_price - sma_20) / sma_20
                    # Normalize to -1 to +1 range
                    trend_strength = max(-1.0, min(1.0, trend * 10))
                else:
                    trend_strength = 0.0
                
                # Cache for 15 minutes
                self.cache[cache_key] = trend_strength
                self.cache_expiry[cache_key] = datetime.now() + timedelta(minutes=15)
                
                return trend_strength
        except Exception as e:
            logger.error(f"Error calculating trend for {symbol}: {e}")
        
        # Default neutral trend
        return 0.0
    
    async def calculate_price_range(self, symbol: str, historical_data: Optional[Dict] = None) -> float:
        """
        Calculate recent price range as percentage
        
        Args:
            symbol: NIFTY or SENSEX
            historical_data: Optional historical price data
            
        Returns:
            Price range percentage over recent period
        """
        try:
            if historical_data:
                high = historical_data.get('high', 0)
                low = historical_data.get('low', 0)
                current = historical_data.get('current_price', 0)
                
                if current > 0:
                    price_range = ((high - low) / current) * 100
                    return price_range
        except Exception as e:
            logger.error(f"Error calculating price range for {symbol}: {e}")
        
        # Default 2% range
        return 2.0
    
    async def get_market_regime_data(self, symbol: str) -> Dict:
        """
        Get all market data needed for regime detection
        
        Args:
            symbol: NIFTY or SENSEX
            
        Returns:
            Dictionary with vix, trend_strength, price_range
        """
        # Fetch VIX
        vix = await self.get_india_vix()
        
        # For now, use default values for trend and range
        # TODO: Fetch actual historical data and calculate
        trend_strength = await self.calculate_trend_strength(symbol)
        price_range = await self.calculate_price_range(symbol)
        
        return {
            'vix': vix,
            'trend_strength': trend_strength,
            'price_range': price_range,
            'symbol': symbol,
            'timestamp': datetime.now().isoformat()
        }
    
    def clear_cache(self):
        """Clear all cached data"""
        self.cache.clear()
        self.cache_expiry.clear()
