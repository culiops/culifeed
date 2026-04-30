# Deployment Guide - VPS/Local Server

## VPS Deployment (Recommended)

### Prerequisites
- VPS/Server with Python 3.8+
- 512MB RAM minimum (1GB recommended)
- Telegram bot token
- AI API key (Gemini free tier recommended)

### 1. Server Setup
```bash
# Update system
sudo apt update && sudo apt upgrade -y

# Install Python and dependencies
sudo apt install python3 python3-pip python3-venv git -y

# Clone repository
git clone <your-culifeed-repo>
cd culifeed

# Create virtual environment
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Configuration
```bash
# Copy and edit configuration
cp config.example.yaml config.yaml
nano config.yaml  # Edit with your settings

# Set environment variables
sudo nano /etc/environment
# Add:
# TELEGRAM_BOT_TOKEN=your_bot_token_here
# GROQ_API_KEY=your_groq_api_key_here
# (No need for TELEGRAM_CHAT_ID - auto-registered when bot added to groups)
```

### 3. Service Setup
```bash
# Create systemd service for bot
sudo cp scripts/culifeed-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable culifeed-bot
sudo systemctl start culifeed-bot

# Setup daily cron job for processing
crontab -e
# Add: 0 8 * * * cd /home/user/culifeed && python daily_processor.py
```

### 4. Test Installation
```bash
# Test bot connectivity
python test_bot.py

# Test processing pipeline
python daily_processor.py --dry-run

# Check service status
sudo systemctl status culifeed-bot
```

### 5. Setup Channels (Auto-Registration)
```bash
# 1. Add bot to your Telegram group/channel
# 2. Bot automatically registers and sends welcome message
# 3. Configure topics and feeds via bot commands:

/add_topic "AWS Lambda performance optimization"
/add_feed "https://aws.amazon.com/blogs/compute/feed/"
/add_topic "Leadership in tech"
/status

# Bot is now ready for daily processing!
```

## Multi-Channel Benefits

### Independent Configuration
- Each group/channel has separate topics and feeds
- Personal feed + team channels supported simultaneously  
- No configuration conflicts between channels

### Flexible Usage Patterns
```
Personal Channel:
├─ AWS technical topics
├─ Programming best practices
└─ Personal development

Team Channel:  
├─ Company-relevant tech news
├─ Industry updates
└─ Tool announcements

Archive Channel:
├─ Long-term interesting articles
├─ Research materials
└─ Reference documentation
```

## Getting API Keys

### Telegram Bot Token
1. Message @BotFather on Telegram
2. Send `/newbot`
3. Choose bot name and username
4. Save the token provided

### Google Gemini API Key (Free Tier - Recommended)
1. Visit https://ai.google.dev/
2. Click "Get API key in Google AI Studio"
3. Create new project or select existing
4. Generate API key
5. Free tier: 1,000 requests/day (perfect for CuliFeed)

### Groq API Key (Backup - Free Tier)
1. Visit https://console.groq.com
2. Sign up with email
3. Generate API key
4. Free tier: 100 requests/day (fallback provider)

### Alternative AI Providers (Paid)
- **OpenAI**: $0.50/1M tokens, higher accuracy
- **Anthropic**: $1/1M tokens, highest quality summaries
- **Together.ai**: $0.20/1M tokens, good pricing

## Configuration

### config.yaml Template
```yaml
# User Settings (no chat_id needed - auto-registered)
user:
  timezone: "UTC"
  admin_user_id: "${TELEGRAM_ADMIN_ID}"  # Optional: for admin commands
  
# Processing Settings  
processing:
  processing_interval_hours: 1
  quiet_hours_start: 22
  quiet_hours_end: 7
  ai_provider: "gemini"  # Primary: gemini, fallback: groq
  max_articles_per_topic: 5
  
# Free Tier Management
limits:
  max_daily_api_calls: 950  # Under Gemini 1000 RPD limit
  fallback_to_groq: true    # When Gemini limit reached
  enable_usage_alerts: true  # Monitor free tier usage
  
# Initial Topics (managed via bot after setup)
topics:
  - name: "AWS Lambda performance optimization"
    keywords: ["lambda", "performance", "cold start"]
    exclude_keywords: ["pricing", "cost"]
    confidence_threshold: 0.8

