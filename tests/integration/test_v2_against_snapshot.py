"""
D4: v2 pipeline smoke test against a copy of the production database snapshot.

Skips automatically when /tmp/culifeed_snapshot.db is absent.  When present the
test:
  1. Copies the snapshot to a pytest tmp_path so the original is never mutated.
  2. Runs DatabaseSchema.create_tables() (idempotent migration).
  3. Seeds the copy with one feed and one topic (the snapshot has 96 articles
     but no feeds/topics; the pipeline needs both to process anything).
  4. Stubs EmbeddingService and AIManager.complete — no real API calls, no cost.
  5. Runs pipeline.process_channel() with use_embedding_pipeline=True.
  6. Asserts that ≥1 v2 processing_results row was written with non-null
     pre_filter_score, embedding_score, and llm_decision.

Any exception during the run is a hard failure — surfaced bugs are the whole
point of this test.
"""

import hashlib
import json
import shutil
import sqlite3
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

# Ensure the project root is on sys.path so imports work regardless of how
# pytest is invoked.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

# ---------------------------------------------------------------------------
# Snapshot guard
# ---------------------------------------------------------------------------

SNAPSHOT_PATH = Path("/tmp/culifeed_snapshot.db")

pytestmark = pytest.mark.integration


@pytest.fixture
def snapshot_db(tmp_path):
    """Copy the production snapshot to an isolated tmp location."""
    dest = tmp_path / "culifeed_snapshot_copy.db"
    shutil.copy2(str(SNAPSHOT_PATH), str(dest))
    return dest


# ---------------------------------------------------------------------------
# Stub helpers
# ---------------------------------------------------------------------------

def _fake_embed(text: str) -> list:
    """Return a deterministic, non-zero 1536-dim vector derived from text hash.

    vec_distance_cosine is undefined for all-zero vectors, so we use a hash to
    derive a value in (-0.5, 0.5) per dimension.
    """
    h = hashlib.sha256(text.encode()).digest()
    return [(h[i % len(h)] / 255.0) - 0.5 for i in range(1536)]


def _make_stub_embedding_service():
    """Return a MagicMock EmbeddingService whose embed/embed_batch are async."""

    svc = MagicMock()

    async def _embed(text):
        return _fake_embed(text)

    async def _embed_batch(texts):
        return [_fake_embed(t) for t in texts]

    svc.embed = AsyncMock(side_effect=_embed)
    svc.embed_batch = AsyncMock(side_effect=_embed_batch)
    return svc


def _make_stub_ai_manager(summary_text: str = "STUB SUMMARY",
                         summary_provider: str = "stub-provider"):
    """Return a MagicMock AIManager whose complete() always returns PASS.

    generate_summary() returns an AIResult populated with the provided
    summary_text and summary_provider.
    """

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


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------

CHAT_ID = "-100123"          # The only channel present in the snapshot
FEED_URL = "https://feed.com"  # All 96 articles reference this source_feed


