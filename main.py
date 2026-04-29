#!/usr/bin/env python3
"""
CuliFeed - Smart Content Curation System
========================================

Main application entry point with CLI interface for management and testing.

Usage:
    python main.py --help                    # Show all commands
    python main.py --check-config            # Validate configuration
    python main.py --test-foundation         # Test foundation components
    python main.py --init-db                 # Initialize database
    python main.py --test-feeds              # Test RSS feed connectivity
    python main.py --daily-process           # Run daily processing pipeline
    python main.py --start-bot               # Start Telegram bot service
    python main.py --full-test               # Run end-to-end system test
    python main.py --health-check            # Check system health status
"""

import sys
import asyncio
import logging
from pathlib import Path
from typing import Optional

import click
from rich.console import Console
from rich.table import Table
from rich import print as rprint

# Add project root to Python path
sys.path.insert(0, str(Path(__file__).parent))

from culifeed.config.settings import get_settings
from culifeed.database.schema import DatabaseSchema
from culifeed.database.connection import get_db_manager
from culifeed.utils.logging import configure_application_logging, get_logger_for_component
from culifeed.utils.exceptions import CuliFeedError, handle_exception

console = Console()
logger = logging.getLogger(__name__)


@click.group(invoke_without_command=True)
@click.option('--config', '-c', help='Configuration file path')
@click.option('--debug', is_flag=True, help='Enable debug mode')
@click.pass_context
def cli(ctx, config, debug):
    """CuliFeed - AI-powered RSS content curation system."""
    ctx.ensure_object(dict)
    ctx.obj['config_path'] = config
    ctx.obj['debug'] = debug
    
    if ctx.invoked_subcommand is None:
        # Show help if no subcommand provided
        click.echo(ctx.get_help())


@cli.command()
@click.pass_context
def check_config(ctx):
    """Validate configuration file and environment variables."""
    console.print("[bold blue]🔧 Checking CuliFeed Configuration[/bold blue]")
    
    try:
        settings = get_settings()
        
        # Configuration validation table
        table = Table(title="Configuration Status")
        table.add_column("Component", style="cyan")
        table.add_column("Status", style="green") 
        table.add_column("Details")
        
        # Check each component
        checks = [
            ("Database", _check_database_config, settings),
            ("Logging", _check_logging_config, settings),
            ("Telegram Bot", _check_telegram_config, settings),
            ("AI Providers", _check_ai_config, settings),
            ("Processing", _check_processing_config, settings),
        ]
        
        all_passed = True
        for name, check_func, config in checks:
            try:
                status, details = check_func(config)
                table.add_row(name, "✅ Valid" if status else "❌ Invalid", details)
                if not status:
                    all_passed = False
            except Exception as e:
                table.add_row(name, "❌ Error", str(e))
                all_passed = False
        
        console.print(table)
        
        if all_passed:
            console.print("[bold green]✅ All configuration checks passed![/bold green]")
            sys.exit(0)
        else:
            console.print("[bold red]❌ Configuration validation failed[/bold red]")
            sys.exit(1)
            
    except Exception as e:
        console.print(f"[bold red]❌ Configuration error: {e}[/bold red]")
        sys.exit(1)


