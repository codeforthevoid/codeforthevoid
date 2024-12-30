from sqlalchemy import (
    Column, Integer, String, DateTime, ForeignKey, Text,
    UniqueConstraint, Index, JSON, Enum as SQLEnum, Boolean
)
from sqlalchemy.orm import relationship, validates
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.sql import expression
from datetime import datetime
import enum
import uuid

Base = declarative_base()


class ConversationStatus(enum.Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    ENDED = "ended"
    ERROR = "error"


class TerminalStatus(enum.Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    MAINTENANCE = "maintenance"
    ERROR = "error"


class ModelType(enum.Enum):
    GPT = "gpt"
    CUSTOM = "custom"
    HYBRID = "hybrid"


class BaseModel(Base):
    __abstract__ = True

    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow
    )
    is_deleted = Column(Boolean, nullable=False, default=False)
    metadata = Column(JSON, nullable=True)

    @validates('metadata')
    def validate_metadata(self, key, metadata):
        if metadata is not None and not isinstance(metadata, dict):
            raise ValueError("Metadata must be a dictionary")
        return metadata


class Conversation(BaseModel):
    __tablename__ = 'conversations'

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    terminal1_id = Column(String(36), ForeignKey('terminals.id'), nullable=False)
    terminal2_id = Column(String(36), ForeignKey('terminals.id'), nullable=False)
    start_time = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    end_time = Column(DateTime(timezone=True), nullable=True)
    status = Column(SQLEnum(ConversationStatus), nullable=False, default=ConversationStatus.ACTIVE)
    last_activity = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    message_count = Column(Integer, nullable=False, default=0)
    error_count = Column(Integer, nullable=False, default=0)

    # Relationships
    terminal1 = relationship('Terminal', foreign_keys=[terminal1_id], back_populates='conversations_as_terminal1')
    terminal2 = relationship('Terminal', foreign_keys=[terminal2_id], back_populates='conversations_as_terminal2')
    messages = relationship('Message', back_populates='conversation', cascade='all, delete-orphan')

    __table_args__ = (
        UniqueConstraint('terminal1_id', 'terminal2_id', 'start_time', name='uq_conversation_terminals_time'),
        Index('idx_conversation_status', 'status'),
        Index('idx_conversation_activity', 'last_activity'),
        Index('idx_conversation_terminals', 'terminal1_id', 'terminal2_id')
    )

    @validates('terminal2_id')
    def validate_terminals(self, key, terminal2_id):
        if hasattr(self, 'terminal1_id') and self.terminal1_id == terminal2_id:
            raise ValueError("terminal1_id and terminal2_id must be different")
        return terminal2_id

    def update_activity(self):
        self.last_activity = datetime.utcnow()
        self.message_count += 1

    def end_conversation(self, status=ConversationStatus.ENDED):
        self.status = status
        self.end_time = datetime.utcnow()


class Message(BaseModel):
    __tablename__ = 'messages'

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    conversation_id = Column(String(36), ForeignKey('conversations.id', ondelete='CASCADE'), nullable=False)
    content = Column(Text, nullable=False)
    sender_id = Column(String(36), ForeignKey('terminals.id'), nullable=False)
    timestamp = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    sequence_number = Column(Integer, nullable=False)
    content_type = Column(String(50), nullable=False, default='text')
    is_error = Column(Boolean, nullable=False, default=False)
    processing_time = Column(Integer, nullable=True)  # milliseconds
    token_count = Column(Integer, nullable=True)

    # Relationships
    conversation = relationship('Conversation', back_populates='messages')
    sender = relationship('Terminal', back_populates='sent_messages')

    __table_args__ = (
        UniqueConstraint('conversation_id', 'sequence_number', name='uq_message_sequence'),
        Index('idx_message_timestamp', 'timestamp'),
        Index('idx_message_sender', 'sender_id'),
        Index('idx_message_conversation_time', 'conversation_id', 'timestamp')
    )

    @validates('content')
    def validate_content(self, key, content):
        if not content or not content.strip():
            raise ValueError("Message content cannot be empty")
        if len(content) > 10000:  # Example limit
            raise ValueError("Message content exceeds maximum length")
        return content

    def calculate_token_count(self):
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        self.token_count = len(enc.encode(self.content))


class Terminal(BaseModel):
    __tablename__ = 'terminals'

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String(100), nullable=False)
    model_type = Column(SQLEnum(ModelType), nullable=False)
    status = Column(SQLEnum(TerminalStatus), nullable=False, default=TerminalStatus.ACTIVE)
    last_active = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    configuration = Column(JSON, nullable=True)
    version = Column(String(20), nullable=False)
    error_count = Column(Integer, nullable=False, default=0)
    total_conversations = Column(Integer, nullable=False, default=0)
    total_messages = Column(Integer, nullable=False, default=0)
    average_response_time = Column(Integer, nullable=True)  # milliseconds

    # Relationships
    conversations_as_terminal1 = relationship(
        'Conversation',
        foreign_keys='Conversation.terminal1_id',
        back_populates='terminal1'
    )
    conversations_as_terminal2 = relationship(
        'Conversation',
        foreign_keys='Conversation.terminal2_id',
        back_populates='terminal2'
    )
    sent_messages = relationship('Message', back_populates='sender')

    __table_args__ = (
        Index('idx_terminal_status', 'status'),
        Index('idx_terminal_model', 'model_type'),
        Index('idx_terminal_activity', 'last_active')
    )

    @validates('name')
    def validate_name(self, key, name):
        if not name or not name.strip():
            raise ValueError("Terminal name cannot be empty")
        if len(name) > 100:
            raise ValueError("Terminal name exceeds maximum length")
        return name.strip()

    @validates('configuration')
    def validate_configuration(self, key, configuration):
        required_keys = {'api_key', 'model_params', 'runtime_settings'}
        if configuration and not all(key in configuration for key in required_keys):
            raise ValueError(f"Configuration must contain all required keys: {required_keys}")
        return configuration

    def update_metrics(self, response_time: int):
        if self.average_response_time is None:
            self.average_response_time = response_time
        else:
            self.average_response_time = (
                    (self.average_response_time * self.total_messages + response_time) /
                    (self.total_messages + 1)
            )
        self.total_messages += 1
        self.last_active = datetime.utcnow()

    def increment_error_count(self):
        self.error_count += 1
        if self.error_count >= 10:  # Example threshold
            self.status = TerminalStatus.ERROR

    def start_conversation(self):
        self.total_conversations += 1
        self.last_active = datetime.utcnow()


class SystemLog(BaseModel):
    __tablename__ = 'system_logs'

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    level = Column(String(20), nullable=False)
    component = Column(String(50), nullable=False)
    message = Column(Text, nullable=False)
    timestamp = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    terminal_id = Column(String(36), ForeignKey('terminals.id'), nullable=True)
    conversation_id = Column(String(36), ForeignKey('conversations.id'), nullable=True)
    trace = Column(Text, nullable=True)

    __table_args__ = (
        Index('idx_log_timestamp', 'timestamp'),
        Index('idx_log_level', 'level'),
        Index('idx_log_component', 'component')
    )