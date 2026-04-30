# CuliFeed Implementation Workflow

## Implementation Overview

**Duration**: 6 weeks (42 days)  
**Approach**: Phase-based development with testing gates  
**Architecture**: Dual-process (bot service + daily processor) on VPS  

## Phase Summary

```
Phase 1: Foundation (Days 1-7)     → Database, models, basic infrastructure
Phase 2: Core Processing (Days 8-14) → RSS ingestion, pre-filtering  
Phase 3: AI Integration (Days 15-21) → AI analysis pipeline, API integration
Phase 4: Bot Service (Days 22-28)   → Telegram bot, command handling
Phase 5: Integration (Days 29-35)   → Connect components, testing
Phase 6: Deployment (Days 36-42)    → VPS setup, monitoring, production
```

## Key Technical Challenges

### **Challenge 1: Multi-Channel Architecture**
- **Problem**: Data isolation across Telegram channels
- **Solution**: Database design with `chat_id` foreign keys
- **Risk Mitigation**: Comprehensive testing for data leakage

### **Challenge 2: Zero-Cost AI Processing**
- **Problem**: Balance quality with Gemini free tier limits (1,000 RPD)
- **Solution**: 85% pre-filtering + dynamic provider switching
- **Risk Mitigation**: Groq fallback + keyword-only mode

### **Challenge 3: RSS Feed Reliability**
- **Problem**: Managing 100+ feeds with varying reliability
- **Solution**: Parallel processing with error isolation
- **Risk Mitigation**: Health monitoring + automatic feed disabling

## Detailed Implementation Plan

### **PHASE 1: Foundation (Days 1-7)**

#### Day 1-2: Database & Models
**Files to Create**:
```
culifeed/database/schema.py      # SQLite table definitions
culifeed/database/models.py      # Data classes and validation
culifeed/database/connection.py  # Database connection management
requirements.txt                 # Core dependencies
```

**Testing Commands**:
```bash
python -c "from database.schema import create_tables; create_tables()"
sqlite3 data.db ".schema channels"
python -c "from database.models import Article; Article.validate({'title': 'test'})"
```

**Milestone**: All 5 tables created, model validation working

#### Day 3-4: Core Infrastructure
**Files to Create**:
```
culifeed/utils/logging.py        # Structured logging
culifeed/utils/exceptions.py     # Custom exceptions
culifeed/utils/validators.py     # Input validation
tests/test_database.py           # Database tests
```

**Testing Commands**:
```bash
python -c "from utils.logging import setup_logger; logger = setup_logger()"
python -m pytest tests/test_database.py -v
```

**Milestone**: Logging configured, all validators working

#### Day 5-7: Configuration System
**Files to Create**:
```
culifeed/config/settings.py     # Application settings
config.yaml                     # Configuration file
.env.example                    # Environment template
main.py                         # Application entry point
```

**Testing Commands**:
```bash
python -c "from config.settings import load_settings; print(load_settings().ai.primary_provider)"
python main.py --check-config --test-foundation
```

**Milestone**: Configuration loads correctly, foundation test passes

### **PHASE 2: Content Processing (Days 8-14)**

#### Day 8-9: RSS Feed Manager
**Files to Create**:
```
culifeed/ingestion/feed_manager.py    # RSS parsing
culifeed/ingestion/content_cleaner.py # Content extraction
tests/test_feed_manager.py            # Feed tests
```

**Testing Commands**:
```bash
python -c "from ingestion.feed_manager import fetch_feed; articles = fetch_feed('https://aws.amazon.com/blogs/compute/feed/'); print(f'Fetched {len(articles)} articles')"
python -m pytest tests/test_feed_manager.py -v
```

**Milestone**: RSS feeds parsed correctly, content cleaned

#### Day 10-12: Pre-filtering System
**Files to Create**:
```
culifeed/processing/pre_filter.py     # Keyword matching & Relevance scoring
tests/test_pre_filter.py              # Pre-filter tests
```

**Testing Commands**:
```bash
python main.py --test-prefilter --sample-articles=100
python -m pytest tests/test_pre_filter.py -v
```

**Milestone**: 85% article volume reduction achieved

#### Day 13-14: Content Storage
**Files to Create**:
```
culifeed/storage/article_repository.py  # Article CRUD
culifeed/storage/topic_repository.py    # Topic management
tests/test_repositories.py              # Repository tests
```

**Testing Commands**:
```bash
python main.py --test-ingestion-pipeline --real-feed
python -m pytest tests/integration/test_ingestion_pipeline.py -v
```

**Milestone**: Full ingestion pipeline processes real feeds

### **PHASE 3: AI Integration (Days 15-21)**

#### Day 15-16: AI Service Clients
**Files to Create**:
```
culifeed/ai/ai_client.py        # Abstract client interface
culifeed/ai/gemini_client.py    # Gemini integration
culifeed/ai/groq_client.py      # Groq fallback
tests/test_ai_clients.py        # AI client tests
```

**Testing Commands**:
```bash
python -c "from ai.gemini_client import GeminiClient; client = GeminiClient(); response = client.test_connection(); print(f'Gemini test: {response}')"
python -m pytest tests/test_ai_clients.py -v
```

**Milestone**: Gemini API working, fallback mechanism functional

#### Day 17-19: AI Processing Pipeline
**Files to Create**:
```
culifeed/processing/ai_processor.py     # AI analysis logic
culifeed/processing/summarizer.py      # Article summarization
tests/test_ai_processor.py             # AI processing tests
```

**Testing Commands**:
```bash
python main.py --test-ai-batch --articles=10
python -m pytest tests/test_ai_processor.py -v
```

**Milestone**: AI processing generates reliable scores and summaries

