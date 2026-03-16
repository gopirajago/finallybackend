"""
Bull Call Spread and Bear Put Spread Strategies
Directional strategies with limited risk
"""

from typing import Dict, Optional
from app.services.strategies.base_strategy import BaseStrategy, StrategyType, MarketRegime


class BullCallSpreadStrategy(BaseStrategy):
    """
    Bull Call Spread: Buy ITM call + Sell OTM call
    
    Entry:
    - Buy ITM call (lower strike)
    - Sell OTM call (higher strike)
    
    Profit: Moderate bullish move
    Max Loss: Net debit paid
    Max Profit: Spread width - net debit
    Win Rate: 65-75%
    """
    
    def __init__(self, config: Optional[Dict] = None):
        super().__init__(config)
        self.spread_width = self.config.get('spread_width', 100)
        self.min_trend_strength = self.config.get('min_trend_strength', 0.6)
        self.profit_target_percent = self.config.get('profit_target_percent', 75)
        self.stop_loss_percent = self.config.get('stop_loss_percent', 50)
        
    def _get_strategy_type(self) -> StrategyType:
        return StrategyType.BULL_CALL_SPREAD
    
    def _get_strategy_name(self) -> str:
        return "Bull Call Spread"
    
    def _get_strategy_description(self) -> str:
        return "Bullish directional strategy. Buy ITM call, sell OTM call. Limited risk and reward."
    
    def is_suitable_for_regime(self, regime: MarketRegime) -> bool:
        """Works best in trending up markets"""
        return regime == MarketRegime.TRENDING_UP
    
    def generate_signal(self, data: Dict, config: Dict) -> Dict:
        """Generate Bull Call Spread signal"""
        spot_price = data.get('spot_price', 0)
        atm_strike = data.get('atm_strike', 0)
        trend_strength = data.get('trend_strength', 0)
        option_chain = data.get('option_chain', {})
        
        # Check trend strength
        if trend_strength < self.min_trend_strength:
            return self._neutral_signal(data)
        
        # Buy ITM call (1 strike below ATM)
        buy_strike = self._round_strike(atm_strike - 50)
        # Sell OTM call
        sell_strike = buy_strike + self.spread_width
        
        # Get prices
        buy_price = self._get_option_price(option_chain, buy_strike, 'CE')
        sell_price = self._get_option_price(option_chain, sell_strike, 'CE')
        
        net_debit = buy_price - sell_price
        
        if net_debit <= 0:
            return self._neutral_signal(data)
        
        max_profit = self.spread_width - net_debit
        max_loss = net_debit
        
        signal_strength = min(1.0, trend_strength)
        
        return {
            'symbol': data.get('symbol', ''),
            'signal_type': 'BULL_CALL_SPREAD',
            'signal_strength': signal_strength,
            'recommended_strikes': [buy_strike, sell_strike],
            'recommended_option_types': ['CE', 'CE'],
            'recommended_entry_prices': [buy_price, sell_price],
            'positions': ['BUY', 'SELL'],
            'net_debit': net_debit,
            'max_loss': max_loss,
            'max_profit': max_profit,
            'risk_reward_ratio': max_profit / max_loss if max_loss > 0 else 0,
            'probability_of_profit': 0.65,
            'stop_loss_price': net_debit * (1 + self.stop_loss_percent / 100),
            'take_profit_price': max_profit * (self.profit_target_percent / 100),
            'expiry_date': data.get('expiry_date'),
            'spot_price': spot_price,
            'breakeven': buy_strike + net_debit,
        }
    
    def calculate_position_size(self, capital: float, risk_percent: float) -> int:
        risk_amount = capital * (risk_percent / 100)
        max_loss_per_lot = self.spread_width / 2  # Approximate
        return max(1, int(risk_amount / max_loss_per_lot))
    
    def get_exit_conditions(self, entry_data: Dict, current_data: Dict) -> Dict:
        entry_debit = entry_data.get('net_debit', 0)
        current_value = current_data.get('current_position_value', entry_debit)
        max_profit = entry_data.get('max_profit', 0)
        
        # Profit target
        if current_value >= max_profit * (self.profit_target_percent / 100):
            return {'should_exit': True, 'exit_reason': 'Profit target', 'exit_price': current_value}
        
        # Stop loss
        if current_value <= entry_debit * (1 - self.stop_loss_percent / 100):
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


