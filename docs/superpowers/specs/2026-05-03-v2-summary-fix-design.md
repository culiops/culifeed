# v2 Pipeline AI Summary Fix — Design

**Date:** 2026-05-03
**Status:** Approved for implementation
**Scope:** Small, focused fix to `culifeed/processing/pipeline.py`

## Problem

The v2 (embedding + LLM-gate) pipeline never calls `ai_manager.generate_summary`. As a result, 87% of v2-delivered articles in the past 7 days had `articles.summary = NULL`. The Telegram delivery formatter (`digest_formatter.py:339-355`) then falls back to `_extract_content_preview(article.content, …)` — rendering the first ~200 chars of source content with `💡` and **no 🤖 indicator**, indistinguishable to users from "AI summary = first paragraph of article."

v1 generates summaries correctly (100% of recent v1 deliveries have AI summaries). v2 was rolled back (commit `34ee04f`) for an unrelated ingestion gap; this fix prepares v2 for re-enablement.

## Goal

When a v2 article passes the LLM gate, generate a real AI summary and persist it so:
- `articles.summary` holds the AI-generated text (read by delivery via `SELECT a.*`)
- `articles.ai_provider` is non-NULL (drives the 🤖 indicator in `digest_formatter.py:344-348`)
- `processing_results.summary` mirrors the same string (parity with v1 for audit/dashboards)

## Design

### Change 1 — Stage 4 of `_process_articles_v2` (~`pipeline.py:1186`)

After the LLM gate decides:

```python
summary_text: Optional[str] = None
summary_provider: Optional[str] = None
if gate_result is not None and gate_result.passed:
    try:
        summary_result = await self.ai_manager.generate_summary(article)
        if summary_result and getattr(summary_result, "summary", None):
            summary_text = summary_result.summary
            summary_provider = getattr(summary_result, "provider", None)
    except Exception as e:
        self.logger.warning(
            f"Summary generation failed for article {article.id}: {e}"
        )

self._persist_v2_result(
    ..., summary=summary_text, summary_provider=summary_provider
)
```

**Threshold decision:** No extra threshold. The gate already made a binary keep/drop decision; v1's `ai_summary_threshold=0.6` doesn't translate cleanly to v2 (embedding scores ~0.3-0.5, gate confidence ~0.5-0.95, neither maps onto 0.6). Gate passed → summarize. Cost: one extra LLM call per pass (same per-article volume as v1).

### Change 2 — `_persist_v2_result` signature + body

Add parameters `summary: Optional[str] = None`, `summary_provider: Optional[str] = None`. Two database mutations:

**A) `processing_results` INSERT** — add `summary` column to the existing INSERT statement.

**B) `articles` UPDATE** — only when `summary is not None`:

```sql
UPDATE articles
SET summary = ?,
    ai_provider = ?,
    ai_relevance_score = ?,
    ai_confidence = ?,
    ai_reasoning = ?
WHERE id = ?
```

Field values:
| Column | Source |
|---|---|
| `summary` | summary text from `generate_summary` |
| `ai_provider` | `summary_provider` if non-None, else `"v2_llm_gate"` |
| `ai_relevance_score` | `gate_result.confidence` |
| `ai_confidence` | `gate_result.confidence` |
| `ai_reasoning` | `gate_result.reasoning` |

Same connection/transaction as the existing `processing_results` INSERT.

### Error handling

- `generate_summary` raises → log warning, `summary_text` stays None, gate row is still written without summary. Same graceful-degradation pattern as v1 (`pipeline.py:603-607`).
- One bad article does not abort the channel loop.

## Test Plan

Extend `tests/integration/test_v2_against_snapshot.py`. Add one new test, lightly modify the existing one.

### New test: `test_v2_pipeline_generates_summaries`

**Setup** (reuse existing fixtures): snapshot copy, schema migration, seeded feed/topic, stub embedding service.

**Stubs:**
- `ai_manager.complete` → PASS at confidence 0.9 (existing helper).
- `ai_manager.generate_summary` → `AsyncMock` returning a stub `AIResult` with `summary="STUB SUMMARY"`, `provider="stub-provider"`, `success=True`.

**Assertions:**
1. `generate_summary` was called at least once.
2. Every v2 row with `llm_decision='pass'` has `processing_results.summary == "STUB SUMMARY"`.
3. Each corresponding `articles` row has `summary == "STUB SUMMARY"` and `ai_provider == "stub-provider"`.
4. v2 rows with `llm_decision='fail'` or `'skipped'` have NULL `summary` in both tables.
5. `generate_summary` was called only for PASS articles (count matches PASS row count).

### Existing test: `test_v2_pipeline_against_snapshot`

Add one assertion: the existing `mgr.generate_summary = AsyncMock(return_value=None)` stub means summary stays None — verify this does not raise and rows are still written. (Already implicit in current passes; one explicit assert keeps the contract visible.)

## Out of Scope

- **No backfill** of the 501 historical v2 rows with NULL summaries. They are all `delivered=1` — users already saw what they saw. Spending 501 LLM calls to populate orphan DB cells is wasted budget.
- **No change** to the delivery formatter, v1 path, or any other consumer.
- **No change** to v2 toggle (`use_embedding_pipeline` stays False until R0 rollback is lifted by separate work).

## Files Touched

- `culifeed/processing/pipeline.py` — ~30 lines added/modified across two functions.
- `tests/integration/test_v2_against_snapshot.py` — one new test (~50 lines), one minor assertion added.
