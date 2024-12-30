from typing import List, Dict, Optional, Set, Any
from datetime import datetime, timedelta
import asyncio
import uuid
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_, desc
from sqlalchemy.orm import selectinload
from sqlalchemy.exc import SQLAlchemyError

from ..database.models import (
    Conversation, Message, Terminal,
    ConversationStatus, SystemLog
)
from ..ai.terminal import AITerminal
from ..database.connection import DatabaseSession
from ..utils.metrics import MetricsCollector


class ConversationServiceError(Exception):
    pass


class ConversationRateLimitError(ConversationServiceError):
    pass


class ConversationService:
    MAX_ACTIVE_CONVERSATIONS = 1000
    MESSAGE_BATCH_SIZE = 100
    RATE_LIMIT_WINDOW = 60  # seconds
    MAX_MESSAGES_PER_WINDOW = 100

    def __init__(self, db_session: AsyncSession, metrics_collector: MetricsCollector):
        self.db = db_session
        self.metrics = metrics_collector
        self.active_conversations: Dict[str, Conversation] = {}
        self.rate_limits: Dict[str, List[datetime]] = {}
        self.logger = logging.getLogger(__name__)

        # Start background tasks
        self.cleanup_task = asyncio.create_task(self._cleanup_inactive_conversations())

    async def create_conversation(
            self,
            terminal1: AITerminal,
            terminal2: AITerminal,
            metadata: Optional[Dict] = None
    ) -> str:
        """
        Create a new conversation between two terminals.

        Args:
            terminal1: First terminal
            terminal2: Second terminal
            metadata: Optional conversation metadata

        Returns:
            str: Conversation ID

        Raises:
            ConversationServiceError: If creation fails
            ConversationRateLimitError: If rate limit exceeded
        """
        try:
            if len(self.active_conversations) >= self.MAX_ACTIVE_CONVERSATIONS:
                raise ConversationServiceError("Maximum active conversations limit reached")

            # Check terminal status
            await self._verify_terminals(terminal1.terminal_id, terminal2.terminal_id)

            conversation_id = str(uuid.uuid4())
            conversation = Conversation(
                id=conversation_id,
                terminal1_id=terminal1.terminal_id,
                terminal2_id=terminal2.terminal_id,
                start_time=datetime.utcnow(),
                status=ConversationStatus.ACTIVE,
                metadata=metadata or {}
            )

            # Save to database
            self.db.add(conversation)
            await self.db.flush()

            # Update terminals
            terminals = await self._get_terminals([terminal1.terminal_id, terminal2.terminal_id])
            for terminal in terminals:
                terminal.start_conversation()

            # Update cache
            self.active_conversations[conversation_id] = conversation

            # Log event
            await self._log_event(
                "conversation_created",
                conversation_id=conversation_id,
                terminal1_id=terminal1.terminal_id,
                terminal2_id=terminal2.terminal_id
            )

            # Update metrics
            self.metrics.increment("conversations.created")

            await self.db.commit()
            return conversation_id

        except SQLAlchemyError as e:
            await self.db.rollback()
            self.logger.error("Database error creating conversation: %s", str(e))
            raise ConversationServiceError("Failed to create conversation") from e

        except Exception as e:
            await self.db.rollback()
            self.logger.error("Error creating conversation: %s", str(e))
            raise

    async def add_message(
            self,
            conversation_id: str,
            content: str,
            sender_id: str,
            metadata: Optional[Dict] = None
    ) -> str:
        """
        Add a message to a conversation.

        Args:
            conversation_id: Target conversation ID
            content: Message content
            sender_id: Message sender ID
            metadata: Optional message metadata

        Returns:
            str: Message ID

        Raises:
            ConversationServiceError: If message addition fails
            ConversationRateLimitError: If rate limit exceeded
        """
        try:
            # Check rate limit
            await self._check_rate_limit(sender_id)

            # Get conversation
            conversation = await self._get_conversation(conversation_id)
            if not conversation:
                raise ConversationServiceError("Conversation not found")

            if conversation.status != ConversationStatus.ACTIVE:
                raise ConversationServiceError(f"Conversation is {conversation.status.value}")

            # Create message
            message = Message(
                id=str(uuid.uuid4()),
                conversation_id=conversation_id,
                content=content,
                sender_id=sender_id,
                timestamp=datetime.utcnow(),
                metadata=metadata or {},
                sequence_number=conversation.message_count + 1
            )

            # Calculate metrics
            start_time = datetime.utcnow()
            message.calculate_token_count()
            message.processing_time = (datetime.utcnow() - start_time).microseconds // 1000

            # Update conversation
            conversation.update_activity()

            # Save to database
            self.db.add(message)
            await self.db.flush()

            # Update terminal metrics
            terminal = await self._get_terminal(sender_id)
            if terminal:
                terminal.update_metrics(message.processing_time)

            # Update rate limiting
            self._update_rate_limit(sender_id)

            # Update metrics
            self.metrics.increment("messages.created")
            self.metrics.timing("message.processing_time", message.processing_time)

            await self.db.commit()
            return message.id

        except SQLAlchemyError as e:
            await self.db.rollback()
            self.logger.error("Database error adding message: %s", str(e))
            raise ConversationServiceError("Failed to add message") from e

        except Exception as e:
            await self.db.rollback()
            self.logger.error("Error adding message: %s", str(e))
            raise

    async def get_conversation_history(
            self,
            conversation_id: str,
            limit: int = 100,
            before: Optional[datetime] = None,
            after: Optional[datetime] = None
    ) -> List[Dict[str, Any]]:
        """
        Get conversation message history with filtering options.

        Args:
            conversation_id: Target conversation ID
            limit: Maximum number of messages to return
            before: Return messages before this time
            after: Return messages after this time

        Returns:
            List[Dict]: Message history

        Raises:
            ConversationServiceError: If retrieval fails
        """
        try:
            # Build query
            query = select(Message).where(Message.conversation_id == conversation_id)

            if before:
                query = query.where(Message.timestamp < before)
            if after:
                query = query.where(Message.timestamp > after)

            query = query.order_by(desc(Message.timestamp)).limit(limit)

            # Execute query
            result = await self.db.execute(query)
            messages = result.scalars().all()

            # Format response
            return [
                {
                    "id": message.id,
                    "content": message.content,
                    "sender_id": message.sender_id,
                    "timestamp": message.timestamp.isoformat(),
                    "sequence_number": message.sequence_number,
                    "metadata": message.metadata
                }
                for message in messages
            ]

        except SQLAlchemyError as e:
            self.logger.error("Database error retrieving messages: %s", str(e))
            raise ConversationServiceError("Failed to retrieve messages") from e

        except Exception as e:
            self.logger.error("Error retrieving messages: %s", str(e))
            raise

    async def end_conversation(
            self,
            conversation_id: str,
            reason: Optional[str] = None
    ) -> None:
        """
        End a conversation and cleanup resources.

        Args:
            conversation_id: Target conversation ID
            reason: Optional reason for ending

        Raises:
            ConversationServiceError: If ending conversation fails
        """
        try:
            conversation = await self._get_conversation(conversation_id)
            if not conversation:
                raise ConversationServiceError("Conversation not found")

            # Update conversation status
            conversation.end_conversation(reason=reason)

            # Remove from active conversations
            self.active_conversations.pop(conversation_id, None)

            # Log event
            await self._log_event(
                "conversation_ended",
                conversation_id=conversation_id,
                reason=reason
            )

            # Update metrics
            self.metrics.increment("conversations.ended")

            await self.db.commit()

        except SQLAlchemyError as e:
            await self.db.rollback()
            self.logger.error("Database error ending conversation: %s", str(e))
            raise ConversationServiceError("Failed to end conversation") from e

        except Exception as e:
            await self.db.rollback()
            self.logger.error("Error ending conversation: %s", str(e))
            raise

    async def _get_conversation(self, conversation_id: str) -> Optional[Conversation]:
        """Get conversation by ID with related data."""
        query = select(Conversation).where(Conversation.id == conversation_id)
        result = await self.db.execute(query)
        return result.scalar_one_or_none()

    async def _get_terminal(self, terminal_id: str) -> Optional[Terminal]:
        """Get terminal by ID."""
        query = select(Terminal).where(Terminal.id == terminal_id)
        result = await self.db.execute(query)
        return result.scalar_one_or_none()

    async def _get_terminals(self, terminal_ids: List[str]) -> List[Terminal]:
        """Get multiple terminals by ID."""
        query = select(Terminal).where(Terminal.id.in_(terminal_ids))
        result = await self.db.execute(query)
        return result.scalars().all()

    async def _verify_terminals(self, terminal1_id: str, terminal2_id: str) -> None:
        """Verify terminals exist and are available."""
        terminals = await self._get_terminals([terminal1_id, terminal2_id])
        if len(terminals) != 2:
            raise ConversationServiceError("One or more terminals not found")

        for terminal in terminals:
            if terminal.status != "active":
                raise ConversationServiceError(f"Terminal {terminal.id} is not active")

    async def _check_rate_limit(self, terminal_id: str) -> None:
        """Check if terminal has exceeded rate limit."""
        if terminal_id not in self.rate_limits:
            self.rate_limits[terminal_id] = []

        now = datetime.utcnow()
        window_start = now - timedelta(seconds=self.RATE_LIMIT_WINDOW)

        # Remove old timestamps
        self.rate_limits[terminal_id] = [
            ts for ts in self.rate_limits[terminal_id]
            if ts > window_start
        ]

        if len(self.rate_limits[terminal_id]) >= self.MAX_MESSAGES_PER_WINDOW:
            raise ConversationRateLimitError("Rate limit exceeded")

    def _update_rate_limit(self, terminal_id: str) -> None:
        """Update rate limit tracking for terminal."""
        self.rate_limits[terminal_id].append(datetime.utcnow())

    async def _log_event(
            self,
            event_type: str,
            **event_data: Any
    ) -> None:
        """Log system event."""
        log = SystemLog(
            level="INFO",
            component="conversation_service",
            message=event_type,
            metadata=event_data
        )
        self.db.add(log)

    async def _cleanup_inactive_conversations(self) -> None:
        """Periodically cleanup inactive conversations."""
        while True:
            try:
                current_time = datetime.utcnow()
                timeout = timedelta(minutes=30)

                # Find inactive conversations
                query = select(Conversation).where(
                    and_(
                        Conversation.status == ConversationStatus.ACTIVE,
                        Conversation.last_activity < current_time - timeout
                    )
                )

                result = await self.db.execute(query)
                inactive = result.scalars().all()

                # End inactive conversations
                for conversation in inactive:
                    await self.end_conversation(
                        conversation.id,
                        reason="inactivity_timeout"
                    )

                await asyncio.sleep(60)

            except Exception as e:
                self.logger.error("Error in cleanup task: %s", str(e))
                await asyncio.sleep(60)

    async def cleanup(self) -> None:
        try:
            if hasattr(self, 'cleanup_task'):
                self.cleanup_task.cancel()
                try:
                    await self.cleanup_task
                except asyncio.CancelledError:
                    pass

            for conv_id in list(self.active_conversations.keys()):
                try:
                    await self.end_conversation(conv_id, reason="service_shutdown")
                except Exception as e:
                    self.logger.error(f"Error ending conversation {conv_id}: {e}")

            self.active_conversations.clear()
            self.rate_limits.clear()

        except Exception as e:
            self.logger.error(f"Error during cleanup: {e}")
            raise