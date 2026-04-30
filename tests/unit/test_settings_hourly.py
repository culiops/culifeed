"""Tests for hourly processing settings."""
import pytest
from pydantic import ValidationError

from culifeed.config.settings import ProcessingSettings


def test_defaults():
    s = ProcessingSettings()
    assert s.processing_interval_hours == 1
    assert s.quiet_hours_start == 22
    assert s.quiet_hours_end == 7
    assert s.max_ai_calls_per_run == 50


def test_interval_validation():
    with pytest.raises(ValidationError):
        ProcessingSettings(processing_interval_hours=0)
    with pytest.raises(ValidationError):
        ProcessingSettings(processing_interval_hours=25)
    ProcessingSettings(processing_interval_hours=24)  # boundary OK


def test_quiet_hours_range():
    with pytest.raises(ValidationError):
        ProcessingSettings(quiet_hours_start=24)
    with pytest.raises(ValidationError):
        ProcessingSettings(quiet_hours_end=-1)


def test_max_ai_calls_per_run_range():
    with pytest.raises(ValidationError):
        ProcessingSettings(max_ai_calls_per_run=0)
    with pytest.raises(ValidationError):
        ProcessingSettings(max_ai_calls_per_run=501)


def test_daily_run_hour_field_removed():
    """Old field must not exist on the model."""
    s = ProcessingSettings()
    assert not hasattr(s, "daily_run_hour")
