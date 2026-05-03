# v2 AI Summary Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the v2 (embedding + LLM-gate) pipeline generate and persist AI summaries for articles that pass the gate, so Telegram delivery shows real AI summaries (with the 🤖 indicator) instead of falling back to first-paragraph content previews.

**Architecture:** Two-line addition to `_process_articles_v2` stage 4: when `gate_result.passed`, call `ai_manager.generate_summary` and pass the text into `_persist_v2_result`. Extend `_persist_v2_result` to write the summary into `processing_results.summary` and UPDATE the existing `articles` row with `summary` + AI metadata so the delivery formatter (`SELECT a.*`) renders the 🤖 line.

**Tech Stack:** Python 3.x, sqlite3, pytest, pytest-asyncio. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-05-03-v2-summary-fix-design.md`

---

## File Structure

- **Modify:** `culifeed/processing/pipeline.py` — `_process_articles_v2` (~line 1186) and `_persist_v2_result` (~line 1222).
- **Modify:** `tests/integration/test_v2_against_snapshot.py` — extend stub helper, add new test, add one assertion to existing test.

No new files.

---

### Task 1: Write failing test for v2 summary persistence

**Files:**
- Modify: `tests/integration/test_v2_against_snapshot.py`

- [ ] **Step 1: Update `_make_stub_ai_manager` to allow controllable summary stub**

Locate the existing helper around line 81. Replace the `mgr.generate_summary = AsyncMock(return_value=None)` line so the stub returns a usable `AIResult`:

```python
def _make_stub_ai_manager(summary_text: str = "STUB SUMMARY",
                         summary_provider: str = "stub-provider"):
    """Return a MagicMock AIManager whose complete() always returns PASS."""

    pass_response = MagicMock()
    pass_response.success = True
    pass_response.content = (
        "DECISION: PASS\nCONFIDENCE: 0.9\nREASONING: Article is on-topic."
    )
    pass_response.error_message = None

    mgr = MagicMock()
    mgr.complete = AsyncMock(return_value=pass_response)

    from culifeed.ai.providers.base import AIResult
    mgr.analyze_relevance = AsyncMock(
        return_value=AIResult(success=False, relevance_score=0.0, confidence=0.0)
    )
    mgr.generate_summary = AsyncMock(
        return_value=AIResult(
            success=True,
            relevance_score=0.0,
            confidence=0.0,
            summary=summary_text,
            provider=summary_provider,
        )
    )
    return mgr
```

Note: existing test passes no args and gets sensible defaults — backward compatible.

- [ ] **Step 2: Add the new test at the end of the file**

Append after `test_v2_pipeline_against_snapshot`:

```python
@pytest.mark.skipif(
    not SNAPSHOT_PATH.exists(),
    reason=f"Production snapshot not found at {SNAPSHOT_PATH}",
)
@pytest.mark.asyncio
async def test_v2_pipeline_generates_summaries(snapshot_db, tmp_path):
    """v2 must call generate_summary for PASS articles and persist the summary
    in both processing_results.summary and articles.summary, with ai_provider
    set so delivery renders the 🤖 indicator.
    """
    import copy
    from culifeed.database.schema import DatabaseSchema
    from culifeed.config.settings import get_settings
    from culifeed.database.connection import DatabaseConnection
    from culifeed.processing.pipeline import ProcessingPipeline

    schema = DatabaseSchema(str(snapshot_db))
    schema.create_tables()
    _seed_feed_and_topic(snapshot_db)

    settings = copy.deepcopy(get_settings())
    settings.filtering.use_embedding_pipeline = True
    settings.ai.openai_api_key = "test-dummy-key"

    db = DatabaseConnection(str(snapshot_db), pool_size=2)

    stub_embedding = _make_stub_embedding_service()
    stub_ai = _make_stub_ai_manager(
        summary_text="STUB SUMMARY", summary_provider="stub-provider"
    )

    pipeline = ProcessingPipeline(
        db_connection=db,
        settings=settings,
        ai_manager=stub_ai,
        embedding_service=stub_embedding,
    )

    await pipeline.process_channel(CHAT_ID)

    # Inspect database
    conn = sqlite3.connect(str(snapshot_db))
    conn.row_factory = sqlite3.Row
    try:
        pass_rows = conn.execute(
            "SELECT * FROM processing_results "
            "WHERE pipeline_version='v2' AND llm_decision='pass'"
        ).fetchall()
        non_pass_rows = conn.execute(
            "SELECT * FROM processing_results "
            "WHERE pipeline_version='v2' AND llm_decision != 'pass'"
        ).fetchall()
    finally:
        conn.close()

    # Stub returns PASS at confidence 0.9 for every article that reaches the
    # gate, so we expect at least one pass row to exercise the summary path.
    assert len(pass_rows) >= 1, "Expected ≥1 v2 PASS row to exercise summary path"

    # Assertion 1: every PASS row has the stub summary in processing_results
    for r in pass_rows:
        assert r["summary"] == "STUB SUMMARY", (
            f"PASS row missing summary: {dict(r)}"
        )

    # Assertion 2: the linked articles row has summary + ai_provider set
    conn = sqlite3.connect(str(snapshot_db))
    conn.row_factory = sqlite3.Row
    try:
        for r in pass_rows:
            art = conn.execute(
                "SELECT summary, ai_provider FROM articles WHERE id = ?",
                (r["article_id"],),
            ).fetchone()
            assert art is not None, f"Article row missing for {r['article_id']}"
            assert art["summary"] == "STUB SUMMARY", (
                f"articles.summary not populated for {r['article_id']}: "
                f"{dict(art)}"
            )
            assert art["ai_provider"] == "stub-provider", (
                f"articles.ai_provider not set for {r['article_id']}: "
                f"{dict(art)}"
            )
    finally:
        conn.close()

    # Assertion 3: non-PASS rows must NOT have a summary in processing_results
    for r in non_pass_rows:
        assert r["summary"] is None, (
            f"Non-PASS row should not have summary: {dict(r)}"
        )

    # Assertion 4: generate_summary called exactly once per PASS row
    assert stub_ai.generate_summary.await_count == len(pass_rows), (
        f"generate_summary called {stub_ai.generate_summary.await_count} "
        f"times; expected {len(pass_rows)} (one per PASS row)"
    )
