# Topic-Matching Pipeline Redesign

**Date**: 2026-04-29
**Status**: Design approved, awaiting implementation plan
**Scope**: Replace the article ↔ topic matching pipeline end-to-end (filter, match, topic config)

## Problem

Diagnostic on the production database (96 processing results, 13 active topics) shows the current pipeline fails on all three symptoms simultaneously:

- **False positives**: irrelevant articles delivered with high scores
- **False negatives**: relevant articles dropped
- **Wrong-topic assignment**: articles assigned to topics they have nothing to do with

Concrete examples from `processing_results` (live data):

| Topic | Article | AI score |
|---|---|---|
| aws serverless with lambda function | "SmartBear's Swagger update targets the API drift problem AI coding tools created" | 1.0 |
| AI engineering | "Canonical expands Ubuntu support to MediaTek Genio chips" | 0.89 |
| AI engineering | "SRE Weekly Issue #513" | 1.0 |
| cloudflare cdn waf… | "Orchestrating AI Code Review at scale" | 1.0 |
| exploit development | "After Bluesky, Mastodon Targeted in DDoS Attack" | 0.9 |

Average AI relevance score across all topics: 0.66–0.86 — no topic discriminates.

### Root causes

1. **Topics are judged in isolation**: the AI is asked "Is this article about Topic X?" once per topic. The model never sees the alternatives, so it can't choose between them. Articles routinely score 0.9+ for two unrelated topics.
2. **Topic keywords are generic and overlap**: `linux` appears in 3 topics, `automation` in 3, `performance`/`container` in 2 each. Some topics include keywords like `best practices`, `new update`, `new feature` that match almost any tech article.
3. **`exclude_keywords` is empty for all 13 topics** — a disambiguation tool exists but is unused.
4. **The AI prompt has no calibration**: "rate relevance 0.0–1.0" with no anchor or refusal cue → the model is biased toward high scores.
5. **`pre_filter_score` is NULL in all 96 processing_results** — the column exists but is never written. We can't diagnose the pre-filter at all.
6. **`ai_reasoning` stores router metadata, not actual reasoning**: every entry says `"Smart routing (confident): score=X"`. The model's actual REASONING output from the prompt is parsed and discarded before storage.
7. **Topic names are inconsistent**: some are 1 word, some are 20-word sentences (with typos), used verbatim in prompts as classification labels.

## Solution: Hybrid 4-stage pipeline

```
Article  →  [1] Keyword pre-filter  →  [2] Embedding ranker  →  [3] LLM gate  →  [4] Persist + deliver
              (volume reduction)         (cross-topic ranking)    (yes/no judgment)
```

### Stage 1 — Keyword pre-filter (existing, fixed)

