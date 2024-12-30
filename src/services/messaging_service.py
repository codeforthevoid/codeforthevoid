from typing import Dict, Any, Optional, AsyncIterator, Set, List
import asyncio
from datetime import datetime, timedelta
import logging
from asyncio import Queue, QueueFull, Task
import uuid
from enum import Enum

from ..ai.terminal import AITerminal
from ..database.models import Message, Terminal, SystemLog
from ..utils.metrics import MetricsCollector


class MessagePriority(Enum):
    HIGH = 0
    NORMAL = 1
    LOW = 2


class MessageState(Enum):
    PENDING = "pending"
    DELIVERED = "delivered"
    FAILED = "failed"
    EXPIRED = "expired"


class MessagingError(Exception):
    pass


class MessagingService:
    QUEUE_SIZE = 1000
    MESSAGE_TIMEOUT = 30  # seconds
    MAX_RETRY_ATTEMPTS = 3
    BATCH_SIZE = 50

    def __init__(self, metrics_collector: MetricsCollector):
        self.terminals: Dict[str, AITerminal] = {}
        self.message_queues: Dict[str, Queue] = {}
        self.processing_tasks: Dict[str, Task] = {}
        self.message_states: Dict[str, Dict[str, Any]] = {}
        self.active_connections: Set[str] = set()
        self.metrics = metrics_collector
        self.logger = logging.getLogger(__name__)

        # Start background tasks
        self.cleanup_task = asyncio.create_task(self._cleanup_expired_messages())
        self.monitor_task = asyncio.create_task(self._monitor_queue_health())

    async def register_terminal(
            self,
            terminal: AITerminal,
            queue_size: Optional[int] = None
    ) -> None:
        """
        Register a terminal with the messaging service.

        Args:
            terminal: Terminal to register
            queue_size: Optional custom queue size

        Raises:
            MessagingError: If registration fails
        """
        try:
            if terminal.terminal_id in self.terminals:
                raise MessagingError("Terminal already registered")

            self.terminals[terminal.terminal_id] = terminal
            self.message_queues[terminal.terminal_id] = Queue(
                maxsize=queue_size or self.QUEUE_SIZE
            )
            self.active_connections.add(terminal.terminal_id)

            # Start message processing task for this terminal
            self.processing_tasks[terminal.terminal_id] = asyncio.create_task(
                self._process_messages(terminal.terminal_id)
            )

            self.logger.info(f"Terminal {terminal.terminal_id} registered")
            self.metrics.increment("terminals.registered")

        except Exception as e:
            self.logger.error(f"Failed to register terminal: {e}")
            raise MessagingError(f"Terminal registration failed: {str(e)}")

    async def unregister_terminal(
            self,
            terminal_id: str,
            cleanup: bool = True
    ) -> None:
        """
        Unregister a terminal from the messaging service.

        Args:
            terminal_id: Terminal ID to unregister
            cleanup: Whether to perform cleanup

        Raises:
            MessagingError: If unregistration fails
        """
        try:
            if terminal_id not in self.terminals:
                return

            # Cancel processing task
            if terminal_id in self.processing_tasks:
                self.processing_tasks[terminal_id].cancel()
                del self.processing_tasks[terminal_id]

            self.active_connections.discard(terminal_id)

            if cleanup:
                # Process remaining messages
                queue = self.message_queues[terminal_id]
                while not queue.empty():
                    try:
                        message = queue.get_nowait()
                        await self._handle_undelivered_message(message)
                    except asyncio.QueueEmpty:
                        break

            # Cleanup resources
            del self.terminals[terminal_id]
            del self.message_queues[terminal_id]

            self.logger.info(f"Terminal {terminal_id} unregistered")
            self.metrics.increment("terminals.unregistered")

        except Exception as e:
            self.logger.error(f"Failed to unregister terminal: {e}")
            raise MessagingError(f"Terminal unregistration failed: {str(e)}")

    async def send_message(
            self,
            sender_id: str,
            recipient_id: str,
            content: str,
            priority: MessagePriority = MessagePriority.NORMAL,
            metadata: Optional[Dict] = None,
            timeout: Optional[int] = None
    ) -> str:
        """
        Send a message to a terminal.

        Args:
            sender_id: Sender terminal ID
            recipient_id: Recipient terminal ID
            content: Message content
            priority: Message priority
            metadata: Optional message metadata
            timeout: Optional custom timeout

        Returns:
            str: Message ID

        Raises:
            MessagingError: If sending fails
        """
        try:
            if recipient_id not in self.active_connections:
                raise MessagingError("Recipient terminal not connected")

            message_id = str(uuid.uuid4())
            message = Message(
                id=message_id,
                content=content,
                sender_id=sender_id,
                recipient_id=recipient_id,
                timestamp=datetime.utcnow(),
                priority=priority.value,
                metadata=metadata or {}
            )

            # Track message state
            self.message_states[message_id] = {
                "state": MessageState.PENDING,
                "attempts": 0,
                "created_at": datetime.utcnow(),
                "timeout": timeout or self.MESSAGE_TIMEOUT
            }

            # Try to deliver message
            try:
                await asyncio.wait_for(
                    self.message_queues[recipient_id].put(message),
                    timeout=1.0  # Quick timeout for queue put
                )
                self.metrics.increment("messages.queued")
                return message_id

            except QueueFull:
                self.logger.warning(f"Message queue full for terminal {recipient_id}")
                self.metrics.increment("messages.queue_full")
                await self._handle_queue_full(message)
                return message_id

            except asyncio.TimeoutError:
                self.logger.warning(f"Message queue timeout for terminal {recipient_id}")
                self.metrics.increment("messages.queue_timeout")
                await self._handle_queue_timeout(message)
                return message_id

        except Exception as e:
            self.logger.error(f"Failed to send message: {e}")
            raise MessagingError(f"Message sending failed: {str(e)}")

    async def get_messages(
            self,
            terminal_id: str,
            batch_size: Optional[int] = None
    ) -> AsyncIterator[Message]:
        """
        Get messages for a terminal.

        Args:
            terminal_id: Terminal ID
            batch_size: Optional custom batch size

        Yields:
            Message: Next message

        Raises:
            MessagingError: If message retrieval fails
        """
        if terminal_id not in self.message_queues:
            raise MessagingError("Terminal not registered")

        queue = self.message_queues[terminal_id]
        batch_count = 0

        try:
            while terminal_id in self.active_connections:
                if batch_size and batch_count >= batch_size:
                    break

                try:
                    message = await asyncio.wait_for(
                        queue.get(),
                        timeout=1.0
                    )

                    # Update message state
                    if message.id in self.message_states:
                        self.message_states[message.id]["state"] = MessageState.DELIVERED

                    self.metrics.increment("messages.delivered")
                    yield message
                    batch_count += 1

                except asyncio.TimeoutError:
                    continue

        except Exception as e:
            self.logger.error(f"Error retrieving messages: {e}")
            raise MessagingError(f"Message retrieval failed: {str(e)}")

    async def _process_messages(self, terminal_id: str) -> None:
        """Process messages for a terminal."""
        while terminal_id in self.active_connections:
            try:
                queue = self.message_queues[terminal_id]
                messages = []

                # Collect batch of messages
                while len(messages) < self.BATCH_SIZE and not queue.empty():
                    try:
                        message = queue.get_nowait()
                        messages.append(message)
                    except asyncio.QueueEmpty:
                        break

                if messages:
                    await self._process_message_batch(messages)

                await asyncio.sleep(0.1)  # Prevent CPU spinning

            except Exception as e:
                self.logger.error(f"Error processing messages for {terminal_id}: {e}")
                await asyncio.sleep(1)  # Back off on error

    async def _process_message_batch(self, messages: List[Message]) -> None:
        """Process a batch of messages."""
        for message in messages:
            try:
                if message.id in self.message_states:
                    state = self.message_states[message.id]

                    # Check for expired messages
                    if self._is_message_expired(state):
                        await self._handle_expired_message(message)
                        continue

                    # Process message based on priority
                    if message.priority == MessagePriority.HIGH.value:
                        await self._process_high_priority_message(message)
                    else:
                        await self._process_normal_message(message)

            except Exception as e:
                self.logger.error(f"Error processing message {message.id}: {e}")
                await self._handle_failed_message(message)

    async def _cleanup_expired_messages(self) -> None:
        """Cleanup expired messages periodically."""
        while True:
            try:
                current_time = datetime.utcnow()

                for message_id, state in list(self.message_states.items()):
                    if self._is_message_expired(state):
                        await self._handle_expired_message_state(message_id)

                await asyncio.sleep(60)  # Check every minute

            except Exception as e:
                self.logger.error(f"Error in message cleanup: {e}")
                await asyncio.sleep(60)

    async def _monitor_queue_health(self) -> None:
        """Monitor queue health metrics."""
        while True:
            try:
                for terminal_id, queue in self.message_queues.items():
                    queue_size = queue.qsize()
                    self.metrics.gauge(f"queue.size.{terminal_id}", queue_size)

                    if queue_size >= self.QUEUE_SIZE * 0.8:  # 80% full
                        self.logger.warning(f"Queue near capacity for {terminal_id}")

                await asyncio.sleep(5)  # Check every 5 seconds

            except Exception as e:
                self.logger.error(f"Error in queue monitoring: {e}")
                await asyncio.sleep(5)

    def _is_message_expired(self, state: Dict) -> bool:
        """Check if a message has expired."""
        return (datetime.utcnow() - state["created_at"]).total_seconds() > state["timeout"]

    async def _handle_expired_message_state(self, message_id: str) -> None:
        """Handle an expired message state."""
        if message_id in self.message_states:
            self.message_states[message_id]["state"] = MessageState.EXPIRED
            self.metrics.increment("messages.expired")

    async def _handle_failed_message(self, message: Message) -> None:
        """Handle a failed message."""
        if message.id in self.message_states:
            state = self.message_states[message.id]
            state["attempts"] += 1

            if state["attempts"] >= self.MAX_RETRY_ATTEMPTS:
                state["state"] = MessageState.FAILED
                self.metrics.increment("messages.failed")
            else:
                # Requeue with backoff
                await asyncio.sleep(2 ** state["attempts"])
                await self.send_message(
                    message.sender_id,
                    message.recipient_id,
                    message.content,
                    MessagePriority(message.priority),
                    message.metadata
                )

    async def cleanup(self) -> None:
        """Clean up service resources."""
        try:
            # Cancel background tasks
            self.cleanup_task.cancel()
            self.monitor_task.cancel()

            # Unregister all terminals
            for terminal_id in list(self.terminals.keys()):
                await self.unregister_terminal(terminal_id)

            # Clear all states
            self.message_states.clear()
            self.active_connections.clear()

        except Exception as e:
            self.logger.error(f"Error during cleanup: {e}")
            raise