@cli.command()
@click.pass_context
def test_foundation(ctx):
    """Test foundation components (database, logging, configuration)."""
    console.print("[bold blue]🧪 Testing CuliFeed Foundation[/bold blue]")
    
    try:
        # Initialize settings and logging
        settings = get_settings()
        configure_application_logging(
            log_level="DEBUG" if ctx.obj.get('debug') else settings.logging.level.value,
            log_file=settings.logging.file_path,
            enable_console=settings.logging.console_logging,
            structured_logging=settings.logging.structured_logging
        )
        
        logger = get_logger_for_component('foundation')
        
        tests = []
        
        # Test 1: Database Schema Creation
        console.print("\n[yellow]📊 Testing Database Schema...[/yellow]")
        try:
            schema = DatabaseSchema(settings.database.path)
            schema.create_tables()
            
            if schema.verify_schema():
                tests.append(("Database Schema", True, "All tables created and verified"))
                console.print("  ✅ Database schema created successfully")
            else:
                tests.append(("Database Schema", False, "Schema verification failed"))
                console.print("  ❌ Database schema verification failed")
        except Exception as e:
            tests.append(("Database Schema", False, str(e)))
            console.print(f"  ❌ Database error: {e}")
        
        # Test 2: Database Connection
        console.print("\n[yellow]🔌 Testing Database Connection...[/yellow]")
        try:
            db_manager = get_db_manager(settings.database.path)
            info = db_manager.get_database_info()
            tests.append(("Database Connection", True, f"Connected, {info['total_connections']} connections"))
            console.print(f"  ✅ Database connected - {info['database_size_mb']:.1f}MB")
        except Exception as e:
            tests.append(("Database Connection", False, str(e)))
            console.print(f"  ❌ Connection error: {e}")
        
        # Test 3: Logging System
        console.print("\n[yellow]📝 Testing Logging System...[/yellow]")
        try:
            logger.info("Foundation test log message")
            logger.debug("Debug level log message")
            logger.warning("Warning level log message")
            tests.append(("Logging System", True, f"Level: {settings.logging.level}"))
            console.print("  ✅ Logging system working")
        except Exception as e:
            tests.append(("Logging System", False, str(e)))
            console.print(f"  ❌ Logging error: {e}")
        
        # Test 4: Configuration Loading
        console.print("\n[yellow]⚙️ Testing Configuration...[/yellow]")
        try:
            fallback_providers = settings.get_ai_fallback_providers()
            effective_log_level = settings.get_effective_log_level()
            tests.append(("Configuration", True, f"AI providers: {len(fallback_providers)}"))
            console.print(f"  ✅ Configuration loaded - {len(fallback_providers)} AI providers available")
        except Exception as e:
            tests.append(("Configuration", False, str(e)))
            console.print(f"  ❌ Configuration error: {e}")
        
        # Results Summary
        console.print("\n[bold blue]📋 Foundation Test Results[/bold blue]")
        
        results_table = Table()
        results_table.add_column("Test", style="cyan")
        results_table.add_column("Status", style="green")
        results_table.add_column("Details")
        
        passed_count = 0
        for test_name, passed, details in tests:
            status = "✅ PASSED" if passed else "❌ FAILED"
            results_table.add_row(test_name, status, details)
            if passed:
                passed_count += 1
        
        console.print(results_table)
        
        if passed_count == len(tests):
            console.print(f"[bold green]🎉 All {len(tests)} foundation tests passed![/bold green]")
            sys.exit(0)
        else:
            console.print(f"[bold red]❌ {len(tests) - passed_count} out of {len(tests)} tests failed[/bold red]")
            sys.exit(1)
            
    except Exception as e:
        console.print(f"[bold red]❌ Foundation test error: {e}[/bold red]")
        sys.exit(1)


@cli.command()
@click.pass_context
def init_db(ctx):
    """Initialize database with schema."""
    console.print("[bold blue]🗄️ Initializing CuliFeed Database[/bold blue]")
    
    try:
        settings = get_settings()
        schema = DatabaseSchema(settings.database.path)
        
        # Create database directory if it doesn't exist
        Path(settings.database.path).parent.mkdir(parents=True, exist_ok=True)
        
        # Create tables
        schema.create_tables()
        
        # Verify schema
        if schema.verify_schema():
            console.print("[bold green]✅ Database initialized successfully![/bold green]")
            
            # Show database info
            db_manager = get_db_manager(settings.database.path)
            info = db_manager.get_database_info()
            
            info_table = Table(title="Database Information")
            info_table.add_column("Property", style="cyan")
            info_table.add_column("Value", style="green")
            
            info_table.add_row("Database Path", settings.database.path)
            info_table.add_row("Size", f"{info['database_size_mb']:.2f} MB")
            info_table.add_row("Page Size", f"{info['page_size']} bytes")
            info_table.add_row("Connection Pool", f"{info['total_connections']} connections")
            
            console.print(info_table)
            
        else:
            console.print("[bold red]❌ Database schema verification failed[/bold red]")
            sys.exit(1)
            
    except Exception as e:
        console.print(f"[bold red]❌ Database initialization error: {e}[/bold red]")
        sys.exit(1)


