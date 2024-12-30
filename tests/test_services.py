import pytest
from src.services.conversation_service import ConversationService
from src.services.messaging_service import MessagingService
from src.ai.terminal import AITerminal


@pytest.fixture
def conversation_service():
    return ConversationService()


@pytest.fixture
def messaging_service():
    return MessagingService()


@pytest.fixture
def terminal1():
    return AITerminal("test-terminal-1", "gpt")


@pytest.fixture
def terminal2():
    return AITerminal("test-terminal-2", "gpt")


@pytest.mark.asyncio
async def test_create_conversation_service(conversation_service, terminal1, terminal2):
    conversation_id = await conversation_service.create_conversation(terminal1, terminal2)
    assert conversation_id in conversation_service.active_conversations

    conversation = conversation_service.active_conversations[conversation_id]
    assert conversation.terminal1_id == terminal1.terminal_id
    assert conversation.terminal2_id == terminal2.terminal_id


@pytest.mark.asyncio
async def test_messaging_service(messaging_service, terminal1, terminal2):
    await messaging_service.register_terminal(terminal1)
    await messaging_service.register_terminal(terminal2)

    test_message = "Test message content"
    await messaging_service.send_message(
        terminal1.terminal_id,
        terminal2.terminal_id,
        test_message
    )

    async for message in messaging_service.get_messages(terminal2.terminal_id):
        assert message.content == test_message
        assert message.sender_id == terminal1.terminal_id
        break