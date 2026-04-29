# Hourly Processing — Design

**Date:** 2026-04-29
**Status:** Draft, awaiting review
**Branch context:** `feat/topic-matching-v2`

## Goal

Switch CuliFeed from daily processing (one run at a fixed hour) to hourly processing, so relevant articles are scored and delivered within ~1 hour of publication instead of up to 24 hours.

## Non-goals

- Multi-tenant / SaaS scaling (solo user only).
- Changes to AI providers, pre-filter logic, or topic matching.
- Configurable per-channel cadence.
- A larger scheduler refactor (a feature/provider trim is planned separately).

## Constraints

- Keep AI cost flat. Existing content-hash dedup ensures hourly runs only AI-process *genuinely new* articles, so total AI calls per day stay roughly the same as the daily mode. No design choice should break this property.
- Keep current deployment shape: one container, supervisord runs bot + scheduler. Don't introduce host-level cron or systemd timers.
- Keep the change small and reversible — a feature/provider refactor is coming.

## Approach

Patch the existing scheduler service loop in place. No new infra. Quiet hours gate *delivery only*, not processing — processing runs every hour, 24/7, so AI scoring is fresh and we never face a flood of stale articles at quiet-end.

## Behavior summary

- **Processing:** runs every `processing_interval_hours` (default 1), 24/7. Scheduler loop wakes every 5 minutes and runs the pipeline if the configured interval has elapsed since `last_processed_at`.
- **Delivery during active hours:** runs `deliver_daily_digest` for each channel as today.
- **Delivery during quiet hours:** scheduler skips the delivery call. Pipeline still writes results to `processing_results` with `delivered = 0`, which is the existing "ready for delivery" state.
- **Flush:** no separate flush logic needed. The next active-hour run calls `deliver_daily_digest`, which already queries `WHERE delivered = 0` and picks up everything queued during quiet hours along with anything new from the current run.

## Files & changes

### `culifeed/config/settings.py` — `ProcessingSettings`

- Remove `daily_run_hour`.
- Add:
  - `processing_interval_hours: int = 1` (1–24).
  - `quiet_hours_start: int = 22` (0–23).
  - `quiet_hours_end: int = 7` (0–23). Window wraps midnight if `start > end`.
  - `max_ai_calls_per_run: int = 50` (1–500).
- Validators: ranges as above. If `quiet_hours_start == quiet_hours_end`, treat as "no quiet hours" (always deliver).

### `culifeed/scheduler/` — rename `daily_scheduler.py` → `hourly_scheduler.py`

- Class `DailyScheduler` → `HourlyScheduler`. Public methods unchanged where possible.
- Replace the `processed_today` flag with an in-memory `last_processed_at: Optional[datetime]`. Reset on restart is acceptable — content-hash dedup prevents reprocessing of articles, and a missed hour at restart is recoverable on the next run.
- Loop pseudo-code:

  ```
  while running:
      flush_pending_deliveries_if_outside_quiet_hours()
      if last_processed_at is None or (now - last_processed_at) >= interval:
          run pipeline for all channels
          last_processed_at = now
      sleep 5 minutes
  ```

### `run_daily_scheduler.py` → `run_scheduler.py`

- Same CLI surface. Update logging strings ("daily" → "scheduled").
- Update import path to `HourlyScheduler`.

### `docker/supervisord.conf`

- Program `culifeed-daily` → `culifeed-scheduler`. `command = python run_scheduler.py --service`.
- Document rename in `OPERATIONS.md`.

### `culifeed/scheduler/hourly_scheduler.py` — quiet-hour gate

- New helper `_in_quiet_hours(now: datetime, start: int, end: int) -> bool` handling wrap-around (`start > end` ⇒ window crosses midnight) and the equal-start-end case (no quiet hours, always returns False).
- In `_process_channel`, the existing call to `message_sender.deliver_daily_digest` is wrapped:
  - If `_in_quiet_hours(now, start, end)` → skip the call. Articles remain in `processing_results` with `delivered = 0`. Log INFO `delivery skipped: quiet hours`.
  - Else → call as today.

### `culifeed/delivery/message_sender.py`

No structural changes. Existing `_get_articles_for_delivery` already queries `WHERE delivered = 0`, so it naturally picks up rows queued during quiet hours alongside fresh ones.

### Database

No schema changes. The existing `processing_results.delivered` column is the queue.

### `culifeed/processing/pipeline.py` — cost guard

- After pre-filter, before AI processing, cap candidates at `max_ai_calls_per_run` per run (top-N by pre-filter score). Articles above the cap are NOT marked processed; they will be candidates again next run. Log a WARNING when the cap is hit with the deferred count.

### Observability

- One INFO log line per run: `scheduler.run_complete chat_id=... ai_calls=... delivered=... queued=... pending_queue_size=...`.

## Migration

- `daily_run_hour` in `.env.prd` or YAML: read on startup; if present, emit one WARNING log on startup and ignore. Do not fail.
- No DB migration needed — reuses existing `processing_results.delivered` column.
- Supervisord program rename: `OPERATIONS.md` gets a note about the renamed program for `supervisorctl` users.

## Error handling

- Telegram send failure: existing `delivery_error` column + retry-next-run behavior unchanged.
- Pipeline failure for one channel: existing isolation unchanged.
- No new failure modes introduced by this change.

## Testing

**Unit:**
- `_in_quiet_hours` boundary cases: wrap (22→7), non-wrap (7→22), equal start/end, exact boundary minutes (`22:00:00`, `06:59:59`, `07:00:00`).
- `HourlyScheduler` interval check: `last_processed_at is None`, just-elapsed, not-yet, far-past.
- Pipeline cost cap: more than `max_ai_calls_per_run` candidates → top-N processed, rest deferred, WARNING logged.

**Integration:**
- Full run with `now` mocked into quiet hour → pipeline writes results with `delivered = 0`, `deliver_daily_digest` is NOT called.
- Subsequent run with `now` mocked outside quiet hour → `deliver_daily_digest` called, queued rows + new rows delivered together, all marked `delivered = 1`.

**Regression:** existing pipeline tests unchanged.

## Out of scope

- Per-channel cadence (D from brainstorm).
- Replacing supervisord with cron / systemd timers (B from brainstorm).
- Cron expressions / APScheduler.
- Refactoring the AI provider lineup (separate work).