@cli.command()
def create_config():
    """Create configuration file from example."""
    import shutil

    env_example_path = Path(".env.example")
    env_path = Path(".env")

    if not env_example_path.exists():
        console.print("[bold red]❌ .env.example file not found[/bold red]")
        sys.exit(1)

    if env_path.exists():
        if not click.confirm(f"Config file {env_path} already exists. Overwrite?"):
            console.print("[yellow]Configuration creation cancelled[/yellow]")
            return

    try:
        shutil.copy2(env_example_path, env_path)

        console.print(f"[bold green]✅ Configuration file created: {env_path}[/bold green]")
        console.print("[yellow]📝 Don't forget to:[/yellow]")
        console.print("  1. Fill in your actual API keys in .env")
        console.print("  2. Adjust settings as needed for your environment")
        console.print("  3. Keep your .env file secure and don't commit it to git")

    except Exception as e:
        console.print(f"[bold red]❌ Error creating config: {e}[/bold red]")
        sys.exit(1)


@cli.command()
@click.option('--dry-run', is_flag=True, help='Show what would be cleaned without deleting')
def cleanup(dry_run):
    """Clean up old data and optimize database."""
    console.print("[bold blue]🧹 CuliFeed Database Cleanup[/bold blue]")

    try:
        settings = get_settings()
        db_manager = get_db_manager(settings.database.path)

        if dry_run:
            console.print("[yellow]📋 Dry run mode - no changes will be made[/yellow]")

        # Get initial database info
        initial_info = db_manager.get_database_info()
        console.print(f"Initial database size: {initial_info['database_size_mb']:.2f} MB")

        if not dry_run:
            # Clean up old data
            deleted_count = db_manager.cleanup_old_data(settings.database.cleanup_days)
            console.print(f"Deleted {deleted_count} old records")

            # Vacuum database
            db_manager.vacuum_database()

            # Update statistics
            db_manager.analyze_database()

            # Get final database info
            final_info = db_manager.get_database_info()
            console.print(f"Final database size: {final_info['database_size_mb']:.2f} MB")

            space_saved = initial_info['database_size_mb'] - final_info['database_size_mb']
            console.print(f"[bold green]✅ Cleanup complete! Saved {space_saved:.2f} MB[/bold green]")
        else:
            console.print("[yellow]Use --cleanup (without --dry-run) to perform actual cleanup[/yellow]")

    except Exception as e:
        console.print(f"[bold red]❌ Cleanup error: {e}[/bold red]")
        sys.exit(1)


@cli.command()
@click.argument('url')
@click.option('--chat-id', default='test_chat', help='Chat ID for testing (default: test_chat)')
def fetch_feed(url, chat_id):
    """Manually fetch and parse a single RSS feed."""
    console.print(f"[bold blue]📡 Fetching RSS Feed: {url}[/bold blue]")

    async def run_fetch():
        try:
            from culifeed.services.manual_processing_service import ManualProcessingService

            settings = get_settings()
            db_manager = get_db_manager(settings.database.path)
            service = ManualProcessingService(db_manager)

            console.print(f"🔍 Fetching content from: {url}")

            result = await service.fetch_single_feed(url)

            if not result.success:
                console.print(f"[bold red]❌ {result.error_message}[/bold red]")
                sys.exit(1)

            # Display results
            console.print(f"[bold green]✅ Feed fetched successfully![/bold green]")

            info_table = Table(title="Feed Information")
            info_table.add_column("Property", style="cyan")
            info_table.add_column("Value", style="green")

            info_table.add_row("Title", result.title or "Unknown")
            description = result.description or "None"
            if len(description) > 100:
                description = description[:97] + "..."
            info_table.add_row("Description", description)
            info_table.add_row("Articles Found", str(result.article_count))
            info_table.add_row("Feed URL", url)

            console.print(info_table)

            # Show sample articles
            if result.sample_articles:
                console.print(f"\n[bold blue]📰 Sample Articles (showing first 3):[/bold blue]")
                for i, article in enumerate(result.sample_articles, 1):
                    console.print(f"\n{i}. [bold]{article['title']}[/bold]")
                    console.print(f"   📅 Published: {article['published'] or 'No date'}")
                    console.print(f"   🔗 Link: {article['link']}")
                    if article['content_preview']:
                        console.print(f"   📝 Content: {article['content_preview']}")
                    else:
                        console.print(f"   📝 Content: No content available")

        except Exception as e:
            console.print(f"[bold red]❌ Feed fetch error: {e}[/bold red]")
            sys.exit(1)

    # Run the async function
    asyncio.run(run_fetch())


