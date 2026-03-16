"""
Base strategy class for all option trading strategies
"""

from abc import ABC, abstractmethod
from typing import Dict, List, Optional
from datetime import datetime
from enum import Enum


class StrategyType(str, Enum):
    """Available strategy types"""
    SKEW_HUNTER = "skew_hunter"
    IRON_CONDOR = "iron_condor"
    STRADDLE = "straddle"
    STRANGLE = "strangle"
    BULL_CALL_SPREAD = "bull_call_spread"
    BEAR_PUT_SPREAD = "bear_put_spread"
    CALENDAR_SPREAD = "calendar_spread"
    RATIO_SPREAD = "ratio_spread"


class MarketRegime(str, Enum):
    """Market regime types"""
    HIGH_VOLATILITY = "high_volatility"  # VIX > 20
    LOW_VOLATILITY = "low_volatility"    # VIX < 15
    TRENDING_UP = "trending_up"
    TRENDING_DOWN = "trending_down"
    RANGE_BOUND = "range_bound"


class BaseStrategy(ABC):
    """
    Abstract base class for all option trading strategies
    """
    
    def __init__(self, config: Optional[Dict] = None):
        self.config = config or {}
        self.strategy_type = self._get_strategy_type()
        self.name = self._get_strategy_name()
        self.description = self._get_strategy_description()
        
    @abstractmethod
    def _get_strategy_type(self) -> StrategyType:
        """Return the strategy type"""
        pass
    
    @abstractmethod
    def _get_strategy_name(self) -> str:
        """Return human-readable strategy name"""
        pass
    
    @abstractmethod
    def _get_strategy_description(self) -> str:
        """Return strategy description"""
        pass
    
    @abstractmethod
    def is_suitable_for_regime(self, regime: MarketRegime) -> bool:
        """
        Check if this strategy is suitable for the current market regime
        
        Args:
            regime: Current market regime
            
        Returns:
            True if strategy is suitable for this regime
        """
        pass
    
    @abstractmethod
    def generate_signal(self, data: Dict, config: Dict) -> Dict:
        """
        Generate trading signal based on market data
        
        Args:
            data: Market data including options chain, spot price, etc.
            config: Strategy configuration with thresholds
            
        Returns:
            Signal dictionary with:
            - signal_type: str (LONG_CALL, LONG_PUT, IRON_CONDOR, etc.)
            - signal_strength: float (0-1)
            - recommended_strikes: List[float]
            - recommended_option_types: List[str]
            - recommended_entry_prices: List[float]
            - stop_loss_price: float
            - take_profit_price: float
            - max_loss: float
            - max_profit: float
            - probability_of_profit: float
        """
        pass
    
    @abstractmethod
    def calculate_position_size(self, capital: float, risk_percent: float) -> int:
        """
        Calculate position size based on capital and risk tolerance
        
        Args:
            capital: Available capital
            risk_percent: Risk percentage (e.g., 2 for 2%)
            
        Returns:
            Number of lots to trade
        """
        pass
    
    @abstractmethod
    def get_exit_conditions(self, entry_data: Dict, current_data: Dict) -> Dict:
        """
        Determine if exit conditions are met
        
        Args:
            entry_data: Data at entry time
            current_data: Current market data
            
        Returns:
            Dictionary with:
            - should_exit: bool
            - exit_reason: str
            - exit_price: float
        """
        pass
    
    def calculate_stop_loss(self, entry_price: float, stop_loss_percent: float) -> float:
        """Calculate stop loss price"""
        return entry_price * (1 - stop_loss_percent / 100)
    
    def calculate_take_profit(self, entry_price: float, take_profit_percent: float) -> float:
        """Calculate take profit price"""
        return entry_price * (1 + take_profit_percent / 100)
    
    def get_risk_reward_ratio(self, max_loss: float, max_profit: float) -> float:
        """Calculate risk-reward ratio"""
        if max_loss == 0:
            return 0
        return max_profit / max_loss
    
    def validate_signal(self, signal: Dict) -> bool:
        """
        Validate generated signal
        
        Args:
            signal: Signal dictionary
            
        Returns:
            True if signal is valid
        """
        required_fields = [
            'signal_type', 'signal_strength', 'recommended_strikes',
            'recommended_option_types', 'recommended_entry_prices'
        ]
        
        for field in required_fields:
            if field not in signal:
                return False
                
        if signal['signal_strength'] < 0 or signal['signal_strength'] > 1:
            return False
            
        return True
