"""Bot /addtopic v2 description-generation flow."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_addtopic_generates_and_persists_description():
    from culifeed.bot.commands.topic_commands import TopicCommandHandler

    handler = TopicCommandHandler.__new__(TopicCommandHandler)  # bypass __init__
    handler.logger = MagicMock()
    handler.ai_manager = MagicMock()
    handler.topic_repo = MagicMock()
    handler.topic_repo.get_topic_by_name = MagicMock(return_value=None)
    handler.topic_repo.create_topic = MagicMock(return_value=42)

    # Stubs the existing prereqs in the handler
    handler._validate_topic_creation = AsyncMock(return_value=(True, ""))
    handler._handle_error = AsyncMock()
    handler._send_add_topic_help = AsyncMock()
    handler._parse_add_topic_args = MagicMock(return_value=("MyTopic", ["k1", "k2"]))

    update = MagicMock()
    update.message = MagicMock()
    update.message.reply_text = AsyncMock()
    update.effective_chat.id = 1
    update.effective_user.id = 99
    context = MagicMock()
    context.args = ["MyTopic", "k1, k2"]

    fake_gen = MagicMock()
    fake_gen.generate = AsyncMock(return_value="Generated description for MyTopic.")
    with patch(
        "culifeed.bot.commands.topic_commands.TopicDescriptionGenerator",
        return_value=fake_gen,
    ):
        await handler.handle_add_topic(update, context)

    # The Topic passed to create_topic should include the generated description
    handler.topic_repo.create_topic.assert_called_once()
    saved = handler.topic_repo.create_topic.call_args.args[0]
    assert saved.description == "Generated description for MyTopic."
    # Success message mentions the description
    update.message.reply_text.assert_called()
    msgs = " ".join(call.args[0] for call in update.message.reply_text.call_args_list)
    assert "Generated description for MyTopic." in msgs


@pytest.mark.asyncio
async def test_edit_topic_updates_description_and_clears_embedding():
    """handle_edit_topic with integer first arg updates description and clears embedding_signature."""
    from culifeed.bot.commands.topic_commands import TopicCommandHandler

    handler = TopicCommandHandler.__new__(TopicCommandHandler)  # bypass __init__
    handler.logger = MagicMock()
    handler.topic_repo = MagicMock()
    handler.topic_repo.update_description = MagicMock()
    handler.topic_repo.clear_embedding_signature = MagicMock()
    handler._handle_error = AsyncMock()

    update = MagicMock()
    update.message = MagicMock()
    update.message.reply_text = AsyncMock()
    context = MagicMock()
    context.args = ["1", "New description text"]

    await handler.handle_edit_topic(update, context)

    handler.topic_repo.update_description.assert_called_once_with(1, "New description text")
    handler.topic_repo.clear_embedding_signature.assert_called_once_with(1)
    update.message.reply_text.assert_awaited_once()