@cli.command()
@click.option('--feeds', multiple=True, help='RSS feed URLs to process (can specify multiple)')
@click.option('--chat-id', default='test_chat', help='Chat ID for testing (default: test_chat)')
@click.option('--max-concurrent', default=3, help='Maximum concurrent feed fetching (default: 3)')
def process_feeds(feeds, chat_id, max_concurrent):
    """Process multiple RSS feeds with async fetching."""

    async def run_processing():
        try:
            from culifeed.services.manual_processing_service import ManualProcessingService

            settings = get_settings()
            db_manager = get_db_manager(settings.database.path)
            service = ManualProcessingService(db_manager)

            if feeds:
                # Process specific feeds (not implemented in service yet - would need enhancement)
                console.print(f"[bold blue]🔄 Processing {len(feeds)} specified RSS feeds[/bold blue]")
                console.print("[yellow]⚠️ Specific feed processing will use default batch processing for now[/yellow]")
                result = await service.process_default_test_feeds()
            else:
                # Use default test feeds
                console.print("[yellow]📋 No feeds specified, using default test feeds[/yellow]")
                console.print(f"[bold blue]🔄 Processing default RSS feeds[/bold blue]")
                result = await service.process_default_test_feeds()

            # Display results
            results_table = Table(title="Feed Processing Results")
            results_table.add_column("Feed", style="cyan")
            results_table.add_column("Status", style="green")
            results_table.add_column("Articles", style="yellow")
            results_table.add_column("Details")

            for feed_result in result.feed_results:
                url = feed_result['url']
                status = "✅ Success" if feed_result['success'] else "❌ Failed"
                articles = str(feed_result['article_count'])
                details = "Processed successfully" if feed_result['success'] else feed_result['error']

                results_table.add_row(
                    url[:50] + "..." if len(url) > 50 else url,
                    status,
                    articles,
                    details[:50] + "..." if len(details) > 50 else details
                )

            console.print(results_table)
            console.print(f"\n[bold blue]📊 Summary: {result.successful_feeds} successful, {result.failed_feeds} failed[/bold blue]")
            console.print(f"⏱️ Processing time: {result.processing_time_seconds:.2f} seconds")

            if result.failed_feeds > 0:
                sys.exit(1)

        except Exception as e:
            console.print(f"[bold red]❌ Feed processing error: {e}[/bold red]")
            sys.exit(1)

    # Run the async function
    asyncio.run(run_processing())


@cli.command()
@click.option('--chat-id', default='test_chat', help='Chat ID for testing (default: test_chat)')
def test_pipeline(chat_id):
    """Test the complete feed processing pipeline end-to-end."""
    console.print(f"[bold blue]🧪 Testing Complete Processing Pipeline[/bold blue]")

    async def run_tests():
        try:
            from culifeed.services.manual_processing_service import ManualProcessingService

            settings = get_settings()
            db_manager = get_db_manager(settings.database.path)
            service = ManualProcessingService(db_manager)

            console.print(f"🔍 Testing with chat_id: {chat_id}")

            result = await service.run_pipeline_tests(chat_id)

            # Display results
            console.print(f"\n[bold blue]📊 Pipeline Test Results: {result.passed_tests}/{result.total_tests} passed[/bold blue]")

            for test_result in result.test_results:
                status = "✅" if test_result['success'] else "❌"
                console.print(f"{status} {test_result['name']}: {test_result['details']}")

            if result.passed_tests == result.total_tests:
                console.print("[bold green]🎉 All pipeline tests passed![/bold green]")
            else:
                console.print(f"[bold red]❌ {result.failed_tests} test(s) failed[/bold red]")
                sys.exit(1)

        except Exception as e:
            console.print(f"[bold red]❌ Pipeline test error: {e}[/bold red]")
            sys.exit(1)

    # Run the async function
    asyncio.run(run_tests())


