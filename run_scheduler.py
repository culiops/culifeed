#!/usr/bin/env python3
"""
CuliFeed Scheduler Runner
=========================

Main entry point for running the CuliFeed processing scheduler.
Handles initialization, startup, and graceful shutdown.
"""

import sys
import asyncio
import argparse
import logging
from datetime import datetime, timedelta
from pathlib import Path

# Add the project root to Python path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from culifeed.scheduler.hourly_scheduler import HourlyScheduler
from culifeed.config.settings import get_settings
from culifeed.utils.logging import setup_logger


class SchedulerService:
    """
    Service wrapper for HourlyScheduler that runs processing on a fixed interval.
    """

    def __init__(self, scheduler):
        self.scheduler = scheduler
        self.settings = get_settings()
        self.logger = logging.getLogger("culifeed.scheduler_service")
        self.running = True
        self.last_processed_at: datetime | None = None

    def _should_run(self, now: datetime) -> bool:
        if self.last_processed_at is None:
            return True
        interval = timedelta(hours=self.settings.processing.processing_interval_hours)
        return (now - self.last_processed_at) >= interval

    async def run_service(self):
        interval_h = self.settings.processing.processing_interval_hours
        self.logger.info(f"Starting scheduler service: every {interval_h}h")

        while self.running:
            try:
                now = datetime.now()
                if self._should_run(now):
                    self.logger.info("Starting scheduled processing run")
                    result = await self.scheduler.run_daily_processing(dry_run=False)
                    self.last_processed_at = now
                    if result.get("success"):
                        self.logger.info(
                            f"Run complete: {result.get('channels_processed', 0)} channels, "
                            f"{result.get('total_articles_processed', 0)} articles"
                        )
                    else:
                        self.logger.error(
                            f"Run failed: {result.get('message', 'Unknown error')}"
                        )

                # Wake every 5 min to re-check the interval condition
                await asyncio.sleep(5 * 60)

            except KeyboardInterrupt:
                self.logger.info("Service interrupted by user")
                self.running = False
                break
            except Exception as e:
                self.logger.error(f"Service loop error: {e}", exc_info=True)
                await asyncio.sleep(5 * 60)

        self.logger.info("Scheduler service stopped")


async def main():
    """Main entry point for the scheduler service."""
    parser = argparse.ArgumentParser(description='CuliFeed Scheduler')
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

    # Setup logging on the "culifeed" parent so all child loggers
    # (culifeed.scheduler, culifeed.pipeline, culifeed.ai_manager, ...)
    # inherit handlers via standard propagation.
    setup_logger(
        name="culifeed",
        level="DEBUG" if args.debug else settings.logging.level.value,
        log_file=settings.logging.file_path,
        console=settings.logging.console_logging
    )
    logger = logging.getLogger("culifeed.scheduler_runner")

    logger.info("Starting CuliFeed Scheduler...")

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
            print(f"CuliFeed Scheduler Service starting...")
            print(f"Running every {settings.processing.processing_interval_hours}h")
            print("Press Ctrl+C to stop.")

            service = SchedulerService(scheduler)
            await service.run_service()
            
        else:
            # One-time processing mode
            logger.info("Running one-time processing...")
            print("Running processing...")
            
            result = await scheduler.run_daily_processing(dry_run=args.dry_run)
            
            # Use enhanced formatting for detailed output
            formatted_summary = scheduler.format_processing_summary(result)
            print(formatted_summary)
            
            logger.info("Processing completed successfully" if result['success'] else f"Processing failed: {result.get('message', 'Unknown error')}")
            sys.exit(0 if result['success'] else 1)
                
    except KeyboardInterrupt:
        print("\nScheduler stopped by user")
        logger.info("Scheduler stopped by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        print(f"Failed to start scheduler: {e}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())