Same TF/phrase scoring as today. Two changes:
- Threshold lowered to `0.05` (volume reduction is its only job; ranking moves to stage 2).
- `pre_filter_score` is actually persisted (root cause #5).
- No longer assigns a topic — it only decides "should this reach the embedding stage?".

### Stage 2 — Embedding ranker (new)

For each surviving article:
- Embed `title + content[:1500]` using OpenAI `text-embedding-3-small` (1536 dims).
- Cosine-similarity against every active topic's cached embedding.
- Pick top-1 topic if score ≥ `embedding_min_score` (default `0.45`).
- Below threshold: drop without an LLM call. The article is logged with `embedding_top_topics` for diagnostics.

This stage is the structural fix for root cause #1: every topic competes for the article in a single comparison. An article cannot score 1.0 for two unrelated topics.

### Stage 3 — LLM gate (replaces today's relevance scoring)

For the chosen topic only:
- Single yes/no judgment via `LLMGate.judge(article, topic)`.
- Prompt is calibrated and instructs refusal on tangential matches.
- Returns `pass | fail`, confidence (0–1), and the model's actual reasoning sentence.
- Reuses existing `AIManager` for provider selection and fallback chain.

Token cost drops ~10× vs today (one judgment per article instead of N relevance scores).

### Stage 4 — Persist + deliver

- All four scores stored: `pre_filter_score`, `embedding_score`, `llm_confidence`, plus the runner-up topics from the embedding stage.
- `llm_reasoning` stores the model's actual sentence (root cause #6 fixed).
- Delivery rule: `llm_decision = 'pass' AND llm_confidence ≥ topic.confidence_threshold`.
- Graceful degradation: if LLM gate fails, deliver based on `embedding_score ≥ embedding_fallback_threshold` (default `0.65`).

## Data model

### Schema changes (additive, no destructive migration)

**`topics` table — gain a description:**

```sql
ALTER TABLE topics ADD COLUMN description TEXT;
ALTER TABLE topics ADD COLUMN embedding_signature TEXT;
ALTER TABLE topics ADD COLUMN embedding_updated_at TIMESTAMP;
```

- `description`: 1–2 natural-language sentences. LLM-drafted on `/addtopic`, user-editable. NULL means "fall back to name + keywords joined."
- `embedding_signature`: SHA-256 of `name | description | sorted(keywords)`. Mismatch with stored signature triggers re-embedding on next pipeline run.

**New `topic_embeddings` virtual table (sqlite-vec):**

```sql
CREATE VIRTUAL TABLE topic_embeddings USING vec0(
    topic_id INTEGER PRIMARY KEY,
    embedding FLOAT[1536]
);
```

**New `article_embeddings` virtual table (sqlite-vec):**

```sql
CREATE VIRTUAL TABLE article_embeddings USING vec0(
    article_id TEXT PRIMARY KEY,
    embedding FLOAT[1536]
);
```

Storage estimate: 1536 dims × 4 bytes × 754 articles ≈ 4.6 MB today. At 100k articles ≈ 600 MB. Acceptable for SQLite. Pruned to 90 days (configurable).

**`processing_results` — gain observability columns:**

```sql
ALTER TABLE processing_results ADD COLUMN embedding_score REAL;
ALTER TABLE processing_results ADD COLUMN embedding_top_topics TEXT;  -- JSON: top-3 candidates
ALTER TABLE processing_results ADD COLUMN llm_decision TEXT;          -- 'pass' | 'fail' | 'skipped'
ALTER TABLE processing_results ADD COLUMN llm_reasoning TEXT;
ALTER TABLE processing_results ADD COLUMN pipeline_version TEXT DEFAULT 'v1';  -- 'v1' | 'v2'
```

`pipeline_version` distinguishes legacy from new-pipeline rows during shadow mode. Delivery filters by version: old path reads v1 rows, new path reads v2 rows. After cutover, v1 rows are retained for historical comparison.

The existing `UNIQUE(article_id, chat_id, topic_name)` constraint must be widened to `UNIQUE(article_id, chat_id, topic_name, pipeline_version)` so v1 and v2 rows can coexist. SQLite handles this via table rebuild (drop + recreate index).

### New runtime dependency

```
sqlite-vec >= 0.1.0    # MIT, bundled wheels for linux/mac
```

Loaded at `DatabaseConnection` init via `conn.enable_load_extension(True); conn.load_extension("vec0")`.

## Components

### `culifeed/ai/embedding_service.py` (new)

```python
class EmbeddingService:
    def __init__(self, api_key: str, model: str = "text-embedding-3-small"): ...
    async def embed(self, text: str) -> list[float]: ...
    async def embed_batch(self, texts: list[str]) -> list[list[float]]: ...
```

- Truncates inputs to 8192 tokens (model limit).
- Reuses `recovery/retry_logic.py` for retry/backoff.
- Raises `AIError(error_code=ErrorCode.AI_EMBEDDING_ERROR)` on hard failure — no silent fallback to zero vectors.

### `culifeed/storage/vector_store.py` (new)

```python
class VectorStore:
    def __init__(self, db: DatabaseConnection): ...
    def upsert_topic_embedding(self, topic_id: int, vec: list[float]) -> None: ...
    def upsert_article_embedding(self, article_id: str, vec: list[float]) -> None: ...
    def rank_topics_for_article(self, article_id: str,
                                active_topic_ids: list[int],
                                top_k: int = 3) -> list[tuple[int, float]]: ...
    def prune_articles_older_than(self, days: int) -> int: ...
```

Cosine-similarity ranking is one SQL call against `topic_embeddings`.

### `culifeed/processing/topic_matcher.py` (new)

```python
@dataclass
class MatchResult:
    article_id: str
    top_topics: list[tuple[Topic, float]]   # top 3
    chosen: Optional[Topic]
    chosen_score: float

class TopicMatcher:
    async def ensure_topic_embeddings(self, topics: list[Topic]) -> None: ...
    async def match(self, article: Article, topics: list[Topic]) -> MatchResult: ...
```

`ensure_topic_embeddings` recomputes any topic whose stored `embedding_signature` differs from the live one.

### `culifeed/processing/llm_gate.py` (new)

```python
class LLMGate:
    async def judge(self, article: Article, topic: Topic) -> GateResult: ...
```

Prompt template:

```
You are deciding whether an article is centrally about a topic.

TOPIC: <topic.name>
DESCRIPTION: <topic.description, or "<name>. Keywords: <keywords>" if description is NULL>
KEYWORDS: <topic.keywords>

ARTICLE TITLE: <title>
ARTICLE BODY: <first 1500 chars>

Decide:
- "PASS" only if the article's CENTRAL subject matches the topic.
  Tangential mentions, passing references, or different-but-adjacent
  subjects = FAIL.
- Confidence: 0.9+ = strongly central, 0.7 = clearly relevant,
  0.5 = borderline.

Respond in this exact format:
DECISION: PASS | FAIL
CONFIDENCE: 0.0-1.0
REASONING: <one sentence>
```

### `culifeed/processing/pipeline.py` (refactored, ~30% smaller)

Becomes a thin orchestrator over the new components. Heavy logic moves out.

### Bot UX — `/addtopic` flow

After a user enters name + keywords, the bot:
1. Calls a `TopicDescriptionGenerator` (one LLM call) to draft a description.
2. Replies: *"I drafted this description for your topic. Reply 'ok' to accept, or send a corrected version."*
3. Stores the confirmed description and updates `embedding_signature`.

For the existing 13 topics: a one-time migration script generates descriptions in a single batch (no per-topic confirmation; users edit later via a new `/edittopic` command).

### Deletions

`culifeed/processing/smart_analyzer.py` (571 LOC) becomes redundant — its job is replaced by the embedding stage gating which articles reach the LLM. Deleted in cleanup phase after 2 weeks of stable operation.

## Error handling

| Failure | Behavior | Error code |
|---|---|---|
| Embedding API 5xx / timeout | Retry w/ backoff (3 attempts). Skip stage for run; mark articles for retry. **Do not deliver.** | `A005` AI_EMBEDDING_ERROR (new) |
| Embedding API 429 | Honor `Retry-After`; queue remainder for next tick | `A002` (existing) |
| `sqlite-vec` extension fails to load | Hard fail at startup with clear error | `D009` VECTOR_STORE_UNAVAILABLE (new) |
| Topic embedding stale, recompute fails | Skip that topic for this run; other topics unaffected | `A005` |
| LLM gate fails for an article | Fall back to embedding-only delivery if `embedding_score ≥ embedding_fallback_threshold`. Mark `llm_decision='skipped'`. | `A001` (existing) |
| Topic deleted mid-run | Skip results for that topic_id at persist | — |
| Article body empty/null | Use title only; if also empty, drop with `pre_filter_reason='empty content'` | `F004` CONTENT_EMPTY (new) |

A single failure (one article, one topic, one API call) never aborts the channel run.

## Configuration (additions to `FilteringSettings`)

```python
embedding_provider: str = "openai"
embedding_model: str = "text-embedding-3-small"
embedding_min_score: float = 0.45        # below this → drop pre-LLM
embedding_fallback_threshold: float = 0.65  # for LLM-failure delivery
embedding_retention_days: int = 90       # vector pruning
use_embedding_pipeline: bool = False     # feature flag for shadow mode
```

## Observability

- Every `processing_results` row carries the full diagnostic chain: pre_filter_score, embedding_score, embedding_top_topics (JSON), llm_decision, llm_confidence, llm_reasoning.
- New CLI command: `culifeed diagnose <article_id>` prints all four scores, top-3 topic candidates, and the model's reasoning. Answers "why did this article match topic X?" in one command.
- Per-stage timing logged via existing `PerformanceLogger`.

## Testing

### Unit tests (mocked external APIs)

- `test_embedding_service.py`: batching, truncation, retry, error → `AIError(A005)`.
- `test_vector_store.py`: real sqlite-vec in-memory; upsert, ranking with hand-crafted vectors, prune.
- `test_topic_matcher.py`: stub embeddings + vector store; assert top-k, threshold, stale-signature triggers re-embed.
- `test_llm_gate.py`: stub provider; prompt snapshot, response parsing (PASS/FAIL/CONFIDENCE), malformed-response handling.
- `test_pipeline_v2.py`: stubs end-to-end; **regression-asserts `pre_filter_score IS NOT NULL`** in stored rows; assert one stage failing for one article doesn't abort the run.

### Integration tests (real SQLite + sqlite-vec, mocked OpenAI)

- Seeded topics + articles; assert correct topic chosen and correct rows in `processing_results`.

### Evaluation harness (new)

- `scripts/eval_matching.py` reads a hand-labeled CSV (`article_id, expected_topic`), runs the pipeline, prints precision/recall and confusion matrix per topic.
- Seed corpus: ~50 articles drawn from the existing 754, hand-labeled. Used to (a) validate the new pipeline before flipping the flag, (b) regression-check future changes.

### Success criteria

- ≥ 90% top-1 topic accuracy on the labeled set (current ~50% based on audit).
- Zero `pre_filter_score IS NULL` in new processing_results rows.
- `llm_reasoning` is the model's actual sentence, not router metadata.
- No regression in delivery latency (currently <30s/channel).

## Migration / rollout

1. Ship schema changes + new modules behind feature flag `use_embedding_pipeline` (default OFF).
2. Backfill script generates topic descriptions + embeddings for the 13 existing topics.
3. Run the backfill script (bypasses the feature flag) to re-process the existing 754 articles through the new pipeline. Results are written to `processing_results` with `pipeline_version='v2'`. Output a CSV diff (v1 vs v2 scores per article) for review.
4. Enable the flag → new pipeline runs in **shadow mode** for one week: it writes v2 rows alongside v1 rows, but delivery still reads v1. Compare side-by-side.
5. Flip the flag → new path is primary.
6. After 2 weeks of stable operation, delete `smart_analyzer.py` and the old relevance-scoring code paths in `ai_manager.py`.

## Out of scope

- Replacing the keyword pre-filter algorithm itself (only the threshold and persistence change).
- Migrating away from SQLite to a dedicated vector DB.
- Multi-language support for embeddings (current corpus is English).
- Per-user/per-channel topic personalization beyond what already exists.
