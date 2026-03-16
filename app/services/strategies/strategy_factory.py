"""
Strategy Factory - Creates strategy instances based on type
"""

from typing import Dict, Optional, List
from app.services.strategies.base_strategy import BaseStrategy, StrategyType, MarketRegime
from app.services.skew_hunter import SkewHunterStrategy
from app.services.strategies.iron_condor_strategy import IronCondorStrategy
from app.services.strategies.straddle_strategy import StraddleStrategy
from app.services.strategies.spread_strategies import BullCallSpreadStrategy, BearPutSpreadStrategy
from app.services.strategies.calendar_spread_strategy import CalendarSpreadStrategy
from app.services.strategies.ratio_spread_strategy import RatioSpreadStrategy


class StrategyFactory:
    """Factory for creating strategy instances"""
    
    _strategy_map = {
        StrategyType.SKEW_HUNTER: SkewHunterStrategy,
        StrategyType.IRON_CONDOR: IronCondorStrategy,
        StrategyType.STRADDLE: StraddleStrategy,
        StrategyType.STRANGLE: lambda config: StraddleStrategy({**(config or {}), 'use_strangle': True}),
        StrategyType.BULL_CALL_SPREAD: BullCallSpreadStrategy,
        StrategyType.BEAR_PUT_SPREAD: BearPutSpreadStrategy,
        StrategyType.CALENDAR_SPREAD: CalendarSpreadStrategy,
        StrategyType.RATIO_SPREAD: RatioSpreadStrategy,
    }
    
    @classmethod
    def create_strategy(cls, strategy_type: StrategyType, config: Optional[Dict] = None) -> BaseStrategy:
        """
        Create a strategy instance
        
        Args:
            strategy_type: Type of strategy to create
            config: Configuration dictionary
            
        Returns:
            Strategy instance
        """
        strategy_class = cls._strategy_map.get(strategy_type)
        if not strategy_class:
            raise ValueError(f"Unknown strategy type: {strategy_type}")
        
        return strategy_class(config)
    
    @classmethod
    def get_all_strategies(cls, config: Optional[Dict] = None) -> List[BaseStrategy]:
        """Get instances of all available strategies"""
        return [cls.create_strategy(st, config) for st in StrategyType]
    
    @classmethod
    def get_strategies_for_regime(cls, regime: MarketRegime, config: Optional[Dict] = None) -> List[BaseStrategy]:
        """
        Get strategies suitable for a specific market regime
        
        Args:
            regime: Current market regime
            config: Configuration dictionary
            
        Returns:
            List of suitable strategies
        """
        all_strategies = cls.get_all_strategies(config)
        return [s for s in all_strategies if s.is_suitable_for_regime(regime)]
    
    @classmethod
    def get_recommended_strategies(cls, market_data: Dict, config: Optional[Dict] = None) -> List[BaseStrategy]:
        """
        Get recommended strategies based on current market conditions
        
        Args:
            market_data: Dictionary with VIX, trend, etc.
            config: Configuration dictionary
            
        Returns:
            List of recommended strategies (sorted by suitability)
        """
        regime = cls._detect_market_regime(market_data)
        suitable_strategies = cls.get_strategies_for_regime(regime, config)
        
        # Sort by priority for the regime
        priority_map = {
            MarketRegime.HIGH_VOLATILITY: [StrategyType.STRADDLE, StrategyType.STRANGLE, StrategyType.IRON_CONDOR],
            MarketRegime.LOW_VOLATILITY: [StrategyType.IRON_CONDOR, StrategyType.CALENDAR_SPREAD, StrategyType.SKEW_HUNTER],
            MarketRegime.TRENDING_UP: [StrategyType.BULL_CALL_SPREAD, StrategyType.SKEW_HUNTER, StrategyType.RATIO_SPREAD],
            MarketRegime.TRENDING_DOWN: [StrategyType.BEAR_PUT_SPREAD, StrategyType.SKEW_HUNTER, StrategyType.RATIO_SPREAD],
            MarketRegime.RANGE_BOUND: [StrategyType.IRON_CONDOR, StrategyType.CALENDAR_SPREAD, StrategyType.SKEW_HUNTER],
        }
        
        priority_list = priority_map.get(regime, [])
        
        def get_priority(strategy: BaseStrategy) -> int:
            try:
                return priority_list.index(strategy.strategy_type)
            except ValueError:
                return 999
        
        return sorted(suitable_strategies, key=get_priority)
    
    @classmethod
    def _detect_market_regime(cls, market_data: Dict) -> MarketRegime:
        """
        Detect current market regime based on market data
        
        Args:
            market_data: Dictionary with:
                - vix: Current VIX level
                - trend_strength: Trend strength (-1 to 1)
                - price_range: Recent price range percentage
                
        Returns:
            Detected market regime
        """
        vix = market_data.get('vix', 15)
        trend_strength = market_data.get('trend_strength', 0)
        price_range = market_data.get('price_range', 2)  # Percentage
        
        # High volatility
        if vix > 20:
            return MarketRegime.HIGH_VOLATILITY
        
        # Low volatility
        if vix < 15:
            return MarketRegime.LOW_VOLATILITY
        
        # Trending markets
        if trend_strength > 0.6:
            return MarketRegime.TRENDING_UP
        
        if trend_strength < -0.6:
            return MarketRegime.TRENDING_DOWN
        
        # Range-bound (low price movement)
        if price_range < 2:
            return MarketRegime.RANGE_BOUND
        
        # Default to low volatility
        return MarketRegime.LOW_VOLATILITY
