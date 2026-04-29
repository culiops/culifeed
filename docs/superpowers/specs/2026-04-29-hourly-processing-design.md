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
- **Delivery during active hours:** sends Telegram digests immediately.
- **Delivery during quiet hours:** queues deliverable articles to a `pending_deliveries` table; no Telegram calls.
- **Flush:** at the *start* of every run, before pipeline processing, the scheduler calls `flush_pending` for each active channel if we are now outside quiet hours. This is the only place flush happens — `message_sender.send` is not responsible for flushing. As a result, queued articles from a quiet hour are delivered on the first run after quiet-end, in the same digest as any new articles found that hour (since the run flushes first, then sends new).

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

### `culifeed/delivery/message_sender.py`

- New helper `_in_quiet_hours(now, start, end) -> bool` handling the wrap-around case.
- Send path:
  1. If in quiet hours → insert row into `pending_deliveries`, return without calling Telegram.
  2. Else → call existing send logic.
- New method `flush_pending(chat_id) -> List[PendingDelivery]` returning queued rows for a channel and deleting them on successful send. Failed sends leave rows in place; `delivery_attempts` increments; rows with `delivery_attempts >= 5` are deleted with an ERROR log.

### Database — new table

```sql
CREATE TABLE pending_deliveries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id TEXT NOT NULL,
    article_id TEXT NOT NULL,
    topic_id INTEGER,
    relevance_score REAL,
    summary TEXT,
    delivery_attempts INTEGER NOT NULL DEFAULT 0,
    queued_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(chat_id, article_id, topic_id)
);
CREATE INDEX idx_pending_deliveries_chat ON pending_deliveries(chat_id);
```

Created via the standard schema-init path on next startup. No data migration needed.

### `culifeed/processing/pipeline.py` — cost guard

- After pre-filter, before AI processing, cap candidates at `max_ai_calls_per_run` per run (top-N by pre-filter score). Articles above the cap are NOT marked processed; they will be candidates again next run. Log a WARNING when the cap is hit with the deferred count.

### Observability

- One INFO log line per run: `scheduler.run_complete chat_id=... ai_calls=... delivered=... queued=... pending_queue_size=...`.

## Migration

- `daily_run_hour` in `.env.prd` or YAML: read on startup; if present, emit one WARNING log on startup and ignore. Do not fail.
- No DB migration script needed — `CREATE TABLE IF NOT EXISTS` in schema init handles it.
- Supervisord program rename: `OPERATIONS.md` gets a note about the renamed program for `supervisorctl` users.

## Error handling

- Telegram send failure during flush: leave row, increment `delivery_attempts`, retry next run. Drop after 5 attempts with ERROR log (avoids infinite retry on a permanently invalid chat).
- Pipeline failure for one channel: existing isolation unchanged.
- DB failure on `pending_deliveries` insert: log ERROR, fall through (article will be picked up again next run via standard processing — content-hash dedup means we won't re-AI-score it, but we may re-deliver to active hours; acceptable for solo use).

## Testing

**Unit:**
- `_in_quiet_hours` boundary cases: wrap (22→7), non-wrap (7→22), equal start/end, exact boundary minutes (`22:00:00`, `06:59:59`, `07:00:00`).
- `HourlyScheduler` interval check: `last_processed_at is None`, just-elapsed, not-yet, far-past.
- `flush_pending` with mixed new + queued articles → single digest, queued rows deleted on success.
- `delivery_attempts` increment + drop at 5.
- Pipeline cost cap: more than `max_ai_calls_per_run` candidates → top-N processed, rest deferred, WARNING logged.

**Integration:**
- Full run with `now` mocked into quiet hour → no Telegram call, row in `pending_deliveries`.
- Subsequent run with `now` mocked outside quiet hour → flush succeeds, new articles merged into one digest, table empty after.

**Regression:** existing pipeline tests unchanged.

## Out of scope

- Per-channel cadence (D from brainstorm).
- Replacing supervisord with cron / systemd timers (B from brainstorm).
- Cron expressions / APScheduler.
- Refactoring the AI provider lineup (separate work).
