# Hourly Processing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Switch CuliFeed from daily processing (one fixed hour) to hourly processing with quiet-hour delivery suppression, so relevant articles reach the user within ~1 hour of publication.

**Architecture:** Patch the existing supervisord-managed scheduler service in place. Loop wakes every 5 min, fires the pipeline when `processing_interval_hours` has elapsed since the last run. Pipeline writes results as today; the scheduler skips the delivery call during quiet hours, leaving rows in `processing_results` with `delivered = 0`. The next active-hour run picks them up via the existing `WHERE delivered = 0` query. No new tables, no new infra.

**Tech Stack:** Python 3, Pydantic settings, SQLite, supervisord, pytest. All existing.

**Spec:** [`docs/superpowers/specs/2026-04-29-hourly-processing-design.md`](../specs/2026-04-29-hourly-processing-design.md)

---

## File Inventory

**Create:**
- `culifeed/scheduler/hourly_scheduler.py` (renamed from `daily_scheduler.py`, with logic changes)
- `run_scheduler.py` (renamed from `run_daily_scheduler.py`, with logic changes)
- `tests/unit/test_hourly_scheduler.py`
- `tests/unit/test_quiet_hours.py`

**Modify:**
- `culifeed/config/settings.py` — `ProcessingSettings` fields
- `culifeed/processing/pipeline.py` — `max_ai_calls_per_run` cap
- `docker/supervisord.conf` — program rename
- `OPERATIONS.md` — document rename
- `main.py:798` — drop `daily_run_hour` reference
- `tests/integration/test_end_to_end.py` — update settings references

**Delete:**
- `culifeed/scheduler/daily_scheduler.py` (after rename)
- `run_daily_scheduler.py` (after rename)

---

## Task 1: Add new settings fields and deprecate `daily_run_hour`

**Files:**
- Modify: `culifeed/config/settings.py:48-92` (`ProcessingSettings`)
- Test: `tests/unit/test_settings_hourly.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_settings_hourly.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

```bash
source venv/bin/activate && python -m pytest tests/unit/test_settings_hourly.py -v
```

Expected: FAIL — fields not present, `daily_run_hour` still exists.

- [ ] **Step 3: Edit `culifeed/config/settings.py` `ProcessingSettings`**

Replace the `daily_run_hour` field and its validator with the new fields. Final block (lines 48–92 region):

```python
class ProcessingSettings(BaseModel):
    """Processing pipeline configuration."""

    processing_interval_hours: int = Field(
        default=1, ge=1, le=24,
        description="Hours between processing runs (1-24)",
    )
    quiet_hours_start: int = Field(
        default=22, ge=0, le=23,
        description="Hour delivery starts being suppressed (0-23)",
    )
    quiet_hours_end: int = Field(
        default=7, ge=0, le=23,
        description="Hour delivery resumes (0-23). If equal to start, no quiet hours.",
    )
    max_articles_per_topic: int = Field(
        default=5, ge=1, le=20, description="Maximum articles to deliver per topic"
    )
    ai_provider: AIProvider = Field(
        default=AIProvider.GROQ, description="Primary AI provider"
    )
    batch_size: int = Field(
        default=10, ge=1, le=50, description="Articles to process in one batch"
    )
    parallel_feeds: int = Field(
        default=5, ge=1, le=20, description="Concurrent feed fetches"
    )
    cache_embeddings: bool = Field(default=True, description="Cache article embeddings")
    max_content_length: int = Field(
        default=2000, ge=500, le=10000,
        description="Max content length for AI processing",
    )
    ai_relevance_threshold: float = Field(
        default=0.5, ge=0.0, le=1.0,
        description="Minimum AI relevance score to include article",
    )
    ai_summary_threshold: float = Field(
        default=0.6, ge=0.0, le=1.0,
        description="Minimum AI relevance score to generate summary",
    )
    max_ai_calls_per_run: int = Field(
        default=50, ge=1, le=500,
        description="Hard cap on AI calls per scheduler run",
    )
