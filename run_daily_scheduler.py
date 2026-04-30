#!/usr/bin/env python3
"""
CuliFeed Daily Scheduler Runner
==============================

Main entry point for running the CuliFeed daily processing scheduler.
Handles initialization, startup, and graceful shutdown.
"""

import sys
import asyncio
import argparse
import logging
from pathlib import Path

# Add the project root to Python path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from culifeed.scheduler.hourly_scheduler import HourlyScheduler
from culifeed.config.settings import get_settings
from culifeed.utils.logging import setup_logger


class HourlySchedulerService:
    """
    Service wrapper for HourlyScheduler that handles continuous operation.
    """
    
    def __init__(self, scheduler: HourlyScheduler):
        self.scheduler = scheduler
        self.settings = get_settings()
        self.logger = setup_logger(
            name="culifeed.scheduler_service",
            level=self.settings.logging.level.value,
            log_file=self.settings.logging.file_path,
            console=self.settings.logging.console_logging
        )
        self.running = True
        
    async def run_service(self):
        """Run scheduler as a continuous service."""
        self.logger.info("Starting daily processing service")
        self.logger.info(f"Scheduled to run daily at {self.settings.processing.daily_run_hour}:00")
        
        while self.running:
            try:
                current_hour = asyncio.get_event_loop().time() // 3600 % 24
                target_hour = self.settings.processing.daily_run_hour
                
                # Use datetime for proper hour checking
                from datetime import datetime
                current_hour = datetime.now().hour
                
                # Check if it's time to run processing
                if current_hour == target_hour:
                    # Check if we already processed today
                    status = await self.scheduler.check_processing_status()
                    if not status.get('processed_today', False):
                        self.logger.info("Starting scheduled daily processing")
                        
                        result = await self.scheduler.run_daily_processing(dry_run=False)
                        
                        if result['success']:
                            self.logger.info("Daily processing completed successfully")
                            self.logger.info(f"Processed {result['channels_processed']} channels, {result['total_articles_processed']} articles")
                        else:
                            self.logger.error(f"Daily processing failed: {result.get('message', 'Unknown error')}")
                    else:
                        self.logger.debug("Processing already completed today")
                else:
                    self.logger.debug(f"Not time for processing (current: {current_hour}:xx, target: {target_hour}:00)")
                
                # Sleep for 30 minutes before checking again
                await asyncio.sleep(30 * 60)
                
            except KeyboardInterrupt:
                self.logger.info("Service interrupted by user")
                self.running = False
                break
            except Exception as e:
                self.logger.error(f"Service error: {e}", exc_info=True)
                # Sleep for 5 minutes before retrying on error
                await asyncio.sleep(5 * 60)
        
        self.logger.info("Daily processing service stopped")


async def main():
    """Main entry point for the scheduler service."""
    parser = argparse.ArgumentParser(description='CuliFeed Daily Scheduler')
    parser.add_argument('--dry-run', action='store_true', 
                       help='Simulate processing without sending messages')
    parser.add_argument('--check-status', action='store_true',
                       help='Check processing status and exit')
    parser.add_argument('--service', action='store_true',
                       help='Run as continuous service (for Docker/systemd)')
    parser.add_argument('--config', help='Configuration file path')
    parser.add_argument('--debug', action='store_true', help='Enable debug logging')
    
    args = parser.parse_args()
    
    # Load settings
    settings = get_settings()

    # Setup logging
    logger = setup_logger(
        name="culifeed.scheduler_runner",
        level="DEBUG" if args.debug else settings.logging.level.value,
        log_file=settings.logging.file_path,
        console=settings.logging.console_logging
    )

    logger.info("Starting CuliFeed Daily Scheduler...")

    try:
        # Initialize scheduler
        scheduler = HourlyScheduler()
        
        if args.check_status:
            # Status check mode
            logger.info("Checking processing status...")
            status = await scheduler.check_processing_status()
            print(f"Processing Status: {status['health_status']}")
            print(f"Last Success: {status.get('last_successful_run', 'Never')}")
            print(f"Recent Success Rate: {status.get('recent_success_rate', 0)}%")
            
            # Exit with appropriate code for monitoring systems
            sys.exit(0 if status['health_status'] == 'healthy' else 1)
            
        elif args.service:
            # Service mode - run continuously
            logger.info("Starting service mode...")
            print(f"🕐 CuliFeed Daily Scheduler Service starting...")
            print(f"📅 Scheduled to run daily at {settings.processing.daily_run_hour}:00")
            print("Press Ctrl+C to stop.")
            
            service = HourlySchedulerService(scheduler)
            await service.run_service()
            
        else:
            # One-time processing mode
            logger.info("Running one-time processing...")
            print("🔄 Running daily processing...")
            
            result = await scheduler.run_daily_processing(dry_run=args.dry_run)
            
            # Use enhanced formatting for detailed output
            formatted_summary = scheduler.format_processing_summary(result)
            print(formatted_summary)
            
            logger.info("Daily processing completed successfully" if result['success'] else f"Daily processing failed: {result.get('message', 'Unknown error')}")
            sys.exit(0 if result['success'] else 1)
                
    except KeyboardInterrupt:
        print("\n👋 Scheduler stopped by user")
        logger.info("Scheduler stopped by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        print(f"❌ Failed to start scheduler: {e}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())