class BearPutSpreadStrategy(BaseStrategy):
    """
    Bear Put Spread: Buy ITM put + Sell OTM put
    
    Entry:
    - Buy ITM put (higher strike)
    - Sell OTM put (lower strike)
    
    Profit: Moderate bearish move
    Max Loss: Net debit paid
    Max Profit: Spread width - net debit
    Win Rate: 65-75%
    """
    
    def __init__(self, config: Optional[Dict] = None):
        super().__init__(config)
        self.spread_width = self.config.get('spread_width', 100)
        self.min_trend_strength = self.config.get('min_trend_strength', -0.6)
        self.profit_target_percent = self.config.get('profit_target_percent', 75)
        self.stop_loss_percent = self.config.get('stop_loss_percent', 50)
        
    def _get_strategy_type(self) -> StrategyType:
        return StrategyType.BEAR_PUT_SPREAD
    
    def _get_strategy_name(self) -> str:
        return "Bear Put Spread"
    
    def _get_strategy_description(self) -> str:
        return "Bearish directional strategy. Buy ITM put, sell OTM put. Limited risk and reward."
    
    def is_suitable_for_regime(self, regime: MarketRegime) -> bool:
        """Works best in trending down markets"""
        return regime == MarketRegime.TRENDING_DOWN
    
    def generate_signal(self, data: Dict, config: Dict) -> Dict:
        """Generate Bear Put Spread signal"""
        spot_price = data.get('spot_price', 0)
        atm_strike = data.get('atm_strike', 0)
        trend_strength = data.get('trend_strength', 0)
        option_chain = data.get('option_chain', {})
        
        # Check trend strength (negative for bearish)
        if trend_strength > self.min_trend_strength:
            return self._neutral_signal(data)
        
        # Buy ITM put (1 strike above ATM)
        buy_strike = self._round_strike(atm_strike + 50)
        # Sell OTM put
        sell_strike = buy_strike - self.spread_width
        
        # Get prices
        buy_price = self._get_option_price(option_chain, buy_strike, 'PE')
        sell_price = self._get_option_price(option_chain, sell_strike, 'PE')
        
        net_debit = buy_price - sell_price
        
        if net_debit <= 0:
            return self._neutral_signal(data)
        
        max_profit = self.spread_width - net_debit
        max_loss = net_debit
        
        signal_strength = min(1.0, abs(trend_strength))
        
        return {
            'symbol': data.get('symbol', ''),
            'signal_type': 'BEAR_PUT_SPREAD',
            'signal_strength': signal_strength,
            'recommended_strikes': [buy_strike, sell_strike],
            'recommended_option_types': ['PE', 'PE'],
            'recommended_entry_prices': [buy_price, sell_price],
            'positions': ['BUY', 'SELL'],
            'net_debit': net_debit,
            'max_loss': max_loss,
            'max_profit': max_profit,
            'risk_reward_ratio': max_profit / max_loss if max_loss > 0 else 0,
            'probability_of_profit': 0.65,
            'stop_loss_price': net_debit * (1 + self.stop_loss_percent / 100),
            'take_profit_price': max_profit * (self.profit_target_percent / 100),
            'expiry_date': data.get('expiry_date'),
            'spot_price': spot_price,
            'breakeven': buy_strike - net_debit,
        }
    
    def calculate_position_size(self, capital: float, risk_percent: float) -> int:
        risk_amount = capital * (risk_percent / 100)
        max_loss_per_lot = self.spread_width / 2
        return max(1, int(risk_amount / max_loss_per_lot))
    
    def get_exit_conditions(self, entry_data: Dict, current_data: Dict) -> Dict:
        entry_debit = entry_data.get('net_debit', 0)
        current_value = current_data.get('current_position_value', entry_debit)
        max_profit = entry_data.get('max_profit', 0)
        
        if current_value >= max_profit * (self.profit_target_percent / 100):
            return {'should_exit': True, 'exit_reason': 'Profit target', 'exit_price': current_value}
        
        if current_value <= entry_debit * (1 - self.stop_loss_percent / 100):
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
