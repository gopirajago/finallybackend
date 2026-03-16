"""
Ratio Spread Strategy - Advanced directional strategy
For strong directional views with limited risk
"""

from typing import Dict, Optional
from app.services.strategies.base_strategy import BaseStrategy, StrategyType, MarketRegime


class RatioSpreadStrategy(BaseStrategy):
    """
    Ratio Spread: Buy 1 ATM option + Sell 2 OTM options
    
    Entry:
    - Buy 1 ATM call/put
    - Sell 2 OTM calls/puts
    
    Profit: Moderate move in expected direction
    Max Loss: Unlimited on one side (needs monitoring)
    Win Rate: 55-65%
    """
    
    def __init__(self, config: Optional[Dict] = None):
        super().__init__(config)
        self.ratio = self.config.get('ratio', 2)  # 1:2 ratio
        self.otm_distance = self.config.get('otm_distance', 100)
        self.is_bullish = self.config.get('is_bullish', True)
        self.min_trend_strength = self.config.get('min_trend_strength', 0.7)
        self.profit_target_percent = self.config.get('profit_target_percent', 80)
        self.stop_loss_percent = self.config.get('stop_loss_percent', 60)
        
    def _get_strategy_type(self) -> StrategyType:
        return StrategyType.RATIO_SPREAD
    
    def _get_strategy_name(self) -> str:
        return "Ratio Spread"
    
    def _get_strategy_description(self) -> str:
        return "Advanced directional strategy. Buy 1 ATM, sell 2 OTM options. Limited risk with careful monitoring."
    
    def is_suitable_for_regime(self, regime: MarketRegime) -> bool:
        """Works in trending markets"""
        if self.is_bullish:
            return regime == MarketRegime.TRENDING_UP
        return regime == MarketRegime.TRENDING_DOWN
    
    def generate_signal(self, data: Dict, config: Dict) -> Dict:
        """Generate Ratio Spread signal"""
        atm_strike = data.get('atm_strike', 0)
        trend_strength = data.get('trend_strength', 0)
        option_chain = data.get('option_chain', {})
        
        # Check trend strength
        if abs(trend_strength) < self.min_trend_strength:
            return self._neutral_signal(data)
        
        if self.is_bullish:
            option_type = 'CE'
            buy_strike = atm_strike
            sell_strike = self._round_strike(atm_strike + self.otm_distance)
        else:
            option_type = 'PE'
            buy_strike = atm_strike
            sell_strike = self._round_strike(atm_strike - self.otm_distance)
        
        # Get prices
        buy_price = self._get_option_price(option_chain, buy_strike, option_type)
        sell_price = self._get_option_price(option_chain, sell_strike, option_type)
        
        # Net credit/debit
        net_position = (sell_price * self.ratio) - buy_price
        
        # Calculate max profit (at short strike)
        if self.is_bullish:
            max_profit = (sell_strike - buy_strike) + net_position
        else:
            max_profit = (buy_strike - sell_strike) + net_position
        
        # Max loss is unlimited beyond short strike, but we set a monitoring level
        max_loss = buy_price if net_position > 0 else abs(net_position)
        
        signal_strength = min(1.0, abs(trend_strength))
        
        return {
            'symbol': data.get('symbol', ''),
            'signal_type': 'RATIO_SPREAD',
            'signal_strength': signal_strength,
            'recommended_strikes': [buy_strike, sell_strike],
            'recommended_option_types': [option_type, option_type],
            'recommended_entry_prices': [buy_price, sell_price],
            'positions': ['BUY', f'SELL_{self.ratio}'],
            'net_position': net_position,
            'max_loss': max_loss,
            'max_profit': max_profit,
            'risk_reward_ratio': max_profit / max_loss if max_loss > 0 else 0,
            'probability_of_profit': 0.60,
            'stop_loss_price': max_loss * (1 + self.stop_loss_percent / 100),
            'take_profit_price': max_profit * (self.profit_target_percent / 100),
            'expiry_date': data.get('expiry_date'),
            'spot_price': data.get('spot_price', 0),
            'warning': 'Unlimited risk beyond short strike - requires active monitoring',
        }
    
    def calculate_position_size(self, capital: float, risk_percent: float) -> int:
        """Conservative position sizing due to unlimited risk"""
        risk_amount = capital * (risk_percent / 100)
        max_loss_per_lot = 150  # Conservative estimate
        return max(1, int(risk_amount / max_loss_per_lot))
    
    def get_exit_conditions(self, entry_data: Dict, current_data: Dict) -> Dict:
        """
        Exit conditions - more aggressive due to unlimited risk
        """
        max_profit = entry_data.get('max_profit', 0)
        current_value = current_data.get('current_position_value', 0)
        spot_price = current_data.get('spot_price', 0)
        sell_strike = entry_data.get('recommended_strikes', [0, 0])[1]
        
        # Exit if price approaches or breaches short strike
        if self.is_bullish and spot_price >= sell_strike * 0.98:
            return {'should_exit': True, 'exit_reason': 'Price near short strike', 'exit_price': current_value}
        
        if not self.is_bullish and spot_price <= sell_strike * 1.02:
            return {'should_exit': True, 'exit_reason': 'Price near short strike', 'exit_price': current_value}
        
        # Profit target
        if current_value >= max_profit * (self.profit_target_percent / 100):
            return {'should_exit': True, 'exit_reason': 'Profit target', 'exit_price': current_value}
        
        # Stop loss
        max_loss = entry_data.get('max_loss', 0)
        if current_value <= -max_loss * (self.stop_loss_percent / 100):
            return {'should_exit': True, 'exit_reason': 'Stop loss', 'exit_price': current_value}
        
        return {'should_exit': False, 'exit_reason': '', 'exit_price': 0}
    
    def _round_strike(self, price: float) -> float:
        return round(price / 50) * 50
    
    def _get_option_price(self, option_chain: Dict, strike: float, option_type: str) -> float:
        key = f"{strike}_{option_type}"
        return option_chain.get(key, {}).get('ltp', 0)
    
    def _neutral_signal(self, data: Dict) -> Dict:
        return {
            'symbol': data.get('symbol', ''),
            'signal_type': 'NEUTRAL',
            'signal_strength': 0,
            'recommended_strikes': [],
            'recommended_option_types': [],
            'recommended_entry_prices': [],
        }
