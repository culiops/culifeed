"""
Topic Management Commands
========================

Telegram bot commands for managing topics in CuliFeed channels.
Handles topic creation, editing, deletion, and listing.

Commands:
- /topics - List all topics for the channel
- /addtopic - Add a new topic with keywords
- /removetopic - Remove an existing topic
- /edittopic - Edit an existing topic
"""

import re
from typing import List, Optional, Dict, Any

from telegram import Update
from telegram.ext import ContextTypes

from ...database.connection import DatabaseConnection
from ...database.models import Topic, UserTier
from ...storage.topic_repository import TopicRepository
from ...services.user_subscription_service import UserSubscriptionService
from ...utils.logging import get_logger_for_component
from ...utils.validators import ContentValidator, ValidationError
from ...config.settings import get_settings
from ...ai.ai_manager import AIManager
from ...utils.exceptions import TelegramError, ErrorCode, AIError
from ...processing.topic_description_generator import TopicDescriptionGenerator


class TopicCommandHandler:
    """Handler for topic-related bot commands."""

    def __init__(self, db_connection: DatabaseConnection):
        """Initialize topic command handler.

        Args:
            db_connection: Database connection manager
        """
        self.db = db_connection
        self.topic_repo = TopicRepository(db_connection)
        self.ai_manager = AIManager()
        self.user_service = UserSubscriptionService(
            db_connection
        )  # NEW: User subscription service
        self.logger = get_logger_for_component("topic_commands")

    async def handle_list_topics(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle /topics command - list topics for current chat with global awareness.

        Args:
            update: Telegram update object
            context: Bot context
        """
        try:
            chat_id = str(update.effective_chat.id)
            telegram_user_id = update.effective_user.id
            chat_title = update.effective_chat.title or update.effective_chat.first_name or "this chat"
            settings = get_settings()

            # Get all topics for this channel
            topics = self.topic_repo.get_topics_for_channel(chat_id, active_only=True)

            if not topics:
                message = f"📝 *No topics in {chat_title}*\n\n"
                message += "Add your first topic with:\n"
                message += "`/addtopic AI machine learning, artificial intelligence, ML`\n\n"
                message += "Topics help me understand what content you're interested in!"
            else:
                message = f"📝 *Topics in {chat_title}* ({len(topics)} topics)\n\n"
                for topic in topics:
                    # Show all keywords with clear visual separation
                    keywords_display = ", ".join(topic.keywords)
                    message += f"🎯 *{topic.name}*\n" f"    → {keywords_display}\n\n"

            # Add global limit summary if SaaS mode is enabled
            if settings.saas.saas_mode:
                try:
                    user_subscription = await self.user_service.get_user_subscription(telegram_user_id)
                    total_topics = await self.user_service.count_user_topics(telegram_user_id)

                    if user_subscription.subscription_tier == UserTier.FREE:
                        limit = settings.saas.free_tier_topic_limit_per_user
                        if total_topics >= limit:
                            message += f"\n🚫 *Using {total_topics}/{limit} topics* (limit reached)\n"
                            message += "💎 Use `/pro_info` to learn about Pro tier\n"
                        elif total_topics > 0:
                            message += f"\n📊 *Using {total_topics}/{limit} topics* across all chats\n"
                    else:
                        message += f"\n💎 *Pro User* - {total_topics} topics (unlimited)\n"

                    message += "📋 Use `/account` to manage your subscription\n"
                except Exception as e:
                    self.logger.warning(f"Failed to get user limits: {e}")

            if topics:
                message += "\n💡 Use `/addtopic` to add more or `/removetopic` to remove."

            await update.message.reply_text(message, parse_mode="Markdown")

        except Exception as e:
            await self._handle_error(update, "list topics", e)

    async def handle_add_topic(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle /addtopic command - add a new topic with user-based limits.

        Format: /addtopic <name> <keyword1, keyword2, keyword3>

        Args:
            update: Telegram update object
            context: Bot context
        """
        try:
            chat_id = str(update.effective_chat.id)
            telegram_user_id = (
                update.effective_user.id
            )  # NEW: Get user ID for ownership
            args = context.args

            if not args:
                await self._send_add_topic_help(update)
                return

            # NEW: Validate user can add topic based on SaaS limits
            can_add, reason = await self._validate_topic_creation(telegram_user_id)
            if not can_add:
                await update.message.reply_text(
                    f"❌ *Cannot add topic:* {reason}", parse_mode="Markdown"
                )
                return

            # Parse arguments
            parsed_data = self._parse_add_topic_args(args)
            if not parsed_data:
                await self._send_add_topic_help(update)
                return

            topic_name, keywords = parsed_data

            # Validate topic name
            try:
                validated_name = ContentValidator.validate_topic_name(topic_name)
            except ValidationError as e:
                await update.message.reply_text(
                    f"❌ *Invalid topic name:* {e.message}", parse_mode="Markdown"
                )
                return

            # Handle keywords - either provided or AI-generated
            if keywords is None:
                # AI keyword generation - validate topic has enough context
                try:
                    ai_validated_name = (
                        ContentValidator.validate_topic_name_for_ai_generation(
                            validated_name
                        )
                    )
                except ValidationError as e:
                    await update.message.reply_text(
                        f"❌ *AI keyword generation needs more context:*\n\n{str(e).split('] ')[1]}",
                        parse_mode="Markdown",
                    )
                    return

                progress_msg = await update.message.reply_text(
                    f"🤖 Generating keywords for '{ai_validated_name}'..."
                )
                try:
                    validated_keywords = await self._generate_keywords_with_ai(
                        ai_validated_name, chat_id
                    )
                    await progress_msg.edit_text(
                        f"✅ Generated keywords: {', '.join(validated_keywords)}"
                    )
                except Exception as e:
                    await progress_msg.edit_text(f"⚠️ Using fallback keywords")
                    validated_keywords = [ai_validated_name.lower()]
            else:
                # Manual keywords provided
                try:
                    validated_keywords = ContentValidator.validate_keywords(keywords)
                except ValidationError as e:
                    await update.message.reply_text(
                        f"❌ *Invalid keywords:* {e.message}", parse_mode="Markdown"
                    )
                    return

            # v2: generate a description for embedding-based matching
            description: str
            try:
                gen = TopicDescriptionGenerator(self.ai_manager)
                description = await gen.generate(name=validated_name, keywords=validated_keywords)
            except Exception as e:
                self.logger.warning(f"Description generation failed: {e}")
                description = f"{validated_name}. Keywords: {', '.join(validated_keywords)}"

            # Check if topic already exists
            existing_topic = self.topic_repo.get_topic_by_name(chat_id, validated_name)
            if existing_topic:
                await update.message.reply_text(
                    f"❌ Topic *'{validated_name}'* already exists.\n"
                    f"Use `/edittopic {validated_name}` to modify it.",
                    parse_mode="Markdown",
                )
                return

            # Create new topic WITH user ownership
            topic = Topic(
                chat_id=chat_id,
                name=validated_name,
                keywords=validated_keywords,
                exclude_keywords=[],
                confidence_threshold=0.6,  # Default threshold (Phase 1)
                active=True,
                telegram_user_id=telegram_user_id,  # NEW: Set topic owner
                description=description,
            )

            # Save to database
            topic_id = self.topic_repo.create_topic(topic)

            if topic_id:
                success_message = (
                    f"✅ *Topic '{validated_name}' created successfully!*\n\n"
                    f"*Keywords:* {', '.join(validated_keywords)}\n"
                    f"*Description:* {description}\n"
                    f"*Confidence threshold:* {topic.confidence_threshold}\n\n"
                    f"🎯 I'll now look for content matching this topic!\n\n"
                    f"💡 Use `/edittopic` to refine the description, or `/addfeed` to add RSS feeds."
                )
                await update.message.reply_text(success_message, parse_mode="Markdown")

                self.logger.info(
                    f"Created topic '{validated_name}' for channel {chat_id}"
                )
            else:
                await update.message.reply_text(
                    "❌ Failed to create topic. Please try again.",
                    parse_mode="Markdown",
                )

        except Exception as e:
            await self._handle_error(update, "add topic", e)

    async def handle_remove_topic(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle /removetopic command - remove an existing topic.

        Format: /removetopic <name>

        Args:
            update: Telegram update object
            context: Bot context
        """
        try:
            chat_id = str(update.effective_chat.id)
            args = context.args

            # Get the appropriate message object for reply
            message = None
            if update.message:
                message = update.message
            elif update.effective_message:
                message = update.effective_message
            elif update.callback_query and update.callback_query.message:
                message = update.callback_query.message

            if not message:
                self.logger.warning(
                    "No message object available for remove topic response"
                )
                return

            if not args:
                await message.reply_text(
                    "❌ *Missing topic name*\n\n"
                    "Usage: `/removetopic <topic_name>`\n"
                    "Example: `/removetopic AI`\n\n"
                    "Use `/topics` to see all your topics.",
                    parse_mode="Markdown",
                )
                return

            topic_name = " ".join(args).strip()

            # Find the topic
            topic = self.topic_repo.get_topic_by_name(chat_id, topic_name)
            if not topic:
                await message.reply_text(
                    f"❌ Topic *'{topic_name}'* not found.\n\n"
                    f"Use `/topics` to see all your topics.",
                    parse_mode="Markdown",
                )
                return

            # Remove the topic
            success = self.topic_repo.delete_topic(topic.id)

            if success:
                await message.reply_text(
                    f"✅ Topic *'{topic_name}'* removed successfully!",
                    parse_mode="Markdown",
                )
                self.logger.info(f"Removed topic '{topic_name}' from channel {chat_id}")
            else:
                await message.reply_text(
                    "❌ Failed to remove topic. Please try again.",
                    parse_mode="Markdown",
                )

        except Exception as e:
            await self._handle_error(update, "remove topic", e)

    async def handle_edit_topic(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle /edittopic command - edit an existing topic.

        Two modes:
        - /edittopic <topic_id> <new description>  — updates description and clears embedding
        - /edittopic <name> <keyword1, keyword2>   — updates topic keywords (legacy mode)

        Args:
            update: Telegram update object
            context: Bot context
        """
        try:
            chat_id = str(update.effective_chat.id)
            args = context.args

            if not args:
                await self._send_edit_topic_help(update)
                return

            # Detect new mode: first arg is an integer topic_id
            if args[0].isdigit():
                await self._handle_edit_topic_description(update, context, args)
                return

            # Parse arguments using smart topic name matching
            parsed_data = self._parse_edit_topic_args(args, chat_id)
            if not parsed_data:
                await self._send_edit_topic_help(update)
                return

            topic_name, keywords = parsed_data

            if not keywords:
                await self._send_edit_topic_help(update)
                return

            # Find the topic
            topic = self.topic_repo.get_topic_by_name(chat_id, topic_name)
            if not topic:
                await update.message.reply_text(
                    f"❌ Topic *'{topic_name}'* not found.\n\n"
                    f"Use `/topics` to see all your topics.",
                    parse_mode="Markdown",
                )
                return

            # Validate new keywords
            try:
                validated_keywords = ContentValidator.validate_keywords(keywords)
            except ValidationError as e:
                await update.message.reply_text(
                    f"❌ *Invalid keywords:* {e.message}", parse_mode="Markdown"
                )
                return

            # Update the topic
            topic.keywords = validated_keywords
            success = self.topic_repo.update_topic_object(topic)

            if success:
                await update.message.reply_text(
                    f"✅ Topic *'{topic_name}'* updated successfully!\n\n"
                    f"*New keywords:* {', '.join(validated_keywords)}",
                    parse_mode="Markdown",
                )
                self.logger.info(f"Updated topic '{topic_name}' for channel {chat_id}")
            else:
                await update.message.reply_text(
                    "❌ Failed to update topic. Please try again.",
                    parse_mode="Markdown",
                )

        except Exception as e:
            await self._handle_error(update, "edit topic", e)

    async def _handle_edit_topic_description(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE, args: List[str]
    ) -> None:
        """Handle /edittopic <topic_id> <new description> — update description and clear embedding.

        Args:
            update: Telegram update object
            context: Bot context
            args: Parsed command arguments
        """
        if len(args) < 2:
            await update.message.reply_text(
                "❌ *Usage:* `/edittopic <topic_id> <new description>`\n\n"
                "Example: `/edittopic 3 A topic about cloud computing and DevOps`",
                parse_mode="Markdown",
            )
            return

        try:
            topic_id = int(args[0])
        except ValueError:
            await update.message.reply_text(
                "❌ *Invalid topic ID.* The first argument must be a number.\n\n"
                "Use `/topics` to see your topics and their IDs.",
                parse_mode="Markdown",
            )
            return

        description = " ".join(args[1:]).strip()[:300]

        if not description:
            await update.message.reply_text(
                "❌ *Description cannot be empty.*\n\n"
                "Usage: `/edittopic <topic_id> <new description>`",
                parse_mode="Markdown",
            )
            return

        self.topic_repo.update_description(topic_id, description)
        self.topic_repo.clear_embedding_signature(topic_id)

        await update.message.reply_text(
            f"✅ *Topic updated!*\n\n"
            f"*New description:* {description}\n\n"
            f"The topic will be re-embedded on the next pipeline run.",
            parse_mode="Markdown",
        )
        self.logger.info(f"Updated description for topic {topic_id}")

    async def _generate_keywords_with_ai(
        self, topic_name: str, chat_id: str
    ) -> List[str]:
        """Generate keywords for a topic using AI."""
        try:
            # Remove context contamination to prevent keyword bleeding between unrelated topics
            # Each topic should generate keywords based solely on its own content
            context = ""

            # Use AIManager with proper fallback strategy - same as other AI operations
            result = await self.ai_manager.generate_keywords(
                topic_name, context, max_keywords=7
            )

            if result.success and result.content:
                keywords = result.content if isinstance(result.content, list) else []
                return keywords[:7] if keywords else [topic_name.lower()]
            else:
                # AI failed, use fallback
                self.logger.warning(
                    f"AI keyword generation failed: {result.error_message}"
                )
                raise AIError(result.error_message or "AI generation failed")

        except Exception as e:
            self.logger.error(f"AI keyword generation failed: {e}")
            # Simple fallback - same as AIManager fallback but simpler
            return [topic_name.lower(), f"{topic_name.lower()} technology"]

    def _parse_add_topic_args(
        self, args: List[str]
    ) -> Optional[tuple[str, Optional[List[str]]]]:
        """Parse arguments for /addtopic command.

        Args:
            args: Command arguments

        Returns:
            Tuple of (topic_name, keywords) or None if invalid.
            keywords can be None to indicate AI generation should be used.
        """
        if len(args) < 1:
            return None

        # Join all args to analyze the full input
        full_text = " ".join(args)

        # Check if input contains commas (explicit manual keyword format)
        if "," in full_text:
            # Format: /addtopic AI machine learning, artificial intelligence, ML
            parts = [part.strip() for part in full_text.split(",")]
            if len(parts) >= 2:
                topic_name = parts[0]
                keywords = parts[1:]
                return topic_name, keywords

        # No commas found - treat entire input as topic name for AI generation
        # This handles both single words and multi-word topic names
        topic_name = full_text.strip()
        return topic_name, None  # None indicates AI generation

    def _parse_edit_topic_args(
        self, args: List[str], chat_id: str
    ) -> Optional[tuple[str, List[str]]]:
        """Parse arguments for /edittopic command with smart topic name matching.

        Args:
            args: Command arguments
            chat_id: Chat ID to look up existing topics

        Returns:
            Tuple of (topic_name, keywords) or None if invalid.
        """
        if len(args) < 2:
            return None

        # Join all args to analyze the full input
        full_text = " ".join(args)

        # edittopic requires comma-separated keywords (manual mode only)
        if "," not in full_text:
            return None

        # Split on FIRST comma only to separate topic area from keywords
        first_comma_index = full_text.index(",")
        potential_topic_part = full_text[:first_comma_index].strip()
        keywords_part = full_text[first_comma_index + 1 :].strip()

        if not potential_topic_part or not keywords_part:
            return None

        # Try to find existing topic by removing words from end of potential topic part
        words = potential_topic_part.split()
        for i in range(len(words), 0, -1):
            candidate_topic = " ".join(words[:i])
            if self.topic_repo.get_topic_by_name(chat_id, candidate_topic):
                # Found a matching topic! Calculate remaining keywords
                remaining_words = words[i:]  # Words that weren't part of topic name
                if remaining_words:
                    # Add remaining words to keywords
                    full_keywords = " ".join(remaining_words) + ", " + keywords_part
                else:
                    full_keywords = keywords_part

                keywords = [k.strip() for k in full_keywords.split(",") if k.strip()]
                return candidate_topic, keywords

        # No existing topic found - return first part as topic name for error message
        keywords = [k.strip() for k in keywords_part.split(",") if k.strip()]
        return potential_topic_part, keywords

    async def _send_add_topic_help(self, update: Update) -> None:
        """Send help message for /addtopic command."""
        help_message = (
            "❓ *How to add a topic:*\n\n"
            "*🤖 AI Generation:* `/addtopic <topic_name>`\n"
            "*📝 Manual Keywords:* `/addtopic <topic_name>, <keyword1>, <keyword2>, <keyword3>`\n\n"
            "*Examples:*\n"
            "• `/addtopic Machine Learning` - AI will generate keywords\n"
            "• `/addtopic AWS ECS Performance` - AI will generate keywords\n"
            "• `/addtopic Cloud, AWS, Azure, GCP, kubernetes` - Manual keywords\n"
            "• `/addtopic Python, python programming, django, flask` - Manual keywords\n\n"
            "*Tips:*\n"
            "• No commas = AI generates keywords automatically\n"
            "• With commas = Manual keyword specification\n"
            "• AI considers your existing topics for context"
        )
        await update.message.reply_text(help_message, parse_mode="Markdown")

    async def _send_edit_topic_help(self, update: Update) -> None:
        """Send help message for /edittopic command."""
        help_message = (
            "❓ *How to edit a topic:*\n\n"
            "*Format:* `/edittopic <topic_name> <keyword1, keyword2, keyword3>`\n\n"
            "*Examples:*\n"
            "• `/edittopic AI machine learning, deep learning, neural networks`\n"
            "• `/edittopic TikTok software engineers programming, coding, app development`\n"
            "• `/edittopic Cloud kubernetes, docker, containers`\n\n"
            "*Important:* Keywords must be separated by commas.\n"
            "Use `/topics` to see your current topics."
        )
        await update.message.reply_text(help_message, parse_mode="Markdown")

    async def _handle_error(
        self, update: Update, operation: str, error: Exception
    ) -> None:
        """Handle errors in topic operations.

        Args:
            update: Telegram update object
            operation: Operation that failed
            error: Exception that occurred
        """
        self.logger.error(f"Error in {operation}: {error}")

        try:
            error_message = (
                f"❌ *Error in {operation}*\n\n"
                f"Please try again or use `/help` for usage instructions."
            )

            # Try multiple message sources in order of preference
            message = None
            if update.message:
                message = update.message
            elif update.effective_message:
                message = update.effective_message
            elif update.callback_query and update.callback_query.message:
                message = update.callback_query.message

            if message:
                await message.reply_text(error_message, parse_mode="Markdown")
            else:
                self.logger.warning(
                    "No message object available to send error response"
                )
        except Exception as e:
            self.logger.error(f"Failed to send error message: {e}")

    # ================================================================
    # UTILITY METHODS
    # ================================================================

    def get_topic_statistics(self, chat_id: str) -> Dict[str, Any]:
        """Get topic statistics for a channel.

        Args:
            chat_id: Channel chat ID

        Returns:
            Dictionary with topic statistics
        """
        try:
            topics = self.topic_repo.get_topics_for_channel(chat_id, active_only=True)

            total_keywords = sum(len(topic.keywords) for topic in topics)
            avg_keywords = total_keywords / len(topics) if topics else 0

            return {
                "total_topics": len(topics),
                "total_keywords": total_keywords,
                "average_keywords_per_topic": round(avg_keywords, 1),
                "topics": [
                    {
                        "name": topic.name,
                        "keyword_count": len(topic.keywords),
                        "threshold": topic.confidence_threshold,
                    }
                    for topic in topics
                ],
            }

        except Exception as e:
            self.logger.error(f"Error getting topic statistics: {e}")
            return {}

    async def validate_topic_setup(self, chat_id: str) -> Dict[str, Any]:
        """Validate topic setup for a channel.

        Args:
            chat_id: Channel chat ID

        Returns:
            Validation results dictionary
        """
        try:
            topics = self.topic_repo.get_topics_for_channel(chat_id, active_only=True)

            issues = []
            warnings = []

            if not topics:
                issues.append("No topics configured")
            else:
                # Check for topics with very few keywords
                for topic in topics:
                    if len(topic.keywords) < 2:
                        warnings.append(
                            f"Topic '{topic.name}' has only {len(topic.keywords)} keyword(s)"
                        )

                # Check for very low thresholds
                low_threshold_topics = [
                    t for t in topics if t.confidence_threshold < 0.3
                ]
                if low_threshold_topics:
                    warnings.append(
                        f"{len(low_threshold_topics)} topic(s) have very low confidence thresholds"
                    )

            return {
                "valid": len(issues) == 0,
                "topic_count": len(topics),
                "issues": issues,
                "warnings": warnings,
            }

        except Exception as e:
            self.logger.error(f"Error validating topic setup: {e}")
            return {"valid": False, "issues": ["Validation error occurred"]}

    # ================================================================
    # NEW: SaaS USER VALIDATION METHODS
    # ================================================================

    async def _validate_topic_creation(self, telegram_user_id: int) -> tuple[bool, str]:
        """Validate if user can create another topic based on SaaS limits.

        Args:
            telegram_user_id: Telegram user ID

        Returns:
            Tuple of (can_add: bool, reason: str)
        """
        return await self.user_service.can_add_topic(telegram_user_id)

    # ================================================================
    # NEW: USER MANAGEMENT COMMANDS
    # ================================================================

    async def handle_account(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle /account command - show user account, subscription, and topics across all chats.

        Args:
            update: Telegram update object
            context: Bot context
        """
        try:
            telegram_user_id = update.effective_user.id

            # Get user's topic summary
            summary = await self.user_service.get_user_topic_summary(telegram_user_id)

            if not summary["saas_mode_enabled"]:
                await update.message.reply_text(
                    "💎 *Account (Self-Hosted Mode)*\n\n"
                    "SaaS mode is disabled. Topic limits not enforced.\n"
                    "Use `/topics` to see topics in this chat.",
                    parse_mode="Markdown",
                )
                return

            # Build message
            message = f"💎 *Account & Subscription*\n\n"
            message += f"**Tier:** {summary['subscription_tier'].upper()}\n"
            message += f"**Topics Used:** {summary['total_topics']}"

            if summary["subscription_tier"] == UserTier.FREE:
                message += f"/{summary['limit']}\n"
            else:
                message += " (Unlimited)\n"

            # Get user subscription for member since date
            user_subscription = await self.user_service.get_user_subscription(telegram_user_id)
            message += f"**Member Since:** {user_subscription.created_at.strftime('%B %Y')}\n"

            if summary["topics_by_chat"]:
                message += "\n**Topics by Chat:**\n"
                for chat_info in summary["topics_by_chat"]:
                    chat_type_emoji = {
                        "private": "👤",
                        "group": "👥",
                        "supergroup": "👥",
                        "channel": "📢",
                    }.get(chat_info["chat_type"], "💬")

                    message += f"\n{chat_type_emoji} *{chat_info['chat_title']}* ({chat_info['topic_count']} topics)\n"

                    # Split topic names and display as bullet points
                    topic_names = [name.strip() for name in chat_info['topic_names'].split(',') if name.strip()]
                    for topic_name in topic_names:
                        message += f"   • {topic_name}\n"

                if summary["can_add_more"]:
                    remaining = (
                        summary["limit"] - summary["total_topics"]
                        if summary["limit"] != "Unlimited"
                        else "unlimited"
                    )
                    message += f"\n✅ You can add {remaining} more topics."
                else:
                    message += (
                        f"\n⚠️ *Free tier limit reached!*\n"
                        f"💎 Use `/pro_info` to learn about Pro upgrade"
                    )
            else:
                message += "\n*No topics created yet.*\n"
                message += "Use `/addtopic` to create your first topic!"

            await update.message.reply_text(message, parse_mode="Markdown")

        except Exception as e:
            await self._handle_error(update, "account", e)

    async def handle_topic_usage(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle /topic_usage command - show topic count and limits.

        Args:
            update: Telegram update object
            context: Bot context
        """
        try:
            telegram_user_id = update.effective_user.id
            settings = get_settings()

            if not settings.saas.saas_mode:
                await update.message.reply_text(
                    "📈 *Topic Usage (Self-Hosted)*\n\n"
                    "SaaS mode is disabled. No topic limits enforced.\n"
                    "You can create unlimited topics.",
                    parse_mode="Markdown",
                )
                return

            # Get user subscription and usage
            user_subscription = await self.user_service.get_user_subscription(
                telegram_user_id
            )
            current_count = await self.user_service.count_user_topics(telegram_user_id)

            message = "📈 *Topic Usage & Limits*\n\n"
            message += (
                f"**Current Tier:** {user_subscription.subscription_tier.upper()}\n"
            )
            message += f"**Topics Used:** {current_count}"

            if user_subscription.subscription_tier == UserTier.FREE:
                limit = settings.saas.free_tier_topic_limit_per_user
                message += f"/{limit}\n"

                # Progress bar
                percentage = (current_count / limit) * 100
                filled = int(percentage / 10)
                bar = "█" * filled + "░" * (10 - filled)
                message += f"**Progress:** {bar} {percentage:.0f}%\n"

                if current_count >= limit:
                    message += "\n🚫 *Limit reached!* Cannot add more topics.\n"
                    message += "💎 Pro upgrade coming soon for unlimited topics!"
                else:
                    remaining = limit - current_count
                    message += f"\n✅ You can add {remaining} more topics."
            else:
                message += " (Unlimited)\n"
                message += "💎 **Pro User** - No limits on topic creation!"

            message += f"\n\n📅 **Member since:** {user_subscription.created_at.strftime('%B %Y')}"

            await update.message.reply_text(message, parse_mode="Markdown")

        except Exception as e:
            await self._handle_error(update, "topic usage", e)

    async def handle_pro_info(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle /pro_info command - show pro tier benefits.

        Args:
            update: Telegram update object
            context: Bot context
        """
        try:
            telegram_user_id = update.effective_user.id
            settings = get_settings()

            if not settings.saas.saas_mode:
                await update.message.reply_text(
                    "💎 *Pro Tier (Self-Hosted)*\n\n"
                    "SaaS mode is disabled. All features are free!\n"
                    "No Pro tier needed in self-hosted mode.",
                    parse_mode="Markdown",
                )
                return

            user_subscription = await self.user_service.get_user_subscription(
                telegram_user_id
            )

            if user_subscription.subscription_tier == UserTier.PRO:
                message = "💎 *You're Already Pro!*\n\n"
                message += "✅ Unlimited topics across all chats\n"
                message += "✅ Priority AI processing\n"
                message += "✅ Advanced content filtering\n"
                message += "✅ Premium support\n\n"
                message += "Thank you for supporting CuliFeed! 🙏"
            else:
                current_count = await self.user_service.count_user_topics(
                    telegram_user_id
                )
                limit = settings.saas.free_tier_topic_limit_per_user

                message = "💎 *CuliFeed Pro Benefits*\n\n"
                message += "**Current Plan:** FREE\n"
                message += f"**Current Usage:** {current_count}/{limit} topics\n\n"

                message += "**Upgrade to Pro for:**\n"
                message += "🚀 Unlimited topics across all chats\n"
                message += "⚡ Priority AI processing\n"
                message += "🎯 Advanced content filtering\n"
                message += "💬 Premium support\n"
                message += "🔧 Early access to new features\n\n"

                message += "💰 **Pricing:** Coming soon!\n"
                message += (
                    "Payment integration with Ko-fi/PayPal is in development.\n\n"
                )

                if current_count >= limit:
                    message += "⚠️ *You've reached your free tier limit.*\n"
                    message += "Upgrade to Pro to add unlimited topics!"

            await update.message.reply_text(message, parse_mode="Markdown")

        except Exception as e:
            await self._handle_error(update, "pro info", e)