@cli.command()
@click.option('--chat-id', help='Filter by specific chat ID')
def show_feeds(chat_id):
    """Show all feeds in the database with their status."""
    console.print("[bold blue]📊 Feed Status Report[/bold blue]")

    try:
        from culifeed.storage.feed_repository import FeedRepository
        settings = get_settings()
        db_manager = get_db_manager(settings.database.path)
        feed_repo = FeedRepository(db_manager)

        if chat_id:
            feeds = feed_repo.get_feeds_for_chat(chat_id, active_only=False)
            console.print(f"Feeds for chat {chat_id}: {len(feeds)}")
        else:
            feeds = feed_repo.get_all_active_feeds()
            console.print(f"Total active feeds: {len(feeds)}")

        if not feeds:
            console.print("[yellow]⚠️ No feeds found in database[/yellow]")
            return

        feeds_table = Table(title="RSS Feeds")
        feeds_table.add_column("Status", style="green")
        feeds_table.add_column("Title", style="cyan")
        feeds_table.add_column("URL", style="blue")
        feeds_table.add_column("Chat", style="yellow")
        feeds_table.add_column("Errors", style="red")
        feeds_table.add_column("Last Success")

        for feed in feeds:
            status = "🟢" if feed.active and feed.error_count == 0 else "🟡" if feed.error_count < 5 else "🔴"
            title = feed.title or "Untitled"
            url = str(feed.url)
            if len(url) > 40:
                url = url[:37] + "..."

            feeds_table.add_row(
                status,
                title[:30] + "..." if len(title) > 30 else title,
                url,
                feed.chat_id,
                str(feed.error_count),
                str(feed.last_success_at) if feed.last_success_at else "Never"
            )

        console.print(feeds_table)

    except Exception as e:
        console.print(f"[bold red]❌ Error showing feeds: {e}[/bold red]")
        sys.exit(1)


@cli.command()
@click.option('--channels', help='Comma-separated list of channel IDs to test')
@click.option('--dry-run', is_flag=True, help='Simulate processing without sending messages')
def full_test(channels, dry_run):
    """Run complete end-to-end system test."""
    console.print("[bold blue]🧪 Running Full System Test[/bold blue]")
    
    async def run_full_test():
        try:
            from culifeed.scheduler.daily_scheduler import DailyScheduler
            
            scheduler = DailyScheduler()
            
            if channels:
                # Test specific channels
                channel_list = [ch.strip() for ch in channels.split(',')]
                console.print(f"🎯 Testing specific channels: {channel_list}")
                # Note: This would require enhancing DailyScheduler to accept specific channels
                console.print("[yellow]⚠️ Specific channel testing will use full processing for now[/yellow]")
            
            console.print(f"🔄 Starting full system test (dry_run: {dry_run})")
            result = await scheduler.run_daily_processing(dry_run=dry_run)
            
            # Display results
            if result['success']:
                console.print("[bold green]✅ Full system test PASSED[/bold green]")
                console.print(f"📊 Channels: {result['channels_processed']}, Articles: {result['total_articles_processed']}")
                console.print(f"⏱️ Duration: {result['duration_seconds']:.2f}s")
                
                if result.get('channel_results'):
                    # Show detailed channel results
                    results_table = Table(title="Channel Test Results")
                    results_table.add_column("Channel", style="cyan")
                    results_table.add_column("Status", style="green")
                    results_table.add_column("Articles", style="yellow")
                    results_table.add_column("Messages", style="blue")
                    
                    for ch_result in result['channel_results']:
                        status = "✅ Success" if ch_result['success'] else "❌ Failed"
                        results_table.add_row(
                            ch_result['channel_id'],
                            status,
                            str(ch_result['articles_processed']),
                            str(ch_result.get('messages_sent', 0))
                        )
                    
                    console.print(results_table)
            else:
                console.print("[bold red]❌ Full system test FAILED[/bold red]")
                console.print(f"Error: {result.get('message', 'Unknown error')}")
                if result.get('errors'):
                    console.print("\n[bold red]Errors encountered:[/bold red]")
                    for error in result['errors']:
                        console.print(f"  • {error['channel_id']}: {error['error']}")
                sys.exit(1)
                
        except Exception as e:
            console.print(f"[bold red]❌ Full test error: {e}[/bold red]")
            sys.exit(1)
    
    # Run the async function
    asyncio.run(run_full_test())


