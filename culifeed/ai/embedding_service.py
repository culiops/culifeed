"""OpenAI embeddings client wrapper for the v2 topic-matching pipeline."""

from typing import List

from openai import AsyncOpenAI

from ..utils.exceptions import AIError, ErrorCode
from ..utils.logging import get_logger_for_component


# Rough char budget for 8192 tokens at ~4 chars/token
_MAX_INPUT_CHARS = 32_000
# OpenAI embeddings API accepts up to 2048 inputs per call
_MAX_BATCH = 2048


class EmbeddingService:
    """Thin wrapper around OpenAI's embeddings API."""

    def __init__(self, api_key: str, model: str = "text-embedding-3-small"):
        self._client = AsyncOpenAI(api_key=api_key)
        self._model = model
        self._logger = get_logger_for_component("embedding_service")

    @staticmethod
    def _truncate(text: str) -> str:
        return text[:_MAX_INPUT_CHARS] if len(text) > _MAX_INPUT_CHARS else text

    async def embed(self, text: str) -> List[float]:
        """Return a single embedding vector."""
        vecs = await self.embed_batch([text])
        return vecs[0]

    async def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """Return embedding vectors for a batch of texts.

        Splits into chunks of _MAX_BATCH if the batch exceeds the API limit.
        """
        if not texts:
            return []

        truncated = [self._truncate(t or " ") for t in texts]
        results: List[List[float]] = []
        for start in range(0, len(truncated), _MAX_BATCH):
            chunk = truncated[start:start + _MAX_BATCH]
            try:
                resp = await self._client.embeddings.create(
                    model=self._model,
                    input=chunk,
                )
            except Exception as e:
                self._logger.error(
                    "Embedding API failed",
                    exc_info=True,
                    extra={"model": self._model, "batch_size": len(chunk)},
                )
                raise AIError(
                    f"Embedding request failed: {e}",
                    provider="openai",
                    error_code=ErrorCode.AI_EMBEDDING_ERROR,
                    recoverable=True,
                ) from e
            results.extend([list(item.embedding) for item in resp.data])
        return results
