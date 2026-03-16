"""
Skew Hunter Options Trading Strategy
Exploits volatility skew in NIFTY options market
"""

from datetime import datetime, time
from typing import Dict, List, Optional, Tuple
import math


class SkewHunterStrategy:
    """
    Sophisticated options trading algorithm that monitors ATM and OTM options
    to identify trading opportunities based on volatility skew and volume/OI patterns
    """
    
    def __init__(self, config: Optional[Dict] = None):
        self.config = config or {}
        self.alpha1_long_call_threshold = self.config.get('alpha1_long_call_threshold', 0.75)
        self.alpha2_long_call_threshold = self.config.get('alpha2_long_call_threshold', 0.8)
        self.alpha1_long_put_threshold = self.config.get('alpha1_long_put_threshold', 0.25)
        self.alpha2_long_put_threshold = self.config.get('alpha2_long_put_threshold', 0.2)
        self.min_option_price = self.config.get('min_option_price', 20.0)
        self.stop_loss_percent = self.config.get('stop_loss_percent', 40.0)
        
    def is_trading_hours(self, current_time: datetime) -> bool:
        """Check if current time is within trading hours (10:15 AM - 2:15 PM)"""
        start_time = time(10, 15)
        end_time = time(14, 15)
        current = current_time.time()
        return start_time <= current <= end_time
    
    def calculate_alpha1(
        self,
        otm_call_volume_ratio: float,
        itm_put_volume_ratio: float,
        otm_call_oi_change: float,
        itm_put_oi_change: float
    ) -> float:
        """
        Calculate Alpha1: Volume ratio + OI change for OTM calls and ITM puts
        
        Alpha1 combines:
        - Volume ratio between OTM calls and ITM puts
        - Open Interest changes for both
        
        Higher values (>0.75) suggest bullish sentiment (long call signal)
        Lower values (<0.25) suggest bearish sentiment (long put signal)
        """
        # Normalize volume ratio (0 to 1 scale)
        volume_component = self._normalize_ratio(otm_call_volume_ratio / (itm_put_volume_ratio + 1e-6))
        
        # Normalize OI change difference
        oi_component = self._normalize_ratio(otm_call_oi_change - itm_put_oi_change)
        
        # Weighted combination (60% volume, 40% OI)
        alpha1 = 0.6 * volume_component + 0.4 * oi_component
        
        return max(0.0, min(1.0, alpha1))  # Clamp between 0 and 1
    
    def calculate_alpha2(
        self,
        otm_call_iv: float,
        itm_call_iv: float,
        otm_put_iv: float,
        itm_put_iv: float
    ) -> float:
        """
        Calculate Alpha2: IV skew between OTM and ITM options
        
        Alpha2 measures:
        - IV skew for calls (OTM vs ITM)
        - IV skew for puts (OTM vs ITM)
        
        Higher values (>0.8) suggest bullish IV skew (long call signal)
        Lower values (<0.2) suggest bearish IV skew (long put signal)
        """
        # Calculate call skew (OTM IV / ITM IV)
        call_skew = otm_call_iv / (itm_call_iv + 1e-6)
        
        # Calculate put skew (ITM IV / OTM IV) - inverted for puts
        put_skew = itm_put_iv / (otm_put_iv + 1e-6)
        
        # Normalize the skew difference
        skew_ratio = call_skew / (put_skew + 1e-6)
        alpha2 = self._normalize_ratio(skew_ratio)
        
        return max(0.0, min(1.0, alpha2))  # Clamp between 0 and 1
    
    def _normalize_ratio(self, ratio: float) -> float:
        """Normalize ratio using sigmoid function to 0-1 scale"""
        # Sigmoid normalization: 1 / (1 + e^(-x))
        # Adjusted to center around 1.0 ratio
        return 1 / (1 + math.exp(-(ratio - 1.0) * 2))
    
    def generate_signal(
        self,
        spot_price: float,
        atm_strike: float,
        options_data: Dict[str, Dict]
    ) -> Optional[Dict]:
        """
        Generate trading signal based on alpha calculations
        
        Args:
            spot_price: Current NIFTY spot price
            atm_strike: ATM strike price
            options_data: Dictionary with option chain data
                {
                    'otm_call': {'volume': x, 'oi': y, 'oi_change': z, 'iv': a, 'ltp': b, 'strike': c},
                    'itm_call': {...},
                    'otm_put': {...},
                    'itm_put': {...}
                }
        
        Returns:
            Signal dictionary or None
        """
        # Extract data
        otm_call = options_data.get('otm_call', {})
        itm_call = options_data.get('itm_call', {})
        otm_put = options_data.get('otm_put', {})
        itm_put = options_data.get('itm_put', {})
        
        # Calculate volume ratios
        otm_call_volume_ratio = otm_call.get('volume', 0) / (otm_put.get('volume', 1) + 1e-6)
        itm_put_volume_ratio = itm_put.get('volume', 0) / (itm_call.get('volume', 1) + 1e-6)
        
        # Get OI changes
        otm_call_oi_change = otm_call.get('oi_change', 0)
        itm_put_oi_change = itm_put.get('oi_change', 0)
        
        # Get IVs
        otm_call_iv = otm_call.get('iv', 0)
        itm_call_iv = itm_call.get('iv', 0)
        otm_put_iv = otm_put.get('iv', 0)
        itm_put_iv = itm_put.get('iv', 0)
        
        call_iv_skew = 0
        put_iv_skew = 0
        
        if itm_call_iv > 0:
            call_iv_skew = otm_call_iv / itm_call_iv
            
        if itm_put_iv > 0:
            put_iv_skew = otm_put_iv / itm_put_iv
        
        # Calculate Alpha1 (volume-based)
        alpha1 = call_volume_ratio - put_volume_ratio  # Range: -1 to 1
        
        # Calculate Alpha2 (IV skew-based)
        alpha2 = call_iv_skew - put_iv_skew  # Positive = calls expensive, negative = puts expensive
        
        # Determine signal type based on thresholds
        signal_type = "NEUTRAL"
        signal_strength = 0.0
        recommended_strike = data.get("atm_strike", 0)
        recommended_option_type = "CE"
        recommended_entry_price = 0
        
        if (alpha1 >= config.alpha1_long_call_threshold and 
            alpha2 >= config.alpha2_long_call_threshold):
            signal_type = "LONG_CALL"
            signal_strength = (alpha1 + alpha2) / 2
            recommended_strike = otm_call.get("strike", recommended_strike)
            recommended_option_type = "CE"
            recommended_entry_price = otm_call.get("price", 0)
            
        elif (alpha1 <= config.alpha1_long_put_threshold and 
              alpha2 <= config.alpha2_long_put_threshold):
            signal_type = "LONG_PUT"
            signal_strength = (abs(alpha1) + abs(alpha2)) / 2
            recommended_strike = otm_put.get("strike", recommended_strike)
            recommended_option_type = "PE"
            recommended_entry_price = otm_put.get("price", 0)
        
        # Calculate stop loss
        stop_loss_price = self.calculate_stop_loss(
            recommended_entry_price,
            config.stop_loss_percent
        )
        
        return {
            "symbol": data.get("symbol", ""),
            "signal_type": signal_type,
            "signal_strength": signal_strength,
            "alpha1": alpha1,
            "alpha2": alpha2,
            "spot_price": data.get("spot_price", 0),
            "recommended_strike": recommended_strike,
            "recommended_option_type": recommended_option_type,
            "recommended_entry_price": recommended_entry_price,
            "stop_loss_price": stop_loss_price,
            "expiry_date": data.get("expiry_date"),
        }
    
    def calculate_stop_loss(self, entry_price: float) -> float:
        """Calculate stop loss price (40% below entry)"""
        return entry_price * (1 - self.stop_loss_percent / 100)
    
    def calculate_trailing_stop(
        self,
        entry_price: float,
        current_price: float,
        highest_price: float,
        trailing_percent: float = 30.0
    ) -> float:
        """
        Calculate trailing stop loss
        
        Args:
            entry_price: Original entry price
            current_price: Current option price
            highest_price: Highest price reached since entry
            trailing_percent: Trailing stop percentage
        
        Returns:
            Trailing stop price
        """
        # Update highest price
        new_highest = max(highest_price, current_price)
        
        # Trailing stop is trailing_percent below highest price
        trailing_stop = new_highest * (1 - trailing_percent / 100)
        
        # But never below initial stop loss
        initial_stop = self.calculate_stop_loss(entry_price)
        
        return max(trailing_stop, initial_stop)
    
    def should_exit(
        self,
        current_price: float,
        stop_loss_price: float,
        current_time: datetime,
        version: str = "regular"
    ) -> Tuple[bool, Optional[str]]:
        """
        Check if position should be exited
        
        Returns:
            (should_exit, exit_reason)
        """
        # Check stop loss
        if current_price <= stop_loss_price:
            return True, "STOP_LOSS"
        
        # Check EOD square off (after 3:15 PM)
        eod_time = time(15, 15)
        if current_time.time() >= eod_time:
            return True, "EOD_SQUAREOFF"
        
        return False, None
    
    def validate_entry(
        self,
        option_price: float,
        current_time: datetime,
        active_positions_count: int,
        max_positions: int = 1
    ) -> Tuple[bool, Optional[str]]:
        """
        Validate if trade entry is allowed
        
        Returns:
            (is_valid, rejection_reason)
        """
        # Check trading hours
        if not self.is_trading_hours(current_time):
            return False, "Outside trading hours (10:15 AM - 2:15 PM)"
        
        # Check minimum option price
        if option_price < self.min_option_price:
            return False, f"Option price ₹{option_price} below minimum ₹{self.min_option_price}"
        
        # Check max positions (prevent multiple active trades)
        if active_positions_count >= max_positions:
            return False, f"Maximum {max_positions} active position(s) already running"
        
        return True, None
