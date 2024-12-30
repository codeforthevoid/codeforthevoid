from typing import List, Dict, Optional, Set, TypedDict
from datetime import datetime, timedelta
import asyncio
import json
from enum import Enum
import logging


class ConversationStatus(Enum):
    """Enum representing the possible states of a conversation"""
    ACTIVE = "active"
    PAUSED = "paused"
    ENDED = "ended"
    ERROR = "error"


class Message(TypedDict):
    """Type definition for a conversation message"""
    id: str
    conversation_id: str
    content: str
    sender_id: str
    timestamp: datetime
    metadata: Dict
    processed: bool


class Conversation(TypedDict):
    """Type definition for a conversation"""
    id: str
    terminal1_id: str
    terminal2_id: str
    start_time: datetime
    last_activity: datetime
    status: ConversationStatus
    messages: List[Message]
    metadata: Dict


class ConversationError(Exception):
    """Base class for conversation-related errors"""
    pass


class ConversationManager:
    """
    Manages AI terminal conversations including creation, message handling,
    and conversation lifecycle management.
    """

    def __init__(self, config: Optional[Dict] = None):
        """
        Initialize the Conversation Manager.

        Args:
            config: Configuration dictionary
                - max_conversations: Maximum number of active conversations
                - message_limit: Maximum messages per conversation
                - idle_timeout: Timeout for inactive conversations (minutes)
                - backup_interval: Interval for conversation backups (minutes)
        """
        self.config = {
            'max_conversations': 1000,
            'message_limit': 1000,
            'idle_timeout': 30,  # minutes
            'backup_interval': 15  # minutes
        }
        if config:
            self.config.update(config)

        self.active_conversations: Dict[str, Conversation] = {}
        self.conversation_pairs: Dict[str, tuple] = {}
        self.terminal_conversations: Dict[str, Set[str]] = {}
        self.logger = logging.getLogger(__name__)

        # Start background tasks
        self.cleanup_task = asyncio.create_task(self._cleanup_inactive_conversations())
        self.backup_task = asyncio.create_task(self._periodic_backup())

    async def create_conversation(
            self,
            terminal1_id: str,
            terminal2_id: str,
            metadata: Optional[Dict] = None
    ) -> str:
        """
        Create a new conversation between two terminals.

        Args:
            terminal1_id: ID of the first terminal
            terminal2_id: ID of the second terminal
            metadata: Additional conversation metadata

        Returns:
            str: Newly created conversation ID

        Raises:
            ConversationError: If creation fails or limits are exceeded
        """
        try:
            if len(self.active_conversations) >= self.config['max_conversations']:
                raise ConversationError("Maximum number of active conversations reached")

            # Generate unique conversation ID
            conversation_id = self._generate_conversation_id(terminal1_id, terminal2_id)

            # Create conversation object
            conversation: Conversation = {
                'id': conversation_id,
                'terminal1_id': terminal1_id,
                'terminal2_id': terminal2_id,
                'start_time': datetime.now(),
                'last_activity': datetime.now(),
                'status': ConversationStatus.ACTIVE,
                'messages': [],
                'metadata': metadata or {}
            }

            # Update tracking dictionaries
            self.active_conversations[conversation_id] = conversation
            self.conversation_pairs[conversation_id] = (terminal1_id, terminal2_id)

            # Update terminal mappings
            for terminal_id in (terminal1_id, terminal2_id):
                if terminal_id not in self.terminal_conversations:
                    self.terminal_conversations[terminal_id] = set()
                self.terminal_conversations[terminal_id].add(conversation_id)

            self.logger.info(f"Created conversation {conversation_id}")
            await self._backup_conversation(conversation_id)

            return conversation_id

        except Exception as e:
            self.logger.error(f"Failed to create conversation: {str(e)}")
            raise ConversationError(f"Failed to create conversation: {str(e)}")

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
            sender_id: ID of the sending terminal
            metadata: Additional message metadata

        Returns:
            str: Added message ID

        Raises:
            ConversationError: If message cannot be added
        """
        try:
            if conversation_id not in self.active_conversations:
                raise ConversationError(f"Conversation {conversation_id} not found")

            conversation = self.active_conversations[conversation_id]

            if conversation['status'] != ConversationStatus.ACTIVE:
                raise ConversationError(
                    f"Conversation {conversation_id} is {conversation['status'].value}"
                )

            if len(conversation['messages']) >= self.config['message_limit']:
                raise ConversationError("Message limit reached for conversation")

            if sender_id not in conversation['terminal1_id', conversation['terminal2_id']]:
                raise ConversationError("Sender is not part of the conversation")

            # Create message object
            message: Message = {
                'id': f"msg-{len(conversation['messages'])}-{datetime.now().timestamp()}",
                'conversation_id': conversation_id,
                'content': content,
                'sender_id': sender_id,
                'timestamp': datetime.now(),
                'metadata': metadata or {},
                'processed': False
            }

            # Add message and update conversation
            conversation['messages'].append(message)
            conversation['last_activity'] = datetime.now()

            self.logger.debug(f"Added message to conversation {conversation_id}")
            await self._backup_conversation(conversation_id)

            return message['id']

        except Exception as e:
            self.logger.error(f"Failed to add message: {str(e)}")
            raise ConversationError(f"Failed to add message: {str(e)}")

    async def get_conversation_history(
            self,
            conversation_id: str,
            start_time: Optional[datetime] = None,
            end_time: Optional[datetime] = None,
            limit: Optional[int] = None
    ) -> List[Message]:
        """
        Get conversation history with optional filtering.

        Args:
            conversation_id: Target conversation ID
            start_time: Filter messages after this time
            end_time: Filter messages before this time
            limit: Maximum number of messages to return

        Returns:
            List[Message]: Filtered conversation history

        Raises:
            ConversationError: If history cannot be retrieved
        """
        try:
            if conversation_id not in self.active_conversations:
                raise ConversationError(f"Conversation {conversation_id} not found")

            messages = self.active_conversations[conversation_id]['messages']

            # Apply time filters
            if start_time:
                messages = [m for m in messages if m['timestamp'] >= start_time]
            if end_time:
                messages = [m for m in messages if m['timestamp'] <= end_time]

            # Apply limit
            if limit:
                messages = messages[-limit:]

            return messages

        except Exception as e:
            self.logger.error(f"Failed to get conversation history: {str(e)}")
            raise ConversationError(f"Failed to get conversation history: {str(e)}")

    async def end_conversation(
            self,
            conversation_id: str,
            reason: Optional[str] = None
    ) -> None:
        """
        End a conversation and clean up resources.

        Args:
            conversation_id: Target conversation ID
            reason: Reason for ending the conversation

        Raises:
            ConversationError: If conversation cannot be ended
        """
        try:
            if conversation_id not in self.active_conversations:
                raise ConversationError(f"Conversation {conversation_id} not found")

            conversation = self.active_conversations[conversation_id]

            # Update conversation status
            conversation['status'] = ConversationStatus.ENDED
            conversation['metadata']['end_reason'] = reason
            conversation['metadata']['end_time'] = datetime.now()

            # Remove from tracking dictionaries
            terminal1_id, terminal2_id = self.conversation_pairs[conversation_id]
            for terminal_id in (terminal1_id, terminal2_id):
                if terminal_id in self.terminal_conversations:
                    self.terminal_conversations[terminal_id].remove(conversation_id)

            del self.conversation_pairs[conversation_id]

            # Perform final backup before removal
            await self._backup_conversation(conversation_id)

            del self.active_conversations[conversation_id]

            self.logger.info(f"Ended conversation {conversation_id}")

        except Exception as e:
            self.logger.error(f"Failed to end conversation: {str(e)}")
            raise ConversationError(f"Failed to end conversation: {str(e)}")

    def _generate_conversation_id(self, terminal1_id: str, terminal2_id: str) -> str:
        """Generate a unique conversation ID."""
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        return f"conv-{terminal1_id}-{terminal2_id}-{timestamp}"

    async def _cleanup_inactive_conversations(self) -> None:
        """Periodically clean up inactive conversations."""
        while True:
            try:
                current_time = datetime.now()
                timeout = timedelta(minutes=self.config['idle_timeout'])

                for conv_id, conv in list(self.active_conversations.items()):
                    if (current_time - conv['last_activity']) > timeout:
                        await self.end_conversation(
                            conv_id,
                            reason="inactivity_timeout"
                        )

                await asyncio.sleep(60)  # Check every minute

            except Exception as e:
                self.logger.error(f"Error in cleanup task: {str(e)}")
                await asyncio.sleep(60)  # Retry after error

    async def _periodic_backup(self) -> None:
        """Periodically backup active conversations."""
        while True:
            try:
                for conversation_id in self.active_conversations:
                    await self._backup_conversation(conversation_id)

                await asyncio.sleep(self.config['backup_interval'] * 60)

            except Exception as e:
                self.logger.error(f"Error in backup task: {str(e)}")
                await asyncio.sleep(300)  # Retry after error

    async def _backup_conversation(self, conversation_id: str) -> None:
        """Backup a specific conversation."""
        try:
            conversation = self.active_conversations[conversation_id]
            # Implement actual backup logic here (e.g., to database or file)
            pass
        except Exception as e:
            self.logger.error(f"Failed to backup conversation {conversation_id}: {str(e)}")

    async def cleanup(self) -> None:
        """Clean up manager resources."""
        try:
            # Cancel background tasks
            self.cleanup_task.cancel()
            self.backup_task.cancel()

            # End all active conversations
            for conv_id in list(self.active_conversations.keys()):
                await self.end_conversation(conv_id, reason="manager_shutdown")

            self.logger.info("Conversation manager cleaned up successfully")

        except Exception as e:
            self.logger.error(f"Error during cleanup: {str(e)}")
            raise