```

- [ ] **Step 3: Run the new test — it MUST fail**

Run:
```bash
source venv/bin/activate && pytest tests/integration/test_v2_against_snapshot.py::test_v2_pipeline_generates_summaries -v
```

Expected: FAIL on `assert r["summary"] == "STUB SUMMARY"` (current code never writes summary to `processing_results`). This proves the test exercises the gap.

- [ ] **Step 4: Commit the failing test**

```bash
git add tests/integration/test_v2_against_snapshot.py
git commit -m "test(v2): add failing test for AI summary generation in v2 pipeline"
```

---

### Task 2: Implement summary call in `_process_articles_v2`

**Files:**
- Modify: `culifeed/processing/pipeline.py` (around line 1186, the stage 4 loop)

- [ ] **Step 1: Replace the stage 4 loop body**

Locate `# Stage 4: LLM gate per article` (around line 1186). Replace the entire `for ... in zip(survivors, matches):` block with:

```python
        # Stage 4: LLM gate per article + AI summary on PASS
        for (article, pf_score), match in zip(survivors, matches):
            gate_result = None
            gate_error: Optional[str] = None
            if match.chosen is not None:
                try:
                    gate_result = await self._llm_gate.judge(article, match.chosen)
                except Exception as e:
                    self.logger.warning(
                        f"LLM gate failed for article {article.id}: {e}"
                    )
                    gate_error = str(e)

            # Generate AI summary for articles that pass the gate so Telegram
            # delivery renders the 🤖 line instead of the first-paragraph
            # fallback. Failure here must not block persistence of the gate
            # decision row.
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
                article=article,
                chat_id=chat_id,
                match=match,
                gate_result=gate_result,
                pre_filter_score=pf_score,
                gate_error=gate_error,
                mark_delivered=mark_delivered,
                summary=summary_text,
                summary_provider=summary_provider,
            )
```

- [ ] **Step 2: Do not run the test yet** — `_persist_v2_result` doesn't accept the new params. Proceed directly to Task 3.

---

### Task 3: Extend `_persist_v2_result` to write summary to both tables

**Files:**
- Modify: `culifeed/processing/pipeline.py` (around line 1222)

- [ ] **Step 1: Update signature**

Locate `def _persist_v2_result(` (around line 1222). Add the two new parameters:

```python
    def _persist_v2_result(
        self,
        article: Article,
        chat_id: str,
        match,  # MatchResult
        gate_result,  # Optional[GateResult]
        pre_filter_score: float,
        gate_error: Optional[str] = None,
        mark_delivered: bool = False,
        summary: Optional[str] = None,
        summary_provider: Optional[str] = None,
    ) -> None:
```

- [ ] **Step 2: Add `summary` to the processing_results INSERT**

Locate the SQL block (around line 1271). Replace it with:

