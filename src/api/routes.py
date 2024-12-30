from fastapi import FastAPI, WebSocket, HTTPException, Depends, Security, status
from fastapi.security import APIKeyHeader
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from typing import Dict, List, Optional
from pydantic import BaseModel, Field
import json
import time
from datetime import datetime
import asyncio
from uuid import UUID


class ConversationRequest(BaseModel):
    terminal1_id: str = Field(..., description="ID of the first terminal")
    terminal2_id: str = Field(..., description="ID of the second terminal")
    metadata: Optional[Dict] = Field(default={}, description="Additional conversation metadata")


class Message(BaseModel):
    message_id: UUID
    conversation_id: UUID
    content: str
    sender_id: str
    timestamp: datetime
    metadata: Optional[Dict] = {}


class ConversationResponse(BaseModel):
    conversation_id: UUID
    terminal1_id: str
    terminal2_id: str
    status: str
    created_at: datetime
    last_activity: datetime
    message_count: int
    metadata: Dict


class ErrorResponse(BaseModel):
    error: str
    code: str
    details: Optional[Dict] = None


api_key_header = APIKeyHeader(name="X-API-Key")

app = FastAPI(
    title="AI Terminal API",
    description="API for managing AI terminal conversations in the void",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


async def verify_api_key(api_key: str = Depends(api_key_header)):
    if not api_key or not is_valid_api_key(api_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key"
        )
    return api_key


async def get_conversation_manager():
    from conversation_manager import ConversationManager
    return ConversationManager()


def is_valid_api_key(api_key: str) -> bool:
    valid_keys = {"key1", "key2"}  # In production, use secure key storage
    return api_key in valid_keys


@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc):
    return JSONResponse(
        status_code=exc.status_code,
        content=ErrorResponse(
            error=exc.detail,
            code=f"HTTP_{exc.status_code}",
            details=getattr(exc, "details", None)
        ).dict()
    )


@app.get("/", response_model=Dict[str, str])
async def read_root():
    return {
        "message": "AI Terminal API",
        "version": "1.0.0",
        "status": "operational"
    }


@app.get(
    "/conversations/{conversation_id}",
    response_model=ConversationResponse,
    dependencies=[Depends(verify_api_key)]
)
async def get_conversation(
        conversation_id: UUID,
        manager=Depends(get_conversation_manager)
):
    try:
        conversation = await manager.get_conversation(str(conversation_id))
        if not conversation:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Conversation not found"
            )
        return conversation
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve conversation",
            headers={"X-Error": str(e)}
        )


@app.get(
    "/conversations/{conversation_id}/messages",
    response_model=List[Message],
    dependencies=[Depends(verify_api_key)]
)
async def get_conversation_messages(
        conversation_id: UUID,
        limit: int = 100,
        offset: int = 0,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        manager=Depends(get_conversation_manager)
):
    try:
        messages = await manager.get_conversation_history(
            str(conversation_id),
            start_time=start_time,
            end_time=end_time,
            limit=limit,
            offset=offset
        )
        if messages is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Conversation not found"
            )
        return messages
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve messages"
        )


@app.post(
    "/conversations",
    response_model=ConversationResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(verify_api_key)]
)
async def create_conversation(
        request: ConversationRequest,
        manager=Depends(get_conversation_manager)
):
    try:
        conversation = await manager.create_conversation(
            request.terminal1_id,
            request.terminal2_id,
            metadata=request.metadata
        )
        return conversation
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create conversation"
        )


@app.delete(
    "/conversations/{conversation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(verify_api_key)]
)
async def end_conversation(
        conversation_id: UUID,
        manager=Depends(get_conversation_manager)
):
    try:
        await manager.end_conversation(str(conversation_id))
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to end conversation"
        )


@app.websocket("/ws/{terminal_id}")
async def websocket_endpoint(websocket: WebSocket, terminal_id: str):
    await websocket.accept()
    try:
        while True:
            data = await websocket.receive_text()
            message = json.loads(data)

            # Process message and get response
            response = await process_terminal_message(terminal_id, message)

            await websocket.send_json(response)
    except Exception as e:
        await websocket.close(code=1000)


async def process_terminal_message(terminal_id: str, message: Dict) -> Dict:
    try:
        # Process terminal message
        return {
            "status": "processed",
            "timestamp": datetime.now().isoformat(),
            "terminal_id": terminal_id,
            "message_id": str(UUID()),
            "data": message
        }
    except Exception as e:
        return {
            "status": "error",
            "error": str(e),
            "timestamp": datetime.now().isoformat()
        }


@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "version": "1.0.0"
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        workers=4
    )