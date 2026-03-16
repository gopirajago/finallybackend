"""
Background scheduler for automated strategy signal generation
"""

import asyncio
import logging
from datetime import datetime, time
from typing import Dict, List
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal
from app.models.strategy_signal import StrategyConfig
from app.models.user import User
from app.services.skew_hunter import SkewHunterStrategy
from app.services.strategy_data_fetcher import StrategyDataFetcher

logger = logging.getLogger(__name__)


class StrategyScheduler:
    """Manages automated strategy execution"""
    
    def __init__(self):
        self.scheduler = AsyncIOScheduler()
        self.strategy = SkewHunterStrategy()
        self.is_running = False
        
    def start(self):
        """Start the scheduler"""
        if self.is_running:
            logger.warning("Scheduler already running")
            return
            
        # Run every 3 minutes during market hours (10:15 AM - 2:15 PM IST)
        # Cron: minute, hour, day, month, day_of_week
        self.scheduler.add_job(
            self.scan_and_generate_signals,
            CronTrigger(
                minute='*/3',  # Every 3 minutes
                hour='10-14',  # 10 AM to 2 PM
                day_of_week='mon-fri'  # Monday to Friday
            ),
            id='strategy_signal_scan',
            replace_existing=True,
            max_instances=1
        )
        
        self.scheduler.start()
        self.is_running = True
        logger.info("Strategy scheduler started - scanning every 3 minutes during market hours")
        
    def stop(self):
        """Stop the scheduler"""
        if not self.is_running:
            return
            
        self.scheduler.shutdown()
        self.is_running = False
        logger.info("Strategy scheduler stopped")
        
    async def scan_and_generate_signals(self):
        """Scan all enabled strategies and generate signals"""
        logger.info("Starting strategy signal scan...")
        
        try:
            async with AsyncSessionLocal() as db:
                # Get all enabled strategy configs
                result = await db.execute(
                    select(StrategyConfig, User)
                    .join(User, StrategyConfig.user_id == User.id)
                    .where(StrategyConfig.is_enabled == True)
                )
                configs = result.all()
                
                if not configs:
                    logger.info("No enabled strategies found")
                    return
                
                logger.info(f"Found {len(configs)} enabled strategies")
                
                # Process each enabled strategy
                for config, user in configs:
                    try:
                        await self.process_strategy(db, config, user)
                    except Exception as e:
                        logger.error(f"Error processing strategy for user {user.id}: {e}", exc_info=True)
                        
        except Exception as e:
            logger.error(f"Error in strategy scan: {e}", exc_info=True)
            
    async def process_strategy(self, db: AsyncSession, config: StrategyConfig, user: User):
        """Process a single strategy configuration"""
        # Check if we're within trading hours
        now = datetime.now()
        current_time = now.time()
        
        start_time = time.fromisoformat(config.start_time)
        end_time = time.fromisoformat(config.end_time)
        
        if not (start_time <= current_time <= end_time):
            logger.debug(f"Outside trading hours for user {user.id}")
            return
            
        logger.info(f"Processing strategy for user {user.id} - symbols: {config.symbols}")
        
        # Fetch data and generate signals for each symbol
        data_fetcher = StrategyDataFetcher(user, db)
        
        for symbol in config.symbols:
            try:
                # Fetch options data from Groww
                options_data = await data_fetcher.fetch_options_data(symbol)
                
                if not options_data:
                    logger.warning(f"No options data for {symbol}")
                    continue
                
                # Generate signal using the strategy
                signal_result = self.strategy.generate_signal(options_data, config)
                
                if signal_result['signal_type'] != 'NEUTRAL':
                    logger.info(f"Signal generated for {symbol}: {signal_result['signal_type']}")
                    
                    # Save signal to database
                    await data_fetcher.save_signal(signal_result, config)
                    
                    # Send notification if enabled
                    if config.send_signal_alerts:
                        await self.send_notification(user, signal_result)
                        
            except Exception as e:
                logger.error(f"Error processing {symbol} for user {user.id}: {e}", exc_info=True)
                
    async def send_notification(self, user: User, signal: Dict):
        """Send notification about new signal (placeholder for future implementation)"""
        # TODO: Implement email/SMS/push notifications
        logger.info(f"Notification for user {user.id}: {signal['signal_type']} on {signal['symbol']}")


# Global scheduler instance
strategy_scheduler = StrategyScheduler()