```

Delete the existing `daily_run_hour` field (line 51) and its `@field_validator("daily_run_hour")` block (lines 86-92).

- [ ] **Step 4: Run test to verify it passes**

```bash
source venv/bin/activate && python -m pytest tests/unit/test_settings_hourly.py -v
```

Expected: 5 passed.

- [ ] **Step 5: Drop the orphan `daily_run_hour` reference in `main.py:798`**

Find and edit:

```python
# Before
return True, f"Hour: {settings.processing.daily_run_hour}, Max articles: {settings.processing.max_articles_per_topic}"

# After
return True, f"Interval: every {settings.processing.processing_interval_hours}h, Max articles: {settings.processing.max_articles_per_topic}"
```

- [ ] **Step 6: Update `tests/integration/test_end_to_end.py`**

Three references at lines 66, 417, 518. Replace each `daily_run_hour = 8` (or assertion) with the equivalent for the new field. Search-replace:

```bash
sed -i 's/settings.processing.daily_run_hour = 8/settings.processing.processing_interval_hours = 1/g' tests/integration/test_end_to_end.py
sed -i 's/test_settings.processing.daily_run_hour == 8/test_settings.processing.processing_interval_hours == 1/g' tests/integration/test_end_to_end.py
```

Verify the file still parses:

```bash
source venv/bin/activate && python -m py_compile tests/integration/test_end_to_end.py
```

- [ ] **Step 7: Add deprecation warning for `daily_run_hour` env var**

Pydantic v2 settings ignore unknown env vars by default (verified: `extra` is not set to `forbid` in `model_config`), so a stale `CULIFEED_PROCESSING__DAILY_RUN_HOUR` will not crash the app. But the spec asks for an explicit one-time WARNING so the user notices.

In `culifeed/config/settings.py`, locate the `get_settings()` function (it's the cached settings accessor at the bottom of the file). Add this near where settings are first constructed:

```python
import os
import logging

_DEPRECATED_ENV = "CULIFEED_PROCESSING__DAILY_RUN_HOUR"

def _warn_deprecated_env_once():
    if os.getenv(_DEPRECATED_ENV) is not None:
        logging.getLogger("culifeed.settings").warning(
            f"{_DEPRECATED_ENV} is deprecated and ignored. "
            f"Use CULIFEED_PROCESSING__PROCESSING_INTERVAL_HOURS instead."
        )
```

Call `_warn_deprecated_env_once()` once inside `get_settings()` the first time settings are loaded. Use a module-level flag to avoid spamming on repeated calls:

```python
_deprecation_warned = False

def get_settings() -> "CuliFeedSettings":
    global _deprecation_warned
    if not _deprecation_warned:
        _warn_deprecated_env_once()
        _deprecation_warned = True
    # ... existing body ...
```

Verify by running:

```bash
source venv/bin/activate && CULIFEED_PROCESSING__DAILY_RUN_HOUR=8 python -c "from culifeed.config.settings import get_settings; get_settings()"
```

Expected: WARNING logged about deprecation; no crash.

```bash
source venv/bin/activate && python -c "from culifeed.config.settings import get_settings; get_settings()"
```

Expected: no warning (env var not set).

- [ ] **Step 8: Run the full unit suite to confirm no other regressions**

```bash
source venv/bin/activate && python -m pytest tests/unit/ -x --no-header -q 2>&1 | tail -20
```

Expected: all pass (or only failures clearly tied to the not-yet-renamed scheduler — those are addressed in Task 3).

- [ ] **Step 9: Commit**

```bash
git add culifeed/config/settings.py tests/unit/test_settings_hourly.py main.py tests/integration/test_end_to_end.py
git commit -m "feat(settings): replace daily_run_hour with hourly interval + quiet hours

- New fields: processing_interval_hours, quiet_hours_start, quiet_hours_end,
  max_ai_calls_per_run.
- Deprecated CULIFEED_PROCESSING__DAILY_RUN_HOUR env var now logs a WARNING
  on startup instead of being silently ignored."
