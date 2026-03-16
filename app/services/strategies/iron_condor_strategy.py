"""
Iron Condor Strategy - Non-directional income strategy
Best for low volatility, range-bound markets
"""

from typing import Dict, Optional
from app.services.strategies.base_strategy import BaseStrategy, StrategyType, MarketRegime


class IronCondorStrategy(BaseStrategy):
    """
    Iron Condor: Sell OTM call spread + OTM put spread
    
    Entry:
    - Sell OTM call (e.g., +2 SD)
    - Buy further OTM call (e.g., +3 SD)
    - Sell OTM put (e.g., -2 SD)
    - Buy further OTM put (e.g., -3 SD)
    
    Profit: Collect premium if price stays in range
    Max Loss: Spread width - premium collected
    Win Rate: 70-80%
    """
    
    def __init__(self, config: Optional[Dict] = None):
        super().__init__(config)
        self.min_days_to_expiry = self.config.get('min_days_to_expiry', 7)
        self.max_days_to_expiry = self.config.get('max_days_to_expiry', 45)
        self.wing_width = self.config.get('wing_width', 100)  # Strike width
        self.min_credit = self.config.get('min_credit', 30)  # Minimum premium to collect
        self.profit_target_percent = self.config.get('profit_target_percent', 50)  # Close at 50% profit
        self.max_loss_percent = self.config.get('max_loss_percent', 200)  # Close at 2x loss
        
    def _get_strategy_type(self) -> StrategyType:
        return StrategyType.IRON_CONDOR
    
    def _get_strategy_name(self) -> str:
        return "Iron Condor"
    
    def _get_strategy_description(self) -> str:
        return "Non-directional income strategy. Profit from range-bound markets by selling OTM call and put spreads."
    
    def is_suitable_for_regime(self, regime: MarketRegime) -> bool:
        """Iron Condor works best in low volatility, range-bound markets"""
        return regime in [MarketRegime.LOW_VOLATILITY, MarketRegime.RANGE_BOUND]
    
    def generate_signal(self, data: Dict, config: Dict) -> Dict:
        """
        Generate Iron Condor signal
        
        Data should include:
        - spot_price: Current index price
        - atm_strike: ATM strike
        - option_chain: Full options chain with strikes, prices, IVs
        - vix: Current VIX level
        - days_to_expiry: Days until expiry
        """
        spot_price = data.get('spot_price', 0)
        atm_strike = data.get('atm_strike', 0)
        vix = data.get('vix', 20)
        days_to_expiry = data.get('days_to_expiry', 30)
        
        # Check if conditions are suitable
        if vix > 20:  # High volatility - not ideal for Iron Condor
            return self._neutral_signal(data)
        
        if days_to_expiry < self.min_days_to_expiry or days_to_expiry > self.max_days_to_expiry:
            return self._neutral_signal(data)
        
        # Calculate strikes (approximately 1 SD from ATM)
        # For NIFTY/SENSEX, 1 SD ≈ 2-3% of spot price
        one_sd = spot_price * 0.025  # 2.5% as approximation
        
        # Call side
        short_call_strike = self._round_strike(atm_strike + one_sd)
        long_call_strike = short_call_strike + self.wing_width
        
        # Put side
        short_put_strike = self._round_strike(atm_strike - one_sd)
        long_put_strike = short_put_strike - self.wing_width
        
        # Get option prices from chain
        option_chain = data.get('option_chain', {})
        
        short_call_price = self._get_option_price(option_chain, short_call_strike, 'CE')
        long_call_price = self._get_option_price(option_chain, long_call_strike, 'CE')
        short_put_price = self._get_option_price(option_chain, short_put_strike, 'PE')
        long_put_price = self._get_option_price(option_chain, long_put_strike, 'PE')
        
        # Calculate net credit
        net_credit = (short_call_price - long_call_price) + (short_put_price - long_put_price)
        
        if net_credit < self.min_credit:
            return self._neutral_signal(data)
        
        # Calculate max loss and profit
        max_loss = self.wing_width - net_credit
        max_profit = net_credit
        
        # Calculate probability of profit (rough estimate based on strikes)
        prob_of_profit = self._estimate_probability(spot_price, short_put_strike, short_call_strike)
        
        # Signal strength based on credit and probability
        signal_strength = min(1.0, (net_credit / self.min_credit) * prob_of_profit)
        
        return {
            'symbol': data.get('symbol', ''),
            'signal_type': 'IRON_CONDOR',
            'signal_strength': signal_strength,
            'recommended_strikes': [short_put_strike, long_put_strike, short_call_strike, long_call_strike],
            'recommended_option_types': ['PE', 'PE', 'CE', 'CE'],
            'recommended_entry_prices': [short_put_price, long_put_price, short_call_price, long_call_price],
            'positions': ['SELL', 'BUY', 'SELL', 'BUY'],
            'net_credit': net_credit,
            'max_loss': max_loss,
            'max_profit': max_profit,
            'risk_reward_ratio': max_profit / max_loss if max_loss > 0 else 0,
            'probability_of_profit': prob_of_profit,
            'stop_loss_price': net_credit * (1 + self.max_loss_percent / 100),
            'take_profit_price': net_credit * (1 - self.profit_target_percent / 100),
            'expiry_date': data.get('expiry_date'),
            'spot_price': spot_price,
            'breakeven_lower': short_put_strike - net_credit,
            'breakeven_upper': short_call_strike + net_credit,
        }
    
    def calculate_position_size(self, capital: float, risk_percent: float) -> int:
        """
        Calculate position size based on capital and risk
        For Iron Condor, risk is the max loss per spread
        """
        risk_amount = capital * (risk_percent / 100)
        max_loss_per_lot = self.wing_width - self.min_credit
        
        if max_loss_per_lot <= 0:
            return 0
            
        lots = int(risk_amount / max_loss_per_lot)
        return max(1, lots)
    
    def get_exit_conditions(self, entry_data: Dict, current_data: Dict) -> Dict:
        """
        Exit conditions for Iron Condor:
        1. Profit target reached (50% of max profit)
        2. Stop loss hit (2x max loss)
        3. Price breaches short strike
        4. 7 days to expiry (close early to avoid gamma risk)
        """
        entry_credit = entry_data.get('net_credit', 0)
        current_price = current_data.get('current_position_value', entry_credit)
        days_to_expiry = current_data.get('days_to_expiry', 30)
        spot_price = current_data.get('spot_price', 0)
        
        short_put = entry_data.get('recommended_strikes', [])[0]
        short_call = entry_data.get('recommended_strikes', [])[2]
        
        # Profit target
        if current_price <= entry_credit * (1 - self.profit_target_percent / 100):
            return {
                'should_exit': True,
                'exit_reason': 'Profit target reached',
                'exit_price': current_price
            }
        
        # Stop loss
        if current_price >= entry_credit * (1 + self.max_loss_percent / 100):
            return {
                'should_exit': True,
                'exit_reason': 'Stop loss hit',
                'exit_price': current_price
            }
        
        # Price breach
        if spot_price <= short_put or spot_price >= short_call:
            return {
                'should_exit': True,
                'exit_reason': 'Price breached short strike',
                'exit_price': current_price
            }
        
        # Close before expiry
        if days_to_expiry <= 7:
            return {
                'should_exit': True,
                'exit_reason': 'Approaching expiry',
                'exit_price': current_price
            }
        
        return {
            'should_exit': False,
            'exit_reason': '',
            'exit_price': 0
        }
    
    def _round_strike(self, price: float) -> float:
        """Round to nearest strike (50 for NIFTY, 100 for SENSEX)"""
        return round(price / 50) * 50
    
    def _get_option_price(self, option_chain: Dict, strike: float, option_type: str) -> float:
        """Get option price from chain"""
        key = f"{strike}_{option_type}"
        return option_chain.get(key, {}).get('ltp', 0)
    
    def _estimate_probability(self, spot: float, lower_strike: float, upper_strike: float) -> float:
        """Estimate probability of profit (price staying in range)"""
        range_width = upper_strike - lower_strike
        spot_position = (spot - lower_strike) / range_width
        
        # Higher probability if spot is centered
        centered_score = 1 - abs(spot_position - 0.5) * 2
        return max(0.5, min(0.9, centered_score))
    
    def _neutral_signal(self, data: Dict) -> Dict:
        """Return neutral signal when conditions not met"""
        return {
            'symbol': data.get('symbol', ''),
            'signal_type': 'NEUTRAL',
            'signal_strength': 0,
            'recommended_strikes': [],
            'recommended_option_types': [],
            'recommended_entry_prices': [],
            'positions': [],
            'net_credit': 0,
            'max_loss': 0,
            'max_profit': 0,
            'probability_of_profit': 0,
        }
