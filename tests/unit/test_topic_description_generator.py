"""Tests for TopicDescriptionGenerator."""
import pytest
from unittest.mock import AsyncMock, MagicMock

from culifeed.processing.topic_description_generator import TopicDescriptionGenerator


@pytest.mark.asyncio
async def test_generate_returns_short_description():
    ai = MagicMock()
    ai.complete = AsyncMock(return_value=MagicMock(
        success=True,
        content="AWS Lambda serverless compute updates and best practices.",
        error_message=None,
    ))
    gen = TopicDescriptionGenerator(ai)
    desc = await gen.generate(name="AWS Lambda updates",
                              keywords=["lambda", "serverless"])
    assert "Lambda" in desc
    assert len(desc) < 300


@pytest.mark.asyncio
async def test_generate_strips_quotes_and_whitespace():
    ai = MagicMock()
    ai.complete = AsyncMock(return_value=MagicMock(
        success=True,
        content='   "Some description text."   ',
        error_message=None,
    ))
    gen = TopicDescriptionGenerator(ai)
    desc = await gen.generate(name="X", keywords=["a"])
    assert desc == "Some description text."


@pytest.mark.asyncio
async def test_generate_caps_at_300_chars():
    ai = MagicMock()
    ai.complete = AsyncMock(return_value=MagicMock(
        success=True, content="x" * 500, error_message=None,
    ))
    gen = TopicDescriptionGenerator(ai)
    desc = await gen.generate(name="X", keywords=["a"])
    assert len(desc) <= 300


@pytest.mark.asyncio
async def test_generate_falls_back_on_failure():
    ai = MagicMock()
    ai.complete = AsyncMock(return_value=MagicMock(
        success=False, content=None, error_message="provider failure"))
    gen = TopicDescriptionGenerator(ai)
    desc = await gen.generate(name="X", keywords=["a", "b"])
    # Falls back to a deterministic string built from name+keywords
    assert "X" in desc
    assert "a" in desc and "b" in desc


@pytest.mark.asyncio
async def test_generate_falls_back_on_empty_content():
    ai = MagicMock()
    ai.complete = AsyncMock(return_value=MagicMock(
        success=True, content="   ", error_message=None,
    ))
    gen = TopicDescriptionGenerator(ai)
    desc = await gen.generate(name="X", keywords=["a"])
    assert "X" in desc
    assert "a" in desc


def test_prompt_mentions_name_and_keywords():
    gen = TopicDescriptionGenerator(MagicMock())
    prompt = gen._build_prompt("MyTopic", ["k1", "k2"])
    assert "MyTopic" in prompt
    assert "k1" in prompt
    assert "k2" in prompt