```

---

## Task 2: Quiet-hours helper

**Files:**
- Create: `culifeed/scheduler/quiet_hours.py`
- Test: `tests/unit/test_quiet_hours.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_quiet_hours.py`:

```python
"""Tests for the quiet-hours window helper."""
from datetime import datetime

import pytest

from culifeed.scheduler.quiet_hours import in_quiet_hours


def _at(h: int, m: int = 0, s: int = 0) -> datetime:
    return datetime(2026, 4, 29, h, m, s)


class TestWrappingWindow:
    """start > end ⇒ window crosses midnight (e.g. 22→7)."""

    @pytest.mark.parametrize("hour", [22, 23, 0, 1, 6])
    def test_inside_window(self, hour):
        assert in_quiet_hours(_at(hour), 22, 7) is True

    @pytest.mark.parametrize("hour", [7, 8, 12, 21])
    def test_outside_window(self, hour):
        assert in_quiet_hours(_at(hour), 22, 7) is False

    def test_exact_start_is_quiet(self):
        assert in_quiet_hours(_at(22, 0, 0), 22, 7) is True

    def test_one_second_before_end_is_quiet(self):
        assert in_quiet_hours(_at(6, 59, 59), 22, 7) is True

    def test_exact_end_is_active(self):
        assert in_quiet_hours(_at(7, 0, 0), 22, 7) is False


class TestNonWrappingWindow:
    """start < end ⇒ window within a single day (e.g. 1→5 = quiet 01:00-04:59)."""

    @pytest.mark.parametrize("hour", [1, 2, 4])
    def test_inside_window(self, hour):
        assert in_quiet_hours(_at(hour), 1, 5) is True

    @pytest.mark.parametrize("hour", [0, 5, 12, 23])
    def test_outside_window(self, hour):
        assert in_quiet_hours(_at(hour), 1, 5) is False


class TestEqualStartEnd:
    """start == end ⇒ no quiet hours, always active."""

    @pytest.mark.parametrize("hour", range(0, 24))
    def test_always_active(self, hour):
        assert in_quiet_hours(_at(hour), 0, 0) is False
        assert in_quiet_hours(_at(hour), 12, 12) is False
```

- [ ] **Step 2: Run test to verify it fails**

```bash
source venv/bin/activate && python -m pytest tests/unit/test_quiet_hours.py -v
```

Expected: FAIL — module does not exist.

- [ ] **Step 3: Create `culifeed/scheduler/quiet_hours.py`**

```python
"""Quiet-hours window check for delivery suppression."""
from datetime import datetime


def in_quiet_hours(now: datetime, start: int, end: int) -> bool:
    """Return True if `now.hour` falls within the quiet window [start, end).

    The window is half-open: `start` is inside, `end` is outside.
    If `start > end`, the window wraps midnight.
    If `start == end`, there are no quiet hours and this returns False for every hour.
    """
    if start == end:
        return False
    h = now.hour
    if start < end:
        return start <= h < end
    return h >= start or h < end
```

- [ ] **Step 4: Run test to verify it passes**

```bash
source venv/bin/activate && python -m pytest tests/unit/test_quiet_hours.py -v
```

Expected: all parametrized cases pass.

- [ ] **Step 5: Commit**

```bash
git add culifeed/scheduler/quiet_hours.py tests/unit/test_quiet_hours.py
git commit -m "feat(scheduler): add quiet_hours helper with wrap-around handling"
```

---

## Task 3: Rename scheduler module and class

**Files:**
- Move: `culifeed/scheduler/daily_scheduler.py` → `culifeed/scheduler/hourly_scheduler.py`
- Modify: imports across the project

This task is a pure rename — no logic changes. Logic changes happen in Task 4.

- [ ] **Step 1: Find all references**

```bash
grep -rn "daily_scheduler\|DailyScheduler" --include="*.py" /home/claude/culifeed | grep -v venv | grep -v __pycache__
```

Capture the list. Expected hits: `culifeed/scheduler/daily_scheduler.py`, `run_daily_scheduler.py`, possibly tests.

- [ ] **Step 2: Rename the file with `git mv`**

```bash
git mv culifeed/scheduler/daily_scheduler.py culifeed/scheduler/hourly_scheduler.py
```

- [ ] **Step 3: Inside the renamed file, rename the class**

In `culifeed/scheduler/hourly_scheduler.py`, change the class name everywhere it appears in the file:

```python
# Before
class DailyScheduler:

