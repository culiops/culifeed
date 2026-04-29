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


def _make_stub_ai_manager():
    """Return a MagicMock AIManager whose complete() always returns PASS."""

    pass_response = MagicMock()
    pass_response.success = True
    pass_response.content = (
        "DECISION: PASS\nCONFIDENCE: 0.9\nREASONING: Article is on-topic."
    )
    pass_response.error_message = None

    mgr = MagicMock()
    mgr.complete = AsyncMock(return_value=pass_response)

    # analyze_relevance and generate_summary are not called in the v2 path but
    # guard against accidental invocations reaching real providers.
    from culifeed.ai.providers.base import AIResult
    mgr.analyze_relevance = AsyncMock(
        return_value=AIResult(success=False, relevance_score=0.0, confidence=0.0)
    )
    mgr.generate_summary = AsyncMock(return_value=None)
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
    2. Insert a feed row linking FEED_URL to CHAT_ID.
    3. Insert one active topic whose keyword ("title") matches every
       "Title N" article in the snapshot.
    """
    conn = sqlite3.connect(str(db_path))
    try:
        # Clear legacy processing_results — these are v1 rows without a
        # pipeline_version column; the migration will add the column but the
        # rows would still block _get_unprocessed_articles (pr.article_id IS
        # NULL filter).  Clearing them is safe because this is a test copy.
        conn.execute("DELETE FROM processing_results")

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
async def test_v2_pipeline_against_snapshot(snapshot_db, tmp_path):
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
