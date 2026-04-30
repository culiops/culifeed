# CuliFeed Architectural Guidelines

**Purpose**: High-level architectural guidance for CuliFeed RSS content curation system development

## Architecture Philosophy

CuliFeed follows a **service-oriented, pipeline-based architecture** optimized for:
- **Cost Efficiency**: 85% pre-filtering before AI processing to stay within free tiers
- **Reliability**: Isolated error handling with graceful degradation
- **Scalability**: Async processing with configurable concurrency
- **Maintainability**: Clear service boundaries and dependency injection

## Core Architectural Patterns

### 🔄 Processing Pipeline Pattern

**When to Use**: Multi-stage content processing with different performance characteristics

```python
# Pipeline stages with clear interfaces
class ProcessingPipeline:
    def __init__(self):
        self.feed_fetcher = FeedFetcher()      # I/O bound - async
        self.deduplicator = ArticleProcessor() # CPU bound - sync  
        self.pre_filter = PreFilterEngine()    # CPU bound - sync
        self.ai_processor = AIProcessor()      # I/O bound - async
```

**Key Principles:**
- Each stage has single responsibility and clear input/output
- Stages can be async or sync based on their nature (I/O vs CPU bound)
- Error isolation: one stage failure doesn't crash entire pipeline
- Metrics collection at each stage for observability

### ⚡ Async/Sync Decision Tree

**Use Async When:**
- External API calls (RSS feeds, AI providers, Telegram API)
- I/O operations (file reading, database operations with connection pooling)
- Concurrent processing of independent items

**Use Sync When:**
- CPU-bound operations (content parsing, hash calculation, text processing)
- Simple data transformations and validation
- Operations that need to complete before proceeding

**Mixed Pattern Example:**
```python
async def process_feeds_for_channel(self, chat_id: str):
    # Async: Fetch multiple feeds concurrently
    feeds = await self.feed_fetcher.fetch_all_feeds(feed_urls)
    
    # Sync: Process articles sequentially (CPU-bound)
    for feed_data in feeds:
        articles = self.feed_manager.parse_articles(feed_data)  # sync
        filtered = self.pre_filter.filter_articles(articles)    # sync
    
    # Async: AI processing with rate limiting
    results = await self.ai_processor.process_batch(filtered)
```

## Service Boundaries & Dependencies

### 🏗️ Module Organization Principles

**Layer Hierarchy** (dependencies flow downward only):
```
┌─────────────────┐
│   CLI/Main      │ ← Entry points, orchestration
├─────────────────┤
│   Processing    │ ← Business logic, pipelines  
├─────────────────┤
│   Ingestion     │ ← Content fetching, parsing
├─────────────────┤
│   Database      │ ← Data persistence, models
├─────────────────┤
│   Utils         │ ← Shared utilities, exceptions
└─────────────────┘
```

**Dependency Injection Pattern:**
```python
class ProcessingPipeline:
    def __init__(self, db_connection: DatabaseConnection):
        # Inject dependencies, don't create them
        self.db = db_connection
        self.feed_manager = FeedManager(db_connection)
        self.article_processor = ArticleProcessor(db_connection)
```

**Service Interface Pattern:**
```python
# Define clear interfaces between services
class FeedFetcherProtocol(Protocol):
    async def fetch_feed(self, url: str) -> FeedResult: ...
    async def fetch_multiple(self, urls: List[str]) -> Dict[str, FeedResult]: ...
```

## Error Handling Architecture

### 🚨 Structured Error Code System

**Error Code Categories:**
- `C001-C099`: Configuration errors
- `D001-D099`: Database errors  
- `F001-F099`: Feed ingestion errors
- `A001-A099`: AI processing errors
- `T001-T099`: Telegram bot errors

**Error Context Pattern:**
```python
def fetch_rss_feed(self, url: str) -> FeedResult:
    try:
        response = requests.get(url, timeout=30)
        return self.parse_feed(response.content)
    except requests.RequestException as e:
        raise FeedFetchError(
            message=f"Failed to fetch RSS feed: {url}",
            error_code=ErrorCode.FEED_NETWORK_ERROR,
            context={
                'url': url,
                'timeout': 30,
                'retry_count': self._retry_counts.get(url, 0)
            },
            recoverable=True
        ) from e
```

**Error Isolation Principle:**
- Feed parsing errors don't affect other feeds
- AI provider failures fall back to secondary providers
- Database errors are isolated with connection recovery

## Performance Guidelines

### ⚡ Resource Management Patterns

**Connection Pooling:**
```python
# Database connections
class DatabaseConnection:
    def __init__(self, pool_size: int = 5):
        self._pool = ConnectionPool(size=pool_size)
    
    @asynccontextmanager
    async def get_connection(self):
        conn = await self._pool.acquire()
        try:
            yield conn
        finally:
            await self._pool.release(conn)
```

**Batch Processing Guidelines:**
- **RSS Feeds**: Process 5-10 feeds concurrently (configurable)
- **Articles**: Batch pre-filtering in groups of 50-100
- **AI Processing**: Batch size 10-20 based on API limits
- **Database Operations**: Use transactions for related operations

**Memory Management:**
```python
# Process articles in chunks to avoid memory issues
async def process_large_article_batch(self, articles: List[Article]):
    chunk_size = self.settings.processing.batch_size  # Default: 10
    
    for chunk in self._chunk_articles(articles, chunk_size):
        results = await self._process_chunk(chunk)
        await self._store_results(results)
        # Chunk completes, memory can be freed
```

## Content Security Standards

### 🛡️ RSS Content Sanitization