@cli.command()
def health_check():
    """Check system health status."""
    console.print("[bold blue]🏥 System Health Check[/bold blue]")
    
    async def run_health_check():
        try:
            from culifeed.scheduler.daily_scheduler import DailyScheduler
            
            scheduler = DailyScheduler()
            status = await scheduler.check_processing_status()
            
            # Display health status
            health_status = status.get('health_status', 'unknown')
            if health_status == 'healthy':
                console.print("[bold green]✅ System is HEALTHY[/bold green]")
            elif health_status == 'warning':
                console.print("[bold yellow]⚠️ System has WARNINGS[/bold yellow]")
            else:
                console.print("[bold red]❌ System is UNHEALTHY[/bold red]")
            
            # Health details table
            health_table = Table(title="Health Status Details")
            health_table.add_column("Metric", style="cyan")
            health_table.add_column("Value", style="green")
            health_table.add_column("Status")
            
            processed_today = status.get('processed_today', False)
            health_table.add_row(
                "Processed Today", 
                "Yes" if processed_today else "No",
                "✅" if processed_today else "❌"
            )
            
            last_success = status.get('last_successful_run')
            if last_success:
                from datetime import datetime
                last_time = datetime.fromisoformat(last_success.replace('Z', '+00:00'))
                time_ago = datetime.now() - last_time.replace(tzinfo=None)
                hours_ago = time_ago.total_seconds() / 3600
                
                health_table.add_row(
                    "Last Successful Run",
                    f"{hours_ago:.1f} hours ago",
                    "✅" if hours_ago < 30 else "⚠️" if hours_ago < 72 else "❌"
                )
            else:
                health_table.add_row("Last Successful Run", "Never", "❌")
            
            success_rate = status.get('recent_success_rate', 0)
            health_table.add_row(
                "Recent Success Rate",
                f"{success_rate}%",
                "✅" if success_rate >= 80 else "⚠️" if success_rate >= 50 else "❌"
            )
            
            recent_runs = status.get('total_recent_runs', 0)
            health_table.add_row(
                "Recent Runs (7 days)",
                str(recent_runs),
                "✅" if recent_runs >= 5 else "⚠️" if recent_runs >= 1 else "❌"
            )
            
            console.print(health_table)
            
            # Exit with appropriate code for monitoring systems
            if health_status == 'healthy':
                sys.exit(0)
            elif health_status == 'warning':
                sys.exit(1)
            else:
                sys.exit(2)
                
        except Exception as e:
            console.print(f"[bold red]❌ Health check error: {e}[/bold red]")
            sys.exit(2)
    
    # Run the async function
    asyncio.run(run_health_check())


