"""
Straddle/Strangle Strategy - Volatility play
Best for high volatility events and big market moves
"""

from typing import Dict, Optional
from app.services.strategies.base_strategy import BaseStrategy, StrategyType, MarketRegime


class StraddleStrategy(BaseStrategy):
    """
    Straddle: Buy ATM call + ATM put
    Strangle: Buy OTM call + OTM put (cheaper version)
    
    Entry:
    - Buy ATM/OTM call
    - Buy ATM/OTM put
    
    Profit: Big move in either direction
    Max Loss: Total premium paid
    Win Rate: 50-60%
    """
    
    def __init__(self, config: Optional[Dict] = None):
        super().__init__(config)
        self.use_strangle = self.config.get('use_strangle', False)  # False = Straddle, True = Strangle
        self.otm_distance = self.config.get('otm_distance', 100)  # For strangle
        self.min_vix = self.config.get('min_vix', 18)  # Minimum VIX for entry
        self.max_premium = self.config.get('max_premium', 200)  # Max premium to pay
        self.profit_target_percent = self.config.get('profit_target_percent', 100)  # 100% profit
        self.stop_loss_percent = self.config.get('stop_loss_percent', 50)  # 50% loss
        
    def _get_strategy_type(self) -> StrategyType:
        return StrategyType.STRANGLE if self.use_strangle else StrategyType.STRADDLE
    
    def _get_strategy_name(self) -> str:
        return "Strangle" if self.use_strangle else "Straddle"
    
    def _get_strategy_description(self) -> str:
        if self.use_strangle:
            return "Volatility strategy. Buy OTM call and put. Profit from big moves in either direction."
        return "Volatility strategy. Buy ATM call and put. Profit from big moves in either direction."
    
    def is_suitable_for_regime(self, regime: MarketRegime) -> bool:
        """Straddle/Strangle works best in high volatility"""
        return regime == MarketRegime.HIGH_VOLATILITY
    
    def generate_signal(self, data: Dict, config: Dict) -> Dict:
        """
        Generate Straddle/Strangle signal
        
        Best before:
        - RBI policy announcements
        - Budget day
        - Election results
        - Major earnings
        - Global events
        """
        spot_price = data.get('spot_price', 0)
        atm_strike = data.get('atm_strike', 0)
        vix = data.get('vix', 15)
        option_chain = data.get('option_chain', {})
        
        # Check if VIX is high enough
        if vix < self.min_vix:
            return self._neutral_signal(data)
        
        # Determine strikes
        if self.use_strangle:
            call_strike = self._round_strike(atm_strike + self.otm_distance)
            put_strike = self._round_strike(atm_strike - self.otm_distance)
        else:
            call_strike = atm_strike
            put_strike = atm_strike
        
        # Get option prices
        call_price = self._get_option_price(option_chain, call_strike, 'CE')
        put_price = self._get_option_price(option_chain, put_strike, 'PE')
        
        total_premium = call_price + put_price
        
        # Check if premium is acceptable
        if total_premium > self.max_premium or total_premium == 0:
            return self._neutral_signal(data)
        
        # Calculate breakevens
        upper_breakeven = call_strike + total_premium
        lower_breakeven = put_strike - total_premium
        
        # Calculate required move percentage
        required_move_up = ((upper_breakeven - spot_price) / spot_price) * 100
        required_move_down = ((spot_price - lower_breakeven) / spot_price) * 100
        
        # Signal strength based on VIX and premium
        vix_score = min(1.0, (vix - self.min_vix) / 10)  # Higher VIX = stronger signal
        premium_score = 1 - (total_premium / self.max_premium)  # Lower premium = stronger signal
        signal_strength = (vix_score + premium_score) / 2
        
        return {
            'symbol': data.get('symbol', ''),
            'signal_type': 'STRANGLE' if self.use_strangle else 'STRADDLE',
            'signal_strength': signal_strength,
            'recommended_strikes': [call_strike, put_strike],
            'recommended_option_types': ['CE', 'PE'],
            'recommended_entry_prices': [call_price, put_price],
            'positions': ['BUY', 'BUY'],
            'total_premium': total_premium,
            'max_loss': total_premium,
            'max_profit': float('inf'),  # Unlimited
            'risk_reward_ratio': float('inf'),
            'probability_of_profit': 0.5,  # 50-50 for big move
            'stop_loss_price': total_premium * (1 + self.stop_loss_percent / 100),
            'take_profit_price': total_premium * (1 - self.profit_target_percent / 100),
            'expiry_date': data.get('expiry_date'),
            'spot_price': spot_price,
            'breakeven_upper': upper_breakeven,
            'breakeven_lower': lower_breakeven,
            'required_move_up_percent': required_move_up,
            'required_move_down_percent': required_move_down,
            'vix': vix,
        }
    
    def calculate_position_size(self, capital: float, risk_percent: float) -> int:
        """
        For Straddle/Strangle, risk is the total premium paid
        """
        risk_amount = capital * (risk_percent / 100)
        max_loss_per_lot = self.max_premium
        
        if max_loss_per_lot <= 0:
            return 0
            
        lots = int(risk_amount / max_loss_per_lot)
        return max(1, lots)
    
    def get_exit_conditions(self, entry_data: Dict, current_data: Dict) -> Dict:
        """
        Exit conditions:
        1. Profit target (100% profit)
        2. Stop loss (50% loss)
        3. Event passed (if entered for specific event)
        4. Time decay (close if no move after 50% time passed)
        """
        entry_premium = entry_data.get('total_premium', 0)
        current_value = current_data.get('current_position_value', entry_premium)
        days_to_expiry = current_data.get('days_to_expiry', 30)
        entry_days = entry_data.get('days_to_expiry', 30)
        
        # Profit target
        profit = current_value - entry_premium
        if profit >= entry_premium * (self.profit_target_percent / 100):
            return {
                'should_exit': True,
                'exit_reason': 'Profit target reached',
                'exit_price': current_value
            }
        
        # Stop loss
        loss = entry_premium - current_value
        if loss >= entry_premium * (self.stop_loss_percent / 100):
            return {
                'should_exit': True,
                'exit_reason': 'Stop loss hit',
                'exit_price': current_value
            }
        
        # Time decay - if 50% time passed and no significant move
        time_passed_percent = ((entry_days - days_to_expiry) / entry_days) * 100
        if time_passed_percent > 50 and abs(profit / entry_premium) < 0.2:
            return {
                'should_exit': True,
                'exit_reason': 'Time decay - no significant move',
                'exit_price': current_value
            }
        
        return {
            'should_exit': False,
            'exit_reason': '',
            'exit_price': 0
        }
    
    def _round_strike(self, price: float) -> float:
        """Round to nearest strike"""
        return round(price / 50) * 50
    
    def _get_option_price(self, option_chain: Dict, strike: float, option_type: str) -> float:
        """Get option price from chain"""
        key = f"{strike}_{option_type}"
        return option_chain.get(key, {}).get('ltp', 0)
    
    def _neutral_signal(self, data: Dict) -> Dict:
        """Return neutral signal"""
        return {
            'symbol': data.get('symbol', ''),
            'signal_type': 'NEUTRAL',
            'signal_strength': 0,
            'recommended_strikes': [],
            'recommended_option_types': [],
            'recommended_entry_prices': [],
            'positions': [],
            'total_premium': 0,
            'max_loss': 0,
            'max_profit': 0,
        }
