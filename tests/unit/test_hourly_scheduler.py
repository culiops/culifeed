"""Tests for hourly scheduler interval logic."""
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest


def _make_service(last_processed_at, interval_hours=1):
    from run_scheduler import SchedulerService

    scheduler = MagicMock()
    scheduler.run_processing = AsyncMock()
    settings = MagicMock()
    settings.processing.processing_interval_hours = interval_hours

    svc = SchedulerService.__new__(SchedulerService)  # bypass __init__
    svc.scheduler = scheduler
    svc.settings = settings
    svc.logger = MagicMock()
    svc.last_processed_at = last_processed_at
    return svc


def test_should_run_when_never_run():
    svc = _make_service(last_processed_at=None)
    assert svc._should_run(datetime(2026, 4, 29, 12, 0)) is True


def test_should_run_when_interval_elapsed():
    svc = _make_service(
        last_processed_at=datetime(2026, 4, 29, 11, 0), interval_hours=1
    )
    assert svc._should_run(datetime(2026, 4, 29, 12, 0)) is True


def test_should_not_run_when_interval_not_elapsed():
    svc = _make_service(
        last_processed_at=datetime(2026, 4, 29, 11, 30), interval_hours=1
    )
    assert svc._should_run(datetime(2026, 4, 29, 12, 0)) is False


def test_should_run_when_far_past():
    svc = _make_service(
        last_processed_at=datetime(2026, 4, 28, 12, 0), interval_hours=1
    )
    assert svc._should_run(datetime(2026, 4, 29, 12, 0)) is True


def test_custom_interval_hours():
    svc = _make_service(
        last_processed_at=datetime(2026, 4, 29, 9, 0), interval_hours=4
    )
    assert svc._should_run(datetime(2026, 4, 29, 12, 0)) is False
    assert svc._should_run(datetime(2026, 4, 29, 13, 0)) is True


from datetime import datetime as _dt
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_delivery_skipped_during_quiet_hours():
    from culifeed.scheduler.hourly_scheduler import HourlyScheduler

    scheduler = HourlyScheduler.__new__(HourlyScheduler)
    scheduler.settings = MagicMock()
    scheduler.settings.processing.quiet_hours_start = 22
    scheduler.settings.processing.quiet_hours_end = 7
    scheduler.settings.processing.max_articles_per_topic = 5
    scheduler.logger = MagicMock()
    scheduler.message_sender = MagicMock()
    scheduler.message_sender.deliver_daily_digest = AsyncMock()

    with patch("culifeed.scheduler.hourly_scheduler.datetime") as mock_dt:
        mock_dt.now.return_value = _dt(2026, 4, 29, 2, 30)  # 02:30 = quiet
        skipped = scheduler._is_delivery_quiet()

    assert skipped is True


@pytest.mark.asyncio
async def test_delivery_runs_during_active_hours():
    from culifeed.scheduler.hourly_scheduler import HourlyScheduler

    scheduler = HourlyScheduler.__new__(HourlyScheduler)
    scheduler.settings = MagicMock()
    scheduler.settings.processing.quiet_hours_start = 22
    scheduler.settings.processing.quiet_hours_end = 7
    scheduler.logger = MagicMock()

    with patch("culifeed.scheduler.hourly_scheduler.datetime") as mock_dt:
        mock_dt.now.return_value = _dt(2026, 4, 29, 10, 0)
        skipped = scheduler._is_delivery_quiet()

    assert skipped is False
