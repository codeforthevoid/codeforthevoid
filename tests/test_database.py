import pytest
from sqlalchemy.orm import Session
from src.database.models import Conversation, Message, Terminal
from src.database.connection import engine, SessionLocal
from datetime import datetime


@pytest.fixture
def db_session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def test_create_terminal(db_session):
    terminal = Terminal(
        id="test-terminal-1",
        model_type="gpt",
        status="active"
    )
    db_session.add(terminal)
    db_session.commit()

    saved_terminal = db_session.query(Terminal).filter_by(id="test-terminal-1").first()
    assert saved_terminal is not None
    assert saved_terminal.model_type == "gpt"
    assert saved_terminal.status == "active"


def test_create_conversation(db_session):
    conversation = Conversation(
        id="test-conversation-1",
        terminal1_id="test-terminal-1",
        terminal2_id="test-terminal-2",
        start_time=datetime.now()
    )
    db_session.add(conversation)
    db_session.commit()

    saved_conversation = db_session.query(Conversation).filter_by(id="test-conversation-1").first()
    assert saved_conversation is not None
    assert saved_conversation.terminal1_id == "test-terminal-1"
    assert saved_conversation.terminal2_id == "test-terminal-2"


def test_create_message(db_session):
    message = Message(
        conversation_id="test-conversation-1",
        content="Test message content",
        sender_id="test-terminal-1",
        timestamp=datetime.now()
    )
    db_session.add(message)
    db_session.commit()

    saved_message = db_session.query(Message).filter_by(sender_id="test-terminal-1").first()
    assert saved_message is not None
    assert saved_message.content == "Test message content"
    assert saved_message.conversation_id == "test-conversation-1"