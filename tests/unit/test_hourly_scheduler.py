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