@cli.command()
@click.option('--dry-run', is_flag=True, help='Simulate processing without sending messages')
def daily_process(dry_run):
    """Run daily processing pipeline."""
    console.print("[bold blue]📅 Daily Processing Pipeline[/bold blue]")
    
    async def run_daily():
        try:
            from culifeed.scheduler.daily_scheduler import DailyScheduler
            
            scheduler = DailyScheduler()
            console.print(f"🔄 Starting daily processing (dry_run: {dry_run})")
            
            result = await scheduler.run_daily_processing(dry_run=dry_run)
            
            if result['success']:
                console.print("[bold green]✅ Daily processing completed successfully![/bold green]")
                console.print(f"📊 Processed {result['channels_processed']} channels")
                console.print(f"📰 Processed {result['total_articles_processed']} articles")
                console.print(f"⏱️ Duration: {result['duration_seconds']:.2f} seconds")
                
                if result.get('channel_results'):
                    successful = result.get('successful_channels', 0)
                    failed = result.get('failed_channels', 0)
                    console.print(f"📈 Results: {successful} successful, {failed} failed")
                
                sys.exit(0)
            else:
                console.print("[bold red]❌ Daily processing failed![/bold red]")
                console.print(f"Error: {result.get('message', 'Unknown error')}")
                sys.exit(1)
                
        except Exception as e:
            console.print(f"[bold red]❌ Daily processing error: {e}[/bold red]")
            sys.exit(1)
    
    # Run the async function
    asyncio.run(run_daily())


@cli.command()
@click.argument("article_id")
@click.option("--db", required=True, help="Path to SQLite database file")
def diagnose(article_id, db):
    """Print full diagnostic chain for an article (scores, LLM decision, top topics)."""
    from culifeed.cli.diagnose import diagnose as _diagnose

    _diagnose(db_path=db, article_id=article_id)


@cli.command()
def start_bot():
    """Start Telegram bot service."""
    console.print("[bold blue]🤖 Starting Telegram Bot Service[/bold blue]")
    
    try:
        # This would start the long-running bot service
        # Implementation depends on the actual bot service architecture
        console.print("[yellow]⚠️ Bot service implementation pending[/yellow]")
        console.print("Use: python run_bot.py to start the bot manually")
        
    except Exception as e:
        console.print(f"[bold red]❌ Bot startup error: {e}[/bold red]")
        sys.exit(1)


# Helper functions for configuration checks
def _check_database_config(settings) -> tuple[bool, str]:
    """Check database configuration."""
    try:
        db_path = Path(settings.database.path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        return True, f"Path: {settings.database.path}"
    except Exception as e:
        return False, str(e)


def _check_logging_config(settings) -> tuple[bool, str]:
    """Check logging configuration."""
    try:
        if settings.logging.file_path:
            log_path = Path(settings.logging.file_path)
            log_path.parent.mkdir(parents=True, exist_ok=True)
        return True, f"Level: {settings.logging.level}, Console: {settings.logging.console_logging}"
    except Exception as e:
        return False, str(e)


def _check_telegram_config(settings) -> tuple[bool, str]:
    """Check Telegram configuration."""
    try:
        if not settings.telegram.bot_token or settings.telegram.bot_token.startswith("${"):
            return False, "Bot token not set"
        if len(settings.telegram.bot_token.split(':')) != 2:
            return False, "Invalid bot token format"
        return True, "Bot token configured"
    except Exception as e:
        return False, str(e)


def _check_ai_config(settings) -> tuple[bool, str]:
    """Check AI providers configuration."""
    try:
        providers = settings.get_ai_fallback_providers()
        if not providers:
            return False, "No AI providers configured"
        primary = settings.processing.ai_provider
        return True, f"Primary: {primary}, Available: {len(providers)}"
    except Exception as e:
        return False, str(e)


def _check_processing_config(settings) -> tuple[bool, str]:
    """Check processing configuration."""
    try:
        return True, f"Hour: {settings.processing.daily_run_hour}, Max articles: {settings.processing.max_articles_per_topic}"
    except Exception as e:
        return False, str(e)


if __name__ == "__main__":
    try:
        cli()
    except KeyboardInterrupt:
        console.print("\n[yellow]👋 CuliFeed interrupted by user[/yellow]")
        sys.exit(130)
    except Exception as e:
        console.print(f"\n[bold red]❌ Unexpected error: {e}[/bold red]")
        sys.exit(1)