# After
class HourlyScheduler:
```

(Use Edit with `replace_all=True` for `DailyScheduler` → `HourlyScheduler`.)

Also update the module docstring:

```python
# Before
"""
CuliFeed Daily Scheduler - Cron Coordination
==========================================

Orchestrates daily processing workflow for content curation and delivery.
"""

# After
"""
CuliFeed Hourly Scheduler - Loop Coordination
=============================================

Orchestrates hourly processing workflow for content curation and delivery.
"""
```

- [ ] **Step 4: Update import sites**

For every file from Step 1's grep that referenced the old module/class, edit the imports. Likely:

- `run_daily_scheduler.py:21` (this file is renamed in Task 4): `from culifeed.scheduler.daily_scheduler import DailyScheduler` → `from culifeed.scheduler.hourly_scheduler import HourlyScheduler`
- Any other hits found by the grep.

- [ ] **Step 5: Compile-check**

```bash
source venv/bin/activate && python -c "from culifeed.scheduler.hourly_scheduler import HourlyScheduler; print('ok')"
```

Expected: `ok`.

- [ ] **Step 6: Run unit tests**

```bash
source venv/bin/activate && python -m pytest tests/unit/ -x --no-header -q 2>&1 | tail -10
```

Expected: pre-existing tests still pass.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "refactor(scheduler): rename DailyScheduler → HourlyScheduler (rename only)"
```

---

## Task 4: Replace the service loop with hourly interval logic

**Files:**
- Move: `run_daily_scheduler.py` → `run_scheduler.py`
- Modify: the service-loop body
- Test: extend `tests/unit/test_hourly_scheduler.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_hourly_scheduler.py`:

```python
"""Tests for hourly scheduler interval logic."""
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

# Imported lazily inside tests to keep parse-time errors localized
# during refactor. The class lives in run_scheduler after this task.


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
```

- [ ] **Step 2: Run test to verify it fails**

```bash
source venv/bin/activate && python -m pytest tests/unit/test_hourly_scheduler.py -v
```

Expected: FAIL — `run_scheduler` module does not exist yet.

- [ ] **Step 3: Rename the runner**

```bash
git mv run_daily_scheduler.py run_scheduler.py
```

- [ ] **Step 4: Replace the loop body in `run_scheduler.py`**

Open `run_scheduler.py`. Replace the entire `DailySchedulerService` class with:

```python
class SchedulerService:
    """
    Service wrapper for HourlyScheduler that runs processing on a fixed interval.
    """

    def __init__(self, scheduler):
        self.scheduler = scheduler
        self.settings = get_settings()
        self.logger = setup_logger(
            name="culifeed.scheduler_service",
            level=self.settings.logging.level.value,
            log_file=self.settings.logging.file_path,
            console=self.settings.logging.console_logging,
        )
        self.running = True
        self.last_processed_at: datetime | None = None

    def _should_run(self, now: datetime) -> bool:
        if self.last_processed_at is None:
            return True
        interval = timedelta(hours=self.settings.processing.processing_interval_hours)
        return (now - self.last_processed_at) >= interval

    async def run_service(self):
        from datetime import datetime

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
```

Add the missing import at top of file:

```python
from datetime import datetime, timedelta
```

Replace any remaining `DailySchedulerService` references in the same file with `SchedulerService`. Update the import line:

```python
from culifeed.scheduler.hourly_scheduler import HourlyScheduler
```

And in `main()` or wherever the service is constructed:

```python
scheduler = HourlyScheduler()
service = SchedulerService(scheduler)
```

Update any user-facing `print(...)` lines that referenced `daily_run_hour` (e.g., `run_daily_scheduler.py:135`):

```python
print(f"⏱  Running every {settings.processing.processing_interval_hours}h")
```