def _seed_feed_and_topic(db_path: Path) -> None:
    """Prepare the snapshot copy for the v2 smoke test.

    The snapshot has 96 articles with source_feed='https://feed.com' but:
    - No feeds table rows (the JOIN in _get_unprocessed_articles needs one).
    - No topics rows.
    - 96 pre-existing processing_results rows (v1-era, no pipeline_version
      column) that would cause _get_unprocessed_articles to skip all articles
      since it filters on pr.article_id IS NULL.

    Remediation:
    1. Clear the stale v1 processing_results so articles appear unprocessed.
    2. Refresh article created_at timestamps to 'now' so the -2 day recency
       window in _get_unprocessed_articles doesn't exclude snapshot rows that
       may be older than 2 days when the test is run.
    3. Insert a feed row linking FEED_URL to CHAT_ID.
    4. Insert one active topic whose keyword ("title") matches every
       "Title N" article in the snapshot.
    """
    conn = sqlite3.connect(str(db_path))
    try:
        # Clear legacy processing_results — these are v1 rows without a
        # pipeline_version column; the migration will add the column but the
        # rows would still block _get_unprocessed_articles (pr.article_id IS
        # NULL filter).  Clearing them is safe because this is a test copy.
        conn.execute("DELETE FROM processing_results")

        # Refresh created_at so the -2 day recency filter in
        # _get_unprocessed_articles always lets snapshot articles through,
        # regardless of when the test is run.
        conn.execute("UPDATE articles SET created_at = datetime('now')")

        # Feed: binds FEED_URL to CHAT_ID so _get_unprocessed_articles JOIN works
        conn.execute(
            """
            INSERT OR IGNORE INTO feeds (chat_id, url, title, active)
            VALUES (?, ?, ?, 1)
            """,
            (CHAT_ID, FEED_URL, "Smoke-test feed"),
        )

        # Topic: keyword "title" matches every "Title N" article
        conn.execute(
            """
            INSERT OR IGNORE INTO topics
                (chat_id, name, keywords, exclude_keywords, active, description)
            VALUES (?, ?, ?, ?, 1, ?)
            """,
            (
                CHAT_ID,
                "Smoke-test topic",
                json.dumps(["title"]),
                json.dumps([]),
                "A topic whose keyword matches every seeded article.",
            ),
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Main smoke test
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not SNAPSHOT_PATH.exists(),
    reason=f"Production snapshot not found at {SNAPSHOT_PATH}",
)
@pytest.mark.asyncio
async def test_v2_pipeline_against_snapshot(snapshot_db):
    """Run the v2 embedding pipeline against a prod snapshot copy.

    Asserts:
    - No exceptions propagate out of process_channel().
    - At least one processing_results row with pipeline_version='v2' is written.
    - Every v2 row has non-null pre_filter_score, embedding_score, llm_decision.
    """
    # ------------------------------------------------------------------
    # Step 1: migrate schema (must be idempotent on a populated database)
    # ------------------------------------------------------------------
    from culifeed.database.schema import DatabaseSchema

    schema = DatabaseSchema(str(snapshot_db))
    schema.create_tables()  # should not raise

    # ------------------------------------------------------------------
    # Step 2: seed feed + topic
    # ------------------------------------------------------------------
    _seed_feed_and_topic(snapshot_db)

    # ------------------------------------------------------------------
    # Step 3: build settings with embedding pipeline enabled
    #
    # IMPORTANT: never mutate the global settings singleton — doing so leaks
    # state into unit tests that run later in the same process, causing them
    # to unexpectedly hit the v2/embedding code path.  Instead, deep-copy the
    # singleton and modify only the copy.
    # ------------------------------------------------------------------
    import copy
    from culifeed.config.settings import get_settings

    settings = copy.deepcopy(get_settings())
    settings.filtering.use_embedding_pipeline = True
    # Provide a dummy key so the lazy EmbeddingService creation code path
    # doesn't error before our stub is injected (the stub overrides the actual
    # service, so this key is never sent to the network).
    settings.ai.openai_api_key = "test-dummy-key"

    # ------------------------------------------------------------------
    # Step 4: wire database connection to the snapshot copy
    # ------------------------------------------------------------------
    from culifeed.database.connection import DatabaseConnection

    db = DatabaseConnection(str(snapshot_db), pool_size=2)

    # ------------------------------------------------------------------
    # Step 5: instantiate pipeline with stubs
    # ------------------------------------------------------------------
    from culifeed.processing.pipeline import ProcessingPipeline

    stub_embedding = _make_stub_embedding_service()
    stub_ai = _make_stub_ai_manager()

    pipeline = ProcessingPipeline(
        db_connection=db,
        settings=settings,
        ai_manager=stub_ai,
        embedding_service=stub_embedding,
    )

    # ------------------------------------------------------------------
    # Step 6: run — must not raise
    # ------------------------------------------------------------------
    result = await pipeline.process_channel(CHAT_ID)

    # process_channel returns an empty PipelineResult for v2 (by design);
    # what matters is what landed in the database.

    # ------------------------------------------------------------------
    # Step 7: inspect database for v2 rows
    # ------------------------------------------------------------------
    conn = sqlite3.connect(str(snapshot_db))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT * FROM processing_results WHERE pipeline_version = 'v2'"
        ).fetchall()
    finally:
        conn.close()

    # Primary assertion: at least one v2 row must have been written
    assert len(rows) >= 1, (
        "Expected ≥1 v2 processing_results row, but found none. "
        "The v2 pipeline may have exited early without processing any articles."
    )

    # Integrity assertions: all v2 rows must have the three key v2 fields
    null_pre_filter = [r for r in rows if r["pre_filter_score"] is None]
    null_embedding = [r for r in rows if r["embedding_score"] is None]
    null_decision = [r for r in rows if r["llm_decision"] is None]

    assert not null_pre_filter, (
        f"{len(null_pre_filter)} v2 row(s) have NULL pre_filter_score: "
        f"{[dict(r) for r in null_pre_filter[:3]]}"
    )
    assert not null_embedding, (
        f"{len(null_embedding)} v2 row(s) have NULL embedding_score: "
        f"{[dict(r) for r in null_embedding[:3]]}"
    )
    assert not null_decision, (
        f"{len(null_decision)} v2 row(s) have NULL llm_decision: "
        f"{[dict(r) for r in null_decision[:3]]}"
    )

    # Sanity: report what we found (visible with -v or -s)
    decisions = {}
    for r in rows:
        d = r["llm_decision"]
        decisions[d] = decisions.get(d, 0) + 1
    print(
        f"\n[smoke] v2 rows written: {len(rows)}, "
        f"decision breakdown: {decisions}"
    )


@pytest.mark.skipif(
    not SNAPSHOT_PATH.exists(),
    reason=f"Production snapshot not found at {SNAPSHOT_PATH}",
)
@pytest.mark.asyncio
async def test_v2_pipeline_generates_summaries(snapshot_db):
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

        # Stub returns PASS at confidence 0.9 for every article that reaches the
        # gate, so we expect at least one pass row to exercise the summary path.
        assert len(pass_rows) >= 1, "Expected ≥1 v2 PASS row to exercise summary path"

        # Assertion 1: every PASS row has the stub summary in processing_results
        for r in pass_rows:
            assert r["summary"] == "STUB SUMMARY", (
                f"PASS row missing summary: {dict(r)}"
            )

        # Assertion 2: the linked articles row has summary + ai_provider set
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

        # Assertion 3: non-PASS rows must NOT have a summary in processing_results
        for r in non_pass_rows:
            assert r["summary"] is None, (
                f"Non-PASS row should not have summary: {dict(r)}"
            )

        # Assertion 4 (Important #1): non-PASS rows must NOT have a summary
        # in articles either — spec requires NULL in both tables.
        for r in non_pass_rows:
            art = conn.execute(
                "SELECT summary FROM articles WHERE id = ?",
                (r["article_id"],),
            ).fetchone()
            assert art is not None, (
                f"Article row missing for non-PASS row {dict(r)}"
            )
            assert art["summary"] is None, (
                f"articles.summary should be NULL for non-PASS row {dict(r)}: "
                f"got {dict(art)}"
            )

    finally:
        conn.close()

    # Assertion 5: generate_summary called exactly once per PASS row
    assert stub_ai.generate_summary.await_count == len(pass_rows), (
        f"generate_summary called {stub_ai.generate_summary.await_count} "
        f"times; expected {len(pass_rows)} (one per PASS row)"
    )
