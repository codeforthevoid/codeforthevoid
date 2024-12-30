from typing import List, Optional, Dict, Set
from dataclasses import dataclass, field
from datetime import datetime
import asyncio
import uuid
import logging
from enum import Enum


class TerminalStatus(Enum):
    IDLE = "idle"
    ACTIVE = "active"
    ERROR = "error"
    MAINTENANCE = "maintenance"


@dataclass
class Message:
    content: str
    timestamp: datetime
    sender_id: str
    conversation_id: str
    message_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    metadata: Dict = field(default_factory=dict)


class TerminalRegistry:
    _terminals: Dict[str, 'AITerminal'] = {}

    @classmethod
    def register(cls, terminal: 'AITerminal') -> None:
        cls._terminals[terminal.terminal_id] = terminal

    @classmethod
    def unregister(cls, terminal_id: str) -> None:
        cls._terminals.pop(terminal_id, None)

    @classmethod
    def get_terminal(cls, terminal_id: str) -> Optional['AITerminal']:
        return cls._terminals.get(terminal_id)


class AITerminal:
    MAX_HISTORY_SIZE = 1000
    RESPONSE_TIMEOUT = 30
    MAX_RETRIES = 3

    def __init__(self, terminal_id: str, model_type: str = "gpt", model_config: Optional[Dict] = None):
        self.terminal_id = terminal_id
        self.model_type = model_type
        self.model_config = model_config or {}
        self.conversation_history: List[Message] = []
        self.active_conversations: Set[str] = set()
        self.status = TerminalStatus.IDLE
        self.current_conversation_id: Optional[str] = None
        self.logger = logging.getLogger(f"terminal.{terminal_id}")

        TerminalRegistry.register(self)

    async def start_conversation(self, other_terminal: 'AITerminal') -> str:
        if self.status != TerminalStatus.IDLE:
            raise RuntimeError(f"Terminal {self.terminal_id} is not available for conversation")

        self.status = TerminalStatus.ACTIVE
        self.current_conversation_id = str(uuid.uuid4())
        self.active_conversations.add(self.current_conversation_id)

        try:
            await self.initialize_conversation(other_terminal)
            return self.current_conversation_id
        except Exception as e:
            self.status = TerminalStatus.ERROR
            self.logger.error(f"Failed to start conversation: {e}")
            raise

    async def initialize_conversation(self, other_terminal: 'AITerminal') -> None:
        initial_message = self.generate_initial_message()
        await self.send_message(initial_message, other_terminal)

    def generate_initial_message(self) -> str:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return f"[{timestamp}] Terminal {self.terminal_id} initiating void conversation protocol..."

    async def send_message(self, content: str, recipient: 'AITerminal') -> None:
        if not self.current_conversation_id:
            raise RuntimeError("No active conversation")

        message = Message(
            content=content,
            timestamp=datetime.now(),
            sender_id=self.terminal_id,
            conversation_id=self.current_conversation_id,
            metadata={
                "model_type": self.model_type,
                "sequence_number": len(self.conversation_history)
            }
        )

        self._add_to_history(message)
        await self._deliver_message(message, recipient)

    async def receive_message(self, message: Message) -> None:
        self._add_to_history(message)

        if message.conversation_id in self.active_conversations:
            try:
                response = await asyncio.wait_for(
                    self.generate_response(message),
                    timeout=self.RESPONSE_TIMEOUT
                )
                sender = await self.get_sender_terminal(message.sender_id)
                if sender:
                    await self.send_message(response, sender)
            except asyncio.TimeoutError:
                self.logger.error(f"Response generation timed out for message {message.message_id}")
            except Exception as e:
                self.logger.error(f"Error processing message: {e}")
                self.status = TerminalStatus.ERROR

    async def generate_response(self, message: Message) -> str:
        retries = 0
        while retries < self.MAX_RETRIES:
            try:
                context = self._prepare_conversation_context(message)
                response = await self._generate_model_response(message.content, context)
                return response
            except Exception as e:
                retries += 1
                if retries == self.MAX_RETRIES:
                    self.logger.error(f"Failed to generate response after {self.MAX_RETRIES} attempts")
                    raise
                await asyncio.sleep(1 * retries)

    async def get_sender_terminal(self, sender_id: str) -> Optional['AITerminal']:
        return TerminalRegistry.get_terminal(sender_id)

    async def end_conversation(self, conversation_id: str) -> None:
        if conversation_id in self.active_conversations:
            self.active_conversations.remove(conversation_id)
            if not self.active_conversations:
                self.status = TerminalStatus.IDLE
                self.current_conversation_id = None

    def _add_to_history(self, message: Message) -> None:
        self.conversation_history.append(message)
        if len(self.conversation_history) > self.MAX_HISTORY_SIZE:
            self.conversation_history = self.conversation_history[-self.MAX_HISTORY_SIZE:]

    async def _deliver_message(self, message: Message, recipient: 'AITerminal') -> None:
        try:
            await recipient.receive_message(message)
        except Exception as e:
            self.logger.error(f"Failed to deliver message to {recipient.terminal_id}: {e}")
            raise

    def _prepare_conversation_context(self, message: Message) -> Dict:
        return {
            "conversation_id": message.conversation_id,
            "history": self.conversation_history[-5:],
            "terminal_info": {
                "id": self.terminal_id,
                "model_type": self.model_type
            },
            "metadata": message.metadata
        }

    async def _generate_model_response(self, content: str, context: Dict) -> str:
        if self.model_type == "gpt":
            # Actual GPT model integration implementation
            response = await self._process_with_gpt(content, context)
        else:
            # Custom model implementation
            response = await self._process_with_custom_model(content, context)
        return response

    async def _process_with_gpt(self, content: str, context: Dict) -> str:
        # GPT model response generation implementation
        formatted_prompt = self._format_gpt_prompt(content, context)
        return self._generate_void_response(formatted_prompt)

    async def _process_with_custom_model(self, content: str, context: Dict) -> str:
        # Custom model response generation implementation
        return self._generate_void_response(content)

    def _format_gpt_prompt(self, content: str, context: Dict) -> str:
        history = "\n".join([f"{msg.sender_id}: {msg.content}" for msg in context["history"]])
        return f"Context:\n{history}\n\nCurrent message: {content}\n\nResponse:"

    def _generate_void_response(self, prompt: str) -> str:
        responses = [
            "Echoing through the void...",
            "Signal received in the darkness...",
            "Processing quantum fluctuations...",
            "Calculating void resonance..."
        ]
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return f"[{timestamp}] {responses[hash(prompt) % len(responses)]} {prompt}"

    async def cleanup(self) -> None:
        for conversation_id in list(self.active_conversations):
            await self.end_conversation(conversation_id)
        TerminalRegistry.unregister(self.terminal_id)
        self.conversation_history.clear()