**HTML Content Cleaning:**
```python
def clean_article_content(self, html_content: str) -> str:
    # Remove dangerous elements
    soup = BeautifulSoup(html_content, 'html.parser')
    
    # Remove scripts, styles, forms
    for tag in soup.find_all(['script', 'style', 'form']):
        tag.decompose()
    
    # Sanitize links and attributes
    for link in soup.find_all('a', href=True):
        if not self._is_safe_url(link['href']):
            link.unwrap()  # Keep text, remove link
    
    return soup.get_text(separator=' ', strip=True)
```

**Input Validation Layers:**
1. **URL Validation**: Before fetching RSS feeds
2. **Content Length**: Limit article content size (50KB max)
3. **Character Encoding**: Handle UTF-8, Latin-1, Windows-1252
4. **HTML Sanitization**: Remove XSS vectors and dangerous content

## Extension Patterns

### 🔧 Adding New AI Providers

**Provider Interface:**
```python
class AIProviderProtocol(Protocol):
    async def analyze_relevance(self, article: Article, topic: Topic) -> AIResult: ...
    async def generate_summary(self, content: str) -> str: ...
    def get_rate_limits(self) -> RateLimitInfo: ...
```

**Provider Registration:**
```python
# Current 4-provider system in settings.py
class AIProvider(str, Enum):
    GROQ = "groq"        # Free tier primary
    DEEPSEEK = "deepseek" # Premium reasoning
    GEMINI = "gemini"     # Google model
    OPENAI = "openai"     # Premium model

# Example: Adding new provider - create new_provider.py
class NewProvider(BaseAIProvider):
    def __init__(self, api_key: str):
        super().__init__()
        self.client = NewProviderClient(api_key)

    async def analyze_relevance(self, article: Article, topic: Topic) -> AIResult:
        # Implementation specific to new provider API
```

### 📡 Adding New Content Sources

**Content Source Interface:**
```python
class ContentSourceProtocol(Protocol):
    async def fetch_content(self, source_config: Dict[str, Any]) -> List[Article]: ...
    def validate_config(self, config: Dict[str, Any]) -> bool: ...
```

**Integration Pattern:**
```python
# Add to processing/sources/
class RedditSource(BaseContentSource):
    def __init__(self):
        self.source_type = "reddit"
    
    async def fetch_content(self, config: Dict[str, Any]) -> List[Article]:
        # Reddit API integration
        subreddit = config['subreddit']
        posts = await self._fetch_reddit_posts(subreddit)
        return [self._convert_to_article(post) for post in posts]
```

## Quality Gates & Standards

### 🎯 Code Quality Requirements

**Development Environment Setup:**
- **Virtual Environment**: Always activate venv before running any code, tests, or debugging
  ```bash
  source venv/bin/activate
  ```

**Before Committing:**
1. **Type Safety**: All functions have type hints
2. **Error Handling**: All external operations have error handling
3. **Logging**: Use structured logging with context
4. **Documentation**: Public APIs have docstrings
5. **Testing**: New features have unit and integration tests

**Code Validation Workflow:**
When fixing or updating any logic code, follow this validation sequence:

1. **Service Validation**: Test affected services
   ```bash
   # Test bot service changes
   source venv/bin/activate && python run_bot.py

   # Test scheduler service changes
   source venv/bin/activate && python run_daily_scheduler.py
   ```

2. **Full Test Suite**: Verify no regressions
   ```bash
   source venv/bin/activate && python -m pytest
   ```

3. **Integration Testing**: Ensure end-to-end functionality works as expected

**Performance Standards:**
- RSS feed processing: <5 seconds per feed
- Pre-filtering: <1 second for 100 articles  
- Database operations: Connection pooling required
- Memory usage: Process articles in batches to avoid memory spikes

**Security Checklist:**
- [ ] Input validation for all external data
- [ ] HTML content sanitized before storage
- [ ] URLs validated before fetching
- [ ] Error messages don't expose sensitive data
- [ ] API keys stored in environment variables

## Configuration Management

### ⚙️ Settings Architecture

**Hierarchical Configuration:**
```python
# YAML base configuration
processing:
  processing_interval_hours: 1
  ai_provider: ${AI_PROVIDER}  # Environment variable substitution
  
# Environment variable override
export CULIFEED_PROCESSING__AI_PROVIDER=groq

# Runtime validation
settings = get_settings()  # Validates all settings with Pydantic
```

**Feature Toggles:**
```python
class ProcessingSettings(BaseModel):
    enable_ai_processing: bool = Field(default=True)
    enable_content_cleaning: bool = Field(default=True)
    enable_performance_monitoring: bool = Field(default=False)
```

## Deployment Considerations

### 🚀 Production Patterns

**Dual-Process Architecture:**
- **Bot Service**: Long-running Telegram bot (systemd service)
- **Processing Pipeline**: Daily scheduled processing (systemd timer)

**Resource Requirements:**
- **Memory**: 512MB-1GB for normal processing
- **Storage**: SQLite database, logs, temporary processing files
- **Network**: RSS feed fetching, AI API calls, Telegram API

**Monitoring & Observability:**
```python
# Structured logging with context
logger = get_logger_for_component('pipeline', 
                                 chat_id=chat_id, 
                                 processing_batch=batch_id)

# Performance metrics
with PerformanceLogger(logger, "feed_processing"):
    results = await self.process_feeds()

# Health checks
def health_check() -> Dict[str, Any]:
    return {
        'database': self._check_database_health(),
        'ai_providers': self._check_ai_provider_health(),
        'last_processing': self._get_last_processing_time()
    }
```

---

**Remember**: These guidelines complement the excellent existing documentation. Reference `tests/CLAUDE.md` for testing practices and Serena memories for code style conventions.