- [ ] **Step 5: Run the new tests**

```bash
source venv/bin/activate && python -m pytest tests/unit/test_hourly_scheduler.py -v
```

Expected: 5 passed.

- [ ] **Step 6: Smoke-check imports + dry run**

```bash
source venv/bin/activate && python -c "import run_scheduler; print('ok')"
source venv/bin/activate && python run_scheduler.py --help 2>&1 | head -5
```

Expected: imports OK; `--help` prints usage.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "feat(scheduler): replace daily fixed-hour loop with hourly interval check"
```

---

## Task 5: Wire quiet-hours gate into `_process_channel`

**Files:**
- Modify: `culifeed/scheduler/hourly_scheduler.py` (the `_process_channel` method around the `deliver_daily_digest` call)
- Test: extend `tests/unit/test_hourly_scheduler.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_hourly_scheduler.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

```bash
source venv/bin/activate && python -m pytest tests/unit/test_hourly_scheduler.py::test_delivery_skipped_during_quiet_hours -v
```

Expected: FAIL — `_is_delivery_quiet` does not exist.

- [ ] **Step 3: Add the helper and gate inside `culifeed/scheduler/hourly_scheduler.py`**

Add at the top imports (if not already there):

```python
from ..scheduler.quiet_hours import in_quiet_hours
```

Add this method to `HourlyScheduler` (anywhere alongside the other private methods):

```python
def _is_delivery_quiet(self) -> bool:
    """True if delivery should be suppressed at the current wall-clock hour."""
    return in_quiet_hours(
        datetime.now(),
        self.settings.processing.quiet_hours_start,
        self.settings.processing.quiet_hours_end,
    )
```

Then locate the existing block in `_process_channel` (around lines 300-330 in the original `daily_scheduler.py`):

```python
if not dry_run and processing_result.articles_ready_for_ai > 0:
    try:
        digest_result = await self.message_sender.deliver_daily_digest(
            channel["chat_id"],
            self.settings.processing.max_articles_per_topic,
        )
```

Wrap the condition:

```python
if not dry_run and processing_result.articles_ready_for_ai > 0:
    if self._is_delivery_quiet():
        self.logger.info(
            f"delivery skipped (quiet hours) for {channel['chat_id']}; "
            f"results queued via delivered=0"
        )
        processing_result.delivery_time_seconds = time.time() - delivery_start
    else:
        try:
            digest_result = await self.message_sender.deliver_daily_digest(
                channel["chat_id"],
                self.settings.processing.max_articles_per_topic,
            )
            # ... existing block unchanged ...
```

