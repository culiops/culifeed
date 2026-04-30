"""Tests for bot.message_utils.split_long_message."""

from culifeed.bot.message_utils import split_long_message, SAFE_CHUNK_LENGTH


def test_short_message_returns_single_chunk():
    text = "hello world"
    assert split_long_message(text) == [text]


def test_message_at_threshold_returns_single_chunk():
    text = "x" * SAFE_CHUNK_LENGTH
    assert split_long_message(text) == [text]


def test_long_message_splits_on_paragraph_boundary():
    paragraph = ("a" * 1000 + "\n\n")
    text = paragraph * 5
    chunks = split_long_message(text)
    assert len(chunks) >= 2
    for chunk in chunks:
        assert len(chunk) <= SAFE_CHUNK_LENGTH


def test_long_message_each_chunk_under_telegram_limit():
    feed_block = "🟢 *1. Some Feed Title*\nURL: `https://example.com/feed.xml`\nLast fetch: 04/29 08:27\n\n"
    text = "📡 *Your RSS Feeds:*\n\n" + (feed_block * 60) + "*Total: 60 feeds*"
    chunks = split_long_message(text)
    assert len(chunks) >= 2
    for chunk in chunks:
        assert len(chunk) <= SAFE_CHUNK_LENGTH


def test_paragraph_larger_than_limit_is_hard_split():
    text = "a" * (SAFE_CHUNK_LENGTH + 500)
    chunks = split_long_message(text)
    for chunk in chunks:
        assert len(chunk) <= SAFE_CHUNK_LENGTH
    assert "".join(chunks).replace("\n", "") == text.replace("\n", "")
