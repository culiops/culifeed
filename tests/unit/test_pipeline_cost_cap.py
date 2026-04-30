"""Tests that the AI cost cap defers excess candidates."""
from unittest.mock import MagicMock

from culifeed.processing.pipeline import ProcessingPipeline


def test_cap_truncates_and_warns():
    """Given more candidates than max_ai_calls_per_run, the helper returns
    the top-N by pre-filter score and logs a WARNING with the deferred count."""
    pipeline = ProcessingPipeline.__new__(ProcessingPipeline)
    pipeline.logger = MagicMock()

    candidates = [
        {"article_id": f"a{i}", "pre_filter_score": float(i)}
        for i in range(10)
    ]

    kept = pipeline._apply_ai_call_cap(candidates, cap=3)

    assert len(kept) == 3
    kept_ids = {c["article_id"] for c in kept}
    assert kept_ids == {"a9", "a8", "a7"}  # top 3 by pre_filter_score
    pipeline.logger.warning.assert_called_once()
    assert "7" in str(pipeline.logger.warning.call_args)  # 10 - 3 deferred


def test_cap_no_op_under_limit():
    pipeline = ProcessingPipeline.__new__(ProcessingPipeline)
    pipeline.logger = MagicMock()

    candidates = [{"article_id": "a", "pre_filter_score": 0.9}]
    kept = pipeline._apply_ai_call_cap(candidates, cap=50)

    assert kept == candidates
    pipeline.logger.warning.assert_not_called()