(Keep the existing `try/except` and metrics-update logic inside the new `else`. Don't change anything inside it.)

- [ ] **Step 4: Run new tests**

```bash
source venv/bin/activate && python -m pytest tests/unit/test_hourly_scheduler.py -v
```

Expected: all pass.

- [ ] **Step 5: Run the integration suite to confirm no regressions**

```bash
source venv/bin/activate && python -m pytest tests/integration/ -x --no-header -q 2>&1 | tail -15
```

Expected: pre-existing tests still pass. (The end-to-end test uses the default 22→7 window; if it runs at a wall-clock hour inside that window, expected behavior is `delivery skipped`. If the existing test asserts a digest is delivered regardless of hour, freeze time inside the test or set `quiet_hours_start = quiet_hours_end = 0` for that fixture. Apply the minimal fix needed.)

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat(scheduler): suppress delivery during quiet hours"
```

---

## Task 6: Pipeline cost cap (`max_ai_calls_per_run`)

**Files:**
- Modify: `culifeed/processing/pipeline.py` (around the AI processing call site)
- Test: `tests/unit/test_pipeline_cost_cap.py` (new)

- [ ] **Step 1: Locate the AI call site**

```bash
grep -n "ai_processor\|process_batch\|articles_ready_for_ai" /home/claude/culifeed/culifeed/processing/pipeline.py | head -15
```

Read the surrounding ~30 lines and identify where the post-pre-filter list of articles is handed to the AI processor. This is where the cap applies.

- [ ] **Step 2: Write the failing test**

Create `tests/unit/test_pipeline_cost_cap.py`:

```python
"""Tests that the AI cost cap defers excess candidates."""
from unittest.mock import MagicMock

from culifeed.processing.pipeline import ProcessingPipeline


def test_cap_truncates_and_warns():
    """Given more candidates than max_ai_calls_per_run, the helper returns
    the top-N by pre-filter score and logs a WARNING with the deferred count."""
    pipeline = ProcessingPipeline.__new__(ProcessingPipeline)
    pipeline.logger = MagicMock()

    candidates = [
        {"article_id": f"a{i}", "pre_filter_score": float(i)}
        for i in range(10)
    ]

    kept = pipeline._apply_ai_call_cap(candidates, cap=3)

    assert len(kept) == 3
    kept_ids = {c["article_id"] for c in kept}
    assert kept_ids == {"a9", "a8", "a7"}  # top 3 by pre_filter_score
    pipeline.logger.warning.assert_called_once()
    assert "7" in str(pipeline.logger.warning.call_args)  # 10 - 3 deferred


def test_cap_no_op_under_limit():
    pipeline = ProcessingPipeline.__new__(ProcessingPipeline)
    pipeline.logger = MagicMock()

    candidates = [{"article_id": "a", "pre_filter_score": 0.9}]
    kept = pipeline._apply_ai_call_cap(candidates, cap=50)

    assert kept == candidates
    pipeline.logger.warning.assert_not_called()
```

- [ ] **Step 3: Run test to verify it fails**

```bash
source venv/bin/activate && python -m pytest tests/unit/test_pipeline_cost_cap.py -v
```

Expected: FAIL — `_apply_ai_call_cap` does not exist.

- [ ] **Step 4: Add the helper to `ProcessingPipeline`**

In `culifeed/processing/pipeline.py`, add this method to the class:

```python
def _apply_ai_call_cap(self, candidates: list, cap: int) -> list:
    """Truncate candidates to top-N by pre_filter_score; log on truncation.

    Each candidate is expected to be a dict-like with a `pre_filter_score` field.
    Items above the cap are not marked processed and will reappear next run.
    """
    if len(candidates) <= cap:
        return candidates
    deferred = len(candidates) - cap
    self.logger.warning(
        f"AI cost cap hit: processing top {cap} of {len(candidates)} candidates; "
        f"{deferred} deferred to next run"
    )
    return sorted(
        candidates,
        key=lambda c: c["pre_filter_score"],
        reverse=True,
    )[:cap]
```

- [ ] **Step 5: Wire the cap into the AI call site**

At the location identified in Step 1, immediately before the AI processor is invoked, replace:

```python
# (find the line that hands candidates to the AI processor; the variable
#  may be named e.g. `passed_articles` + `passed_filter_results` — there
#  may be two parallel lists. If two parallel lists are used, see below.)
ai_results = await self.ai_processor.process_batch(passed_articles, ...)
```

with:

```python
cap = self.settings.processing.max_ai_calls_per_run
if len(passed_articles) > cap:
    # Build candidates with score, sort, then split aligned lists back out.
    paired = list(zip(passed_articles, passed_filter_results))
    paired.sort(key=lambda p: p[1].pre_filter_score, reverse=True)
    self.logger.warning(
        f"AI cost cap hit: processing top {cap} of {len(passed_articles)} candidates; "
        f"{len(passed_articles) - cap} deferred to next run"
    )
    paired = paired[:cap]
    passed_articles = [p[0] for p in paired]
    passed_filter_results = [p[1] for p in paired]

ai_results = await self.ai_processor.process_batch(passed_articles, ...)
```

(Adapt the variable names to match what's actually in `pipeline.py`. Keep the same `_apply_ai_call_cap` helper for testability — the inline block above is just for the production call site; the unit test exercises the helper directly. If you prefer to use the helper at the call site, you can — but make sure it preserves alignment between any parallel lists.)

- [ ] **Step 6: Run new tests**

```bash
source venv/bin/activate && python -m pytest tests/unit/test_pipeline_cost_cap.py -v
```

Expected: 2 passed.

- [ ] **Step 7: Run the full pipeline test suite**

```bash
source venv/bin/activate && python -m pytest tests/unit/test_processing_pipeline.py tests/unit/test_pipeline_orchestration.py tests/unit/test_pipeline_v2.py -v
```

Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add culifeed/processing/pipeline.py tests/unit/test_pipeline_cost_cap.py
git commit -m "feat(pipeline): cap AI calls per run with top-N pre-filter selection"
```

---

## Task 7: Update supervisord and OPERATIONS.md

**Files:**
- Modify: `docker/supervisord.conf`
- Modify: `OPERATIONS.md`

- [ ] **Step 1: Edit `docker/supervisord.conf`**

Replace:

```ini
[program:culifeed-daily]
command=python run_daily_scheduler.py --service
```

with:

```ini
[program:culifeed-scheduler]
command=python run_scheduler.py --service
```

(Other directives in the program block — `directory`, `user`, `autostart`, etc. — stay the same.)

- [ ] **Step 2: Update OPERATIONS.md**

Find any references to `culifeed-daily` or `run_daily_scheduler.py`. Replace each with the new names. Add (or update) a one-line note:

```
Note: as of 2026-04-29, the scheduler runs hourly (configurable via
CULIFEED_PROCESSING__PROCESSING_INTERVAL_HOURS). Delivery is suppressed
during quiet hours (CULIFEED_PROCESSING__QUIET_HOURS_START..END,
default 22→7). The supervisord program was renamed to `culifeed-scheduler`.
```

- [ ] **Step 3: Sanity-check the supervisord config syntax**

```bash
grep -E "^\[program:" docker/supervisord.conf
```

Expected: `[program:culifeed-bot]` and `[program:culifeed-scheduler]`. No `culifeed-daily`.

- [ ] **Step 4: Commit**

```bash
git add docker/supervisord.conf OPERATIONS.md
git commit -m "ops: rename culifeed-daily program to culifeed-scheduler"
```

---

## Task 8: Final verification

- [ ] **Step 1: Full test suite**

```bash
source venv/bin/activate && python -m pytest 2>&1 | tail -20
```

Expected: all pass.

- [ ] **Step 2: Compile-check entry points**

```bash
source venv/bin/activate && python -c "import run_scheduler; import run_bot; print('ok')"
source venv/bin/activate && python -c "from culifeed.scheduler.hourly_scheduler import HourlyScheduler; from culifeed.config.settings import get_settings; s = get_settings(); print(s.processing.processing_interval_hours, s.processing.quiet_hours_start, s.processing.quiet_hours_end)"
```

Expected: `ok`; settings print as `1 22 7` (defaults).

- [ ] **Step 3: Confirm dead names are gone**

```bash
grep -rn "DailyScheduler\|daily_run_hour\|run_daily_scheduler\|culifeed-daily" --include="*.py" --include="*.conf" --include="*.md" --include="*.yaml" --include="*.yml" /home/claude/culifeed | grep -v venv | grep -v __pycache__ | grep -v "docs/superpowers/specs/"
```

Expected: no hits outside the spec document (the spec may reference the old names historically — that's fine).

- [ ] **Step 4: Optional smoke run**

```bash
source venv/bin/activate && timeout 10 python run_scheduler.py --service 2>&1 | head -10
```

Expected: scheduler starts, logs `Starting scheduler service: every 1h`, then runs (or sleeps).

- [ ] **Step 5: Final commit if any cleanups needed**

If steps 1-3 surfaced anything missed, fix and commit. Otherwise, this task is a no-op.

---

## Done

- Hourly processing replacing daily fixed-hour scheduling
- Quiet hours suppress delivery 22:00–07:00 by default; pipeline still runs and queues via existing `delivered = 0`
- `max_ai_calls_per_run` cap protects against runaway AI cost when a backfill or feed outage produces an unusual burst
- No new tables, no new infra, no new dependencies
- Supervisord program renamed; OPERATIONS.md updated