# Initial Feeds  
feeds:
  - url: "https://aws.amazon.com/blogs/compute/feed/"
    title: "AWS Compute Blog"
    active: true
```

### Environment Variables
```bash
# Required
export TELEGRAM_BOT_TOKEN="your_bot_token"
export GEMINI_API_KEY="your_gemini_api_key"

# Optional  
export GROQ_API_KEY="backup_api_key"  # Fallback provider
export TELEGRAM_ADMIN_ID="your_user_id"  # For admin commands

# Optional
export OPENAI_API_KEY="tertiary_provider"  # Optional paid backup
export LOG_LEVEL="INFO"
export CONFIG_PATH="./config.yaml"
```

## Operational Procedures

### Daily Operations
**Automated**: System runs daily, no intervention needed

**Monitor**: Check Telegram for:
- Daily digest delivery
- System status messages
- Error notifications

### Weekly Maintenance
```bash
# Check API usage (free tier monitoring)
python -c "from culifeed import usage_monitor; usage_monitor.weekly_report()"

# Review topic performance  
/status  # via Telegram

# Clean old data
python main.py --cleanup
```

### Monthly Review
1. **Cost Analysis**: Review AI API usage and costs
2. **Content Quality**: Assess relevance of delivered articles
3. **Topic Tuning**: Adjust confidence thresholds based on feedback
4. **Source Review**: Remove inactive feeds, add new sources

## Troubleshooting

### Common Issues

#### "No articles delivered"
```bash
# Check feed connectivity
python main.py --test-feeds

# Check AI API
python main.py --test-ai

# Check recent processing
/status  # via Telegram
```

#### "Too many/few articles"
```bash
# Adjust sensitivity
/adjust_sensitivity medium  # via Telegram

# Or adjust specific topic
/set_confidence "topic name" 0.7  # via Telegram
```

#### "Approaching API limits"
```bash
# Check Gemini usage
python -c "from culifeed import usage_monitor; usage_monitor.current_usage()"

# If near 1000 RPD limit:
/adjust_sensitivity high  # Process fewer articles
# System automatically switches to Groq fallback
```

#### "Processing failures"
```bash
# Check logs
tail -f culifeed.log

# Manual run with debug
python main.py --debug --dry-run
```

### Error Recovery

#### Database Issues
```bash
# Backup current data
cp data.db data.backup.db

# Reset database
python main.py --reset-db

# Restore configuration  
/add_topic "your topics"  # via Telegram
```

#### API Key Rotation
```bash
# Update environment variable
export GROQ_API_KEY="new_key"

# Test new key
python main.py --test-ai

# Update GitHub secrets (if using Actions)
```

## Performance Tuning

### Optimization Settings
```yaml
# For high-volume scenarios
performance:
  batch_size: 10  # Articles per AI request
  parallel_feeds: 5  # Concurrent RSS fetches
  cache_embeddings: true  # Reuse calculations
  max_content_length: 2000  # Truncate long articles
```

### Cost Monitoring
```python
# Built-in usage tracking for free tiers
usage_monitor = UsageMonitor()
usage_monitor.set_gemini_daily_limit(950)  # Stay under 1000 RPD
usage_monitor.set_groq_fallback_limit(80)  # Groq backup
usage_monitor.set_usage_alert_threshold(0.8)  # Alert at 80%
```

## Backup & Recovery

### Data Backup
```bash
# Backup database and config
tar -czf backup-$(date +%Y%m%d).tar.gz data.db config.yaml

# Automated backup (add to cron)
0 2 * * 0 cd /path/to/culifeed && ./backup.sh
```

### Disaster Recovery
1. **Config Loss**: Topics and feeds manageable via Telegram
2. **Database Loss**: System rebuilds automatically
3. **API Key Compromise**: Rotate keys, update config
4. **Feed Source Changes**: Auto-detection of dead feeds

## Scaling Considerations

### Current Limits (Designed)
- **Topics**: 20 (optimal for cost/performance)
- **Feeds**: 100 (practical limit for daily processing)  
- **Articles/day**: 1000 (within free tier limits)

### Scale-up Options
- **More Topics**: Increase AI API budget
- **Real-time Processing**: Switch from daily to hourly
- **Multiple Users**: Add user management layer
- **Advanced Features**: ML-based preference learning