"""Unit tests for handling articles with None published_at dates."""

import pytest
from datetime import datetime, timezone, timedelta
from culifeed.database.models import Article


class TestPublishedAtNoneHandling:
    """Test that articles with None published_at are handled correctly."""
    
    def test_sorting_articles_with_none_published_at(self):
        """Test that articles can be sorted even when some have None published_at."""
        # Create articles with mixed published_at values
        now = datetime.now(timezone.utc)
        
        articles = [
            Article(
                title="Old Article",
                url="https://example.com/old",
                source_feed="https://example.com/feed",
                published_at=now - timedelta(days=5)
            ),
            Article(
                title="No Date Article",
                url="https://example.com/no-date",
                source_feed="https://example.com/feed",
                published_at=None  # This is the issue case
            ),
            Article(
                title="Recent Article",
                url="https://example.com/recent",
                source_feed="https://example.com/feed",
                published_at=now - timedelta(hours=1)
            ),
            Article(
                title="Another No Date",
                url="https://example.com/no-date-2",
                source_feed="https://example.com/feed",
                published_at=None
            ),
        ]
        
        # Sort using the same logic as pipeline.py:496
        sorted_articles = sorted(
            articles,
            key=lambda a: a.published_at or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True
        )
        
        # Verify sorting works without error
        assert len(sorted_articles) == 4
        
        # Most recent should be first
        assert sorted_articles[0].title == "Recent Article"
        
        # Old article should be second
        assert sorted_articles[1].title == "Old Article"
        
        # Articles with None published_at should be at the end
        assert sorted_articles[2].published_at is None
        assert sorted_articles[3].published_at is None
        
    def test_all_articles_with_none_published_at(self):
        """Test sorting when ALL articles have None published_at."""
        articles = [
            Article(
                title=f"Article {i}",
                url=f"https://example.com/article-{i}",
                source_feed="https://example.com/feed",
                published_at=None
            )
            for i in range(5)
        ]
        
        # Should not raise TypeError
        sorted_articles = sorted(
            articles,
            key=lambda a: a.published_at or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True
        )
        
        assert len(sorted_articles) == 5
        # All have the same sort key, so order may vary but should complete
        for article in sorted_articles:
            assert article.published_at is None
            
    def test_all_articles_with_valid_published_at(self):
        """Test sorting when all articles have valid published_at."""
        now = datetime.now(timezone.utc)
        
        articles = [
            Article(
                title=f"Article {i}",
                url=f"https://example.com/article-{i}",
                source_feed="https://example.com/feed",
                published_at=now - timedelta(hours=i)
            )
            for i in range(5)
        ]
        
        sorted_articles = sorted(
            articles,
            key=lambda a: a.published_at or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True
        )
        
        assert len(sorted_articles) == 5
        
        # Should be in reverse chronological order
        for i in range(len(sorted_articles) - 1):
            assert sorted_articles[i].published_at > sorted_articles[i + 1].published_at