```python
        with self.db.get_connection() as conn:
            conn.execute(
                """
                INSERT INTO processing_results(
                    article_id, chat_id, topic_name,
                    pre_filter_score, embedding_score, embedding_top_topics,
                    ai_relevance_score, confidence_score,
                    llm_decision, llm_reasoning, pipeline_version, delivered,
                    summary
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'v2', ?, ?)
                ON CONFLICT(article_id, chat_id, topic_name, pipeline_version)
                DO NOTHING
                """,
                (
                    article.id,
                    chat_id,
                    chosen_name,
                    pre_filter_score,
                    match.chosen_score,
                    top_topics_json,
                    match.chosen_score,
                    confidence,
                    decision,
                    reasoning,
                    delivered_value,
                    summary,
                ),
            )

            # When the gate passed and summary generation succeeded, mirror
            # the result onto the articles row so delivery's `SELECT a.*`
            # picks up summary + ai_provider (the latter drives the 🤖
            # indicator in digest_formatter.py).
            if summary is not None:
                conn.execute(
                    """
                    UPDATE articles
                    SET summary = ?,
                        ai_provider = ?,
                        ai_relevance_score = ?,
                        ai_confidence = ?,
                        ai_reasoning = ?
                    WHERE id = ?
                    """,
                    (
                        summary,
                        summary_provider or "v2_llm_gate",
                        confidence,  # gate_result.confidence — see decision logic above
                        confidence,
                        reasoning,
                        article.id,
                    ),
                )
            conn.commit()
```

Note: `confidence` and `reasoning` are local variables already set in this function from `gate_result` / `gate_error` (see existing code above the SQL block). For `summary is not None` to be true, `gate_result.passed` must have been true, so these values are guaranteed to come from a real gate decision — not the fallback `confidence=0.0` / `reasoning="no chosen topic"` paths.

- [ ] **Step 3: Run the new test — it MUST pass now**

Run:
```bash
source venv/bin/activate && pytest tests/integration/test_v2_against_snapshot.py::test_v2_pipeline_generates_summaries -v
```

Expected: PASS. All four assertions hold:
- PASS rows have `summary == "STUB SUMMARY"` in processing_results
- linked articles rows have `summary == "STUB SUMMARY"` and `ai_provider == "stub-provider"`
- non-PASS rows have NULL summary
- `generate_summary` await count equals PASS row count

- [ ] **Step 4: Run the existing v2 test to verify no regression**

Run:
```bash
source venv/bin/activate && pytest tests/integration/test_v2_against_snapshot.py -v
```

Expected: BOTH tests pass. The existing `test_v2_pipeline_against_snapshot` should still pass — it never asserted on summary, and the new params have safe defaults.

- [ ] **Step 5: Commit**

```bash
git add culifeed/processing/pipeline.py
git commit -m "fix(v2): generate AI summary on gate pass and persist to articles

When the v2 LLM gate passes, call ai_manager.generate_summary and write
the result to both processing_results.summary and articles.summary, with
ai_provider set so the digest formatter renders the 🤖 indicator.

Without this, v2-delivered Telegram messages fell back to the first
paragraph of article content (no 🤖 marker), making it look like the AI
summary feature was broken."
```

---

### Task 4: Run full test suite to check for unrelated regressions

**Files:** none

- [ ] **Step 1: Run the entire test suite**

Run:
```bash
source venv/bin/activate && python -m pytest -x --timeout=120
```

Expected: All tests pass. Pay attention to:
- `tests/integration/test_v2_against_snapshot.py` — both tests pass
- `tests/integration/test_backfill_v2.py` — uses `_persist_v2_result` indirectly; new optional params should not break it
- `tests/unit/test_pipeline_orchestration.py` — covers the pipeline class directly

If `test_backfill_v2.py` fails because of signature change: it shouldn't — both new params have `None` defaults — but if it does, fix the call site rather than the signature.

- [ ] **Step 2: If all passes, no commit needed** — Task 3 already committed the fix.

---

## Self-Review Notes

**Spec coverage:**
- "Generate summary on gate pass, no extra threshold" → Task 2 step 1 (`if gate_result.passed`)
- "Write summary to both tables" → Task 3 step 2 (INSERT + UPDATE)
- "ai_provider set on articles row" → Task 3 step 2 (UPDATE includes ai_provider)
- "Graceful degradation on summary failure" → Task 2 step 1 (try/except, gate row still written)
- "Test for PASS/non-PASS behavior + call count" → Task 1 step 2 (4 assertions)
- "No backfill, no formatter change, no v1 change" → Out of scope, not in any task ✓

**Placeholder scan:** No TBDs, no "add error handling," no "similar to" references, all code is concrete.

**Type consistency:** `summary` and `summary_provider` use the same names everywhere they appear (Task 1 stub, Task 2 caller, Task 3 signature, Task 3 SQL parameters).
