"""Tests for the EmbeddingService (OpenAI embeddings wrapper)."""
import pytest
from unittest.mock import AsyncMock, MagicMock

from culifeed.ai.embedding_service import EmbeddingService
from culifeed.utils.exceptions import AIError, ErrorCode


@pytest.fixture
def fake_openai_response():
    resp = MagicMock()
    resp.data = [MagicMock(embedding=[0.1] * 1536)]
    resp.usage = MagicMock(total_tokens=10)
    return resp


@pytest.mark.asyncio
async def test_embed_returns_vector(fake_openai_response):
    svc = EmbeddingService(api_key="fake")
    svc._client.embeddings.create = AsyncMock(return_value=fake_openai_response)
    vec = await svc.embed("hello world")
    assert len(vec) == 1536
    assert all(isinstance(x, float) for x in vec)


@pytest.mark.asyncio
async def test_embed_batch_chunks_inputs(fake_openai_response):
    svc = EmbeddingService(api_key="fake")
    fake_openai_response.data = [MagicMock(embedding=[0.1] * 1536) for _ in range(3)]
    svc._client.embeddings.create = AsyncMock(return_value=fake_openai_response)
    vecs = await svc.embed_batch(["a", "b", "c"])
    assert len(vecs) == 3
    svc._client.embeddings.create.assert_awaited_once()


@pytest.mark.asyncio
async def test_embed_truncates_long_input(fake_openai_response):
    svc = EmbeddingService(api_key="fake")
    long_text = "word " * 20000  # ~20k tokens
    captured = {}
    async def capture(**kwargs):
        captured["input"] = kwargs["input"]
        return fake_openai_response
    svc._client.embeddings.create = AsyncMock(side_effect=capture)
    await svc.embed(long_text)
    # Should be truncated to <=8192 tokens (rough char budget: ~32k chars).
    # captured["input"] is the list passed in; the first element is the truncated text.
    assert len(captured["input"][0]) <= 32_768


@pytest.mark.asyncio
async def test_embed_api_failure_raises_aierror():
    svc = EmbeddingService(api_key="fake")
    svc._client.embeddings.create = AsyncMock(side_effect=Exception("boom"))
    with pytest.raises(AIError) as exc:
        await svc.embed("text")
    assert exc.value.error_code == ErrorCode.AI_EMBEDDING_ERROR


@pytest.mark.asyncio
async def test_embed_batch_empty_returns_empty():
    svc = EmbeddingService(api_key="fake")
    svc._client.embeddings.create = AsyncMock()  # should not be called
    result = await svc.embed_batch([])
    assert result == []
    svc._client.embeddings.create.assert_not_awaited()
