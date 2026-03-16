"""
Calendar Spread Strategy - Time decay strategy
Best for neutral to slightly bullish/bearish markets
"""

from typing import Dict, Optional
from app.services.strategies.base_strategy import BaseStrategy, StrategyType, MarketRegime


class CalendarSpreadStrategy(BaseStrategy):
    """
    Calendar Spread: Sell near-month option + Buy far-month option (same strike)
    
    Entry:
    - Sell near-month ATM option
    - Buy far-month ATM option
    
    Profit: Time decay on near-month option
    Max Loss: Net debit paid
    Win Rate: 60-70%
    """
    
    def __init__(self, config: Optional[Dict] = None):
        super().__init__(config)
        self.near_month_dte = self.config.get('near_month_dte', 15)  # Days to expiry
        self.far_month_dte = self.config.get('far_month_dte', 45)
        self.use_calls = self.config.get('use_calls', True)  # True = calls, False = puts
        self.profit_target_percent = self.config.get('profit_target_percent', 50)
        self.stop_loss_percent = self.config.get('stop_loss_percent', 50)
        
    def _get_strategy_type(self) -> StrategyType:
        return StrategyType.CALENDAR_SPREAD
    
    def _get_strategy_name(self) -> str:
        return "Calendar Spread"
    
    def _get_strategy_description(self) -> str:
        return "Time decay strategy. Sell near-month, buy far-month option. Profit from time decay."
    
    def is_suitable_for_regime(self, regime: MarketRegime) -> bool:
        """Works in range-bound and low volatility markets"""
        return regime in [MarketRegime.RANGE_BOUND, MarketRegime.LOW_VOLATILITY]
    
    def generate_signal(self, data: Dict, config: Dict) -> Dict:
        """Generate Calendar Spread signal"""
        atm_strike = data.get('atm_strike', 0)
        near_month_chain = data.get('near_month_option_chain', {})
        far_month_chain = data.get('far_month_option_chain', {})
        
        option_type = 'CE' if self.use_calls else 'PE'
        
        # Get prices
        near_month_price = self._get_option_price(near_month_chain, atm_strike, option_type)
        far_month_price = self._get_option_price(far_month_chain, atm_strike, option_type)
        
        net_debit = far_month_price - near_month_price
        
        if net_debit <= 0:
            return self._neutral_signal(data)
        
        # Calendar spreads have limited max profit (typically 20-30% of debit)
        max_profit = net_debit * 0.25
        max_loss = net_debit
        
        signal_strength = 0.7  # Moderate signal strength
        
        return {
            'symbol': data.get('symbol', ''),
            'signal_type': 'CALENDAR_SPREAD',
            'signal_strength': signal_strength,
            'recommended_strikes': [atm_strike, atm_strike],
            'recommended_option_types': [option_type, option_type],
            'recommended_entry_prices': [near_month_price, far_month_price],
            'positions': ['SELL', 'BUY'],
            'expiries': ['near', 'far'],
            'net_debit': net_debit,
            'max_loss': max_loss,
            'max_profit': max_profit,
            'risk_reward_ratio': max_profit / max_loss if max_loss > 0 else 0,
            'probability_of_profit': 0.65,
            'stop_loss_price': net_debit * (1 + self.stop_loss_percent / 100),
            'take_profit_price': max_profit * (self.profit_target_percent / 100),
            'spot_price': data.get('spot_price', 0),
        }
    
    def calculate_position_size(self, capital: float, risk_percent: float) -> int:
        risk_amount = capital * (risk_percent / 100)
        max_loss_per_lot = 100  # Approximate
        return max(1, int(risk_amount / max_loss_per_lot))
    
    def get_exit_conditions(self, entry_data: Dict, current_data: Dict) -> Dict:
        entry_debit = entry_data.get('net_debit', 0)
        current_value = current_data.get('current_position_value', entry_debit)
        near_month_dte = current_data.get('near_month_dte', 15)
        
        # Exit before near-month expiry
        if near_month_dte <= 3:
            return {'should_exit': True, 'exit_reason': 'Near expiry', 'exit_price': current_value}
        
        # Profit target
        profit = current_value - entry_debit
        if profit >= entry_debit * (self.profit_target_percent / 100):
            return {'should_exit': True, 'exit_reason': 'Profit target', 'exit_price': current_value}
        
        # Stop loss
        if current_value <= entry_debit * (1 - self.stop_loss_percent / 100):
            return {'should_exit': True, 'exit_reason': 'Stop loss', 'exit_price': current_value}
        
        return {'should_exit': False, 'exit_reason': '', 'exit_price': 0}
    
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
