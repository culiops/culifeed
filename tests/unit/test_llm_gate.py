"""Tests for LLMGate (v2 yes/no judgment over article+topic)."""
import pytest
from unittest.mock import AsyncMock, MagicMock

from culifeed.database.models import Article, Topic
from culifeed.processing.llm_gate import LLMGate, GateResult


def _topic():
    return Topic(
        id=1, chat_id="c", name="AWS Lambda",
        keywords=["lambda", "aws"], exclude_keywords=[],
        description="AWS Lambda serverless compute updates and patterns.",
        confidence_threshold=0.7, active=True,
    )


def _article(title="Lambda news", content="AWS Lambda announces new feature"):
    return Article(
        id="a", title=title, url="http://example.com/a",
        content=content,
        source_feed="https://example.com/feed.xml",
        content_hash="h",
    )


@pytest.mark.asyncio
async def test_judge_passes_on_clear_match():
    ai = MagicMock()
    ai.complete = AsyncMock(return_value=MagicMock(
        success=True,
        content="DECISION: PASS\nCONFIDENCE: 0.92\nREASONING: Article is centrally about Lambda.",
        error_message=None,
    ))
    gate = LLMGate(ai)
    res = await gate.judge(_article(), _topic())
    assert res.passed is True
    assert res.confidence == 0.92
    assert "centrally about" in res.reasoning


@pytest.mark.asyncio
async def test_judge_fails_on_tangential():
    ai = MagicMock()
    ai.complete = AsyncMock(return_value=MagicMock(
        success=True,
        content="DECISION: FAIL\nCONFIDENCE: 0.3\nREASONING: Lambda only mentioned in passing.",
        error_message=None,
    ))
    gate = LLMGate(ai)
    res = await gate.judge(_article(), _topic())
    assert res.passed is False
    assert res.confidence == 0.3


@pytest.mark.asyncio
async def test_judge_handles_malformed_response():
    ai = MagicMock()
    ai.complete = AsyncMock(return_value=MagicMock(
        success=True, content="garbage with no structure", error_message=None,
    ))
    gate = LLMGate(ai)
    res = await gate.judge(_article(), _topic())
    assert res.passed is False
    assert res.confidence == 0.0


@pytest.mark.asyncio
async def test_judge_handles_provider_failure():
    ai = MagicMock()
    ai.complete = AsyncMock(return_value=MagicMock(
        success=False, content=None,
        error_message="all providers failed",
    ))
    gate = LLMGate(ai)
    res = await gate.judge(_article(), _topic())
    assert res.passed is False
    assert res.confidence == 0.0
    assert "all providers failed" in res.reasoning


@pytest.mark.asyncio
async def test_judge_clamps_confidence_to_unit_range():
    ai = MagicMock()
    ai.complete = AsyncMock(return_value=MagicMock(
        success=True,
        content="DECISION: PASS\nCONFIDENCE: 1.5\nREASONING: too confident",
        error_message=None,
    ))
    gate = LLMGate(ai)
    res = await gate.judge(_article(), _topic())
    assert res.confidence == 1.0


def test_prompt_includes_topic_description():
    gate = LLMGate(MagicMock())
    prompt = gate._build_gate_prompt(_article(content="x"), _topic())
    assert "AWS Lambda serverless compute" in prompt
    assert "DECISION: PASS | FAIL" in prompt
    assert "centrally" in prompt.lower()


def test_prompt_falls_back_to_keywords_when_no_description():
    gate = LLMGate(MagicMock())
    t = Topic(id=2, chat_id="c", name="X", keywords=["k1", "k2"], confidence_threshold=0.5, active=True)
    prompt = gate._build_gate_prompt(_article(content="x"), t)
    assert "X" in prompt
    assert "k1" in prompt