#### Day 20-21: Pipeline Integration
**Files to Create**:
```
culifeed/pipeline/daily_processor.py   # Processing orchestrator
culifeed/pipeline/pipeline_stages.py   # Pipeline stages
tests/integration/test_full_pipeline.py # Pipeline tests
```

**Testing Commands**:
```bash
python main.py --test-full-pipeline --dry-run
python -m pytest tests/integration/test_full_pipeline.py -v
```

**Milestone**: Complete pipeline processes content end-to-end

### **PHASE 4: Telegram Bot (Days 22-28)**

#### Day 22-23: Bot Infrastructure
**Files to Create**:
```
culifeed/bot/telegram_bot.py        # Main bot service
culifeed/bot/auto_registration.py   # Channel registration
tests/test_bot_handlers.py          # Bot tests
```

**Testing Commands**:
```bash
python -c "from bot.telegram_bot import TelegramBot; bot = TelegramBot(); bot.test_connection()"
python main.py --test-auto-registration
```

**Milestone**: Bot connects, auto-registration working

#### Day 24-25: Command Implementation
**Files to Create**:
```
culifeed/bot/commands/topic_commands.py    # Topic management
culifeed/bot/commands/feed_commands.py     # Feed management  
culifeed/bot/commands/status_commands.py   # Status and help
tests/test_bot_commands.py                 # Command tests
```

**Testing Commands**:
```bash
python main.py --test-bot-commands --channel="-1001234567890"
python -m pytest tests/test_bot_commands.py -v
```

**Milestone**: All bot commands functional, multi-channel support

#### Day 26-28: Message Delivery
**Files to Create**:
```
culifeed/delivery/message_sender.py     # Telegram delivery
culifeed/delivery/digest_formatter.py   # Message formatting
tests/test_message_delivery.py          # Delivery tests
```

**Testing Commands**:
```bash
python main.py --test-delivery --channel="-1001234567890" --dry-run
python -m pytest tests/integration/test_bot_integration.py -v
```

**Milestone**: Digest delivery works, formatting correct

### **PHASE 5: System Integration (Days 29-35)**

#### Day 29-30: Component Integration
**Files to Create**:
```
culifeed/scheduler/hourly_scheduler.py  # Hourly run coordination
main.py                                 # Updated entry point
tests/integration/test_end_to_end.py    # E2E tests
```

**Testing Commands**:
```bash
python main.py --full-test --channels="-1001234567890" --dry-run
python -m pytest tests/integration/test_end_to_end.py -v
```

**Milestone**: Complete workflow executes end-to-end

#### Day 31-32: Error Handling
**Files to Create**:
```
culifeed/recovery/error_handler.py    # Error management
culifeed/recovery/retry_logic.py      # Retry mechanisms
tests/test_error_handling.py          # Error tests
```

**Testing Commands**:
```bash
python main.py --test-recovery-scenarios
python main.py --health-check
```

**Milestone**: System handles failures gracefully


### **PHASE 6: Production Deployment (Days 36-42)**

#### Day 36-37: VPS Setup
**Files to Create**:
```
deployment/scripts/deploy.sh           # Deployment automation
deployment/systemd/culifeed-bot.service # Service definition
deployment/scripts/health_check.sh     # Health monitoring
```

**Testing Commands**:
```bash
bash deployment/scripts/deploy.sh --target-vps --dry-run
sudo systemctl status culifeed-bot
```

**Milestone**: Services running on VPS

---

## **Testing Strategy Per Phase**

### **Unit Testing** (Each Component)
- Database operations with test fixtures
- AI client responses with mocked APIs
- Bot command parsing and validation
- Utility functions with edge cases

### **Integration Testing** (Cross-Component)
- Database + ingestion pipeline
- AI processing + delivery system
- Bot commands + database persistence
- Full pipeline with real data

### **System Testing** (End-to-End)
- Real RSS feeds → AI processing → Telegram delivery
- Multi-channel processing with different configurations
- Error scenarios and recovery testing
- Performance under realistic load

### **Production Testing** (Live Environment)
- VPS deployment with systemd services
- Daily processing cron job execution
- Real Telegram bot interaction
- Monitoring and alerting validation

---

## **Success Criteria by Phase**

### **Phase 1 Complete When:**
- [ ] Database schema matches documentation exactly
- [ ] All configuration loads from environment + YAML
- [ ] Foundation integration test passes
- [ ] Development environment fully functional

### **Phase 2 Complete When:**
- [ ] RSS feeds processed without errors
- [ ] Pre-filtering achieves 85% volume reduction
- [ ] Content storage and retrieval working
- [ ] Ingestion pipeline handles 100+ articles

### **Phase 3 Complete When:**
- [ ] Gemini API integration returns valid scores
- [ ] Summarization generates quality 2-3 sentence summaries
- [ ] Fallback mechanism tested and working
- [ ] Cost monitoring stays within free tier

### **Phase 4 Complete When:**
- [ ] Bot auto-registers when added to groups
- [ ] All documented commands implemented and tested
- [ ] Multi-channel support verified
- [ ] Message delivery working in real Telegram groups

### **Phase 5 Complete When:**
- [ ] Complete end-to-end workflow functional
- [ ] Error handling tested for all failure modes
- [ ] Performance meets targets (<5min for 1000 articles)
- [ ] System integration test passes

### **Phase 6 Complete When:**
- [ ] VPS deployment successful with systemd services
- [ ] Monitoring and alerting operational
- [ ] 3 consecutive days of successful daily processing
- [ ] Production readiness checklist 100% complete

This implementation workflow provides clear milestones, specific testing procedures, and systematic progression from foundation to production-ready system.