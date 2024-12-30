from fastapi import WebSocket, WebSocketDisconnect, WebSocketState
from typing import Dict, Set, Optional, List, Any
from datetime import datetime, timedelta
import json
import asyncio
import logging
from enum import Enum
from dataclasses import dataclass


class ConnectionState(Enum):
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    ERROR = "error"
    RECONNECTING = "reconnecting"


@dataclass
class ConnectionInfo:
    websocket: WebSocket
    state: ConnectionState
    last_heartbeat: datetime
    connection_time: datetime
    retry_count: int = 0
    metadata: Dict = None


class ConnectionManager:
    HEARTBEAT_INTERVAL = 30  # seconds
    MAX_RETRY_ATTEMPTS = 3
    RECONNECT_TIMEOUT = 60  # seconds
    MAX_MESSAGE_SIZE = 1024 * 1024  # 1MB

    def __init__(self):
        self.active_connections: Dict[str, ConnectionInfo] = {}
        self.terminal_pairs: Dict[str, Set[str]] = {}
        self.message_buffer: Dict[str, List[Dict]] = {}
        self.logger = logging.getLogger("websocket.manager")

        # Start background tasks
        self.heartbeat_task = asyncio.create_task(self._heartbeat_monitor())
        self.cleanup_task = asyncio.create_task(self._cleanup_disconnected())

    async def connect(
            self,
            terminal_id: str,
            websocket: WebSocket,
            metadata: Optional[Dict] = None
    ) -> None:
        try:
            await websocket.accept()

            connection_info = ConnectionInfo(
                websocket=websocket,
                state=ConnectionState.CONNECTED,
                last_heartbeat=datetime.now(),
                connection_time=datetime.now(),
                metadata=metadata or {}
            )

            self.active_connections[terminal_id] = connection_info
            self.message_buffer[terminal_id] = []

            self.logger.info(f"Terminal {terminal_id} connected successfully")

            # Send buffered messages
            await self._send_buffered_messages(terminal_id)

            # Start heartbeat for this connection
            asyncio.create_task(self._connection_heartbeat(terminal_id))

        except Exception as e:
            self.logger.error(f"Error connecting terminal {terminal_id}: {e}")
            await self._handle_connection_error(terminal_id, websocket, str(e))

    async def disconnect(self, terminal_id: str, code: int = 1000, reason: str = "") -> None:
        try:
            if terminal_id in self.active_connections:
                connection = self.active_connections[terminal_id]
                if connection.websocket.client_state != WebSocketState.DISCONNECTED:
                    await connection.websocket.close(code=code, reason=reason)

                connection.state = ConnectionState.DISCONNECTED
                connection.last_heartbeat = datetime.now()

                if terminal_id not in self.message_buffer:
                    self.message_buffer[terminal_id] = []

                self.logger.info(f"Terminal {terminal_id} disconnected: {reason}")
        except Exception as e:
            self.logger.error(f"Error disconnecting terminal {terminal_id}: {e}")

    async def send_message(
            self,
            terminal_id: str,
            message: Any,
            retry: bool = True
    ) -> bool:
        try:
            if terminal_id not in self.active_connections:
                if retry:
                    self._buffer_message(terminal_id, message)
                return False

            connection = self.active_connections[terminal_id]
            if connection.state != ConnectionState.CONNECTED:
                if retry:
                    self._buffer_message(terminal_id, message)
                return False

            message_data = self._prepare_message(message)
            await connection.websocket.send_json(message_data)

            self.logger.debug(f"Message sent to terminal {terminal_id}")
            return True

        except WebSocketDisconnect:
            await self._handle_disconnect(terminal_id)
            if retry:
                self._buffer_message(terminal_id, message)
            return False
        except Exception as e:
            self.logger.error(f"Error sending message to terminal {terminal_id}: {e}")
            await self._handle_connection_error(terminal_id, None, str(e))
            if retry:
                self._buffer_message(terminal_id, message)
            return False

    async def broadcast(self, message: Any, exclude: Optional[Set[str]] = None) -> None:
        exclude = exclude or set()
        tasks = []

        for terminal_id in self.active_connections:
            if terminal_id not in exclude:
                tasks.append(self.send_message(terminal_id, message, retry=False))

        await asyncio.gather(*tasks, return_exceptions=True)

    async def get_connection_info(self, terminal_id: str) -> Optional[Dict]:
        if terminal_id not in self.active_connections:
            return None

        connection = self.active_connections[terminal_id]
        return {
            "state": connection.state.value,
            "connected_since": connection.connection_time.isoformat(),
            "last_heartbeat": connection.last_heartbeat.isoformat(),
            "retry_count": connection.retry_count,
            "metadata": connection.metadata
        }

    def _prepare_message(self, message: Any) -> Dict:
        if isinstance(message, str):
            message = {"content": message}
        elif not isinstance(message, dict):
            message = {"data": message}

        message.update({
            "timestamp": datetime.now().isoformat(),
            "type": "message"
        })

        return message

    def _buffer_message(self, terminal_id: str, message: Any) -> None:
        if terminal_id not in self.message_buffer:
            self.message_buffer[terminal_id] = []

        self.message_buffer[terminal_id].append(self._prepare_message(message))

        # Limit buffer size
        if len(self.message_buffer[terminal_id]) > 1000:
            self.message_buffer[terminal_id] = self.message_buffer[terminal_id][-1000:]

    async def _send_buffered_messages(self, terminal_id: str) -> None:
        if terminal_id in self.message_buffer and self.message_buffer[terminal_id]:
            messages = self.message_buffer[terminal_id]
            self.message_buffer[terminal_id] = []

            for message in messages:
                await self.send_message(terminal_id, message, retry=True)

    async def _connection_heartbeat(self, terminal_id: str) -> None:
        while terminal_id in self.active_connections:
            connection = self.active_connections[terminal_id]
            try:
                if connection.state == ConnectionState.CONNECTED:
                    await connection.websocket.send_json({
                        "type": "heartbeat",
                        "timestamp": datetime.now().isoformat()
                    })
                    connection.last_heartbeat = datetime.now()
            except Exception as e:
                self.logger.error(f"Heartbeat failed for terminal {terminal_id}: {e}")
                await self._handle_connection_error(terminal_id, connection.websocket, str(e))
                break

            await asyncio.sleep(self.HEARTBEAT_INTERVAL)

    async def _heartbeat_monitor(self) -> None:
        while True:
            try:
                current_time = datetime.now()
                for terminal_id, connection in list(self.active_connections.items()):
                    if connection.state == ConnectionState.CONNECTED:
                        time_since_heartbeat = current_time - connection.last_heartbeat
                        if time_since_heartbeat > timedelta(seconds=self.HEARTBEAT_INTERVAL * 2):
                            self.logger.warning(f"Terminal {terminal_id} heartbeat timeout")
                            await self._handle_connection_error(
                                terminal_id,
                                connection.websocket,
                                "Heartbeat timeout"
                            )
            except Exception as e:
                self.logger.error(f"Error in heartbeat monitor: {e}")

            await asyncio.sleep(self.HEARTBEAT_INTERVAL)

    async def _cleanup_disconnected(self) -> None:
        while True:
            try:
                current_time = datetime.now()
                for terminal_id, connection in list(self.active_connections.items()):
                    if connection.state == ConnectionState.DISCONNECTED:
                        time_since_disconnect = current_time - connection.last_heartbeat
                        if time_since_disconnect > timedelta(seconds=self.RECONNECT_TIMEOUT):
                            self._cleanup_terminal_data(terminal_id)
            except Exception as e:
                self.logger.error(f"Error in cleanup task: {e}")

            await asyncio.sleep(60)  # Run cleanup every minute

    async def _handle_connection_error(
            self,
            terminal_id: str,
            websocket: Optional[WebSocket],
            error: str
    ) -> None:
        if terminal_id in self.active_connections:
            connection = self.active_connections[terminal_id]
            connection.state = ConnectionState.ERROR
            connection.retry_count += 1

            if connection.retry_count > self.MAX_RETRY_ATTEMPTS:
                await self.disconnect(terminal_id, code=1011, reason=f"Max retries exceeded: {error}")
            else:
                connection.state = ConnectionState.RECONNECTING

    async def _handle_disconnect(self, terminal_id: str) -> None:
        if terminal_id in self.active_connections:
            connection = self.active_connections[terminal_id]
            connection.state = ConnectionState.DISCONNECTED
            connection.last_heartbeat = datetime.now()

    def _cleanup_terminal_data(self, terminal_id: str) -> None:
        self.active_connections.pop(terminal_id, None)
        self.message_buffer.pop(terminal_id, None)

        # Clean up terminal pairs
        for pair_id, terminals in list(self.terminal_pairs.items()):
            if terminal_id in terminals:
                terminals.remove(terminal_id)
                if not terminals:
                    self.terminal_pairs.pop(pair_id, None)

    async def cleanup(self) -> None:
        try:
            # Cancel background tasks
            self.heartbeat_task.cancel()
            self.cleanup_task.cancel()

            # Disconnect all terminals
            disconnect_tasks = []
            for terminal_id in list(self.active_connections.keys()):
                disconnect_tasks.append(
                    self.disconnect(terminal_id, code=1001, reason="Server shutdown")
                )

            if disconnect_tasks:
                await asyncio.gather(*disconnect_tasks, return_exceptions=True)

            # Clear all data
            self.active_connections.clear()
            self.terminal_pairs.clear()
            self.message_buffer.clear()

        except Exception as e:
            self.logger.error(f"Error during cleanup: {e}")
            raise